"""Shared pydantic schemas used across scanner, setups, and risk modules."""
from __future__ import annotations

from datetime import datetime, date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


class Bar(BaseModel):
    """One OHLCV bar. Timeframe is implicit in context."""
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


class Quote(BaseModel):
    symbol: str
    last: float
    bid: float | None = None
    ask: float | None = None
    ts: datetime


class DailyStats(BaseModel):
    """Per-symbol daily reference data used by the scanner."""
    symbol: str
    prev_close: float
    avg_volume_20d: int
    avg_dollar_volume_20d: float
    atr_14d: float
    float_shares: int | None = None


class PremarketSnapshot(BaseModel):
    symbol: str
    last: float
    premarket_high: float
    premarket_low: float
    premarket_volume: int
    asof: datetime


class ScannerHit(BaseModel):
    symbol: str
    price: float
    gap_pct: float
    relative_volume: float
    premarket_volume: int
    avg_dollar_volume: float
    atr_14d: float
    score: float
    reasons: list[str] = Field(default_factory=list)
    side_bias: Side | None = None  # long if gap up, short if gap down
    asof: datetime
    # Phase 1 enrichments (optional so backtests/tests without the services work)
    rs_pct: float | None = None
    rs_persistence: float | None = None
    rs_flag: str | None = None
    liquidity_tier: str | None = None
    liquidity_score: float | None = None
    spread_pct: float | None = None


class SetupType(str, Enum):
    ORB = "opening_range_breakout"
    VWAP_RECLAIM = "vwap_reclaim"
    VWAP_REJECTION = "vwap_rejection"
    PULLBACK = "pullback_in_trend"
    LEVEL_BREAK = "level_break"


class TradePlan(BaseModel):
    entry: float
    stop: float
    target1: float
    risk_per_share: float
    reward_risk_t1: float


class SetupSignal(BaseModel):
    symbol: str
    setup: SetupType
    side: Side
    plan: TradePlan
    score: float  # 0..100
    reasons: list[str]
    triggered_at: datetime


class PositionSizeRequest(BaseModel):
    account_equity: float
    risk_per_trade_pct: float = Field(ge=0.01, le=5.0)
    entry: float
    stop: float
    side: Side = Side.LONG
    atr: float | None = None
    max_shares: int | None = None


class PositionSizeResult(BaseModel):
    shares: int
    dollar_risk: float
    dollar_exposure: float
    risk_per_share: float
    stop_distance_pct: float
    notes: list[str] = Field(default_factory=list)


class StrengthFlag(str, Enum):
    LEADER = "leader"
    LAGGARD = "laggard"
    NEUTRAL = "neutral"


class RsSnapshot(BaseModel):
    symbol: str
    benchmark: str
    session_rs_pct: float         # session return spread vs benchmark, in %
    persistence: float            # 0..1: fraction of recent bars outperforming
    strength: StrengthFlag
    asof: datetime


class LiquidityTier(str, Enum):
    A = "A"
    B = "B"
    C = "C"


class LiquidityScore(BaseModel):
    symbol: str
    spread_pct: float
    est_round_trip_slippage_pct: float
    tradeability: float           # 0..100
    tier: LiquidityTier
    asof: datetime


class RegimeLabel(str, Enum):
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    CHOP = "chop"
    VOLATILE = "volatile"
    MIXED = "mixed"


class Regime(BaseModel):
    label: RegimeLabel
    trend_score: float            # abs(slope) / ATR; >0.5 = strong trend
    range_ratio: float            # last bar range / ATR
    pct_above_vwap: float | None  # breadth proxy, optional
    vix: float | None = None
    asof: datetime


class RiskStatus(BaseModel):
    trading_day: date
    account_equity: float
    realized_pnl: float
    unrealized_pnl: float
    total_risk_used: float  # sum of open dollar risk
    open_positions: int
    daily_loss_limit: float
    daily_loss_remaining: float
    halted: bool
    halt_reasons: list[str] = Field(default_factory=list)


# ---------------- Journal ----------------

class MistakeType(str, Enum):
    EARLY_EXIT = "early_exit"
    MOVED_STOP_AWAY = "moved_stop_away"
    SIZED_UP_AFTER_LOSS = "sized_up_after_loss"
    NO_STOP = "no_stop"
    OVERSIZED = "oversized"


class TradeOpenRequest(BaseModel):
    symbol: str
    side: Side
    shares: int = Field(gt=0)
    entry: float
    stop: float
    target1: float | None = None
    setup: SetupType | None = None
    signal_id: int | None = None
    # Context captured at open for later analytics
    regime_at_open: str | None = None
    rs_pct_at_open: float | None = None
    liquidity_tier_at_open: str | None = None
    catalyst_at_open: str | None = None
    notes: str = ""
    emotion: str | None = None


class TradeCloseRequest(BaseModel):
    trade_id: int
    exit_price: float
    notes: str | None = None


class TradeRow(BaseModel):
    id: int
    symbol: str
    side: Side
    shares: int
    entry: float
    stop: float
    target1: float | None
    exit_price: float | None
    pnl: float | None
    realized_r: float | None       # pnl / (shares * |entry - stop|)
    setup: str | None
    opened_at: datetime
    closed_at: datetime | None
    hold_minutes: int | None
    regime_at_open: str | None
    rs_pct_at_open: float | None
    liquidity_tier_at_open: str | None
    mistakes: list[str] = Field(default_factory=list)
    notes: str = ""


class SetupStats(BaseModel):
    setup: str
    n: int
    win_rate: float          # 0..1
    avg_r: float             # average realized R across closed trades
    expectancy_r: float      # win_rate*avg_win_r + (1-win_rate)*avg_loss_r
    total_pnl: float


class RegimeStats(BaseModel):
    regime: str
    n: int
    win_rate: float
    expectancy_r: float
    total_pnl: float


class JournalSummary(BaseModel):
    trading_day: date
    n_total: int
    n_open: int
    n_closed: int
    win_rate: float
    total_pnl: float
    avg_r: float
    best_trade_pnl: float
    worst_trade_pnl: float
    mistakes_count: dict[str, int]
