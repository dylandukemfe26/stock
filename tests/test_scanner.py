from datetime import date

import pytest

from backend.data.mock_provider import MockProvider
from backend.liquidity import LiquidityService
from backend.rs import RSService
from backend.scanner.ranking import rank_score
from backend.scanner.scanner import GapVolumeScanner, ScannerConfig


def test_ranking_monotonic_in_gap():
    low = rank_score(gap_pct=3, rvol=3, atr_pct=2, avg_dollar_volume=1e8)[0]
    high = rank_score(gap_pct=10, rvol=3, atr_pct=2, avg_dollar_volume=1e8)[0]
    assert high > low


def test_ranking_monotonic_in_rvol():
    low = rank_score(gap_pct=5, rvol=2, atr_pct=2, avg_dollar_volume=1e8)[0]
    high = rank_score(gap_pct=5, rvol=8, atr_pct=2, avg_dollar_volume=1e8)[0]
    assert high > low


def test_scanner_returns_sorted_hits():
    scanner = GapVolumeScanner(
        MockProvider(),
        ScannerConfig(
            min_gap_pct=1.0,
            min_relative_volume=0.5,
            min_premarket_volume=1000,
            min_avg_dollar_volume=1_000_000,
            top_n=20,
        ),
    )
    hits = scanner.scan(asof=date(2024, 5, 1))
    assert len(hits) > 0
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)
    for h in hits:
        assert abs(h.gap_pct) >= 1.0
        assert h.relative_volume >= 0.5


def test_scanner_liquidity_filter_blocks_low_dollar_volume():
    scanner = GapVolumeScanner(
        MockProvider(),
        ScannerConfig(min_avg_dollar_volume=10**18, top_n=10),
    )
    assert scanner.scan(asof=date(2024, 5, 1)) == []


def test_scanner_with_rs_and_liquidity_services_enriches_hits():
    provider = MockProvider()
    scanner = GapVolumeScanner(
        provider,
        ScannerConfig(
            min_gap_pct=1.0,
            min_relative_volume=0.5,
            min_premarket_volume=1000,
            min_avg_dollar_volume=1_000_000,
            top_n=20,
        ),
        rs_service=RSService(provider),
        liquidity_service=LiquidityService(provider),
    )
    hits = scanner.scan(asof=date(2024, 5, 1))
    assert len(hits) > 0
    # Every hit must have been enriched by both services.
    for h in hits:
        assert h.liquidity_tier in {"A", "B"}, h  # tier C was filtered out
        assert h.spread_pct is not None and h.spread_pct >= 0
        assert h.rs_flag in {"leader", "laggard", "neutral"}
        assert h.rs_pct is not None


def test_scanner_drop_tier_c_filter_actually_drops():
    """Force every symbol's liquidity to tier C; scanner should return nothing."""
    from backend.models.schemas import LiquidityScore, LiquidityTier
    from datetime import datetime, timezone

    class _AllTierC(LiquidityService):
        def score(self, symbol):
            return LiquidityScore(
                symbol=symbol, spread_pct=1.0, est_round_trip_slippage_pct=1.0,
                tradeability=10, tier=LiquidityTier.C,
                asof=datetime.now(timezone.utc),
            )

    provider = MockProvider()
    scanner = GapVolumeScanner(
        provider,
        ScannerConfig(
            min_gap_pct=1.0, min_relative_volume=0.5,
            min_premarket_volume=1000, min_avg_dollar_volume=1_000_000,
            top_n=20, drop_tier_c=True,
        ),
        liquidity_service=_AllTierC(provider),
    )
    assert scanner.scan(asof=date(2024, 5, 1)) == []
