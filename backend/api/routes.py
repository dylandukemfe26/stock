"""HTTP routes. Thin translation layer over the three core engines."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.config import get_settings
from backend.core.cache import TTLCache
from backend.core.events import get_bus
from backend.data.provider import DataProvider, get_provider
from backend.journal import JournalService
from backend.liquidity import LiquidityService
from backend.models.schemas import (
    JournalSummary,
    LiquidityScore,
    PositionSizeRequest,
    PositionSizeResult,
    Regime,
    RegimeStats,
    RiskStatus,
    RsSnapshot,
    ScannerHit,
    SetupSignal,
    SetupStats,
    TradeCloseRequest,
    TradeOpenRequest,
    TradeRow,
)
from backend.regime import RegimeDetector
from backend.risk.engine import RiskConfig, RiskEngine
from backend.rs import RSService
from backend.scanner.scanner import GapVolumeScanner, ScannerConfig
from backend.setups.detector import SetupDetector


# Single shared engine instance for MVP. In production replace with a
# per-user session or Redis-backed state.
_risk_engine = RiskEngine()
_journal = JournalService(bus=get_bus())

# Shared caches so RS + Regime don't refetch SPY bars every request.
_bar_cache = TTLCache(default_ttl_seconds=10.0)
_quote_cache = TTLCache(default_ttl_seconds=5.0)
_regime_cache = TTLCache(default_ttl_seconds=60.0)


def get_data_provider() -> DataProvider:
    return get_provider()


def get_risk_engine() -> RiskEngine:
    return _risk_engine


def get_journal() -> JournalService:
    return _journal


def get_rs_service(
    provider: DataProvider = Depends(get_data_provider),
) -> RSService:
    return RSService(provider, cache=_bar_cache)


def get_liquidity_service(
    provider: DataProvider = Depends(get_data_provider),
) -> LiquidityService:
    return LiquidityService(provider, cache=_quote_cache)


def get_regime_detector(
    provider: DataProvider = Depends(get_data_provider),
) -> RegimeDetector:
    return RegimeDetector(provider, cache=_regime_cache)


router = APIRouter(prefix="/api", tags=["trading"])


# ---------------- Scanner ----------------
@router.get("/scanner", response_model=list[ScannerHit])
def run_scanner(
    min_gap_pct: float | None = Query(None),
    min_rvol: float | None = Query(None),
    top_n: int = Query(30, ge=1, le=100),
    provider: DataProvider = Depends(get_data_provider),
    rs: RSService = Depends(get_rs_service),
    liq: LiquidityService = Depends(get_liquidity_service),
) -> list[ScannerHit]:
    s = get_settings()
    cfg = ScannerConfig(
        min_gap_pct=min_gap_pct if min_gap_pct is not None else s.min_gap_pct,
        min_relative_volume=min_rvol if min_rvol is not None else s.min_relative_volume,
        min_premarket_volume=s.min_premarket_volume,
        min_price=s.min_price,
        max_price=s.max_price,
        min_avg_dollar_volume=s.min_avg_dollar_volume,
        top_n=top_n,
    )
    scanner = GapVolumeScanner(provider, cfg, rs_service=rs, liquidity_service=liq)
    return scanner.scan()


# ---------------- Relative Strength ----------------
@router.get("/rs/{symbol}", response_model=RsSnapshot)
def rs_snapshot(
    symbol: str,
    rs: RSService = Depends(get_rs_service),
) -> RsSnapshot:
    snap = rs.snapshot(symbol.upper())
    if snap is None:
        raise HTTPException(404, f"no RS data for {symbol}")
    return snap


# ---------------- Liquidity ----------------
@router.get("/liquidity/{symbol}", response_model=LiquidityScore)
def liquidity_score(
    symbol: str,
    liq: LiquidityService = Depends(get_liquidity_service),
) -> LiquidityScore:
    return liq.score(symbol.upper())


# ---------------- Regime ----------------
@router.get("/regime/current", response_model=Regime)
def regime_current(
    detector: RegimeDetector = Depends(get_regime_detector),
) -> Regime:
    return detector.current()


# ---------------- Setups ----------------
@router.get("/setups/{symbol}", response_model=list[SetupSignal])
def detect_setups(
    symbol: str,
    provider: DataProvider = Depends(get_data_provider),
) -> list[SetupSignal]:
    symbol = symbol.upper()
    bars = provider.get_intraday_bars(symbol, date.today())
    if not bars:
        raise HTTPException(404, f"no bars for {symbol}")
    stats = provider.get_daily_stats(symbol, date.today())
    detector = SetupDetector()
    # Prior-day high/low proxy: prev_close +/- ATR. Replace with real prior-day
    # data in the Polygon provider.
    pdh = stats.prev_close + stats.atr_14d * 0.5
    pdl = stats.prev_close - stats.atr_14d * 0.5
    return detector.detect(symbol, bars, prev_day_high=pdh, prev_day_low=pdl)


# ---------------- Risk ----------------
@router.post("/risk/size", response_model=PositionSizeResult)
def size_position(
    req: PositionSizeRequest,
    engine: RiskEngine = Depends(get_risk_engine),
) -> PositionSizeResult:
    return engine.size_position(req)


class PreTradeCheck(BaseModel):
    proposed_dollar_risk: float


class PreTradeResult(BaseModel):
    allowed: bool
    reasons: list[str]


@router.post("/risk/check", response_model=PreTradeResult)
def check_trade(
    body: PreTradeCheck,
    engine: RiskEngine = Depends(get_risk_engine),
) -> PreTradeResult:
    ok, reasons = engine.can_open_trade(proposed_dollar_risk=body.proposed_dollar_risk)
    return PreTradeResult(allowed=ok, reasons=reasons)


@router.get("/risk/status", response_model=RiskStatus)
def risk_status(
    unrealized: float = Query(0.0),
    engine: RiskEngine = Depends(get_risk_engine),
) -> RiskStatus:
    return engine.status(unrealized_pnl=unrealized)


@router.post("/risk/halt")
def risk_halt(
    engine: RiskEngine = Depends(get_risk_engine),
) -> dict:
    engine.halt_trading("manual")
    return {"halted": True}


class RiskConfigUpdate(BaseModel):
    account_equity: float
    max_risk_per_trade_pct: float = 1.0
    max_daily_loss_pct: float = 3.0
    max_open_positions: int = 5
    revenge_cooloff_losses: int = 2
    revenge_cooloff_minutes: int = 15
    max_position_pct_of_equity: float = 25.0


@router.post("/risk/configure")
def configure_risk(
    cfg: RiskConfigUpdate,
    engine: RiskEngine = Depends(get_risk_engine),
) -> dict:
    engine.cfg = RiskConfig(**cfg.model_dump())
    return {"ok": True, "config": cfg.model_dump()}


# ---------------- Journal / Trades ----------------
@router.post("/trades/open", response_model=TradeRow)
def open_trade(
    req: TradeOpenRequest,
    journal: JournalService = Depends(get_journal),
    engine: RiskEngine = Depends(get_risk_engine),
) -> TradeRow:
    from backend.risk.engine import OpenPosition

    row = journal.open(req)
    engine.record_open(
        OpenPosition(
            symbol=row.symbol,
            side=row.side,
            shares=row.shares,
            entry=row.entry,
            stop=row.stop,
            opened_at=row.opened_at,
        )
    )
    return row


@router.post("/trades/close", response_model=TradeRow)
def close_trade(
    req: TradeCloseRequest,
    journal: JournalService = Depends(get_journal),
    engine: RiskEngine = Depends(get_risk_engine),
) -> TradeRow:
    try:
        row = journal.close(req.trade_id, req.exit_price, notes=req.notes)
    except KeyError:
        raise HTTPException(404, f"trade {req.trade_id} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    # Keep the risk engine's daily P&L in lockstep with the journal.
    try:
        engine.record_close(row.symbol, req.exit_price)
    except ValueError:
        pass
    return row


class MoveStopRequest(BaseModel):
    new_stop: float


@router.post("/trades/{trade_id}/move-stop", response_model=TradeRow)
def move_stop(
    trade_id: int,
    body: MoveStopRequest,
    journal: JournalService = Depends(get_journal),
) -> TradeRow:
    try:
        return journal.move_stop(trade_id, body.new_stop)
    except KeyError:
        raise HTTPException(404, f"trade {trade_id} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/journal/today", response_model=list[TradeRow])
def journal_today(
    journal: JournalService = Depends(get_journal),
) -> list[TradeRow]:
    return journal.today()


@router.get("/journal/summary", response_model=JournalSummary)
def journal_summary(
    journal: JournalService = Depends(get_journal),
) -> JournalSummary:
    return journal.summary()


@router.get("/journal/by-setup", response_model=list[SetupStats])
def journal_by_setup(
    journal: JournalService = Depends(get_journal),
) -> list[SetupStats]:
    return journal.by_setup()


@router.get("/journal/by-regime", response_model=list[RegimeStats])
def journal_by_regime(
    journal: JournalService = Depends(get_journal),
) -> list[RegimeStats]:
    return journal.by_regime()
