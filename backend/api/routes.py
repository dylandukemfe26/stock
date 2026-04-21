"""HTTP routes. Thin translation layer over the three core engines."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.config import get_settings
from backend.data.provider import DataProvider, get_provider
from backend.models.schemas import (
    PositionSizeRequest,
    PositionSizeResult,
    RiskStatus,
    ScannerHit,
    SetupSignal,
)
from backend.risk.engine import RiskConfig, RiskEngine
from backend.scanner.scanner import GapVolumeScanner, ScannerConfig
from backend.setups.detector import SetupDetector


# Single shared engine instance for MVP. In production replace with a
# per-user session or Redis-backed state.
_risk_engine = RiskEngine()


def get_data_provider() -> DataProvider:
    return get_provider()


def get_risk_engine() -> RiskEngine:
    return _risk_engine


router = APIRouter(prefix="/api", tags=["trading"])


# ---------------- Scanner ----------------
@router.get("/scanner", response_model=list[ScannerHit])
def run_scanner(
    min_gap_pct: float | None = Query(None),
    min_rvol: float | None = Query(None),
    top_n: int = Query(30, ge=1, le=100),
    provider: DataProvider = Depends(get_data_provider),
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
    scanner = GapVolumeScanner(provider, cfg)
    return scanner.scan()


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
