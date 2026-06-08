"""Tests for screen command — scoring logic and funnel stages."""

from __future__ import annotations

from token_research.commands.screen import (
    _build_universe,
    _enrich_with_yields,
    _final_rank,
    _parse_address_field,
    _protocol_age_years,
    _safe_float,
    _score_growth,
)


def test_parse_address_field_with_prefix():
    prefix, addr = _parse_address_field("base:0xabc123")
    assert prefix == "base"
    assert addr == "0xabc123"


def test_parse_address_field_without_prefix():
    prefix, addr = _parse_address_field("0xabc123")
    assert prefix is None
    assert addr == "0xabc123"


def test_safe_float_handles_none():
    assert _safe_float(None) == 0.0
    assert _safe_float("garbage") == 0.0
    assert _safe_float(42.5) == 42.5
    assert _safe_float("3.14") == 3.14


def test_protocol_age_years():
    import time
    one_year_ago = time.time() - 365.25 * 86_400
    age = _protocol_age_years(one_year_ago)
    assert age is not None
    assert 0.9 < age < 1.1

    assert _protocol_age_years(None) is None
    assert _protocol_age_years(0) is None
    assert _protocol_age_years("garbage") is None


def test_score_growth_normalizes_and_ranks():
    """Score growth must produce normalized 0-100 signals and rank by composite."""
    universe = [
        {
            "name": "Alpha",
            "change_7d": 50.0, "change_1m": 100.0,
            "mcap": 10_000_000, "tvl": 5_000_000,
            "age_years": 1.0,
            "best_apy": 20.0,
            "chains": ["ethereum", "base", "arbitrum"],
            "yield_pool_count": 15,
            "audits": 2,
        },
        {
            "name": "Beta",
            "change_7d": -10.0, "change_1m": -20.0,
            "mcap": 100_000_000, "tvl": 1_000_000,
            "age_years": 4.0,
            "best_apy": 1.0,
            "chains": ["ethereum"],
            "yield_pool_count": 2,
            "audits": 0,
        },
    ]
    scored = _score_growth(universe)
    assert len(scored) == 2
    # Alpha should rank higher (growing, better mcap/tvl, multi-chain)
    assert scored[0]["name"] == "Alpha"
    assert scored[0]["growth_score"] > scored[1]["growth_score"]
    # Signals are 0-100 range
    for entry in scored:
        for k, v in entry["growth_signals"].items():
            assert 0 <= v <= 100, f"{k}={v} out of range"


def test_score_growth_single_entry():
    """Single-entry universe shouldn't crash normalization (span=0 case)."""
    universe = [
        {
            "name": "Solo",
            "change_7d": 5.0, "change_1m": 10.0,
            "mcap": 1_000_000, "tvl": 500_000,
            "age_years": 2.0,
            "best_apy": 5.0,
            "chains": ["ethereum"],
            "yield_pool_count": 3,
            "audits": 1,
        },
    ]
    scored = _score_growth(universe)
    assert len(scored) == 1
    assert scored[0]["growth_score"] >= 0


def test_final_rank_applies_penalties():
    """Final rank adjusts growth_score with liquidity/concentration bonuses."""
    candidates = [
        {"growth_score": 80.0, "dex_liquidity_usd": 2_000_000, "top10_share": 30, "is_upgradeable": False, "change_7d": 1.0, "change_1m": 2.0},
        {"growth_score": 85.0, "dex_liquidity_usd": 50_000, "top10_share": 90, "is_upgradeable": True, "change_7d": -5.0, "change_1m": -3.0},
    ]
    ranked = _final_rank(candidates)
    # Candidate 1: 80 + 5 (liq bonus) = 85
    # Candidate 2: 85 - 10 (low liq) - 15 (concentration) - 5 (upgradeable) = 55
    assert ranked[0]["final_score"] == 85.0
    assert ranked[1]["final_score"] == 55.0


def test_enrich_with_yields_joins_by_slug():
    """Yield enrichment must match pools by project slug."""
    universe = [
        {"slug": "morpho", "name": "Morpho"},
        {"slug": "unknown-xyz", "name": "Unknown"},
    ]

    import token_research.providers.defillama as defillama
    original = defillama.get_yield_pools

    def mock_pools(config):
        return [
            {"project": "morpho", "apy": 5.0, "tvlUsd": 1_000_000, "chain": "ethereum"},
            {"project": "morpho", "apy": 8.0, "tvlUsd": 500_000, "chain": "base"},
            {"project": "aave", "apy": 3.0, "tvlUsd": 10_000_000, "chain": "ethereum"},
        ]

    defillama.get_yield_pools = mock_pools
    try:
        _enrich_with_yields(universe, None)  # config unused in mock
    finally:
        defillama.get_yield_pools = original

    assert universe[0]["yield_pool_count"] == 2
    assert universe[0]["best_apy"] == 8.0
    assert universe[0]["yield_chains"] == 2
    assert universe[1]["yield_pool_count"] == 0
    assert universe[1]["best_apy"] == 0.0
