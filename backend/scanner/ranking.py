"""Ranking formula for the scanner.

Inputs are already in human-readable units (percent, ratio, dollars).
We clip each factor so no single component dominates the sum, then combine.

Components (weights deliberately round, easy to tune):
  - gap magnitude        (35%)   bigger abs gaps get attention
  - relative volume      (35%)   conviction / participation
  - ATR% (range)         (15%)   need enough movement for day trading
  - liquidity bonus      (15%)   prefer names you can get in and out of

Why these weights:
  - Gap and RVOL together capture "stocks in play". Either alone is weaker.
  - ATR% filters out names that gapped but chop sideways all day.
  - Liquidity gives a tiebreaker so we don't rank thin names too high.
"""
from __future__ import annotations

import math

_GAP_CLIP = 25.0        # percent; cap the gap contribution at 25%
_RVOL_CLIP = 15.0       # relative volume ratio
_ATR_PCT_CLIP = 15.0    # percent


def _clip(value: float, upper: float) -> float:
    return max(0.0, min(value, upper))


def rank_score(
    *,
    gap_pct: float,
    rvol: float,
    atr_pct: float,
    avg_dollar_volume: float,
) -> tuple[float, list[str]]:
    gap_component = _clip(abs(gap_pct), _GAP_CLIP) / _GAP_CLIP * 100.0
    rvol_component = _clip(rvol, _RVOL_CLIP) / _RVOL_CLIP * 100.0
    atr_component = _clip(atr_pct, _ATR_PCT_CLIP) / _ATR_PCT_CLIP * 100.0
    # Log-scaled liquidity: 10M -> ~7, 100M -> ~8, 1B -> ~9. Normalize to 0..100.
    liq_component = max(0.0, min(100.0, (math.log10(max(avg_dollar_volume, 1)) - 6) * 25))

    score = (
        0.35 * gap_component
        + 0.35 * rvol_component
        + 0.15 * atr_component
        + 0.15 * liq_component
    )

    reasons: list[str] = []
    reasons.append(f"gap {gap_pct:+.1f}%")
    reasons.append(f"RVOL {rvol:.1f}x")
    reasons.append(f"ATR% {atr_pct:.1f}")
    if avg_dollar_volume >= 500_000_000:
        reasons.append("very liquid")
    elif avg_dollar_volume >= 100_000_000:
        reasons.append("liquid")

    return score, reasons
