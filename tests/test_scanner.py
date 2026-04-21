from datetime import date

import pytest

from backend.data.mock_provider import MockProvider
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
