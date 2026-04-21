"""Gap-and-Volume Scanner.

Job: reduce ~8000 tickers to the 10-30 most tradeable names for today.

Rules (MVP):
  1. Hard filters: price band, avg $ volume floor, minimum premarket volume.
  2. Gap filter: abs(gap_pct) >= min_gap_pct.
  3. Relative volume filter: RVOL >= min_relative_volume where
       RVOL = premarket_volume / (avg_volume_20d * premarket_share)
     We normalize premarket against a rough "expected premarket share" of
     the 20d average (5% is a reasonable MVP assumption).
  4. Score = weighted sum of gap, RVOL, ATR%, liquidity. See `ranking.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from backend.data.provider import DataProvider
from backend.models.schemas import ScannerHit, Side
from backend.scanner.ranking import rank_score

# Assumption: ~5% of normal daily volume typically trades premarket for
# liquid names. Tune per universe.
_PREMARKET_SHARE = 0.05


@dataclass
class ScannerConfig:
    min_gap_pct: float = 3.0
    min_relative_volume: float = 2.0
    min_premarket_volume: int = 50_000
    min_price: float = 2.0
    max_price: float = 2_000.0
    min_avg_dollar_volume: float = 10_000_000.0
    top_n: int = 30


class GapVolumeScanner:
    def __init__(self, provider: DataProvider, config: ScannerConfig | None = None):
        self.provider = provider
        self.cfg = config or ScannerConfig()

    def scan(self, asof: date | None = None) -> list[ScannerHit]:
        asof = asof or date.today()
        hits: list[ScannerHit] = []

        for symbol in self.provider.list_universe():
            hit = self._evaluate(symbol, asof)
            if hit is not None:
                hits.append(hit)

        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[: self.cfg.top_n]

    def _evaluate(self, symbol: str, asof: date) -> ScannerHit | None:
        stats = self.provider.get_daily_stats(symbol, asof)

        # Liquidity + price band (pre-snapshot cheap filter).
        if stats.avg_dollar_volume_20d < self.cfg.min_avg_dollar_volume:
            return None
        if not (self.cfg.min_price <= stats.prev_close <= self.cfg.max_price):
            return None

        snap = self.provider.get_premarket_snapshot(symbol, asof)
        if snap is None or snap.premarket_volume < self.cfg.min_premarket_volume:
            return None

        gap_pct = (snap.last - stats.prev_close) / stats.prev_close * 100.0
        if abs(gap_pct) < self.cfg.min_gap_pct:
            return None

        expected_premarket_vol = max(1, stats.avg_volume_20d * _PREMARKET_SHARE)
        rvol = snap.premarket_volume / expected_premarket_vol
        if rvol < self.cfg.min_relative_volume:
            return None

        atr_pct = stats.atr_14d / stats.prev_close * 100.0
        score, reasons = rank_score(
            gap_pct=gap_pct,
            rvol=rvol,
            atr_pct=atr_pct,
            avg_dollar_volume=stats.avg_dollar_volume_20d,
        )

        return ScannerHit(
            symbol=symbol,
            price=snap.last,
            gap_pct=round(gap_pct, 2),
            relative_volume=round(rvol, 2),
            premarket_volume=snap.premarket_volume,
            avg_dollar_volume=stats.avg_dollar_volume_20d,
            atr_14d=stats.atr_14d,
            score=round(score, 2),
            reasons=reasons,
            side_bias=Side.LONG if gap_pct > 0 else Side.SHORT,
            asof=datetime.now(timezone.utc),
        )
