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
