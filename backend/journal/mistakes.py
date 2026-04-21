"""Rule-based trader mistake detection.

Keep the rules few and unambiguous. Every rule is a pure function over the
trade being closed plus the trader's recent history. False positives make the
journal annoying; under-reporting is better than over-reporting.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.models.schemas import MistakeType, Side


@dataclass
class TradeSnapshot:
    """Small struct with just the fields a mistake rule needs."""
    symbol: str
    side: Side
    shares: int
    entry: float
    stop: float
    target1: float | None
    original_stop: float   # what the stop was at open
    exit_price: float
    pnl: float
    opened_at: datetime
    closed_at: datetime


def detect_mistakes(
    trade: TradeSnapshot,
    recent_trades: list[TradeSnapshot],
) -> list[MistakeType]:
    out: list[MistakeType] = []

    if _early_exit(trade):
        out.append(MistakeType.EARLY_EXIT)
    if _moved_stop_away(trade):
        out.append(MistakeType.MOVED_STOP_AWAY)
    if _sized_up_after_loss(trade, recent_trades):
        out.append(MistakeType.SIZED_UP_AFTER_LOSS)
    return out


def _early_exit(t: TradeSnapshot) -> bool:
    """Closed a winner well before planned T1 without hitting stop."""
    if t.target1 is None:
        return False
    if t.pnl <= 0:
        return False
    planned_move = abs(t.target1 - t.entry)
    realized_move = abs(t.exit_price - t.entry)
    if planned_move <= 0:
        return False
    return realized_move < 0.4 * planned_move


def _moved_stop_away(t: TradeSnapshot) -> bool:
    """The stop at close is further from entry than it was at open.

    Hard mistake — widening a stop while the trade is against you is the
    single most common way day traders blow up.
    """
    if t.original_stop == t.stop:
        return False
    orig_dist = abs(t.entry - t.original_stop)
    final_dist = abs(t.entry - t.stop)
    return final_dist > orig_dist * 1.01  # small epsilon for float noise


def _sized_up_after_loss(
    trade: TradeSnapshot, recent_trades: list[TradeSnapshot]
) -> bool:
    """Previous trade was a loss and current shares are >= 1.5× prior."""
    if not recent_trades:
        return False
    prev = recent_trades[-1]
    if prev.pnl >= 0:
        return False
    if prev.shares <= 0:
        return False
    return trade.shares >= prev.shares * 1.5
