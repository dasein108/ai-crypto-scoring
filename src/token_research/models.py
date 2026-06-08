from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class WarningCode(str, Enum):
    """Registry of known `WarningItem.code` values.

    Not enforced at the type layer — `WarningItem.code` is still `str` so
    existing callsites using string literals keep working — but new code
    should prefer ``WarningCode.X`` for typo-safety and discoverability.
    """

    # Provider availability
    DEXSCREENER_UNAVAILABLE = "dexscreener_unavailable"
    DEFILLAMA_UNAVAILABLE = "defillama_unavailable"
    DEFILLAMA_FEES_UNAVAILABLE = "defillama_fees_unavailable"
    DEFILLAMA_REVENUE_UNAVAILABLE = "defillama_revenue_unavailable"
    DEFILLAMA_YIELDS_UNAVAILABLE = "defillama_yields_unavailable"
    BLOCKSCOUT_UNAVAILABLE = "blockscout_unavailable"
    DUNE_UNAVAILABLE = "dune_unavailable"
    RPC_UNAVAILABLE = "rpc_unavailable"
    TOKENOMIST_UNAVAILABLE = "tokenomist_unavailable"
    COIN_PRICE_UNAVAILABLE = "coin_price_unavailable"
    FLOWS_UNAVAILABLE = "flows_unavailable"
    LP_HOLDERS_UNAVAILABLE = "lp_holders_unavailable"
    PRICE_FALLBACK_FAILED = "price_fallback_failed"
    # Identity / resolution
    WRAPPED_PROXY_TOKEN = "wrapped_proxy_token"
    DEFILLAMA_CANONICAL_ADDRESS = "defillama_canonical_address"
    PRELAUNCH_MATCH_AVAILABLE = "prelaunch_match_available"
    # Coverage / mode
    FREE_MODE_UNLOCK_COVERAGE = "free_mode_unlock_coverage"
    FULL_PROTOCOL_CUSTODY = "full_protocol_custody"
    FEES_PASS_THROUGH = "fees_pass_through"
    # CPD defaults
    HACKS_DEFAULT = "hacks_default"
    ZERO_AUDITS = "zero_audits"
    # Trend/signal
    TREND_SAVE_FAILED = "trend_save_failed"
    SCORE_UNAVAILABLE = "score_unavailable"


class CoverageMetric(str, Enum):
    """Registry of known ``CoverageGap.metric`` values."""

    # Resolution / identity
    RESOLUTION_CANDIDATES = "resolution_candidates"
    TOKEN_ADDRESS = "token_address"
    IDENTITY_VERIFICATION = "identity_verification"
    PROTOCOL_MATCH = "protocol_match"
    PROTOCOL_SLUG = "protocol_slug"
    # Market / liquidity
    PRICE_USD = "price_usd"
    SPOT_PRICE_USD = "spot_price_usd"
    POOLS = "pools"
    TOTAL_DEX_LIQUIDITY_USD = "total_dex_liquidity_usd"
    LP_LOCKED_PERCENT = "lp_locked_percent"
    SLIPPAGE_DEPTH = "slippage_depth"
    # Supply / holders
    TOTAL_SUPPLY = "total_supply"
    TOTAL_SUPPLY_ONCHAIN = "total_supply_onchain"
    HOLDERS = "holders"
    ON_CHAIN_HOLDERS = "on_chain_holders"
    TOP_HOLDERS = "top_holders"
    GINI = "gini"
    # Flows / labels
    NETFLOWS = "netflows"
    LABELS = "labels"
    # Locks / staking / unlocks
    LOCKS = "locks"
    LOCKED_ONCHAIN_TOTAL = "locked_onchain_total"
    VESTING_CONTRACTS = "vesting_contracts"
    STAKING = "staking"
    STAKING_CONTRACTS = "staking_contracts"
    STAKED_TOTAL = "staked_total"
    NATIVE_STAKING_APY = "native_staking_apy"
    UNLOCKS = "unlocks"
    ONCHAIN_SCHEDULE = "onchain_schedule"
    VALIDATION_WINDOWS = "validation_windows"
    CURATED_SCHEDULE = "curated_schedule"
    # Yields / fees / treasury
    YIELDS = "yields"
    YIELD_POOLS = "yield_pools"
    APY = "apy"
    PROTOCOL_FEES = "protocol_fees"
    TREASURY_HOLDINGS = "treasury_holdings"
    # Chain-level
    CHAIN_DIRECTORY = "chain_directory"
    # Score pillars
    GOVERNANCE_COMMITMENT = "governance_commitment"
    OWNERSHIP_QUALITY = "ownership_quality"
    LIQUIDITY_QUALITY = "liquidity_quality"
    SUPPLY_PRESSURE = "supply_pressure"
    FUNDAMENTAL_SUPPORT = "fundamental_support"
    TOKEN_CAPTURE = "token_capture"
    # Other commands
    COMPARE = "compare"
    RELATIONS = "relations"
    ENTITIES = "entities"
    DELTAS = "deltas"
    TREND_DELTAS = "trend_deltas"
    PRIMITIVE_INFERENCE = "primitive_inference"


@dataclass(slots=True)
class SourceRef:
    name: str
    kind: str
    access: str
    docs_url: str
    enabled: bool
    notes: str = ""


@dataclass(slots=True)
class WarningItem:
    code: str
    message: str
    severity: str = "info"


@dataclass(slots=True)
class CoverageGap:
    metric: str
    reason: str
    suggested_source: str = ""


@dataclass(slots=True)
class ResolvedIdentity:
    query: str
    normalized_query: str
    query_type: str
    symbol: str
    project_name: str
    chain: str
    token_address: str | None = None


@dataclass(slots=True)
class CommandResult:
    command: str
    input: dict[str, Any]
    resolved_identity: dict[str, Any]
    metrics: dict[str, Any]
    sources: list[dict[str, Any]]
    warnings: list[dict[str, Any]] = field(default_factory=list)
    coverage_gaps: list[dict[str, Any]] = field(default_factory=list)
    generated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DeepReportResult(CommandResult):
    scores: dict[str, Any] = field(default_factory=dict)
    narrative: list[str] = field(default_factory=list)
    evidence_summary: list[str] = field(default_factory=list)


def dataclass_list(items: list[Any]) -> list[dict[str, Any]]:
    return [asdict(item) for item in items]
