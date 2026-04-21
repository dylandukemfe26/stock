"""Relative Strength / Weakness tracker.

Answers: is this stock outperforming the benchmark so far today, and is that
outperformance persisting? Feeds scanner ranking and setup scoring.

Math:
  session_rs = symbol_return_since_open - benchmark_return_since_open
  persistence = fraction of the last N bars where the symbol's bar-over-bar
                change beat the benchmark's

MVP uses SPY as the sole benchmark. Sector ETFs come in v2.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from backend.core.cache import TTLCache
from backend.data.provider import DataProvider
from backend.models.schemas import Bar, RsSnapshot, StrengthFlag


_DEFAULT_BENCH = "SPY"
_LOOKBACK_BARS = 15
_LEADER_RS_PCT = 1.0
_LEADER_PERSISTENCE = 0.65


class RSService:
    def __init__(
        self,
        provider: DataProvider,
        cache: TTLCache | None = None,
        benchmark: str = _DEFAULT_BENCH,
    ) -> None:
        self.provider = provider
        self.benchmark = benchmark
        self._cache = cache or TTLCache(default_ttl_seconds=10.0)

    def snapshot(self, symbol: str, asof: date | None = None) -> RsSnapshot | None:
        asof = asof or date.today()
        sym_bars = self._bars(symbol, asof)
        bench_bars = self._bars(self.benchmark, asof)
        if not sym_bars or not bench_bars:
            return None

        # Align by taking the shorter of the two (should match in practice).
        n = min(len(sym_bars), len(bench_bars))
        sym_bars = sym_bars[:n]
        bench_bars = bench_bars[:n]

        session_rs = _session_rs(sym_bars, bench_bars) * 100
        # Persistence is direction-aware: we ask "how consistent is the sign of
        # outperformance vs bench?" A chronic laggard has persistence close to 1
        # just like a chronic leader does.
        persistence = _directional_persistence(
            sym_bars, bench_bars, _LOOKBACK_BARS, leaning=+1 if session_rs >= 0 else -1
        )
        strength = _classify(session_rs, persistence)

        return RsSnapshot(
            symbol=symbol,
            benchmark=self.benchmark,
            session_rs_pct=round(session_rs, 3),
            persistence=round(persistence, 3),
            strength=strength,
            asof=datetime.now(timezone.utc),
        )

    def _bars(self, symbol: str, asof: date) -> list[Bar]:
        return self._cache.get_or_compute(
            ("bars", symbol, asof),
            lambda: self.provider.get_intraday_bars(symbol, asof),
        )


def _session_rs(sym_bars: list[Bar], bench_bars: list[Bar]) -> float:
    s_open = sym_bars[0].open
    b_open = bench_bars[0].open
    if s_open <= 0 or b_open <= 0:
        return 0.0
    s_ret = sym_bars[-1].close / s_open - 1
    b_ret = bench_bars[-1].close / b_open - 1
    return s_ret - b_ret


def _persistence(
    sym_bars: list[Bar], bench_bars: list[Bar], lookback: int
) -> float:
    """Fraction of recent bars where sym's pct change exceeded bench's."""
    return _directional_persistence(sym_bars, bench_bars, lookback, leaning=+1)


def _directional_persistence(
    sym_bars: list[Bar], bench_bars: list[Bar], lookback: int, leaning: int
) -> float:
    """Consistency of outperformance in the direction `leaning` (+1 or -1).

    leaning=+1 → fraction of bars where sym beat bench (used for leaders).
    leaning=-1 → fraction of bars where sym trailed bench (used for laggards).
    """
    n = min(lookback, len(sym_bars) - 1, len(bench_bars) - 1)
    if n <= 0:
        return 0.0
    hits = 0
    for i in range(-n, 0):
        s_chg = sym_bars[i].close - sym_bars[i - 1].close
        b_chg = bench_bars[i].close - bench_bars[i - 1].close
        s_pct = s_chg / sym_bars[i - 1].close if sym_bars[i - 1].close else 0
        b_pct = b_chg / bench_bars[i - 1].close if bench_bars[i - 1].close else 0
        if leaning >= 0 and s_pct > b_pct:
            hits += 1
        elif leaning < 0 and s_pct < b_pct:
            hits += 1
    return hits / n


def _classify(session_rs_pct: float, persistence: float) -> StrengthFlag:
    if session_rs_pct > _LEADER_RS_PCT and persistence > _LEADER_PERSISTENCE:
        return StrengthFlag.LEADER
    if session_rs_pct < -_LEADER_RS_PCT and persistence > _LEADER_PERSISTENCE:
        return StrengthFlag.LAGGARD
    return StrengthFlag.NEUTRAL
