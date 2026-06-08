from __future__ import annotations

import json
import re

from token_research.chains import SUPPORTED_EVM_CHAINS, normalize_chain_name
from token_research.config import AppConfig
from token_research.models import CoverageGap, CoverageMetric, ResolvedIdentity, WarningCode, WarningItem
from token_research.providers import defillama, dexscreener
from token_research.providers.http import ProviderError


ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

# DexScreener candidates below this liquidity threshold are treated as
# squatter / abandoned tokens and filtered out. The KatanaToken memecoin
# case (circ. mcap = $0) illustrates why: without this filter a dead ticker
# can outrank a legitimate multi-billion-dollar project purely because it
# exists on DexScreener and shares a name.
MIN_DEXSCREENER_LIQUIDITY_USD = 10_000.0
KNOWN_ALIASES: dict[tuple[str, str], dict[str, str]] = {
    ("ethereum", "ETH"): {
        "symbol": "ETH",
        "project_name": "Ethereum",
        "token_address": "0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2",
        "warning": "Using WETH as the canonical token contract for Ethereum market and holder analytics.",
    },
    ("ethereum", "ETHEREUM"): {
        "symbol": "ETH",
        "project_name": "Ethereum",
        "token_address": "0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2",
        "warning": "Using WETH as the canonical token contract for Ethereum market and holder analytics.",
    },
}


# User-defined aliases are loaded from ``$TOKEN_RESEARCH_DATA_DIR/aliases.json``
# if that file exists. File schema:
#
#     [
#       {
#         "chain": "ethereum",
#         "query": "ETH",
#         "symbol": "ETH",
#         "project_name": "Ethereum",
#         "token_address": "0x...",
#         "warning": "..."   // optional
#       },
#       ...
#     ]
#
# Loaded lazily on first ``resolve_with_sources`` call and cached per-process.
_ALIASES_CACHE: dict[tuple[str, str], dict[str, str]] | None = None


def _load_user_aliases(config: AppConfig) -> dict[tuple[str, str], dict[str, str]]:
    """Return user-supplied aliases merged on top of built-in KNOWN_ALIASES.

    If the aliases file is missing or malformed, a ``WarningItem`` is not
    emitted (this is a pure fallback path), but the function logs via the
    module-level cache so repeated resolver calls don't re-parse a broken
    file on every query.
    """
    global _ALIASES_CACHE
    if _ALIASES_CACHE is not None:
        return _ALIASES_CACHE

    merged: dict[tuple[str, str], dict[str, str]] = dict(KNOWN_ALIASES)
    aliases_path = config.data_dir / "aliases.json"
    if aliases_path.is_file():
        try:
            raw = json.loads(aliases_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raw = None
        if isinstance(raw, list):
            for entry in raw:
                if not isinstance(entry, dict):
                    continue
                chain = str(entry.get("chain") or "").strip().lower()
                query_key = str(entry.get("query") or "").strip().upper()
                if not chain or not query_key:
                    continue
                merged[(chain, query_key)] = {
                    "symbol": str(entry.get("symbol") or query_key),
                    "project_name": str(entry.get("project_name") or query_key),
                    "token_address": str(entry.get("token_address") or ""),
                    "warning": str(entry.get("warning") or
                                   f"Using user-supplied alias for {query_key} on {chain}."),
                }

    _ALIASES_CACHE = merged
    return merged


def _reset_aliases_cache() -> None:
    """Testing hook — forget cached aliases so the next call re-reads disk."""
    global _ALIASES_CACHE
    _ALIASES_CACHE = None


def classify_query(query: str) -> str:
    stripped = query.strip()
    if ADDRESS_RE.fullmatch(stripped):
        return "address"
    if stripped.isupper() and 2 <= len(stripped) <= 10 and " " not in stripped:
        return "ticker"
    return "project_name"


def resolve_query(query: str, chain: str | None = None, address: str | None = None) -> ResolvedIdentity:
    query_type = classify_query(query)
    normalized = query.strip()
    effective_chain = normalize_chain_name(chain)
    token_address = address or (normalized if query_type == "address" else None)

    if query_type == "ticker":
        symbol = normalized
        project_name = normalized
    else:
        symbol = normalized.upper().replace(" ", "-")
        project_name = normalized

    return ResolvedIdentity(
        query=query,
        normalized_query=normalized,
        query_type=query_type,
        symbol=symbol,
        project_name=project_name,
        chain=effective_chain,
        token_address=token_address,
    )


def resolve_with_sources(
    query: str,
    chain: str | None,
    address: str | None,
    config: AppConfig,
    *,
    max_candidates: int = 5,
) -> tuple[ResolvedIdentity, list[dict[str, object]], list[WarningItem], list[CoverageGap]]:
    identity = resolve_query(query, chain=chain, address=address)
    warnings: list[WarningItem] = []
    gaps: list[CoverageGap] = []

    aliases = _load_user_aliases(config)
    alias = aliases.get((identity.chain, identity.normalized_query.upper()))
    if alias and not identity.token_address:
        identity = ResolvedIdentity(
            query=identity.query,
            normalized_query=identity.normalized_query,
            query_type=identity.query_type,
            symbol=str(alias["symbol"]),
            project_name=str(alias["project_name"]),
            chain=identity.chain,
            token_address=str(alias["token_address"]),
        )
        warnings.append(WarningItem(code=WarningCode.WRAPPED_PROXY_TOKEN, message=str(alias["warning"])))

    if config.offline:
        gaps.append(CoverageGap(metric=CoverageMetric.RESOLUTION_CANDIDATES, reason="Offline mode enabled.", suggested_source="DexScreener search"))
        return identity, [], warnings, gaps

    candidates: list[dict[str, object]] = []
    if identity.query_type != "address":
        try:
            pairs = dexscreener.search_pairs(identity.normalized_query, config)
            candidates = _build_candidates_from_pairs(
                query=identity.normalized_query,
                target_chain=identity.chain,
                pairs=pairs,
                max_candidates=max_candidates,
            )
        except ProviderError as exc:
            warnings.append(WarningItem(code=WarningCode.DEXSCREENER_UNAVAILABLE, message=str(exc), severity="warning"))

    # Cross-check against DeFiLlama's protocol directory. When a protocol
    # entry matches the query and exposes a canonical token `address` field,
    # that address is authoritative and must beat any DexScreener hit. This
    # fixes the `MintBurnTeamToken` / memecoin-squatter class of failures
    # reproduced 4× across Katana, ONDO, Morpho, HBAR reviews.
    defillama_hit: dict | None = None
    if not identity.token_address and identity.query_type != "address":
        try:
            defillama_hit = _find_defillama_canonical(
                identity.symbol, identity.normalized_query, config
            )
        except ProviderError as exc:
            warnings.append(WarningItem(code=WarningCode.DEFILLAMA_UNAVAILABLE, message=str(exc), severity="info"))

    if not identity.token_address and defillama_hit:
        chain_prefix, raw_addr = _parse_defillama_address(
            str(defillama_hit.get("address") or "")
        )
        if ADDRESS_RE.fullmatch(raw_addr):
            # Use the prefixed chain if valid + supported, otherwise keep
            # whatever the caller set (defaults to ethereum).
            resolved_chain = identity.chain
            if chain_prefix:
                normalized = normalize_chain_name(chain_prefix)
                if normalized in SUPPORTED_EVM_CHAINS:
                    resolved_chain = normalized
            identity = ResolvedIdentity(
                query=identity.query,
                normalized_query=identity.normalized_query,
                query_type=identity.query_type,
                symbol=str(defillama_hit.get("symbol") or identity.symbol),
                project_name=str(defillama_hit.get("name") or identity.project_name),
                chain=resolved_chain,
                token_address=raw_addr,
            )
            warnings.append(
                WarningItem(
                    code=WarningCode.DEFILLAMA_CANONICAL_ADDRESS,
                    message=(
                        f"Using DeFiLlama canonical address {raw_addr} for protocol "
                        f"'{defillama_hit.get('name')}' on chain '{resolved_chain}' "
                        f"(slug: {defillama_hit.get('slug')})."
                    ),
                    severity="info",
                )
            )

    # Fall back to the DexScreener candidate ranking if DeFiLlama didn't help.
    if not identity.token_address and candidates:
        best = candidates[0]
        identity = ResolvedIdentity(
            query=identity.query,
            normalized_query=identity.normalized_query,
            query_type=identity.query_type,
            symbol=str(best["symbol"]),
            project_name=str(best["project_name"]),
            chain=str(best["chain"]),
            token_address=str(best["token_address"]),
        )
    elif not identity.token_address:
        gaps.append(
            CoverageGap(
                metric=CoverageMetric.TOKEN_ADDRESS,
                reason="No token address resolved from free sources.",
                suggested_source="Provide --address or implement broader resolver coverage.",
            )
        )

    # Even when we did find a DexScreener hit, if DefiLlama has a materially
    # larger protocol with the same name but no `address` field (pre-TGE),
    # the user almost certainly wants that project — not our memecoin fallback.
    # Emit a warning pointing at `prelaunch`. Covers the Katana / MegaETH class
    # of failures where the real project doesn't have a token yet.
    if (
        defillama_hit is None
        and identity.token_address  # We used DexScreener
        and identity.query_type != "address"
        and not config.offline
    ):
        try:
            prelaunch_hit = _find_prelaunch_protocol(identity.normalized_query, config)
        except ProviderError:
            prelaunch_hit = None
        if prelaunch_hit:
            tvl = float(prelaunch_hit.get("tvl") or 0)
            warnings.append(
                WarningItem(
                    code=WarningCode.PRELAUNCH_MATCH_AVAILABLE,
                    message=(
                        f"DeFiLlama has a larger matching protocol "
                        f"'{prelaunch_hit.get('name')}' (slug: {prelaunch_hit.get('slug')}, "
                        f"TVL ${tvl / 1_000_000:.2f}M, category: {prelaunch_hit.get('category')}) "
                        f"without a canonical token address — likely a pre-TGE program. "
                        f"Consider: `token-research prelaunch {identity.normalized_query}`"
                    ),
                    severity="warning",
                )
            )

    return identity, candidates, warnings, gaps


def _parse_defillama_address(raw_addr: str) -> tuple[str | None, str]:
    """Split DeFiLlama's chain-prefixed address into (chain_prefix, clean_addr).

    DeFiLlama sometimes prefixes the address with a chain slug
    (e.g., ``base:0x98d0...``, ``solana:...``). Returns ``(None, raw_addr)``
    when no prefix is present.
    """
    if ":" in raw_addr:
        prefix, clean = raw_addr.split(":", 1)
        return prefix, clean
    return None, raw_addr


def _find_prelaunch_protocol(query: str, config: AppConfig) -> dict | None:
    """Find a high-TVL DefiLlama protocol matching the query that has NO
    canonical `address` field — indicating a pre-TGE farming program.
    Returns the protocol entry or None.
    """
    protocols = defillama.get_protocols(config)
    if not protocols:
        return None
    needle = (query or "").strip().lower()
    if not needle:
        return None
    # Pre-launch protocols: meaningful TVL, no address field, name/slug matches.
    candidates = [
        p for p in protocols
        if not p.get("address")
        and (p.get("tvl") or 0) >= 1_000_000  # material — $1M+ TVL
        and (
            needle in str(p.get("name", "")).lower()
            or needle in str(p.get("slug", "")).lower()
        )
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: float(p.get("tvl") or 0))


def _find_defillama_canonical(
    symbol: str, project_name: str, config: AppConfig
) -> dict | None:
    """Find the highest-TVL DeFiLlama protocol with an `address` field
    matching the symbol/name query. Symbol-match wins over name-match.

    Returns the raw protocol dict (so callers can read address / slug / name)
    or None if nothing plausible matches.
    """
    protocols = defillama.get_protocols(config)
    if not protocols:
        return None
    sym_l = (symbol or "").strip().lower()
    name_l = (project_name or "").strip().lower()

    # Filter only protocols that have an address. We do NOT require TVL
    # because Service-category protocols (AI platforms, data tools, etc.)
    # legitimately have $0 TVL but still have a canonical token address
    # and a real market cap (Kaito class).
    with_addr = [p for p in protocols if p.get("address")]

    # Tier 1: exact symbol match
    tier1 = [p for p in with_addr if str(p.get("symbol", "")).strip().lower() == sym_l]
    if tier1:
        return max(tier1, key=lambda p: float(p.get("tvl") or 0))

    # Tier 2: exact name match
    tier2 = [p for p in with_addr if str(p.get("name", "")).strip().lower() == name_l]
    if tier2:
        return max(tier2, key=lambda p: float(p.get("tvl") or 0))

    # Tier 3: substring name match (loose — risk of false positives)
    tier3 = [
        p for p in with_addr
        if name_l and name_l in str(p.get("name", "")).strip().lower()
    ]
    if tier3:
        return max(tier3, key=lambda p: float(p.get("tvl") or 0))

    return None


def _build_candidates_from_pairs(
    *,
    query: str,
    target_chain: str,
    pairs: list[dict[str, object]],
    max_candidates: int,
) -> list[dict[str, object]]:
    normalized_query = query.strip().lower()
    scored: dict[tuple[str, str], dict[str, object]] = {}

    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        chain_id = str(pair.get("chainId") or "")
        # Filter to chains we can actually inspect — otherwise a Solana
        # memecoin sharing a symbol with an EVM project will outrank the real
        # EVM token on raw liquidity.
        if chain_id and chain_id not in SUPPORTED_EVM_CHAINS:
            continue
        # Reject squatter / abandoned tokens: anything with negligible pool
        # liquidity is treated as not-a-real-market. The KatanaToken case
        # showed a circ. mcap of $0 slipping past the earlier filter.
        pair_liquidity = pair.get("liquidity") or {}
        pair_liq_usd = _safe_float(pair_liquidity.get("usd")) or 0.0
        if pair_liq_usd < MIN_DEXSCREENER_LIQUIDITY_USD:
            continue
        for side in ("baseToken", "quoteToken"):
            token = pair.get(side) or {}
            if not isinstance(token, dict):
                continue
            address = str(token.get("address") or "")
            symbol = str(token.get("symbol") or "")
            name = str(token.get("name") or "")
            if not address or not _matches_query(normalized_query, symbol, name):
                continue

            liquidity = pair.get("liquidity") or {}
            liquidity_usd = _safe_float((liquidity or {}).get("usd")) or 0.0
            score = _match_score(normalized_query, symbol, name)
            if chain_id == target_chain:
                score += 100.0
            score += min(liquidity_usd / 1_000.0, 1_000.0)

            key = (chain_id, address.lower())
            existing = scored.get(key)
            candidate = {
                "chain": chain_id,
                "token_address": address,
                "symbol": symbol,
                "project_name": name,
                "top_pair_address": pair.get("pairAddress"),
                "top_pair_dex": pair.get("dexId"),
                "liquidity_usd": liquidity_usd,
                "market_cap": _safe_float(pair.get("marketCap")),
                "fdv": _safe_float(pair.get("fdv")),
                "score": round(score, 2),
                "source": "dexscreener_search",
            }
            if existing is None or float(candidate["score"]) > float(existing["score"]):
                scored[key] = candidate

    # Same-chain tokens always outrank other-chain tokens — liquidity alone
    # should never override the caller's target chain. Within each bucket we
    # keep the score-then-liquidity ranking.
    ranked = sorted(
        scored.values(),
        key=lambda item: (
            item.get("chain") == target_chain,
            float(item["score"]),
            float(item.get("liquidity_usd") or 0.0),
        ),
        reverse=True,
    )
    return ranked[:max_candidates]


# Score table for match quality: exact > substring, symbol > name. Used by
# `_match_score` and the existence-check `_matches_query`. Order matters —
# first hit wins.
_MATCH_TIERS: tuple[tuple[str, str, float], ...] = (
    ("eq", "symbol", 1_000.0),
    ("eq", "name", 800.0),
    ("sub", "symbol", 400.0),
    ("sub", "name", 250.0),
)


def _match_score(query: str, symbol: str, name: str) -> float:
    fields = {"symbol": symbol.lower(), "name": name.lower()}
    for kind, field_name, score in _MATCH_TIERS:
        target = fields[field_name]
        if kind == "eq" and query == target:
            return score
        if kind == "sub" and target and query in target:
            return score
    return 0.0


def _matches_query(query: str, symbol: str, name: str) -> bool:
    return _match_score(query, symbol, name) > 0.0


def _safe_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
