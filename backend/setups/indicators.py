"""Small, dependency-free indicator helpers used by the setup detector.

Using numpy would work too, but these are tiny enough that plain Python
keeps the code easy to read and trivially unit-testable.
"""
from __future__ import annotations

from backend.models.schemas import Bar


def vwap_series(bars: list[Bar]) -> list[float]:
    """Typical-price VWAP, cumulative from session start."""
    cum_pv = 0.0
    cum_v = 0
    out: list[float] = []
    for b in bars:
        typical = (b.high + b.low + b.close) / 3.0
        cum_pv += typical * b.volume
        cum_v += b.volume
        out.append(cum_pv / cum_v if cum_v else b.close)
    return out


def opening_range(bars: list[Bar], minutes: int = 5) -> tuple[float, float] | None:
    """High/low over first N minutes. Returns None if not enough data."""
    if len(bars) < minutes:
        return None
    window = bars[:minutes]
    return max(b.high for b in window), min(b.low for b in window)


def atr(bars: list[Bar], period: int = 14) -> float | None:
    """Simple ATR on the provided bars. Requires `period + 1` bars."""
    if len(bars) <= period:
        return None
    trs: list[float] = []
    for i in range(1, len(bars)):
        prev_close = bars[i - 1].close
        b = bars[i]
        tr = max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close))
        trs.append(tr)
    tail = trs[-period:]
    return sum(tail) / len(tail)


def crossed_above(prev: float, curr: float, level: float) -> bool:
    return prev <= level and curr > level


def crossed_below(prev: float, curr: float, level: float) -> bool:
    return prev >= level and curr < level
