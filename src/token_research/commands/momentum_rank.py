"""momentum-rank — single-token feature view with cross-sectional z-score.

Re-uses the most recent saved momentum basket as the reference universe so a
trader can ask "where does ETH sit in the basket I built yesterday?" without
re-running the full screen. If no saved basket exists, falls back to running
a fresh screen with default parameters.

Usage:
    token-research momentum-rank ETH --chain ethereum
    token-research momentum-rank PENDLE --chain ethereum --basket latest
    token-research momentum-rank ONDO --basket-file .token-research/baskets/v1-...json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from token_research.commands.momentum_screen import (
    PILLAR_FEATURES,
    PILLAR_WEIGHTS,
    _composite_z,
    _derive_features,
    _enrich_market_stats,
    _safe_float,
)
from token_research.commands.screen import _build_universe, _enrich_with_yields
from token_research.config import AppConfig
from token_research.models import (
    CommandResult,
    CoverageGap,
    CoverageMetric,
    WarningItem,
    dataclass_list,
)
from token_research.providers.registry import build_source_refs
from token_research.resolver import resolve_with_sources


def _load_latest_basket(config: AppConfig) -> dict[str, Any] | None:
    d = config.data_dir / "baskets"
    if not d.exists():
        return None
    files = sorted(d.glob("*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _load_basket_file(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _zscore_against_matrix(
    features: dict[str, float | None],
    matrix: list[dict[str, Any]],
) -> dict[str, float | None]:
    """Project a single token's features onto the z-score basis of `matrix`.

    `matrix` is the saved basket's `feature_matrix` — it carries a row of raw
    features per universe member at basket time. We compute mean / stdev over
    each column from the matrix and use those to z-score the new token. This
    keeps the basis stable even after the universe drifts.
    """
    out: dict[str, float | None] = {}
    for pillar, feats in PILLAR_FEATURES.items():
        for name, higher_is_better in feats:
            values = [
                _safe_float((row.get("features") or {}).get(name))
                for row in matrix
                if (row.get("features") or {}).get(name) is not None
            ]
            v = features.get(name)
            if v is None or len(values) < 2:
                out[name] = None
                continue
            m = sum(values) / len(values)
            var = sum((x - m) ** 2 for x in values) / (len(values) - 1)
            sd = var ** 0.5 if var > 0 else 0.0
            if sd <= 0:
                out[name] = 0.0
                continue
            z = (float(v) - m) / sd
            out[name] = z if higher_is_better else -z
    return out


def run(
    query: str,
    chain: str | None,
    address: str | None,
    config: AppConfig,
    include_premium: bool,
    *,
    basket_file: str | None = None,
):
    identity, sources, warnings, coverage_gaps = resolve_with_sources(query, chain, address, config)

    if not identity.token_address:
        coverage_gaps.append(CoverageGap(
            metric=CoverageMetric.TOKEN_ADDRESS,
            reason="Could not resolve a token address — z-score requires on-chain identity.",
            suggested_source="Pass --chain and --address explicitly.",
        ))
        return _result(query, chain, address, include_premium, config,
                       {"verdict": "unresolved"}, warnings, coverage_gaps)

    # Load reference basket
    basket: dict[str, Any] | None = None
    basket_source: str | None = None
    if basket_file:
        basket = _load_basket_file(Path(basket_file))
        basket_source = basket_file
    if basket is None:
        basket = _load_latest_basket(config)
        if basket is not None:
            basket_source = "latest"

    if basket is None:
        coverage_gaps.append(CoverageGap(
            metric=CoverageMetric.PRIMITIVE_INFERENCE,
            reason="No saved basket found — run `momentum-screen` first to build a reference universe.",
            suggested_source="token-research momentum-screen <tag>",
        ))
        return _result(query, chain, address, include_premium, config,
                       {"verdict": "no_basket"}, warnings, coverage_gaps)

    matrix_holder = basket.get("feature_matrix") or (basket.get("metrics") or {}).get("basket", {}).get("feature_matrix")
    matrix: list[dict[str, Any]] = matrix_holder if isinstance(matrix_holder, list) else []
    if not matrix:
        coverage_gaps.append(CoverageGap(
            metric=CoverageMetric.PRIMITIVE_INFERENCE,
            reason="Reference basket has no feature_matrix — cannot compute z-score.",
            suggested_source="Re-run momentum-screen with current code.",
        ))
        return _result(query, chain, address, include_premium, config,
                       {"verdict": "empty_basket", "basket_source": basket_source},
                       warnings, coverage_gaps)

    # Build a one-entry universe and enrich it the same way the screen does.
    if config.offline:
        coverage_gaps.append(CoverageGap(
            metric=CoverageMetric.PRIMITIVE_INFERENCE,
            reason="Offline mode — cannot fetch live features for the target token.",
            suggested_source="Disable offline mode.",
        ))
        return _result(query, chain, address, include_premium, config,
                       {"verdict": "offline", "basket_source": basket_source},
                       warnings, coverage_gaps)

    # _build_universe drives off /protocols. To rank an arbitrary token we
    # build a synthetic entry from the resolved identity and let enrichment
    # do the rest. Some features (chain count, tvl change) are missing for
    # tokens not in the DeFiLlama directory — those become coverage gaps.
    proto_match = _find_protocol_entry(config, identity.token_address)
    entry = proto_match or {
        "name": identity.project_name,
        "symbol": identity.symbol,
        "chain": identity.chain,
        "address": identity.token_address,
        "mcap": 0.0,
        "tvl": 0.0,
        "change_7d": 0.0,
        "change_1m": 0.0,
        "chains": [identity.chain] if identity.chain else [],
    }

    # Enrich the single-entry universe with the same collectors momentum-screen uses.
    universe = [entry]
    _enrich_with_yields(universe, config)
    _enrich_market_stats(universe, config)

    features = _derive_features(entry)
    z_row = _zscore_against_matrix(features, matrix)
    z_total, coverage, pillar_z = _composite_z(z_row)

    side: str
    if z_total >= 1.5:
        side = "long_candidate"
    elif z_total <= -1.5:
        side = "short_candidate"
    else:
        side = "neutral"

    metrics: dict[str, Any] = {
        "basket_source": basket_source,
        "z_total": round(z_total, 4),
        "coverage_ratio": round(coverage, 4),
        "side": side,
        "pillar_z": {p: (round(v, 4) if v is not None else None) for p, v in pillar_z.items()},
        "z_features": {k: (round(v, 4) if v is not None else None) for k, v in z_row.items()},
        "raw_features": features,
        "pillar_weights": PILLAR_WEIGHTS,
    }
    return _result(query, chain, address, include_premium, config, metrics, warnings, coverage_gaps)


def _find_protocol_entry(config: AppConfig, address: str) -> dict[str, Any] | None:
    """Look up a protocol in DefiLlama by token address. Best-effort."""
    try:
        # Reuse momentum-screen's universe builder with a wide TVL band. This
        # is cheaper than scanning the full /protocols list manually and gets
        # us the same enriched fields (chain, change_*, etc.).
        universe = _build_universe(
            config, min_tvl=0.0, max_tvl=1e15, category_filter=None,
        )
    except Exception:
        return None
    addr_lower = (address or "").lower()
    for proto in universe:
        if str(proto.get("address") or "").lower() == addr_lower:
            return proto
    return None


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
        command="momentum-rank",
        input={
            "query": query,
            "chain": chain,
            "address": address,
            "include_premium": include_premium,
        },
        resolved_identity={"query": query, "chain": chain, "address": address},
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
