"""screen command — broad market growth-token screening funnel.

Quant-style multi-stage filter that starts from the full DeFiLlama protocol
universe (~3800 entries) and narrows to a ranked shortlist of growth
candidates using only 2 bulk API calls for the first stages.

Stages:
  1. Universe definition — DeFiLlama /protocols, filter by chain + TVL band
  2. Growth signal scoring — join yield pools, compute momentum/value/diversity
  3. Market structure — DexScreener per-token liquidity (top N only)
  4. Ownership check — Blockscout holders + RPC security probe (top N only)
  5. Final ranking — composite growth score

Usage:
    token-research screen growth --min-tvl 500000 --max-tvl 500000000 --top 30
    token-research screen growth --category lending --top 20
"""

from __future__ import annotations

import re
import time
from typing import Any

from token_research.chains import SUPPORTED_EVM_CHAINS, normalize_chain_name
from token_research.config import AppConfig
from token_research.contract_registry import identify_cex
from token_research.models import CommandResult, CoverageGap, CoverageMetric, WarningCode, WarningItem, dataclass_list
from token_research.providers import blockscout, defillama, dexscreener, rpc
from token_research.providers.http import ProviderError
from token_research.providers.registry import build_source_refs


ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

# DeFiLlama chain name → our normalized chain name
_LLAMA_CHAIN_MAP: dict[str, str] = {
    "ethereum": "ethereum",
    "base": "base",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "bsc": "bsc",
    "binance": "bsc",
}


def _safe_float(v: object) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _protocol_age_years(listed_at: object) -> float | None:
    try:
        ts = float(listed_at)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return (time.time() - ts) / (365.25 * 86_400)


def _parse_address_field(raw: str) -> tuple[str | None, str]:
    """Parse DeFiLlama's optional chain:address format."""
    if ":" in raw:
        prefix, addr = raw.split(":", 1)
        return prefix.strip().lower(), addr.strip()
    return None, raw.strip()


# ---------------------------------------------------------------------------
# Stage 1 — Universe from DeFiLlama /protocols
# ---------------------------------------------------------------------------

def _build_universe(
    config: AppConfig,
    *,
    min_tvl: float,
    max_tvl: float,
    category_filter: str | None,
) -> list[dict[str, Any]]:
    """Return protocols with an EVM address in the TVL band."""
    protocols = defillama.get_protocols(config)
    universe: list[dict[str, Any]] = []

    for proto in protocols:
        tvl = _safe_float(proto.get("tvl"))
        if tvl < min_tvl or tvl > max_tvl:
            continue

        raw_addr = str(proto.get("address") or "")
        if not raw_addr:
            continue

        chain_prefix, clean_addr = _parse_address_field(raw_addr)
        if not ADDRESS_RE.fullmatch(clean_addr):
            continue

        # Resolve chain
        if chain_prefix:
            chain = _LLAMA_CHAIN_MAP.get(chain_prefix)
        else:
            # Pick first supported chain from the protocol's chain list
            proto_chains = proto.get("chains") or []
            chain = None
            for c in proto_chains:
                norm = normalize_chain_name(str(c).strip().lower())
                if norm in SUPPORTED_EVM_CHAINS:
                    chain = norm
                    break
        if not chain:
            continue

        # Category filter
        cat = str(proto.get("category") or "").strip().lower()
        if category_filter and category_filter.lower() not in cat:
            continue

        universe.append({
            "name": proto.get("name"),
            "slug": proto.get("slug"),
            "symbol": proto.get("symbol"),
            "category": proto.get("category"),
            "chain": chain,
            "address": clean_addr,
            "tvl": tvl,
            "mcap": _safe_float(proto.get("mcap")),
            "change_1d": _safe_float(proto.get("change_1d")),
            "change_7d": _safe_float(proto.get("change_7d")),
            "change_1m": _safe_float(proto.get("change_1m")),
            "chains": proto.get("chains") or [],
            "listed_at": proto.get("listedAt"),
            "age_years": _protocol_age_years(proto.get("listedAt")),
            "audits": len(proto.get("audit_links") or []),
        })

    return universe


# ---------------------------------------------------------------------------
# Stage 2 — Growth signal scoring (bulk yield join + derived math)
# ---------------------------------------------------------------------------

def _enrich_with_yields(universe: list[dict], config: AppConfig) -> None:
    """Join yield pool data onto universe entries. Mutates in-place."""
    pools = defillama.get_yield_pools(config)
    if not pools:
        return

    # Index pools by project slug for O(1) lookup
    pools_by_project: dict[str, list[dict]] = {}
    for pool in pools:
        project = str(pool.get("project") or "").strip().lower()
        if project:
            pools_by_project.setdefault(project, []).append(pool)

    for entry in universe:
        slug = str(entry.get("slug") or "").strip().lower()
        matched_pools = pools_by_project.get(slug, [])
        entry["yield_pool_count"] = len(matched_pools)
        entry["yield_chains"] = len({
            str(p.get("chain") or "") for p in matched_pools if p.get("chain")
        })

        apys = [_safe_float(p.get("apy")) for p in matched_pools if _safe_float(p.get("apy")) > 0]
        entry["best_apy"] = max(apys) if apys else 0.0
        entry["median_apy"] = sorted(apys)[len(apys) // 2] if apys else 0.0
        entry["pool_tvl_sum"] = sum(_safe_float(p.get("tvlUsd")) for p in matched_pools)


def _score_growth(universe: list[dict]) -> list[dict]:
    """Compute composite growth score per protocol. Returns sorted list."""

    def _normalize(values: list[float], higher_is_better: bool = True) -> list[float]:
        if not values:
            return []
        lo, hi = min(values), max(values)
        span = hi - lo if hi > lo else 1.0
        normed = [(v - lo) / span for v in values]
        return normed if higher_is_better else [1.0 - n for n in normed]

    if not universe:
        return []

    # Extract raw signal vectors
    tvl_mom = [e["change_7d"] * 0.4 + e["change_1m"] * 0.6 for e in universe]
    mcap_tvl = []
    for e in universe:
        if e["mcap"] > 0 and e["tvl"] > 0:
            mcap_tvl.append(e["mcap"] / e["tvl"])
        else:
            mcap_tvl.append(999.0)  # penalize missing
    age_growth = []
    for e in universe:
        age = e.get("age_years")
        if age and age > 0 and e["change_1m"] > 0:
            # Young + growing = high score
            age_growth.append(e["change_1m"] / (age ** 0.5))
        else:
            age_growth.append(0.0)
    best_apy = [e.get("best_apy", 0.0) for e in universe]
    chain_div = [len(e.get("chains", [])) for e in universe]
    pool_count = [e.get("yield_pool_count", 0) for e in universe]
    audit_sig = [min(e.get("audits", 0), 3) / 3.0 * 100 for e in universe]

    # Normalize
    n_mom = _normalize(tvl_mom, higher_is_better=True)
    n_mcap_tvl = _normalize(mcap_tvl, higher_is_better=False)  # lower = undervalued
    n_age = _normalize(age_growth, higher_is_better=True)
    n_apy = _normalize(best_apy, higher_is_better=True)
    n_chains = _normalize([float(c) for c in chain_div], higher_is_better=True)
    n_pools = _normalize([float(p) for p in pool_count], higher_is_better=True)
    n_audit = _normalize(audit_sig, higher_is_better=True)

    weights = {
        "tvl_momentum": 0.25,
        "mcap_tvl_value": 0.20,
        "age_growth": 0.15,
        "yield_attractiveness": 0.15,
        "chain_diversity": 0.10,
        "pool_ecosystem": 0.10,
        "audit_signal": 0.05,
    }

    for i, entry in enumerate(universe):
        signals = {
            "tvl_momentum": n_mom[i] * 100,
            "mcap_tvl_value": n_mcap_tvl[i] * 100,
            "age_growth": n_age[i] * 100,
            "yield_attractiveness": n_apy[i] * 100,
            "chain_diversity": n_chains[i] * 100,
            "pool_ecosystem": n_pools[i] * 100,
            "audit_signal": n_audit[i] * 100,
        }
        composite = sum(signals[k] * weights[k] for k in weights)
        entry["growth_signals"] = {k: round(v, 1) for k, v in signals.items()}
        entry["growth_score"] = round(composite, 2)

    universe.sort(key=lambda e: e["growth_score"], reverse=True)
    return universe


# ---------------------------------------------------------------------------
# Stage 3 — Market structure (DexScreener per-token, top N only)
# ---------------------------------------------------------------------------

def _enrich_market_structure(candidates: list[dict], config: AppConfig) -> list[dict]:
    """Add DEX liquidity data for each candidate. Filters out illiquid."""
    enriched = []
    for entry in candidates:
        try:
            pairs = dexscreener.get_token_pairs(entry["chain"], entry["address"], config)
        except ProviderError:
            pairs = []

        total_liq = sum(_safe_float((p.get("liquidity") or {}).get("usd")) for p in pairs)
        pair_count = len(pairs)
        vol_24h = sum(_safe_float(p.get("volume", {}).get("h24") if isinstance(p.get("volume"), dict) else 0) for p in pairs)
        mcap_dex = 0.0
        for p in pairs:
            mc = _safe_float(p.get("marketCap"))
            if mc > mcap_dex:
                mcap_dex = mc

        entry["dex_liquidity_usd"] = round(total_liq, 2)
        entry["dex_pair_count"] = pair_count
        entry["dex_volume_24h"] = round(vol_24h, 2)
        entry["dex_mcap"] = mcap_dex
        entry["liq_to_mcap"] = round(total_liq / mcap_dex, 4) if mcap_dex > 0 else None

        # Filter: minimum liquidity + pair count
        if total_liq >= 50_000 and pair_count >= 1:
            enriched.append(entry)

    return enriched


# ---------------------------------------------------------------------------
# Stage 4 — Ownership quick-check (Blockscout + RPC, top N only)
# ---------------------------------------------------------------------------

def _enrich_ownership(candidates: list[dict], config: AppConfig) -> list[dict]:
    """Add holder concentration + security probe + CEX listing + holder count."""
    for entry in candidates:
        chain = entry["chain"]
        address = entry["address"]

        # Token info — holder count (cheap, cached)
        try:
            token_info = blockscout.get_token_info(chain, address, config)
            raw_holders = token_info.get("holders_count") or token_info.get("holders")
            entry["holder_count"] = int(raw_holders) if raw_holders is not None else None
        except (ProviderError, ValueError, TypeError):
            entry["holder_count"] = None

        # Top holders — concentration + CEX fingerprint
        try:
            holders = blockscout.get_token_holders(chain, address, config)
        except ProviderError:
            holders = []

        cex_matches: list[str] = []
        if holders:
            total_raw = sum(_safe_float(h.get("value")) for h in holders)
            top10_raw = sum(_safe_float(h.get("value")) for h in holders[:10])
            entry["top10_share"] = round(top10_raw / total_raw * 100, 2) if total_raw > 0 else None
            entry["holder_snapshot_count"] = len(holders)

            # CEX wallet fingerprint — scan all holders for known exchange wallets
            for h in holders:
                holder_addr = str(h.get("address") or {}).lower()
                if isinstance(h.get("address"), dict):
                    holder_addr = str(h["address"].get("hash", "")).lower()
                cex_name = identify_cex(holder_addr)
                if cex_name and cex_name not in cex_matches:
                    cex_matches.append(cex_name)
        else:
            entry["top10_share"] = None
            entry["holder_snapshot_count"] = 0

        entry["cex_listed"] = cex_matches
        entry["cex_count"] = len(cex_matches)

        # Security probe (lightweight)
        try:
            probe = rpc.probe_mint_privilege(chain, address, config)
            entry["is_upgradeable"] = probe.get("is_upgradeable", False)
            entry["has_owner"] = probe.get("owner") is not None
        except Exception:
            entry["is_upgradeable"] = None
            entry["has_owner"] = None

    return candidates


# ---------------------------------------------------------------------------
# Stage 5 — Final composite ranking
# ---------------------------------------------------------------------------

def _final_rank(
    candidates: list[dict],
    *,
    require_cex: bool = False,
    min_holders: int = 0,
    min_volume: float = 0.0,
) -> list[dict]:
    """Adjust growth_score with market structure + ownership + CEX + aliveness signals."""

    # Hard filters — remove dead / abandoned / outsider tokens.
    # CEX-listed tokens get a pass on volume + stale checks because their
    # trading activity is on centralized exchanges, not DEXes.
    filtered = []
    for c in candidates:
        is_cex = c.get("cex_count", 0) > 0

        # CEX requirement
        if require_cex and not is_cex:
            continue
        # Holder count floor — skip only when we KNOW it's below threshold
        hc = c.get("holder_count")
        if min_holders > 0 and hc is not None and hc < min_holders:
            continue

        if not is_cex:
            # DEX volume floor — only for non-CEX tokens
            vol = c.get("dex_volume_24h", 0)
            if min_volume > 0 and vol < min_volume:
                continue
            # Stale price filter — only for non-CEX tokens
            if c.get("change_7d", 0) == 0 and c.get("dex_volume_24h", 0) < 1000:
                continue

        filtered.append(c)

    for entry in filtered:
        score = entry.get("growth_score", 0.0)

        # Liquidity bonus/penalty
        liq = entry.get("dex_liquidity_usd", 0)
        if liq >= 1_000_000:
            score += 5.0
        elif liq < 100_000:
            score -= 10.0

        # Concentration penalty
        top10 = entry.get("top10_share")
        if top10 is not None and top10 > 80:
            score -= 15.0
        elif top10 is not None and top10 > 60:
            score -= 5.0

        # Upgradeable penalty
        if entry.get("is_upgradeable"):
            score -= 5.0

        # CEX listing bonus — listed on major exchanges = real project
        cex_count = entry.get("cex_count", 0)
        if cex_count >= 3:
            score += 10.0
        elif cex_count >= 1:
            score += 5.0

        # Holder count bonus — large holder base = real distribution
        hc = entry.get("holder_count") or 0
        if hc >= 50_000:
            score += 8.0
        elif hc >= 10_000:
            score += 4.0
        elif hc >= 5_000:
            score += 2.0

        entry["final_score"] = round(score, 2)

    filtered.sort(key=lambda e: e["final_score"], reverse=True)
    return filtered


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def run(
    query: str,
    chain: str | None,
    address: str | None,
    config: AppConfig,
    include_premium: bool,
    *,
    min_tvl: float = 500_000.0,
    max_tvl: float = 500_000_000.0,
    category: str | None = None,
    stage3_top: int = 30,
    stage4_top: int = 15,
    final_top: int = 10,
    cex_listed: bool = False,
    min_holders: int = 0,
    min_volume: float = 0.0,
):
    warnings: list[WarningItem] = []
    coverage_gaps: list[CoverageGap] = []

    if config.offline:
        coverage_gaps.append(CoverageGap(
            metric=CoverageMetric.RESOLUTION_CANDIDATES,
            reason="Offline mode — screen requires DeFiLlama + DexScreener.",
            suggested_source="Disable offline mode.",
        ))
        return _build_result(query, chain, address, include_premium, config,
                             {"stage": "aborted", "reason": "offline"}, warnings, coverage_gaps)

    # --- Stage 1: Universe ---
    universe = _build_universe(config, min_tvl=min_tvl, max_tvl=max_tvl, category_filter=category)
    stage1_count = len(universe)
    if not universe:
        warnings.append(WarningItem(
            code=WarningCode.DEFILLAMA_UNAVAILABLE,
            message=f"No protocols matched filters (TVL ${min_tvl:,.0f}–${max_tvl:,.0f}, category={category}).",
        ))
        return _build_result(query, chain, address, include_premium, config,
                             {"stage": "1_universe", "count": 0}, warnings, coverage_gaps)

    # --- Stage 2: Growth scoring (bulk yield join) ---
    _enrich_with_yields(universe, config)
    scored = _score_growth(universe)
    top_scored = scored[:stage3_top]

    # --- Stage 3: Market structure (per-token DexScreener) ---
    with_market = _enrich_market_structure(top_scored, config)
    stage3_survivors = with_market[:stage4_top]

    # --- Stage 4: Ownership check (per-token Blockscout + RPC) ---
    with_ownership = _enrich_ownership(stage3_survivors, config)

    # --- Stage 5: Final ranking ---
    ranked = _final_rank(with_ownership, require_cex=cex_listed, min_holders=min_holders, min_volume=min_volume)
    final = ranked[:final_top]

    # Build summary table
    results_table = []
    for rank, entry in enumerate(final, 1):
        results_table.append({
            "rank": rank,
            "name": entry.get("name"),
            "symbol": entry.get("symbol"),
            "category": entry.get("category"),
            "chain": entry.get("chain"),
            "address": entry.get("address"),
            "tvl_usd": entry.get("tvl"),
            "mcap_usd": entry.get("dex_mcap") or entry.get("mcap"),
            "change_7d_pct": entry.get("change_7d"),
            "change_1m_pct": entry.get("change_1m"),
            "age_years": round(entry["age_years"], 1) if entry.get("age_years") else None,
            "best_apy_pct": round(entry.get("best_apy", 0), 2),
            "yield_pools": entry.get("yield_pool_count", 0),
            "chains_count": len(entry.get("chains", [])),
            "dex_liquidity_usd": entry.get("dex_liquidity_usd"),
            "dex_pairs": entry.get("dex_pair_count"),
            "liq_to_mcap": entry.get("liq_to_mcap"),
            "top10_share_pct": entry.get("top10_share"),
            "is_upgradeable": entry.get("is_upgradeable"),
            "audits": entry.get("audits", 0),
            "holder_count": entry.get("holder_count"),
            "dex_volume_24h": entry.get("dex_volume_24h"),
            "cex_listed": entry.get("cex_listed", []),
            "cex_count": entry.get("cex_count", 0),
            "growth_score": entry.get("growth_score"),
            "final_score": entry.get("final_score"),
            "growth_signals": entry.get("growth_signals"),
        })

    metrics: dict[str, Any] = {
        "funnel": {
            "stage1_universe": stage1_count,
            "stage2_scored": len(scored),
            "stage3_market_filtered": len(with_market),
            "stage4_ownership_checked": len(with_ownership),
            "stage5_final": len(final),
        },
        "filters": {
            "min_tvl": min_tvl,
            "max_tvl": max_tvl,
            "category": category,
            "stage3_top": stage3_top,
            "stage4_top": stage4_top,
            "final_top": final_top,
            "cex_listed_required": cex_listed,
            "min_holders": min_holders,
            "min_volume": min_volume,
        },
        "results": results_table,
    }

    return _build_result(query, chain, address, include_premium, config, metrics, warnings, coverage_gaps)


def _build_result(
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
        command="screen",
        input={
            "query": query,
            "chain": chain,
            "address": address,
            "include_premium": include_premium,
        },
        resolved_identity={"query": query, "mode": "screen"},
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
