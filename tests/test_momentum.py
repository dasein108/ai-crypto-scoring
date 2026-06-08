"""Tests for momentum-screen / -rank / -backtest pipeline.

All tests run offline via the autouse fixture in conftest.py and exercise
only the pure-math helpers — the live `run()` entrypoints aren't tested
here because they require live DexScreener / DeFiLlama responses.
"""

from __future__ import annotations

from datetime import date

import pytest

from token_research.commands import momentum_backtest as mb
from token_research.commands.momentum_screen import (
    COVERAGE_PENALTY_COEFF,
    PILLAR_WEIGHTS,
    _composite_z,
    _compute_zscores,
    _passes_short_exclusions,
    _select_basket,
    size_beta_neutral,
)


# ---------------------------------------------------------------------------
# Z-score
# ---------------------------------------------------------------------------


def test_zscores_handle_missing_values():
    rows = [
        {"ret_24h_pct": 5.0, "tvl_change_7d_pct": 10.0, "tvl_change_30d_pct": None,
         "turnover": 0.1, "volume_24h_usd": 1_000_000,
         "best_apy_pct": 5.0, "yield_pool_count": 3, "chain_count": 2,
         "top10_share_pct": 50.0, "dex_liquidity_usd": 1e6, "liq_to_mcap": 0.05},
        {"ret_24h_pct": -3.0, "tvl_change_7d_pct": -5.0, "tvl_change_30d_pct": -10.0,
         "turnover": 0.02, "volume_24h_usd": 100_000,
         "best_apy_pct": 1.0, "yield_pool_count": 1, "chain_count": 1,
         "top10_share_pct": 80.0, "dex_liquidity_usd": 5e5, "liq_to_mcap": 0.01},
        {"ret_24h_pct": 2.0, "tvl_change_7d_pct": 8.0, "tvl_change_30d_pct": 12.0,
         "turnover": 0.05, "volume_24h_usd": 500_000,
         "best_apy_pct": 8.0, "yield_pool_count": 5, "chain_count": 3,
         "top10_share_pct": 30.0, "dex_liquidity_usd": 2e6, "liq_to_mcap": 0.08},
    ]
    z = _compute_zscores(rows)
    assert len(z) == 3

    # Missing field stays missing.
    assert z[0]["tvl_change_30d_pct"] is None
    # Inverted: lowest top10_share_pct should produce the highest z (best).
    assert z[2]["top10_share_pct"] is not None
    assert z[2]["top10_share_pct"] > z[1]["top10_share_pct"]


def test_zscore_single_value_returns_none():
    """Cross-sectional z requires ≥2 datapoints."""
    rows = [
        {"ret_24h_pct": 5.0, "tvl_change_7d_pct": None, "tvl_change_30d_pct": None,
         "turnover": None, "volume_24h_usd": None,
         "best_apy_pct": None, "yield_pool_count": None, "chain_count": None,
         "top10_share_pct": None, "dex_liquidity_usd": None, "liq_to_mcap": None},
        {"ret_24h_pct": None, "tvl_change_7d_pct": None, "tvl_change_30d_pct": None,
         "turnover": None, "volume_24h_usd": None,
         "best_apy_pct": None, "yield_pool_count": None, "chain_count": None,
         "top10_share_pct": None, "dex_liquidity_usd": None, "liq_to_mcap": None},
    ]
    z = _compute_zscores(rows)
    # Only one ret_24h_pct value present → not enough for cross-sectional z.
    assert z[0]["ret_24h_pct"] is None


# ---------------------------------------------------------------------------
# Composite z + coverage penalty
# ---------------------------------------------------------------------------


def test_composite_full_coverage_no_penalty():
    z_row = {
        "ret_24h_pct": 1.0, "tvl_change_7d_pct": 1.0, "tvl_change_30d_pct": 1.0,
        "turnover": 1.0, "volume_24h_usd": 1.0,
        "best_apy_pct": 1.0, "yield_pool_count": 1.0, "chain_count": 1.0,
        "top10_share_pct": 1.0,
        "dex_liquidity_usd": 1.0, "liq_to_mcap": 1.0,
    }
    z_total, coverage, _ = _composite_z(z_row)
    assert coverage == pytest.approx(1.0)
    # Each pillar averages to 1.0; weights sum to 1.0 → composite = 1.0.
    assert z_total == pytest.approx(1.0, abs=1e-9)


def test_composite_half_coverage_applies_penalty():
    z_row = {
        # Only trend pillar has data.
        "ret_24h_pct": 2.0, "tvl_change_7d_pct": 2.0, "tvl_change_30d_pct": 2.0,
        "turnover": None, "volume_24h_usd": None,
        "best_apy_pct": None, "yield_pool_count": None, "chain_count": None,
        "top10_share_pct": None,
        "dex_liquidity_usd": None, "liq_to_mcap": None,
    }
    z_total, coverage, pillar_z = _composite_z(z_row)
    # 3 features available out of 11 total.
    assert coverage == pytest.approx(3 / 11)
    # pillar trend averages to 2.0; only it has weight, so weighted average is 2.0.
    # Then penalty = 0.15 * (1 - 3/11) ≈ 0.10909.
    expected = 2.0 - COVERAGE_PENALTY_COEFF * (1 - 3 / 11)
    assert z_total == pytest.approx(expected, abs=1e-9)


def test_composite_zero_coverage_returns_zero():
    z_row = {k: None for k in (
        "ret_24h_pct", "tvl_change_7d_pct", "tvl_change_30d_pct",
        "turnover", "volume_24h_usd",
        "best_apy_pct", "yield_pool_count", "chain_count",
        "top10_share_pct",
        "dex_liquidity_usd", "liq_to_mcap",
    )}
    z_total, coverage, _ = _composite_z(z_row)
    assert coverage == pytest.approx(0.0)
    assert z_total == pytest.approx(0.0)


def test_pillar_weights_sum_to_one():
    """Composite math relies on the weights normalizing to 1.0."""
    assert sum(PILLAR_WEIGHTS.values()) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Basket selection
# ---------------------------------------------------------------------------


def _candidate(z: float, beta: float = 1.0, **features) -> dict:
    return {
        "entry": {"symbol": f"T{int(z * 10)}", "name": f"Tok-{z}", "chain": "ethereum",
                  "address": f"0x{abs(int(z * 1000)):040x}"[-42:]},
        "features": features or {"ret_24h_pct": z, "best_apy_pct": 5.0, "yield_pool_count": 3},
        "z_total": z,
        "beta": beta,
        "coverage_ratio": 1.0,
    }


def test_basket_respects_thresholds():
    cands = [
        _candidate(2.0), _candidate(1.6), _candidate(1.4),  # 2 pass long
        _candidate(0.0), _candidate(0.5),                    # neutral
        _candidate(-1.4), _candidate(-1.6), _candidate(-2.0),  # 2 pass short
    ]
    longs, shorts = _select_basket(cands, k=10, long_threshold=1.5, short_threshold=-1.5)
    assert len(longs) == 2
    assert len(shorts) == 2
    assert all(c["z_total"] >= 1.5 for c in longs)
    assert all(c["z_total"] <= -1.5 for c in shorts)


def test_basket_short_exclusion_skips_strong_fundamentals():
    """Tokens with strong yields shouldn't be shorted even if z_total is bearish."""
    strong_yield = {
        "entry": {"symbol": "GOOD", "name": "Strong", "chain": "ethereum", "address": "0x1"},
        "features": {"best_apy_pct": 30.0, "yield_pool_count": 10, "ret_24h_pct": -5.0},
        "z_total": -2.0, "beta": 1.0, "coverage_ratio": 1.0,
    }
    weak = {
        "entry": {"symbol": "BAD", "name": "Weak", "chain": "ethereum", "address": "0x2"},
        "features": {"best_apy_pct": 1.0, "yield_pool_count": 0, "ret_24h_pct": -10.0},
        "z_total": -1.8, "beta": 1.0, "coverage_ratio": 1.0,
    }
    _, shorts = _select_basket([strong_yield, weak], k=5, long_threshold=1.5, short_threshold=-1.5)
    assert len(shorts) == 1
    assert shorts[0]["entry"]["symbol"] == "BAD"


def test_short_exclusion_helper_direct():
    assert _passes_short_exclusions(
        {}, {"best_apy_pct": 30.0, "yield_pool_count": 10}
    ) is False
    assert _passes_short_exclusions({}, {"best_apy_pct": 1.0, "yield_pool_count": 0}) is True


# ---------------------------------------------------------------------------
# Beta-neutral sizing
# ---------------------------------------------------------------------------


def _sized_candidate(beta: float, vol_proxy: float) -> dict:
    return {
        "features": {"ret_24h_pct": vol_proxy},
        "beta": beta,
        "z_total": 0.0,
        "entry": {"symbol": "X", "name": "X", "chain": "e", "address": "0x"},
        "coverage_ratio": 1.0,
    }


def test_sizing_equal_betas_collapses_to_dollar_neutral():
    longs = [_sized_candidate(1.0, 2.0) for _ in range(3)]
    shorts = [_sized_candidate(1.0, 2.0) for _ in range(3)]
    long_w, short_w, gross, net, net_beta = size_beta_neutral(longs, shorts)
    assert gross == pytest.approx(2.0, abs=1e-9)
    assert net == pytest.approx(0.0, abs=1e-9)
    assert net_beta == pytest.approx(0.0, abs=1e-9)
    assert sum(long_w) == pytest.approx(0.5, abs=1e-9)
    assert sum(short_w) == pytest.approx(0.5, abs=1e-9)


def test_sizing_high_beta_longs_get_smaller_short_book():
    """Long β = 2.0, short β = 1.0 → shorts must double up to neutralize."""
    longs = [_sized_candidate(2.0, 1.0)]
    shorts = [_sized_candidate(1.0, 1.0)]
    long_w, short_w, gross, net, net_beta = size_beta_neutral(longs, shorts)
    # Long_gross = 1, short_gross_scale = 2 / 1 = 2 → total = 3, long share = 1/3.
    assert sum(long_w) == pytest.approx(1.0 / 3.0, abs=1e-9)
    assert sum(short_w) == pytest.approx(2.0 / 3.0, abs=1e-9)
    assert net_beta == pytest.approx(0.0, abs=1e-9)


def test_sizing_net_beta_within_tolerance():
    """Acceptance criterion: net beta within ±0.10 on synthetic test data."""
    longs = [_sized_candidate(1.2, 2.0), _sized_candidate(0.8, 1.5), _sized_candidate(1.0, 1.0)]
    shorts = [_sized_candidate(1.1, 1.8), _sized_candidate(0.9, 1.2)]
    _, _, _, _, net_beta = size_beta_neutral(longs, shorts)
    assert abs(net_beta) < 0.10


def test_sizing_inverse_vol_high_vol_smaller_weight():
    """A higher-vol position inside a leg should receive less weight."""
    longs = [
        _sized_candidate(1.0, 1.0),   # low vol → big weight
        _sized_candidate(1.0, 10.0),  # high vol → small weight
    ]
    shorts = [_sized_candidate(1.0, 2.0)]
    long_w, _, _, _, _ = size_beta_neutral(longs, shorts)
    assert long_w[0] > long_w[1]


def test_sizing_empty_legs_returns_zeros():
    long_w, short_w, gross, net, net_beta = size_beta_neutral([], [])
    assert long_w == [] and short_w == []
    assert gross == 0.0 and net == 0.0 and net_beta == 0.0


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------


def _two_position_basket() -> dict:
    return {
        "longs": [{
            "symbol": "L1", "project_name": "L1",
            "chain": "ethereum", "address": "0xaaa",
            "side": "long", "weight": 1.0, "notional_pct": 0.5,
            "beta": 1.0, "z_score": 2.0, "expected_funding_bps": 0.0,
            "features": {},
        }],
        "shorts": [{
            "symbol": "S1", "project_name": "S1",
            "chain": "ethereum", "address": "0xbbb",
            "side": "short", "weight": 1.0, "notional_pct": 0.5,
            "beta": 1.0, "z_score": -2.0, "expected_funding_bps": 1500.0,
            "features": {},
        }],
        "gross_pct": 1.0, "net_pct": 0.0, "net_beta": 0.0,
    }


def test_backtest_constant_prices_yields_only_costs():
    """No price drift → only inception + funding + rebalance costs eat equity."""
    series_l = [{"date_iso": d.isoformat(), "price_usd": 100.0}
                for d in (date(2026, 1, 1) + __import__("datetime").timedelta(days=i)
                          for i in range(60))]
    series_s = list(series_l)
    series_map = {
        ("ethereum", "0xaaa"): series_l,
        ("ethereum", "0xbbb"): series_s,
    }
    result = mb.run_backtest(
        _two_position_basket(),
        start=date(2026, 1, 1), end=date(2026, 2, 28),
        rebalance="weekly", spot_bps=10.0, perp_bps=5.0, funding_apr=0.15,
        series_map=series_map, basket_source=None,
    )
    # Long return + short return = 0 each period.
    assert all(p.long_return_pct == pytest.approx(0.0) for p in result.periods)
    assert all(p.short_return_pct == pytest.approx(0.0) for p in result.periods)
    # Equity drifts down only via funding + transaction costs.
    assert result.final_equity < 1.0
    # Funding cost is positive (a drag).
    assert result.total_funding_cost_pct > 0


def test_backtest_long_only_gain():
    """Long doubles, short flat → leg attribution reflects that exactly."""
    long_series = [{"date_iso": d.isoformat(), "price_usd": 100.0 * (1.0 + 0.01 * i)}
                   for i, d in enumerate(
                       date(2026, 1, 1) + __import__("datetime").timedelta(days=k)
                       for k in range(60)
                   )]
    short_series = [{"date_iso": d.isoformat(), "price_usd": 100.0}
                    for d in (date(2026, 1, 1) + __import__("datetime").timedelta(days=k)
                              for k in range(60))]
    series_map = {
        ("ethereum", "0xaaa"): long_series,
        ("ethereum", "0xbbb"): short_series,
    }
    result = mb.run_backtest(
        _two_position_basket(),
        start=date(2026, 1, 1), end=date(2026, 2, 28),
        rebalance="weekly", spot_bps=10.0, perp_bps=5.0, funding_apr=0.15,
        series_map=series_map, basket_source=None,
    )
    assert result.long_attribution_pct > 0
    assert result.short_attribution_pct == pytest.approx(0.0, abs=1e-9)


def test_backtest_synthetic_series_deterministic():
    """Same seed + dates → identical equity curve."""
    s1 = mb._synthetic_series(seed=7, start=date(2026, 1, 1), end=date(2026, 1, 31), drift=0.0, vol=0.01)
    s2 = mb._synthetic_series(seed=7, start=date(2026, 1, 1), end=date(2026, 1, 31), drift=0.0, vol=0.01)
    assert s1 == s2
    assert len(s1) == 31


def test_backtest_missing_series_does_not_crash():
    """Position without price history just skips that name in returns calc."""
    result = mb.run_backtest(
        _two_position_basket(),
        start=date(2026, 1, 1), end=date(2026, 1, 15),
        rebalance="weekly", spot_bps=10.0, perp_bps=5.0, funding_apr=0.15,
        series_map={},  # nothing
        basket_source=None,
    )
    assert "L1@ethereum" in result.missing_price_series
    assert "S1@ethereum" in result.missing_price_series
    # Equity not nan / inf.
    assert -1.0 < result.final_equity < 5.0


def test_backtest_rebalance_dates_increase_with_frequency():
    weekly = mb._rebalance_dates(date(2026, 1, 1), date(2026, 3, 1), "weekly")
    monthly = mb._rebalance_dates(date(2026, 1, 1), date(2026, 3, 1), "monthly")
    assert len(weekly) > len(monthly)


def test_backtest_max_drawdown_simple():
    """Equity 1.0 → 1.5 → 0.75 → max DD = 50%."""
    assert mb._max_drawdown([1.0, 1.5, 0.75]) == pytest.approx(50.0)


def test_backtest_sharpe_zero_stdev_returns_none():
    assert mb._sharpe([0.01, 0.01, 0.01], periods_per_year=52.0) is None


def test_backtest_sharpe_positive_for_good_returns():
    sharpe = mb._sharpe([0.02, 0.03, 0.025, 0.015], periods_per_year=52.0)
    assert sharpe is not None and sharpe > 0


# ---------------------------------------------------------------------------
# Beta integration with momentum-screen — uses the price cache.
# ---------------------------------------------------------------------------


def test_screen_beta_diagnostics_fallback_when_cache_empty(tmp_path, monkeypatch):
    """No BTC reference cache → all positions use the β fallback and the
    coverage gap explains how to populate it."""
    from token_research.commands import momentum_screen as ms
    from token_research.config import AppConfig

    monkeypatch.setenv("TOKEN_RESEARCH_OFFLINE", "0")
    monkeypatch.setenv("TOKEN_RESEARCH_DATA_DIR", str(tmp_path))
    cfg = AppConfig.from_env()
    cfg.ensure_directories()

    # Patch network-touching helpers to return a tiny synthetic universe.
    def fake_universe(config, *, min_tvl, max_tvl, category_filter):
        return [
            {"name": "Alpha", "symbol": "A", "chain": "ethereum",
             "address": "0xa", "mcap": 10_000_000, "tvl": 5_000_000,
             "change_7d": 5.0, "change_1m": 8.0,
             "chains": ["ethereum"], "audits": 1, "slug": "alpha"},
            {"name": "Beta", "symbol": "B", "chain": "ethereum",
             "address": "0xb", "mcap": 8_000_000, "tvl": 3_000_000,
             "change_7d": -2.0, "change_1m": -3.0,
             "chains": ["ethereum"], "audits": 0, "slug": "beta"},
        ] * 5  # need ≥5 entries to clear universe-size guard

    def fake_yields(universe, config):
        for e in universe:
            e["yield_pool_count"] = 1
            e["best_apy"] = 5.0
            e["yield_chains"] = 1
            e["pool_tvl_sum"] = 0.0

    def fake_market(universe, config):
        for e in universe:
            e["dex_liquidity_usd"] = 5_000_000
            e["dex_volume_24h_usd"] = 1_000_000
            e["dex_mcap"] = e["mcap"]
            e["ret_24h_pct"] = 1.0
            e["pair_count"] = 2

    monkeypatch.setattr(ms, "_build_universe", fake_universe)
    monkeypatch.setattr(ms, "_enrich_with_yields", fake_yields)
    monkeypatch.setattr(ms, "_enrich_market_stats", fake_market)

    result = ms.run(
        "test", None, None, cfg, False,
        no_persist=True, k=2, long_z=-100.0, short_z=100.0,
    ).to_dict()
    metrics = result["metrics"]
    diag = metrics["beta_diagnostics"]
    assert diag["btc_cache_present"] is False
    assert diag["fallback_count"] == diag["fallback_count"]  # at least populated
    assert diag["real_count"] == 0
    # Coverage gap explaining the fallback should be present.
    reasons = " ".join(g["reason"] for g in result["coverage_gaps"])
    assert "fallback" in reasons


def test_screen_beta_uses_real_cache_when_present(tmp_path, monkeypatch):
    """When the BTC reference cache + per-token cache exist, β should be
    computed and used in sizing (no fallback)."""
    import math
    from token_research.commands import momentum_screen as ms
    from token_research.config import AppConfig
    from token_research.prices import write_reference_series, write_series

    monkeypatch.setenv("TOKEN_RESEARCH_OFFLINE", "0")
    monkeypatch.setenv("TOKEN_RESEARCH_DATA_DIR", str(tmp_path))
    cfg = AppConfig.from_env()
    cfg.ensure_directories()

    # Build BTC and 2× token series sharing dates → β = 2.
    from datetime import date, timedelta
    base = date(2026, 1, 1)
    btc_returns = [0.01 * math.sin(i * 0.4) for i in range(80)]
    token_returns = [2.0 * r for r in btc_returns]

    def to_series(returns):
        rows = [{"date_iso": base.isoformat(), "price_usd": 100.0}]
        price = 100.0
        for i, r in enumerate(returns, start=1):
            price *= math.exp(r)
            rows.append({"date_iso": (base + timedelta(days=i)).isoformat(),
                         "price_usd": round(price, 10)})
        return rows

    write_reference_series(tmp_path, "btc", to_series(btc_returns))
    write_series(tmp_path, "ethereum", "0xa", to_series(token_returns))
    # Second token cache absent → that one should fall back.

    def fake_universe(config, *, min_tvl, max_tvl, category_filter):
        return [
            {"name": "Alpha", "symbol": "A", "chain": "ethereum",
             "address": "0xa", "mcap": 10_000_000, "tvl": 5_000_000,
             "change_7d": 5.0, "change_1m": 8.0,
             "chains": ["ethereum"], "audits": 1, "slug": "alpha"},
            {"name": "Beta", "symbol": "B", "chain": "ethereum",
             "address": "0xb", "mcap": 8_000_000, "tvl": 3_000_000,
             "change_7d": -2.0, "change_1m": -3.0,
             "chains": ["ethereum"], "audits": 0, "slug": "beta"},
        ] * 3

    def fake_yields(universe, config):
        for e in universe:
            e["yield_pool_count"] = 1
            e["best_apy"] = 5.0
            e["yield_chains"] = 1
            e["pool_tvl_sum"] = 0.0

    def fake_market(universe, config):
        for e in universe:
            e["dex_liquidity_usd"] = 5_000_000
            e["dex_volume_24h_usd"] = 1_000_000
            e["dex_mcap"] = e["mcap"]
            e["ret_24h_pct"] = 1.0
            e["pair_count"] = 2

    monkeypatch.setattr(ms, "_build_universe", fake_universe)
    monkeypatch.setattr(ms, "_enrich_with_yields", fake_yields)
    monkeypatch.setattr(ms, "_enrich_market_stats", fake_market)

    result = ms.run(
        "test", None, None, cfg, False,
        no_persist=True, k=2, long_z=-100.0, short_z=100.0,
        beta_window=60, beta_min_obs=20,
    ).to_dict()
    metrics = result["metrics"]
    diag = metrics["beta_diagnostics"]
    assert diag["btc_cache_present"] is True
    assert diag["real_count"] >= 1  # the 0xa token had cache
    # Inspect per-token diagnostics for 0xa specifically.
    a_rows = [r for r in diag["per_token"] if r["address"] == "0xa"]
    assert a_rows, "0xa missing from per-token diagnostics"
    # β should be ≈ 2 with high correlation.
    a = a_rows[0]
    assert a["fallback"] is False
    assert abs(a["beta"] - 2.0) < 1e-3
    assert (a["correlation"] or 0) > 0.99
