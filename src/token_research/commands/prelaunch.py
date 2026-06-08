"""prelaunch command — analyzes pre-TGE programs via DeFiLlama directly.

For programs like Katana Pre-Launch where there's no tradable token yet,
the token-centric pipeline (resolver → holders → locks → …) can't anchor
anything. This command bypasses DexScreener entirely, queries DeFiLlama's
protocol directory by name, picks the real project by TVL, and applies
the CPD 2.0 risk model with whatever inputs can be derived + user overrides.

What this command surfaces that `risk` cannot:
- TVL + momentum (change_1d, change_7d)
- Protocol description + category
- Chain footprint
- Audit count (from DeFiLlama)
- Listed date → age band
- All same-name alternatives ranked by TVL (disambiguation aid)
- Manual CPD + risk formula output for a program that has no token yet

What it still can't do:
- Holder analysis (no token)
- Fees / revenue (most pre-launch programs have none indexed)
- APY (pre-launch yields are tracked under the underlying project, e.g. yearn)
- Tokenomics / unlock schedule (off-chain, needs the project docs)
"""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any

from token_research.format_utils import iso_from_unix
from token_research.config import AppConfig
from token_research.models import CommandResult, CoverageGap, CoverageMetric, ResolvedIdentity, WarningCode, WarningItem, dataclass_list
from token_research.providers import defillama
from token_research.providers.http import ProviderError
from token_research.providers.registry import build_source_refs
from token_research.risk_model import (
    CHAIN_STACK_TIER_MAP,
    CpdInputs,
    RiskInputs,
    compute_cpd,
    compute_risk,
)


def _infer_chain_tier(chains: list | None) -> str | None:
    """Infer stack tier from the first chain a DeFiLlama protocol lists."""
    if not chains:
        return None
    first = str(chains[0]).strip().lower() if chains else ""
    return CHAIN_STACK_TIER_MAP.get(first)


def _infer_protocol_tier(tvl_usd: float | None) -> str | None:
    if tvl_usd is None:
        return None
    if tvl_usd >= 1_000_000_000:
        return "I"
    if tvl_usd >= 100_000_000:
        return "II"
    return "III"


def _infer_dapp_tier(chains_count: int | None, tvl_usd: float | None) -> str | None:
    """Pre-launch dapp tier: rely on chain footprint + TVL since we usually
    have no yield-pool telemetry for these programs."""
    chains = chains_count or 0
    tvl = tvl_usd or 0.0
    if chains >= 3 and tvl >= 100_000_000:
        return "I"
    if chains >= 1 and tvl >= 1_000_000:
        return "II"
    return "III"


def _infer_age_band(listed_at: float | None) -> str | None:
    if not listed_at or listed_at <= 0:
        return None
    years = (time.time() - float(listed_at)) / (365.25 * 86400)
    if years < 1:
        return "0-1"
    if years < 2:
        return "1-2"
    if years < 3:
        return "2-3"
    if years < 5:
        return "3-5"
    return ">5"


def _infer_code_band(audit_count: int | None) -> str | None:
    """DeFiLlama records audit counts on most protocols. Use the count as a
    rough signal; real open-source-status classification needs repo inspection
    we don't do here."""
    if audit_count is None:
        return None
    if audit_count >= 2:
        return "oso_audit"
    if audit_count == 1:
        return "open_source_old"
    return "proprietary_new"  # explicit 0 audits in a pre-launch context is a yellow flag


# --- Category → primitive pair mapping (pre-launch heuristics) ---------------

_CATEGORY_TO_PRIMITIVES: dict[str, tuple[int, int]] = {
    "farm": (4, 5),              # Yearn/vaults layer + farm wrapper
    "launchpad": (5, 10),        # Farm + custom mechanic
    "yield": (4, 5),
    "yield aggregator": (4, 4),
    "liquid staking": (1, 1),
    "liquid staking derivatives": (1, 2),
    "lending": (2, 2),
    "dexs": (3, 3),
    "derivatives": (6, 6),
    "options vault": (4, 7),
    "options": (7, 7),
}


def _infer_primitives(category: str | None) -> tuple[int, int]:
    if not category:
        return 10, 10
    return _CATEGORY_TO_PRIMITIVES.get(category.strip().lower(), (10, 10))


# --- Stage default for pre-launch entries -----------------------------------

def _default_stage(name: str | None, category: str | None) -> str:
    """Pre-launch programs start at 'mvp' by default. If the name literally
    contains 'pre-launch' the default is definitely mvp; if it's a live farm
    category without such language, release is more appropriate."""
    n = (name or "").lower()
    if "pre-launch" in n or "prelaunch" in n or "testnet" in n:
        return "mvp"
    return "release"


# ---------------------------------------------------------------------------

def _summarize_protocol(proto: dict) -> dict:
    """Compact view of a DeFiLlama protocol entry for the `candidates` list."""
    return {
        "name": proto.get("name"),
        "slug": proto.get("slug"),
        "category": proto.get("category"),
        "tvl_usd": proto.get("tvl"),
        "chains": proto.get("chains"),
        "listedAt": proto.get("listedAt"),
        "url": proto.get("url"),
    }


def run(
    query: str,
    chain: str | None,
    address: str | None,
    config: AppConfig,
    include_premium: bool,
    *,
    trade_size_pct: float = 0.05,
    nesting_depth: int = 2,
    cpd_chain_tier: str | None = None,
    cpd_protocol_tier: str | None = None,
    cpd_dapp_tier: str | None = None,
    cpd_stage: str | None = None,
    cpd_age_band: str | None = None,
    cpd_code_band: str | None = None,
    cpd_hacks_band: str | None = None,
    primitive_a: int | None = None,
    primitive_b: int | None = None,
    min_tvl_usd: float = 100_000.0,
):
    warnings: list[WarningItem] = []
    coverage_gaps: list[CoverageGap] = []

    # Resolve via DeFiLlama protocol directory, NOT DexScreener.
    if config.offline:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.PROTOCOL_MATCH,
                reason="Offline mode enabled — cannot fetch DeFiLlama protocol directory.",
                suggested_source="Disable offline mode.",
            )
        )
        return _build_empty(query, chain, address, include_premium, config, warnings, coverage_gaps)

    try:
        candidates = defillama.find_protocols_by_name(query, config, min_tvl_usd=min_tvl_usd)
    except ProviderError as exc:
        warnings.append(WarningItem(code=WarningCode.DEFILLAMA_UNAVAILABLE, message=str(exc), severity="warning"))
        return _build_empty(query, chain, address, include_premium, config, warnings, coverage_gaps)

    if not candidates:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.PROTOCOL_MATCH,
                reason=f"No DeFiLlama protocol matches '{query}' with TVL ≥ ${min_tvl_usd:,.0f}.",
                suggested_source="Check DeFiLlama spelling; lower --min-tvl; or query as a listed token instead.",
            )
        )
        return _build_empty(query, chain, address, include_premium, config, warnings, coverage_gaps)

    # Pick the highest-TVL match as the canonical answer.
    protocol = candidates[0]
    alternates = [_summarize_protocol(p) for p in candidates[1:5]]

    # Build a pseudo-ResolvedIdentity so the output schema matches other commands.
    chains = protocol.get("chains") or []
    primary_chain = str(chains[0]).strip().lower() if chains else (chain or "ethereum")
    identity = ResolvedIdentity(
        query=query,
        normalized_query=query.strip(),
        query_type="project_name",
        symbol=str(protocol.get("symbol") or query).upper(),
        project_name=str(protocol.get("name") or query),
        chain=primary_chain,
        token_address=None,  # deliberately None — no token exists yet
    )

    # Derive CPD inputs from the protocol metadata, overrides always win.
    tvl_usd = protocol.get("tvl")
    chains_count = len(chains) if isinstance(chains, list) else 0
    audits = protocol.get("audits")
    try:
        audit_count = int(audits) if audits is not None else None
    except (TypeError, ValueError):
        audit_count = None

    inferred_chain = _infer_chain_tier(chains)
    inferred_protocol = _infer_protocol_tier(tvl_usd)
    inferred_dapp = _infer_dapp_tier(chains_count, tvl_usd)
    inferred_age = _infer_age_band(protocol.get("listedAt"))
    inferred_code = _infer_code_band(audit_count)
    inferred_stage = _default_stage(protocol.get("name"), protocol.get("category"))

    cpd_inputs = CpdInputs(
        chain_tier=cpd_chain_tier or inferred_chain,  # type: ignore[arg-type]
        protocol_tier=cpd_protocol_tier or inferred_protocol,  # type: ignore[arg-type]
        dapp_tier=cpd_dapp_tier or inferred_dapp,  # type: ignore[arg-type]
        stage=cpd_stage or inferred_stage,  # type: ignore[arg-type]
        age_band=cpd_age_band or inferred_age,  # type: ignore[arg-type]
        code_band=cpd_code_band or inferred_code,  # type: ignore[arg-type]
        hacks_band=cpd_hacks_band or "0",  # type: ignore[arg-type]
    )
    cpd_result = compute_cpd(cpd_inputs)

    # Primitive inference from category — same story as risk.py but with
    # pre-launch-specific defaults (Farm tends to imply Vaults × Yield).
    if primitive_a is None or primitive_b is None:
        a, b = _infer_primitives(protocol.get("category"))
        primitive_a = primitive_a or a
        primitive_b = primitive_b or b

    risk_result = compute_risk(
        RiskInputs(
            trade_size_pct=trade_size_pct,
            nesting_depth=nesting_depth,
            cpd_risk_input=cpd_result.risk_input,
            primitive_a=primitive_a,
            primitive_b=primitive_b,
        )
    )

    metrics: dict[str, Any] = {
        "protocol": {
            "name": protocol.get("name"),
            "slug": protocol.get("slug"),
            "category": protocol.get("category"),
            "symbol": protocol.get("symbol"),
            "chains": chains,
            "tvl_usd": tvl_usd,
            "mcap_usd": protocol.get("mcap"),
            "change_1d_pct": protocol.get("change_1d"),
            "change_7d_pct": protocol.get("change_7d"),
            "listedAt": protocol.get("listedAt"),
            "listedAt_iso": iso_from_unix(protocol.get("listedAt")),
            "audits": audit_count,
            "audit_note": protocol.get("audit_note"),
            "url": protocol.get("url"),
            "twitter": protocol.get("twitter"),
            "description": protocol.get("description"),
            "module": protocol.get("module"),
            "parent_protocol": protocol.get("parentProtocol"),
        },
        "alternates": alternates,
        "cpd": {
            "inputs": cpd_result.inputs,
            "inputs_source": {
                "chain_tier": "override" if cpd_chain_tier else "inferred" if inferred_chain else "unknown",
                "protocol_tier": "override" if cpd_protocol_tier else "inferred" if inferred_protocol else "unknown",
                "dapp_tier": "override" if cpd_dapp_tier else "inferred" if inferred_dapp else "unknown",
                "stage": "override" if cpd_stage else f"default:{inferred_stage}",
                "age_band": "override" if cpd_age_band else "inferred" if inferred_age else "unknown",
                "code_band": "override" if cpd_code_band else "inferred" if inferred_code else "unknown",
                "hacks_band": "override" if cpd_hacks_band else "default:0",
            },
            "sub_scores": cpd_result.sub_scores,
            "total_score": cpd_result.total_score,
            "max_score": cpd_result.max_score,
            "percent_of_max": cpd_result.percent_of_max,
            "tier": cpd_result.tier,
            "risk_input": cpd_result.risk_input,
            "explanation": cpd_result.explanation,
        },
        "risk": {
            "trade_size_pct": trade_size_pct,
            "nesting_depth": nesting_depth,
            "primitive_a": primitive_a,
            "primitive_b": primitive_b,
            "primitive_a_name": risk_result.primitive_a_name,
            "primitive_b_name": risk_result.primitive_b_name,
            "base_risk": risk_result.base_risk,
            "defi_multiplier": risk_result.defi_multiplier,
            "final_risk": risk_result.final_risk,
            "explanation": risk_result.explanation,
        },
        "revenue_vs_risk": {
            "apy_percent": None,  # pre-launch — no measurable APY
            "risk_score": risk_result.final_risk,
            "verdict": "no_yield_data",
            "note": (
                "Pre-launch programs reward future token allocations, not APY. "
                "Expected return requires a manual fair-value estimate of the "
                "allocation (e.g. expected TGE FDV × pro-rata share / deposit size)."
            ),
        },
    }

    # Coverage gaps specific to pre-launch context.
    coverage_gaps.extend(
        [
            CoverageGap(
                metric=CoverageMetric.TOKEN_ADDRESS,
                reason="Pre-launch program has no tradeable token yet — token-centric modules cannot run.",
                suggested_source="Re-run as `deep-report` after TGE.",
            ),
            CoverageGap(
                metric=CoverageMetric.APY,
                reason="Pre-launch reward = future token allocation, not a measurable yield.",
                suggested_source="Project docs for TGE valuation and vesting terms.",
            ),
        ]
    )
    if audit_count == 0:
        warnings.append(
            WarningItem(
                code=WarningCode.ZERO_AUDITS,
                message=f"DeFiLlama records 0 audits for {protocol.get('name')} — smart-contract wrapper risk is unverified.",
                severity="warning",
            )
        )
    if cpd_inputs.hacks_band == "0" and cpd_hacks_band is None:
        warnings.append(
            WarningItem(
                code=WarningCode.HACKS_DEFAULT,
                message="Hacks band defaulted to '0' (clean). Override with --cpd-hacks if incidents are known.",
                severity="info",
            )
        )

    return CommandResult(
        command="prelaunch",
        input={
            "query": query,
            "chain": chain,
            "address": address,
            "include_premium": include_premium,
            "trade_size_pct": trade_size_pct,
            "nesting_depth": nesting_depth,
        },
        resolved_identity=asdict(identity),
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )


def _build_empty(query, chain, address, include_premium, config, warnings, coverage_gaps):
    identity = ResolvedIdentity(
        query=query,
        normalized_query=query.strip(),
        query_type="project_name",
        symbol=query.upper(),
        project_name=query,
        chain=(chain or "ethereum"),
        token_address=None,
    )
    return CommandResult(
        command="prelaunch",
        input={"query": query, "chain": chain, "address": address, "include_premium": include_premium},
        resolved_identity=asdict(identity),
        metrics={"protocol": None, "alternates": [], "cpd": None, "risk": None, "revenue_vs_risk": None},
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
