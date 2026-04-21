"""Intraday setup recognition.

Design goal: few high-quality, explainable setups. Each detector returns
None or a SetupSignal with an entry/stop/target and the reasons it fired.

Setups implemented (MVP):
  1. Opening Range Breakout (ORB) — break of first 5min H/L with volume.
  2. VWAP Reclaim — price crosses back above VWAP after being below.
  3. VWAP Rejection — price rallies into VWAP from below, fails, rolls.
  4. Pullback in Trend — trending above VWAP, pulls to VWAP/20EMA, resumes.
  5. Level Break — break of prior day high/low (clean S/R).

False-signal reducers (v1):
  - Require volume on the trigger bar >= 1.5x median of last 10 bars.
  - Require at least 3 bars of confirmation context (avoid 09:30 wicks).
  - Reject setups where the structural stop is wider than 1.5x ATR(14) of
    the current intraday bars (bad R:R territory).
"""
from __future__ import annotations

from datetime import datetime
from statistics import median

from backend.models.schemas import Bar, SetupSignal, SetupType, Side, TradePlan
from backend.setups.indicators import (
    atr,
    crossed_above,
    crossed_below,
    opening_range,
    vwap_series,
)


_MIN_BARS = 6
_VOL_MULT = 1.5
_MAX_STOP_ATR_MULT = 1.5
_TARGET_R = 2.0  # first target is 2R by default


def _trigger_volume_ok(bars: list[Bar]) -> bool:
    if len(bars) < 11:
        return True  # early session: don't block on volume
    recent = bars[-1].volume
    window = [b.volume for b in bars[-11:-1]]
    med = median(window) or 1
    return recent >= _VOL_MULT * med


def _plan(entry: float, stop: float, side: Side) -> TradePlan:
    risk = abs(entry - stop)
    if side == Side.LONG:
        target1 = entry + _TARGET_R * risk
    else:
        target1 = entry - _TARGET_R * risk
    return TradePlan(
        entry=round(entry, 2),
        stop=round(stop, 2),
        target1=round(target1, 2),
        risk_per_share=round(risk, 4),
        reward_risk_t1=_TARGET_R,
    )


def _stop_is_reasonable(risk_per_share: float, intraday_atr: float | None) -> bool:
    if intraday_atr is None or intraday_atr <= 0:
        return True
    return risk_per_share <= _MAX_STOP_ATR_MULT * intraday_atr


class SetupDetector:
    """Stateless detector: runs fresh against the current bar series."""

    def detect(
        self,
        symbol: str,
        bars: list[Bar],
        prev_day_high: float | None = None,
        prev_day_low: float | None = None,
    ) -> list[SetupSignal]:
        signals: list[SetupSignal] = []
        if len(bars) < _MIN_BARS:
            return signals

        if not _trigger_volume_ok(bars):
            return signals

        intraday_atr = atr(bars, period=min(14, len(bars) - 1))

        for detector in (
            self._detect_orb,
            self._detect_vwap_reclaim,
            self._detect_vwap_rejection,
            self._detect_pullback,
            self._detect_level_break,
        ):
            sig = detector(symbol, bars, intraday_atr, prev_day_high, prev_day_low)
            if sig is not None:
                signals.append(sig)
        return signals

    def _detect_orb(
        self,
        symbol: str,
        bars: list[Bar],
        intraday_atr: float | None,
        prev_day_high: float | None,
        prev_day_low: float | None,
    ) -> SetupSignal | None:
        rng = opening_range(bars, minutes=5)
        if rng is None:
            return None
        or_high, or_low = rng
        last = bars[-1]
        prev = bars[-2]

        if crossed_above(prev.close, last.close, or_high):
            entry = or_high
            stop = or_low
            plan = _plan(entry, stop, Side.LONG)
            if not _stop_is_reasonable(plan.risk_per_share, intraday_atr):
                return None
            return SetupSignal(
                symbol=symbol,
                setup=SetupType.ORB,
                side=Side.LONG,
                plan=plan,
                score=self._score(bars, "trend_with_gap"),
                reasons=[f"broke 5m OR high {or_high:.2f}", "trigger vol confirmed"],
                triggered_at=last.ts,
            )
        if crossed_below(prev.close, last.close, or_low):
            entry = or_low
            stop = or_high
            plan = _plan(entry, stop, Side.SHORT)
            if not _stop_is_reasonable(plan.risk_per_share, intraday_atr):
                return None
            return SetupSignal(
                symbol=symbol,
                setup=SetupType.ORB,
                side=Side.SHORT,
                plan=plan,
                score=self._score(bars, "trend_with_gap"),
                reasons=[f"broke 5m OR low {or_low:.2f}", "trigger vol confirmed"],
                triggered_at=last.ts,
            )
        return None

    def _detect_vwap_reclaim(
        self,
        symbol: str,
        bars: list[Bar],
        intraday_atr: float | None,
        prev_day_high: float | None,
        prev_day_low: float | None,
    ) -> SetupSignal | None:
        vwap = vwap_series(bars)
        if len(vwap) < 5:
            return None
        # Was below VWAP for at least the previous 3 bars, now closes back above.
        if not all(bars[-i].close < vwap[-i] for i in (2, 3, 4)):
            return None
        if not bars[-1].close > vwap[-1]:
            return None
        entry = vwap[-1]
        stop = min(b.low for b in bars[-5:])
        plan = _plan(entry, stop, Side.LONG)
        if not _stop_is_reasonable(plan.risk_per_share, intraday_atr):
            return None
        return SetupSignal(
            symbol=symbol,
            setup=SetupType.VWAP_RECLAIM,
            side=Side.LONG,
            plan=plan,
            score=self._score(bars, "reclaim"),
            reasons=["price reclaimed VWAP", "prior 3 bars were sub-VWAP"],
            triggered_at=bars[-1].ts,
        )

    def _detect_vwap_rejection(
        self,
        symbol: str,
        bars: list[Bar],
        intraday_atr: float | None,
        prev_day_high: float | None,
        prev_day_low: float | None,
    ) -> SetupSignal | None:
        vwap = vwap_series(bars)
        if len(vwap) < 5:
            return None
        if not all(bars[-i].close < vwap[-i] for i in (1, 2, 3, 4)):
            return None
        # Recent bars approached VWAP from below (within 0.3% of VWAP) and closed red.
        approached = any(
            abs(bars[-i].high - vwap[-i]) / vwap[-i] < 0.003 for i in (2, 3)
        )
        if not approached:
            return None
        last = bars[-1]
        if last.close >= last.open:  # need red candle
            return None
        entry = last.close
        stop = max(b.high for b in bars[-5:])
        plan = _plan(entry, stop, Side.SHORT)
        if not _stop_is_reasonable(plan.risk_per_share, intraday_atr):
            return None
        return SetupSignal(
            symbol=symbol,
            setup=SetupType.VWAP_REJECTION,
            side=Side.SHORT,
            plan=plan,
            score=self._score(bars, "rejection"),
            reasons=["tested VWAP from below", "rejection candle"],
            triggered_at=last.ts,
        )

    def _detect_pullback(
        self,
        symbol: str,
        bars: list[Bar],
        intraday_atr: float | None,
        prev_day_high: float | None,
        prev_day_low: float | None,
    ) -> SetupSignal | None:
        vwap = vwap_series(bars)
        if len(vwap) < 10:
            return None
        # Uptrend: majority of last 10 bars above VWAP, last 2-3 pulled toward VWAP
        # but didn't break it, and the latest bar is a reversal up.
        recent = bars[-10:]
        above = sum(1 for i, b in enumerate(recent) if b.close > vwap[-10 + i])
        if above < 7:
            return None
        pullback = bars[-3:]
        vwap_pullback = vwap[-3:]
        close_to_vwap = any(
            (b.low - v) / v < 0.002 for b, v in zip(pullback, vwap_pullback)
        )
        held = all(b.close > v for b, v in zip(pullback, vwap_pullback))
        last = bars[-1]
        reversal = last.close > last.open and last.close > bars[-2].high
        if not (close_to_vwap and held and reversal):
            return None
        entry = last.close
        stop = min(b.low for b in pullback)
        plan = _plan(entry, stop, Side.LONG)
        if not _stop_is_reasonable(plan.risk_per_share, intraday_atr):
            return None
        return SetupSignal(
            symbol=symbol,
            setup=SetupType.PULLBACK,
            side=Side.LONG,
            plan=plan,
            score=self._score(bars, "pullback"),
            reasons=["uptrend vs VWAP", "pullback held VWAP", "reversal candle"],
            triggered_at=last.ts,
        )

    def _detect_level_break(
        self,
        symbol: str,
        bars: list[Bar],
        intraday_atr: float | None,
        prev_day_high: float | None,
        prev_day_low: float | None,
    ) -> SetupSignal | None:
        last = bars[-1]
        prev = bars[-2]
        if prev_day_high is not None and crossed_above(prev.close, last.close, prev_day_high):
            entry = prev_day_high
            stop = min(b.low for b in bars[-5:])
            plan = _plan(entry, stop, Side.LONG)
            if not _stop_is_reasonable(plan.risk_per_share, intraday_atr):
                return None
            return SetupSignal(
                symbol=symbol,
                setup=SetupType.LEVEL_BREAK,
                side=Side.LONG,
                plan=plan,
                score=self._score(bars, "level_break"),
                reasons=[f"broke prior day high {prev_day_high:.2f}"],
                triggered_at=last.ts,
            )
        if prev_day_low is not None and crossed_below(prev.close, last.close, prev_day_low):
            entry = prev_day_low
            stop = max(b.high for b in bars[-5:])
            plan = _plan(entry, stop, Side.SHORT)
            if not _stop_is_reasonable(plan.risk_per_share, intraday_atr):
                return None
            return SetupSignal(
                symbol=symbol,
                setup=SetupType.LEVEL_BREAK,
                side=Side.SHORT,
                plan=plan,
                score=self._score(bars, "level_break"),
                reasons=[f"broke prior day low {prev_day_low:.2f}"],
                triggered_at=last.ts,
            )
        return None

    def _score(self, bars: list[Bar], flavor: str) -> float:
        """Setup quality score (0-100).

        Components:
          - Volume strength on trigger bar vs recent median (40)
          - Clean structure (few overlapping bars before trigger) (30)
          - Bar close near the extreme in signal direction (30)
        """
        if len(bars) < 11:
            return 50.0
        recent = bars[-11:-1]
        med = median(b.volume for b in recent) or 1
        vol_factor = min(bars[-1].volume / med, 3.0) / 3.0 * 40.0

        highs = [b.high for b in recent]
        lows = [b.low for b in recent]
        overlap = sum(1 for h, l in zip(highs, lows) if h - l < (max(highs) - min(lows)) * 0.3)
        structure = max(0.0, 30.0 - overlap * 2.0)

        last = bars[-1]
        rng = max(0.01, last.high - last.low)
        close_pos = (last.close - last.low) / rng  # 0 bottom, 1 top
        if flavor in {"rejection"}:
            extreme = 30.0 * (1 - close_pos)
        else:
            extreme = 30.0 * close_pos

        return round(vol_factor + structure + extreme, 2)
