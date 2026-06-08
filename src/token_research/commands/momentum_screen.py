"""momentum-screen — beta-neutral long/short basket builder.

Selects a paper-mode long/short basket from the EVM token universe based on
a multi-factor momentum composite (trend, flows, fundamental, concentration,
liquidity). Pure research mode — no exchange execution. The output is a
deterministic snapshot that can be re-loaded by `momentum-backtest`.

Pipeline:

    1. Universe   — DeFiLlama /protocols, intersect with supported EVM chains.
    2. Enrichment — DexScreener pair stats + DeFiLlama yields join.
    3. Features   — per-token feature vector (returns, turnover, TVL momentum,
                    liquidity, concentration, fundamental).
    4. Composite  — cross-sectional z-score per pillar, weighted blend, with
                    coverage penalty for missing features.
    5. Basket     — top-K longs (z > +z_long), bottom-K shorts (z < z_short),
                    plus hard exclusions (squeeze risk, fundamentals up, etc.).
    6. Sizing     — equal-vol-weighted inside each leg, then scale shorts so
                    `Σ(w_long * β_long) == Σ(w_short * β_short)` → net beta ≈ 0.
    7. Persist    — basket JSON + the full feature matrix used for ranking.

Beta is currently defaulted to 1.0 per token (no historical price series in
the pipeline yet). When all betas are equal, beta-neutral sizing collapses to
equal-notional long/short, which is fine for the v1 deliverable but a real
hedged book needs proper betas. Recorded as a coverage gap.

Usage:
    token-research momentum-screen <tag> --top 200 --output basket.json
    token-research momentum-screen v1 --k 5 --long-z 1.5 --short-z -1.5
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from token_research.commands.screen import (
    _build_universe,
    _enrich_with_yields,
    _safe_float,
)
from token_research.config import AppConfig
from token_research.models import (
    CommandResult,
    CoverageGap,
    CoverageMetric,
    WarningCode,
    WarningItem,
    dataclass_list,
    utc_now_iso,
)
from token_research.prices import (
    DEFAULT_BTC_REFERENCE,
    DEFAULT_MIN_OBSERVATIONS,
    DEFAULT_WINDOW_DAYS,
    BetaResult,
    compute_beta,
    load_reference_series,
    load_series,
)
from token_research.providers import dexscreener
from token_research.providers.http import ProviderError
from token_research.providers.registry import build_source_refs


# ---------------------------------------------------------------------------
# Defaults (overridable via kwargs / CLI flags)
# ---------------------------------------------------------------------------

DEFAULT_MIN_TVL = 1_000_000.0
DEFAULT_MAX_TVL = 10_000_000_000.0
DEFAULT_MIN_DEX_LIQ = 1_000_000.0
DEFAULT_TOP_N = 200
DEFAULT_K = 5
DEFAULT_LONG_Z = 1.5
DEFAULT_SHORT_Z = -1.5

# Per-pillar feature lists. Cross-sectional z-scores are averaged within a
# pillar (skipping missing values). The pillar weights are then applied.
PILLAR_FEATURES: dict[str, tuple[tuple[str, bool], ...]] = {
    # (feature_name, higher_is_better)
    "trend": (
        ("ret_24h_pct", True),
        ("tvl_change_7d_pct", True),
        ("tvl_change_30d_pct", True),
    ),
    "flows": (
        ("turnover", True),       # 24h volume / mcap
        ("volume_24h_usd", True),
    ),
    "fundamental": (
        ("best_apy_pct", True),
        ("yield_pool_count", True),
        ("chain_count", True),
    ),
    "concentration": (
        # Inverted: smaller top-10 share is better. We negate the value so
        # the same "higher z = better" convention applies downstream.
        ("top10_share_pct", False),
    ),
    "liquidity": (
        ("dex_liquidity_usd", True),
        ("liq_to_mcap", True),
    ),
}

PILLAR_WEIGHTS: dict[str, float] = {
    "trend": 0.35,
    "flows": 0.25,
    "fundamental": 0.20,
    "concentration": 0.10,
    "liquidity": 0.10,
}

COVERAGE_PENALTY_COEFF = 0.15  # in z-score units


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PaperPosition:
    symbol: str | None
    project_name: str | None
    chain: str
    address: str
    side: str             # "long" | "short"
    weight: float         # leg-internal weight (sums to 1.0 within a leg)
    notional_pct: float   # share of total gross exposure
    beta: float
    z_score: float
    expected_funding_bps: float
    features: dict[str, float | None]


@dataclass(frozen=True, slots=True)
class PaperBasket:
    generated_at: str
    universe_size: int
    candidates_after_filter: int
    longs: list[PaperPosition] = field(default_factory=list)
    shorts: list[PaperPosition] = field(default_factory=list)
    gross_pct: float = 0.0
    net_pct: float = 0.0
    net_beta: float = 0.0
    expected_carry_bps: float = 0.0
    coverage_ratio: float = 0.0
    feature_matrix: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Stats helpers (stdlib only — no numpy)
# ---------------------------------------------------------------------------


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs: list[float]) -> float:
    """Sample standard deviation. Returns 0 when fewer than 2 points."""
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var) if var > 0 else 0.0


def _zscore(value: float, mean: float, sd: float) -> float:
    if sd <= 0:
        return 0.0
    return (value - mean) / sd


# ---------------------------------------------------------------------------
# Stage 1 — universe + enrichment
# ---------------------------------------------------------------------------


def _enrich_market_stats(universe: list[dict], config: AppConfig) -> None:
    """Pull DexScreener pair data per token. Mutates entries in place.

    Adds: dex_liquidity_usd, dex_volume_24h_usd, dex_mcap, ret_24h_pct,
    pair_count, top_pair_chain.
    """
    for entry in universe:
        try:
            pairs = dexscreener.get_token_pairs(entry["chain"], entry["address"], config)
        except ProviderError:
            pairs = []

        if not pairs:
            entry["dex_liquidity_usd"] = None
            entry["dex_volume_24h_usd"] = None
            entry["dex_mcap"] = None
            entry["ret_24h_pct"] = None
            entry["pair_count"] = 0
            continue

        liq = sum(_safe_float((p.get("liquidity") or {}).get("usd")) for p in pairs)
        vol = 0.0
        ret_weighted_num = 0.0
        ret_weighted_den = 0.0
        mcap = 0.0
        for p in pairs:
            v = p.get("volume")
            if isinstance(v, dict):
                vol += _safe_float(v.get("h24"))
            pc = p.get("priceChange")
            if isinstance(pc, dict):
                # Liquidity-weighted average of h24 priceChange across pairs.
                # A token traded on five venues should weight its main pool
                # more than dust pools that drift independently.
                pl = _safe_float((p.get("liquidity") or {}).get("usd"))
                ph24 = _safe_float(pc.get("h24"))
                if pl > 0:
                    ret_weighted_num += ph24 * pl
                    ret_weighted_den += pl
            mc = _safe_float(p.get("marketCap"))
            if mc > mcap:
                mcap = mc

        entry["dex_liquidity_usd"] = round(liq, 2)
        entry["dex_volume_24h_usd"] = round(vol, 2)
        entry["dex_mcap"] = mcap if mcap > 0 else None
        entry["ret_24h_pct"] = (
            round(ret_weighted_num / ret_weighted_den, 4) if ret_weighted_den > 0 else None
        )
        entry["pair_count"] = len(pairs)


# ---------------------------------------------------------------------------
# Stage 2 — feature derivation
# ---------------------------------------------------------------------------


def _derive_features(entry: dict) -> dict[str, float | None]:
    """Project an enriched universe entry onto the canonical feature set."""
    mcap = _safe_float(entry.get("dex_mcap")) or _safe_float(entry.get("mcap"))
    liq = entry.get("dex_liquidity_usd")
    vol = entry.get("dex_volume_24h_usd")

    turnover = vol / mcap if (mcap and vol is not None and mcap > 0) else None
    liq_to_mcap = liq / mcap if (mcap and liq is not None and mcap > 0) else None

    return {
        "ret_24h_pct": entry.get("ret_24h_pct"),
        "tvl_change_7d_pct": entry.get("change_7d"),
        "tvl_change_30d_pct": entry.get("change_1m"),
        "turnover": turnover,
        "volume_24h_usd": vol,
        "best_apy_pct": entry.get("best_apy"),
        "yield_pool_count": entry.get("yield_pool_count"),
        "chain_count": float(len(entry.get("chains") or [])) or None,
        "top10_share_pct": entry.get("top10_share_pct"),  # populated by optional enrichment
        "dex_liquidity_usd": liq,
        "liq_to_mcap": liq_to_mcap,
    }


# ---------------------------------------------------------------------------
# Stage 3 — z-score composite
# ---------------------------------------------------------------------------


def _compute_zscores(
    feature_rows: list[dict[str, float | None]],
) -> list[dict[str, float | None]]:
    """Cross-sectional z-score for every feature listed in PILLAR_FEATURES.

    Missing values stay missing. For inverted features (`higher_is_better=False`)
    we flip the sign so downstream "higher z = better" math holds uniformly.
    """
    out: list[dict[str, float | None]] = [dict() for _ in feature_rows]
    for pillar, features in PILLAR_FEATURES.items():
        for name, higher_is_better in features:
            values = [
                _safe_float(row.get(name))
                for row in feature_rows
                if row.get(name) is not None
            ]
            if len(values) < 2:
                # Not enough data to form a meaningful cross-sectional z.
                for i in range(len(feature_rows)):
                    out[i][name] = None
                continue
            mu = _mean(values)
            sd = _stdev(values)
            for i, row in enumerate(feature_rows):
                raw = row.get(name)
                if raw is None:
                    out[i][name] = None
                else:
                    z = _zscore(float(raw), mu, sd)
                    out[i][name] = z if higher_is_better else -z
    return out


def _composite_z(
    z_row: dict[str, float | None],
) -> tuple[float, float, dict[str, float | None]]:
    """Weighted pillar average + coverage ratio + per-pillar z map.

    Returns (z_total_with_penalty, coverage_ratio, pillar_z_map).
    """
    pillar_z: dict[str, float | None] = {}
    weighted_sum = 0.0
    weight_total = 0.0
    available_features = 0
    total_features = 0

    for pillar, features in PILLAR_FEATURES.items():
        zs: list[float] = []
        for name, _hib in features:
            total_features += 1
            v = z_row.get(name)
            if v is not None:
                zs.append(float(v))
                available_features += 1
        if zs:
            pz = _mean(zs)
            pillar_z[pillar] = pz
            weight = PILLAR_WEIGHTS[pillar]
            weighted_sum += pz * weight
            weight_total += weight
        else:
            pillar_z[pillar] = None

    if weight_total <= 0:
        return 0.0, 0.0, pillar_z

    raw = weighted_sum / weight_total
    coverage_ratio = available_features / total_features if total_features else 0.0
    penalty = COVERAGE_PENALTY_COEFF * (1.0 - coverage_ratio)
    return raw - penalty, coverage_ratio, pillar_z


# ---------------------------------------------------------------------------
# Stage 4 — basket selection
# ---------------------------------------------------------------------------


def _passes_short_exclusions(entry: dict, features: dict[str, float | None]) -> bool:
    """Hard short filters from the task spec — squeeze / fundamental risks."""
    # Fundamentals improving despite price decline → don't short.
    apy = _safe_float(features.get("best_apy_pct"))
    pool_count = _safe_float(features.get("yield_pool_count"))
    if apy is not None and apy >= 15 and pool_count and pool_count >= 5:
        return False
    # Add more exclusions here as additional features land (unlock window,
    # fee/revenue trend up). For v1 we have only the proxies above.
    return True


def _select_basket(
    candidates: list[dict[str, Any]],
    *,
    k: int,
    long_threshold: float,
    short_threshold: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Pick top-K longs and bottom-K shorts respecting z-score thresholds."""
    sorted_desc = sorted(candidates, key=lambda c: c["z_total"], reverse=True)
    longs = [c for c in sorted_desc if c["z_total"] >= long_threshold][:k]

    sorted_asc = sorted(candidates, key=lambda c: c["z_total"])
    shorts: list[dict[str, Any]] = []
    for c in sorted_asc:
        if c["z_total"] > short_threshold:
            break
        if not _passes_short_exclusions(c["entry"], c["features"]):
            continue
        shorts.append(c)
        if len(shorts) >= k:
            break

    return longs, shorts


# ---------------------------------------------------------------------------
# Stage 5 — beta-neutral sizing
# ---------------------------------------------------------------------------


def _approx_volatility(features: dict[str, float | None]) -> float:
    """Stand-in volatility used for inverse-vol weighting.

    With no historical price series in v1 we approximate per-token volatility
    from the absolute value of `ret_24h_pct`. Tokens with no return data fall
    back to 1.0 so they neither dominate nor disappear from the leg.
    """
    r = _safe_float(features.get("ret_24h_pct"))
    if r is None or r == 0:
        return 1.0
    # Annualization is irrelevant — only relative scale matters for weighting.
    return max(abs(r), 0.5)


def size_beta_neutral(
    longs: list[dict[str, Any]],
    shorts: list[dict[str, Any]],
) -> tuple[list[float], list[float], float, float, float]:
    """Equal-inverse-vol weight inside each leg, then scale shorts to neutralize beta.

    Returns:
        (long_weights, short_weights, gross_pct, net_pct, net_beta)

    `weights` are leg-internal (sum to 1.0 each). Caller turns them into
    notional fractions of the gross book.

    When the long leg has zero net beta exposure (`Σ w_long * β_long == 0`),
    the short leg is sized 1:1 with the long leg in dollar terms — same gross
    on each side, no further scaling possible.
    """
    if not longs or not shorts:
        return [], [], 0.0, 0.0, 0.0

    def _inv_vol(items: list[dict[str, Any]]) -> list[float]:
        raw = [1.0 / _approx_volatility(it["features"]) for it in items]
        s = sum(raw)
        return [r / s for r in raw] if s > 0 else [1.0 / len(items)] * len(items)

    w_long = _inv_vol(longs)
    w_short = _inv_vol(shorts)

    beta_exposure_long = sum(w_long[i] * longs[i]["beta"] for i in range(len(longs)))
    beta_exposure_short = sum(w_short[j] * shorts[j]["beta"] for j in range(len(shorts)))

    # Scale the short leg's gross dollar exposure so that
    #   long_gross * β_long = short_gross * β_short  →  net beta == 0.
    # If β_short_avg is zero (degenerate), fall back to 1:1.
    if abs(beta_exposure_short) < 1e-12:
        short_gross_scale = 1.0
    else:
        short_gross_scale = beta_exposure_long / beta_exposure_short

    long_gross = 1.0
    short_gross = abs(short_gross_scale)

    total = long_gross + short_gross
    gross_pct = total
    net_pct = long_gross - short_gross
    net_beta = beta_exposure_long - short_gross * beta_exposure_short

    long_notional = [w * (long_gross / total) for w in w_long]
    short_notional = [w * (short_gross / total) for w in w_short]
    return long_notional, short_notional, gross_pct, net_pct, net_beta


# ---------------------------------------------------------------------------
# Stage 6 — assemble output + persist
# ---------------------------------------------------------------------------


def _build_positions(
    candidates: list[dict[str, Any]],
    weights: list[float],
    side: str,
    funding_bps_long: float,
    funding_bps_short: float,
) -> list[PaperPosition]:
    out: list[PaperPosition] = []
    for c, w in zip(candidates, weights):
        entry = c["entry"]
        out.append(PaperPosition(
            symbol=entry.get("symbol"),
            project_name=entry.get("name"),
            chain=entry.get("chain"),
            address=entry.get("address"),
            side=side,
            weight=round(w * (1.0 / max(sum(weights), 1e-12)), 6),
            notional_pct=round(w, 6),
            beta=round(c["beta"], 4),
            z_score=round(c["z_total"], 4),
            expected_funding_bps=funding_bps_long if side == "long" else funding_bps_short,
            features={k: v for k, v in c["features"].items()},
        ))
    return out


def _persist_basket(config: AppConfig, basket: PaperBasket, tag: str) -> Path | None:
    try:
        out_dir = config.data_dir / "baskets"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        fname = f"{tag.replace('/', '_')}-{ts}.json"
        path = out_dir / fname
        path.write_text(json.dumps(asdict(basket), indent=2, sort_keys=True, default=str))
        return path
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run(
    query: str,
    chain: str | None,
    address: str | None,
    config: AppConfig,
    include_premium: bool,
    *,
    min_tvl: float = DEFAULT_MIN_TVL,
    max_tvl: float = DEFAULT_MAX_TVL,
    top_n: int = DEFAULT_TOP_N,
    min_dex_liq: float = DEFAULT_MIN_DEX_LIQ,
    k: int = DEFAULT_K,
    long_z: float = DEFAULT_LONG_Z,
    short_z: float = DEFAULT_SHORT_Z,
    long_funding_bps: float = 0.0,
    short_funding_bps: float = 1500.0,  # 15% APR placeholder
    no_persist: bool = False,
    beta_window: int = DEFAULT_WINDOW_DAYS,
    beta_min_obs: int = DEFAULT_MIN_OBSERVATIONS,
    btc_reference: str = DEFAULT_BTC_REFERENCE,
    beta_default: float = 1.0,
):
    persist = not no_persist
    warnings: list[WarningItem] = []
    coverage_gaps: list[CoverageGap] = []

    if config.offline:
        coverage_gaps.append(CoverageGap(
            metric=CoverageMetric.RESOLUTION_CANDIDATES,
            reason="Offline mode — momentum-screen requires DeFiLlama + DexScreener.",
            suggested_source="Disable offline mode.",
        ))
        return _result(query, chain, address, include_premium, config, {
            "stage": "aborted", "reason": "offline",
        }, warnings, coverage_gaps)

    # 1. Universe
    universe = _build_universe(config, min_tvl=min_tvl, max_tvl=max_tvl, category_filter=None)
    universe.sort(key=lambda e: _safe_float(e.get("mcap")) or 0.0, reverse=True)
    universe = universe[:top_n]
    universe_size = len(universe)

    if universe_size < 5:
        warnings.append(WarningItem(
            code=WarningCode.DEFILLAMA_UNAVAILABLE,
            message=f"Universe too small ({universe_size}) for cross-sectional z-scores.",
        ))
        return _result(query, chain, address, include_premium, config, {
            "stage": "1_universe", "universe_size": universe_size,
        }, warnings, coverage_gaps)

    # 2. Enrichment
    _enrich_with_yields(universe, config)
    _enrich_market_stats(universe, config)

    # Filter by minimum DEX liquidity (per-token, after enrichment).
    eligible = [e for e in universe if (e.get("dex_liquidity_usd") or 0) >= min_dex_liq]
    if len(eligible) < 5:
        warnings.append(WarningItem(
            code=WarningCode.DEXSCREENER_UNAVAILABLE,
            message=f"Only {len(eligible)} tokens met liquidity floor ${min_dex_liq:,.0f}.",
        ))

    # 3. Features + z-scores
    feature_rows = [_derive_features(e) for e in eligible]
    z_rows = _compute_zscores(feature_rows)

    # Beta-vs-BTC is read from the price-history cache populated by
    # `token-research price-history`. When the cache is missing or has
    # insufficient overlap with the BTC reference, we fall back to
    # `beta_default` (typically 1.0) and tag the coverage gap with an
    # accurate count so the user knows how much of the basket is using
    # the fallback.
    btc_series = load_reference_series(config.data_dir, btc_reference)
    btc_cache_present = len(btc_series) > 0

    candidates: list[dict[str, Any]] = []
    beta_fallback_count = 0
    beta_real_count = 0
    beta_diagnostics: list[dict[str, Any]] = []
    for entry, features, z_row in zip(eligible, feature_rows, z_rows):
        z_total, coverage, pillar_z = _composite_z(z_row)
        beta_value = beta_default
        beta_meta: dict[str, Any]
        if btc_cache_present:
            token_series = load_series(config.data_dir, entry["chain"], entry["address"])
            result: BetaResult = compute_beta(
                token_series, btc_series,
                window_days=beta_window, min_observations=beta_min_obs,
            )
            if result.beta is not None and not result.fallback_used:
                beta_value = result.beta
                beta_real_count += 1
                beta_meta = {
                    "beta": result.beta,
                    "observations": result.observations,
                    "correlation": result.correlation,
                    "fallback": False,
                }
            else:
                beta_fallback_count += 1
                beta_meta = {
                    "beta": beta_default,
                    "observations": result.observations,
                    "correlation": result.correlation,
                    "fallback": True,
                    "reason": result.reason,
                }
        else:
            beta_fallback_count += 1
            beta_meta = {
                "beta": beta_default,
                "observations": 0,
                "correlation": None,
                "fallback": True,
                "reason": "btc_cache_missing",
            }
        beta_diagnostics.append({
            "symbol": entry.get("symbol"),
            "chain": entry["chain"],
            "address": entry["address"],
            **beta_meta,
        })
        candidates.append({
            "entry": entry,
            "features": features,
            "z_features": z_row,
            "pillar_z": pillar_z,
            "z_total": z_total,
            "coverage_ratio": coverage,
            "beta": beta_value,
        })

    if beta_fallback_count > 0:
        coverage_gaps.append(CoverageGap(
            metric=CoverageMetric.PRIMITIVE_INFERENCE,
            reason=(
                f"{beta_fallback_count} of {len(candidates)} tokens used beta={beta_default} "
                f"fallback (no cache or <{beta_min_obs} overlapping daily returns). "
                f"BTC cache present: {btc_cache_present}."
            ),
            suggested_source=(
                "Run `token-research price-history --reference btc --since <2y-ago>` "
                "to seed the BTC reference, then `--from-basket latest` for the "
                "constituents (requires the [ccxt] extra)."
            ),
        ))

    if not candidates:
        return _result(query, chain, address, include_premium, config, {
            "stage": "3_features", "candidates": 0,
        }, warnings, coverage_gaps)

    # 4. Basket selection
    longs_raw, shorts_raw = _select_basket(
        candidates, k=k, long_threshold=long_z, short_threshold=short_z,
    )

    # 5. Sizing
    long_n, short_n, gross_pct, net_pct, net_beta = size_beta_neutral(longs_raw, shorts_raw)

    # 6. Build positions + basket dataclass
    long_positions = _build_positions(longs_raw, long_n, "long",
                                      long_funding_bps, short_funding_bps)
    short_positions = _build_positions(shorts_raw, short_n, "short",
                                       long_funding_bps, short_funding_bps)

    # Expected carry = funding-cost weighted by short notional only (longs
    # don't pay funding on spot; shorts on perps do). Negative = bleed.
    short_notional_total = sum(p.notional_pct for p in short_positions)
    expected_carry_bps = -short_funding_bps * short_notional_total

    # Coverage ratio for the basket = mean coverage of selected names.
    selected = longs_raw + shorts_raw
    basket_coverage = _mean([c["coverage_ratio"] for c in selected]) if selected else 0.0

    # Feature matrix snapshot for replay reproducibility.
    feature_matrix = [{
        "symbol": c["entry"].get("symbol"),
        "project_name": c["entry"].get("name"),
        "chain": c["entry"].get("chain"),
        "address": c["entry"].get("address"),
        "z_total": round(c["z_total"], 4),
        "coverage_ratio": round(c["coverage_ratio"], 4),
        "pillar_z": {k: (round(v, 4) if v is not None else None) for k, v in c["pillar_z"].items()},
        "features": c["features"],
    } for c in candidates]

    basket = PaperBasket(
        generated_at=utc_now_iso(),
        universe_size=universe_size,
        candidates_after_filter=len(eligible),
        longs=long_positions,
        shorts=short_positions,
        gross_pct=round(gross_pct, 4),
        net_pct=round(net_pct, 4),
        net_beta=round(net_beta, 4),
        expected_carry_bps=round(expected_carry_bps, 2),
        coverage_ratio=round(basket_coverage, 4),
        feature_matrix=feature_matrix,
    )

    saved_path = _persist_basket(config, basket, query) if persist else None

    metrics: dict[str, Any] = {
        "tag": query,
        "config": {
            "min_tvl": min_tvl,
            "max_tvl": max_tvl,
            "top_n": top_n,
            "min_dex_liq": min_dex_liq,
            "k": k,
            "long_z_threshold": long_z,
            "short_z_threshold": short_z,
            "long_funding_bps": long_funding_bps,
            "short_funding_bps": short_funding_bps,
            "pillar_weights": PILLAR_WEIGHTS,
            "beta_window": beta_window,
            "beta_min_obs": beta_min_obs,
            "btc_reference": btc_reference,
            "beta_default": beta_default,
        },
        "basket": asdict(basket),
        "saved_to": str(saved_path) if saved_path else None,
        "beta_diagnostics": {
            "btc_cache_present": btc_cache_present,
            "real_count": beta_real_count,
            "fallback_count": beta_fallback_count,
            "per_token": beta_diagnostics,
        },
    }

    if not long_positions:
        warnings.append(WarningItem(
            code=WarningCode.DEFILLAMA_UNAVAILABLE,
            message=f"No tokens cleared the long-z threshold ({long_z}).",
            severity="info",
        ))
    if not short_positions:
        warnings.append(WarningItem(
            code=WarningCode.DEFILLAMA_UNAVAILABLE,
            message=f"No tokens cleared the short-z threshold ({short_z}).",
            severity="info",
        ))

    return _result(query, chain, address, include_premium, config, metrics, warnings, coverage_gaps)


def _result(
    query: str,
    chain: str | None,
    address: str | None,
    include_premium: bool,
    config: AppConfig,
    metrics: dict[str, Any],
    warnings: list[WarningItem],
    coverage_gaps: list[CoverageGap],
) -> CommandResult:
    return CommandResult(
        command="momentum-screen",
        input={
            "query": query,
            "chain": chain,
            "address": address,
            "include_premium": include_premium,
        },
        resolved_identity={"query": query, "mode": "momentum-screen"},
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
