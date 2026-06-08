"""Tests for the bullish-hedged portfolio pipeline.

Covers covariance + shrinkage math, the risk-parity solver, hedge sizing
math, and the full portfolio-build / portfolio-rebalance flow against
synthetic price series so it runs offline.
"""
from __future__ import annotations

import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

import pytest

from token_research.commands import portfolio_build as pb
from token_research.commands import portfolio_rebalance as pr
from token_research.config import AppConfig
from token_research.prices import write_reference_series, write_series
from token_research.prices.covariance import (
    compute_covariance,
    sample_covariance,
    shrink_to_identity,
)
from token_research.prices.optimizer import (
    optimize_long_short,
    solve_risk_parity,
    solve_self_funded_hedge,
)
from token_research.prices.portfolio_universe import UNIVERSE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _series_from_returns(start_iso: str, returns: list[float], base: float = 100.0):
    out = [{"date_iso": start_iso, "price_usd": base}]
    price = base
    for i, r in enumerate(returns, start=1):
        price *= math.exp(r)
        out.append({
            "date_iso": (date.fromisoformat(start_iso) + timedelta(days=i)).isoformat(),
            "price_usd": round(price, 8),
        })
    return out


def _correlated_series(n_days: int, n_assets: int, seed: int = 0):
    """Build a small set of synthetic series with known covariance structure."""
    rng = random.Random(seed)
    common = [rng.gauss(0, 0.02) for _ in range(n_days)]
    out = []
    for i in range(n_assets):
        idio_scale = 0.005 + 0.005 * i
        beta = 0.8 + 0.1 * i
        rets = [beta * c + rng.gauss(0, idio_scale) for c in common]
        out.append((f"A{i}", _series_from_returns("2026-01-01", rets, base=100.0 + i)))
    return out


# ---------------------------------------------------------------------------
# Covariance + shrinkage
# ---------------------------------------------------------------------------


def test_sample_covariance_diagonal_matches_variance():
    rng = random.Random(7)
    rows = [[rng.gauss(0, 0.01) for _ in range(50)] for _ in range(3)]
    sigma = sample_covariance(rows)
    for i in range(3):
        m = sum(rows[i]) / len(rows[i])
        var = sum((x - m) ** 2 for x in rows[i]) / (len(rows[i]) - 1)
        assert abs(sigma[i][i] - var) < 1e-12


def test_sample_covariance_symmetric():
    rng = random.Random(11)
    rows = [[rng.gauss(0, 0.01) for _ in range(40)] for _ in range(4)]
    sigma = sample_covariance(rows)
    for i in range(4):
        for j in range(4):
            assert abs(sigma[i][j] - sigma[j][i]) < 1e-15


def test_shrinkage_zero_returns_sample():
    sample = [[2.0, 0.5], [0.5, 3.0]]
    out = shrink_to_identity(sample, intensity=0.0)
    assert out == sample


def test_shrinkage_one_returns_diagonal_meanvar():
    sample = [[2.0, 0.5], [0.5, 3.0]]
    out = shrink_to_identity(sample, intensity=1.0)
    mean_var = (2.0 + 3.0) / 2
    assert out[0][0] == pytest.approx(mean_var)
    assert out[1][1] == pytest.approx(mean_var)
    assert out[0][1] == pytest.approx(0.0)


def test_shrinkage_partial_blends():
    sample = [[2.0, 0.5], [0.5, 3.0]]
    out = shrink_to_identity(sample, intensity=0.5)
    mean_var = 2.5
    assert out[0][0] == pytest.approx(0.5 * 2.0 + 0.5 * mean_var)
    assert out[0][1] == pytest.approx(0.5 * 0.5 + 0.5 * 0.0)


def test_compute_covariance_aligns_dates():
    series_a = [(name, rows) for name, rows in _correlated_series(60, 4, seed=1)]
    cov = compute_covariance(series_a, window_days=50, shrinkage=0.0)
    assert cov is not None
    assert len(cov.assets) == 4
    assert cov.observations == 50
    # Sigma is square + symmetric.
    n = len(cov.sigma)
    assert n == 4
    for i in range(n):
        for j in range(n):
            assert cov.sigma[i][j] == pytest.approx(cov.sigma[j][i], rel=1e-12, abs=1e-15)


def test_compute_covariance_returns_none_on_disjoint_dates():
    a = ("X", _series_from_returns("2025-01-01", [0.01, 0.02, 0.01]))
    b = ("Y", _series_from_returns("2026-06-01", [0.01, 0.02, 0.01]))
    cov = compute_covariance([a, b], window_days=10, shrinkage=0.2)
    assert cov is None


# ---------------------------------------------------------------------------
# Risk-parity solver
# ---------------------------------------------------------------------------


def test_risk_parity_equal_diagonal_yields_equal_weights():
    """Σ = σ²·I → equal weights for any size."""
    sigma = [[0.04 if i == j else 0.0 for j in range(5)] for i in range(5)]
    res = solve_risk_parity(sigma)
    assert res.converged
    for w in res.weights:
        assert abs(w - 0.2) < 1e-6


def test_risk_parity_dispersion_under_one_percent_for_correlated_inputs():
    """With realistic shrinked covariance, risk shares should be ≤ 1% apart."""
    series = _correlated_series(120, 8, seed=42)
    cov = compute_covariance(series, window_days=120, shrinkage=0.2)
    res = solve_risk_parity(cov.sigma)
    port_var = sum(
        res.weights[i] * sum(cov.sigma[i][j] * res.weights[j] for j in range(len(res.weights)))
        for i in range(len(res.weights))
    )
    rc_share = [(res.weights[i] * sum(cov.sigma[i][j] * res.weights[j] for j in range(len(res.weights)))) / port_var
                for i in range(len(res.weights))]
    target = 1.0 / len(res.weights)
    assert max(abs(rc - target) for rc in rc_share) < 0.01
    assert sum(res.weights) == pytest.approx(1.0)


def test_risk_parity_single_asset():
    res = solve_risk_parity([[0.01]])
    assert res.weights == [1.0]


def test_risk_parity_empty():
    res = solve_risk_parity([])
    assert res.weights == []


# ---------------------------------------------------------------------------
# Hedge sizing — closed form
# ---------------------------------------------------------------------------


def test_hedge_self_funded_invariant():
    res = solve_self_funded_hedge(
        long_weights=[0.5, 0.5], long_betas=[1.2, 0.8], target_net_beta=0.4,
    )
    assert res.long_pct + res.short_pct == pytest.approx(1.0)
    res.assert_invariants()


def test_hedge_sizing_targets_correct_net_beta():
    """β_long = 1.0, β_short = 1.0, target = 0.4 → long=0.7, short=0.3, net=0.4."""
    res = solve_self_funded_hedge(
        long_weights=[1.0], long_betas=[1.0], target_net_beta=0.4,
    )
    assert res.long_pct == pytest.approx(0.7)
    assert res.short_pct == pytest.approx(0.3)
    assert res.realized_net_beta == pytest.approx(0.4)


def test_hedge_high_beta_longs_need_smaller_long_share():
    """β_long avg = 1.5 → long_pct = (0.4+1)/(1.5+1) = 0.56."""
    res = solve_self_funded_hedge(
        long_weights=[0.5, 0.5], long_betas=[1.6, 1.4], target_net_beta=0.4,
    )
    assert res.long_pct == pytest.approx(0.56, abs=1e-6)
    assert res.short_pct == pytest.approx(0.44, abs=1e-6)
    assert res.realized_net_beta == pytest.approx(0.4, abs=1e-9)


def test_hedge_zero_net_beta_request():
    """target=0 with β_long=1 → long=0.5, short=0.5 (full neutral)."""
    res = solve_self_funded_hedge(
        long_weights=[1.0], long_betas=[1.0], target_net_beta=0.0,
    )
    assert res.long_pct == pytest.approx(0.5)
    assert res.short_pct == pytest.approx(0.5)
    assert res.realized_net_beta == pytest.approx(0.0)


def test_hedge_clamps_at_full_long_when_target_unreachable():
    """target above β_long → 100% long, 0% short, realized < target."""
    res = solve_self_funded_hedge(
        long_weights=[1.0], long_betas=[0.5], target_net_beta=0.9,
    )
    assert res.long_pct == pytest.approx(1.0)
    assert res.short_pct == pytest.approx(0.0)
    assert res.realized_net_beta == pytest.approx(0.5)


def test_optimize_long_short_full_round_trip():
    series = _correlated_series(120, 6, seed=99)
    cov = compute_covariance(series, window_days=120, shrinkage=0.2)
    long_betas = [0.8 + 0.1 * i for i in range(6)]
    res = optimize_long_short(cov.sigma, long_betas, target_net_beta=0.4)
    # long pct + short pct ≈ 1
    long_total = sum(res.long_notional_pct)
    assert long_total + res.short_notional_pct == pytest.approx(1.0, abs=1e-9)
    # Realized beta within tolerance of target.
    assert abs(res.realized_net_beta - 0.4) < 0.05


# ---------------------------------------------------------------------------
# Single-position cap
# ---------------------------------------------------------------------------


def test_single_position_cap_redistributes():
    notionals = [0.5, 0.2, 0.2, 0.1]
    capped, fired = pb._apply_single_position_cap(notionals, cap=0.30)
    assert fired
    assert max(capped) <= 0.30 + 1e-9
    assert sum(capped) == pytest.approx(sum(notionals), abs=1e-9)


def test_single_position_cap_noop_when_under():
    notionals = [0.25, 0.25, 0.25, 0.25]
    capped, fired = pb._apply_single_position_cap(notionals, cap=0.40)
    assert not fired
    assert capped == notionals


# ---------------------------------------------------------------------------
# Universe sanity
# ---------------------------------------------------------------------------


def test_universe_categories_present():
    cats = {a.category for a in UNIVERSE}
    # Need ≥ 3 categories represented to satisfy diversification minima.
    assert len(cats) >= 5


def test_universe_unique_names():
    names = [a.name for a in UNIVERSE]
    assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# End-to-end: build + rebalance using synthetic caches
# ---------------------------------------------------------------------------


def _seed_cache_for_universe(tmp_path: Path, n_days: int = 120, seed: int = 1):
    """Populate a synthetic cache for every UNIVERSE member + BTC reference.

    All return series share a common BTC-like factor so β-vs-BTC ≈ 1 for
    each name and the optimizer has enough dispersion to converge.
    """
    rng = random.Random(seed)
    common = [rng.gauss(0.0008, 0.025) for _ in range(n_days)]
    btc = _series_from_returns("2026-01-01", common, base=80_000.0)
    write_reference_series(tmp_path, "btc", btc)

    for i, asset in enumerate(UNIVERSE):
        idio = [rng.gauss(0, 0.015) for _ in range(n_days)]
        beta = 0.8 + 0.05 * (i % 6)  # vary β per asset 0.8 .. 1.05
        rets = [beta * c + idio[k] for k, c in enumerate(common)]
        rows = _series_from_returns("2026-01-01", rets, base=100.0 + i)
        if asset.chain and asset.address:
            write_series(tmp_path, asset.chain, asset.address, rows)
        else:
            write_reference_series(tmp_path, asset.cache_key, rows)


def test_portfolio_build_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_RESEARCH_OFFLINE", "0")
    monkeypatch.setenv("TOKEN_RESEARCH_DATA_DIR", str(tmp_path))
    cfg = AppConfig.from_env()
    cfg.ensure_directories()

    _seed_cache_for_universe(tmp_path)
    res = pb.run("test-book", None, None, cfg, False, no_persist=True).to_dict()
    book = res["metrics"]["book"]
    assert book["selected_long_count"] >= 10
    assert book["selected_long_count"] <= 15
    # Gross check: |target_pct| sums to 1.0 within rounding.
    total = sum(p["target_pct"] for p in book["positions"])
    assert total == pytest.approx(1.0, abs=1e-3)
    # Net beta hits target within tolerance.
    assert abs(book["realized_net_beta"] - 0.4) < 0.10
    # At least one BTC short position present.
    shorts = [p for p in book["positions"] if p["direction"] == "short"]
    assert any(p["name"] == "BTC" for p in shorts)


def test_portfolio_build_long_only_has_no_short(tmp_path, monkeypatch):
    """--long-only skips the BTC hedge entirely. Gross = net = 100% long."""
    monkeypatch.setenv("TOKEN_RESEARCH_OFFLINE", "0")
    monkeypatch.setenv("TOKEN_RESEARCH_DATA_DIR", str(tmp_path))
    cfg = AppConfig.from_env()
    cfg.ensure_directories()
    _seed_cache_for_universe(tmp_path)

    res = pb.run("long-only-test", None, None, cfg, False,
                 no_persist=True, long_only=True).to_dict()
    book = res["metrics"]["book"]
    shorts = [p for p in book["positions"] if p["direction"] == "short"]
    assert shorts == [], f"long-only book should have no shorts: {shorts}"
    longs = [p for p in book["positions"] if p["direction"] == "long"]
    long_total = sum(p["target_pct"] for p in longs)
    # Gross = 100% all on the long side.
    assert long_total == pytest.approx(1.0, abs=1e-3)
    assert book["short_pct_total"] == pytest.approx(0.0)
    # Realized net beta = long-leg average beta (no hedge to subtract).
    assert book["realized_net_beta"] == pytest.approx(book["realized_long_avg_beta"])


def test_portfolio_build_with_alpha_shorts(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_RESEARCH_OFFLINE", "0")
    monkeypatch.setenv("TOKEN_RESEARCH_DATA_DIR", str(tmp_path))
    cfg = AppConfig.from_env()
    cfg.ensure_directories()
    _seed_cache_for_universe(tmp_path)

    # max_longs=12 leaves the universe (16 names) with 4 candidates for
    # alpha shorts. Asking for 2 alpha shorts then yields 1 BTC + 2 alpha.
    res = pb.run("alpha-test", None, None, cfg, False,
                 no_persist=True, alpha_shorts=2, max_longs=12).to_dict()
    book = res["metrics"]["book"]
    shorts = [p for p in book["positions"] if p["direction"] == "short"]
    assert len(shorts) >= 3
    assert any(p["name"] == "BTC" for p in shorts)
    assert sum(1 for p in shorts if p["name"] != "BTC") >= 2


def test_portfolio_rebalance_no_drift_when_unchanged(tmp_path, monkeypatch):
    """Rebalance immediately after build → drift ≈ 0, no trades."""
    monkeypatch.setenv("TOKEN_RESEARCH_OFFLINE", "0")
    monkeypatch.setenv("TOKEN_RESEARCH_DATA_DIR", str(tmp_path))
    cfg = AppConfig.from_env()
    cfg.ensure_directories()
    _seed_cache_for_universe(tmp_path)

    pb.run("nochange", None, None, cfg, False).to_dict()
    res = pr.run("nochange", None, None, cfg, False).to_dict()
    report = res["metrics"]["report"]
    assert report["portfolio_id"] == "nochange"
    # All drifts effectively zero — entry price == current price.
    for r in report["rows"]:
        assert abs(r["drift_pct"]) < 1e-9


def test_portfolio_rebalance_detects_price_drift(tmp_path, monkeypatch):
    """After build, mutate one cache file to simulate a price move; the
    rebalance command should flag drift on that name only."""
    monkeypatch.setenv("TOKEN_RESEARCH_OFFLINE", "0")
    monkeypatch.setenv("TOKEN_RESEARCH_DATA_DIR", str(tmp_path))
    cfg = AppConfig.from_env()
    cfg.ensure_directories()
    _seed_cache_for_universe(tmp_path)

    pb.run("drift-test", None, None, cfg, False).to_dict()

    # Find ONDO's cache and double its last close to simulate a +100% move.
    ondo = next(a for a in UNIVERSE if a.name == "ONDO")
    from token_research.prices.cache import cache_path, load_series
    p = cache_path(tmp_path, ondo.chain, ondo.address)
    rows = load_series(tmp_path, ondo.chain, ondo.address)
    rows[-1]["price_usd"] = rows[-1]["price_usd"] * 2.0
    p.write_text(json.dumps(rows))

    res = pr.run("drift-test", None, None, cfg, False, drift_threshold_pct=0.005).to_dict()
    report = res["metrics"]["report"]
    drifts = {r["name"]: r["drift_pct"] for r in report["rows"]}
    assert drifts["ONDO"] > 0.005, drifts
    assert any(t["name"] == "ONDO" for t in report["trades"])
