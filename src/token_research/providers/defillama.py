from __future__ import annotations

from typing import Any
from urllib.parse import quote

from token_research.cache import persist_raw_payload
from token_research.config import AppConfig
from token_research.providers._memoize import cached
from token_research.providers.http import ProviderError, get_json


DEFILLAMA_COINS_BASE = "https://coins.llama.fi"
DEFILLAMA_API_BASE = "https://api.llama.fi"
DEFILLAMA_YIELDS_BASE = "https://yields.llama.fi"


def _coin_id(chain: str, token_address: str) -> str:
    return f"{chain}:{token_address}"


def get_current_price(chain: str, token_address: str, config: AppConfig) -> dict[str, Any]:
    """Fetch current price for a single token.

    Returns the coin entry dict with keys: price, symbol, decimals, timestamp, confidence.
    Returns empty dict if the coin is not found.
    """
    coin_id = _coin_id(chain, token_address)
    payload = get_json(
        f"{DEFILLAMA_COINS_BASE}/prices/current/{quote(coin_id, safe=':')}",
        timeout_seconds=config.request_timeout_seconds,
    )
    if not isinstance(payload, dict):
        return {}
    persist_raw_payload(config, "defillama", "prices-current", f"{chain}-{token_address.lower()}", payload)
    coins = payload.get("coins") or {}
    return coins.get(coin_id) or {}


@cached("defillama.get_chains", lambda config: ("all",))
def get_chains(config: AppConfig) -> list[dict[str, Any]]:
    """Fetch DefiLlama's chain directory. Returns [] on failure."""
    payload = get_json(
        f"{DEFILLAMA_API_BASE}/chains",
        timeout_seconds=config.request_timeout_seconds,
    )
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def find_chain_by_name(query: str, config: AppConfig) -> dict[str, Any] | None:
    """Case-insensitive lookup of a chain entry by name. Returns None if missing."""
    needle = (query or "").strip().lower()
    if not needle:
        return None
    for ch in get_chains(config):
        if str(ch.get("name", "")).strip().lower() == needle:
            return ch
    # Loose substring fallback — useful for "bsc" → "Binance"
    for ch in get_chains(config):
        if needle in str(ch.get("name", "")).strip().lower():
            return ch
    return None


@cached(
    "defillama.get_chain_historical_tvl",
    lambda chain_name, config: (chain_name.lower(),),
)
def get_chain_historical_tvl(chain_name: str, config: AppConfig) -> list[dict[str, Any]]:
    """Fetch historical chain TVL series via /v2/historicalChainTvl/{chain}.

    Returned entries have `{date, tvl}` keys. Used by chain-report to compute
    peak / drawdown / trend deltas.
    """
    from urllib.parse import quote
    try:
        payload = get_json(
            f"{DEFILLAMA_API_BASE}/v2/historicalChainTvl/{quote(chain_name)}",
            timeout_seconds=max(config.request_timeout_seconds, 10.0),
        )
    except ProviderError:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


@cached(
    "defillama.get_chain_fees",
    lambda chain_name, config: (chain_name.lower(),),
)
def get_chain_fees(chain_name: str, config: AppConfig) -> dict[str, Any]:
    """Fetch chain-level fees via /overview/fees/{chain}.

    Hedera, Solana, etc. have this data even though they aren't indexed as
    protocols. Returns {} on transport failure or when the chain has no fees
    data (many small chains don't).
    """
    from urllib.parse import quote
    try:
        payload = get_json(
            f"{DEFILLAMA_API_BASE}/overview/fees/{quote(chain_name)}",
            timeout_seconds=max(config.request_timeout_seconds, 10.0),
        )
    except ProviderError:
        return {}
    return payload if isinstance(payload, dict) else {}


def get_chain_coin_price(gecko_id: str, config: AppConfig) -> dict[str, Any]:
    """Fetch the chain's native token price via DefiLlama coins (by coingecko id).

    Used for L1 native tokens where we don't have a contract address
    (HBAR, ATOM, SOL, etc.). Returns a {price, symbol, timestamp, confidence}
    dict or {} on failure.
    """
    if not gecko_id:
        return {}
    try:
        payload = get_json(
            f"{DEFILLAMA_COINS_BASE}/prices/current/coingecko:{gecko_id}",
            timeout_seconds=config.request_timeout_seconds,
        )
    except ProviderError:
        return {}
    if not isinstance(payload, dict):
        return {}
    coins = payload.get("coins") or {}
    entry = coins.get(f"coingecko:{gecko_id}") or {}
    return entry


@cached("defillama.get_protocols", lambda config: ("all",))
def get_protocols(config: AppConfig) -> list[dict[str, Any]]:
    """Fetch the DeFiLlama protocols directory. Returns [] on failure."""
    payload = get_json(
        f"{DEFILLAMA_API_BASE}/protocols",
        timeout_seconds=config.request_timeout_seconds,
    )
    if isinstance(payload, list):
        persist_raw_payload(config, "defillama", "protocols", "all", {"protocols": payload})
        return [item for item in payload if isinstance(item, dict)]
    return []


@cached(
    "defillama._fees_summary",
    lambda slug, data_type, config: (slug.lower(), data_type),
)
def _fees_summary(slug: str, data_type: str, config: AppConfig) -> dict[str, Any]:
    """Fetch a DeFiLlama fees/revenue summary for a protocol slug.

    data_type: 'dailyFees' or 'dailyRevenue'. Returns the raw payload or {}.
    """
    from urllib.parse import quote
    payload = get_json(
        f"{DEFILLAMA_API_BASE}/summary/fees/{quote(slug)}?dataType={data_type}",
        timeout_seconds=config.request_timeout_seconds,
    )
    if isinstance(payload, dict):
        persist_raw_payload(
            config,
            "defillama",
            f"fees-{data_type}",
            slug.lower(),
            payload,
        )
        return payload
    return {}


def get_protocol_fees(slug: str, config: AppConfig) -> dict[str, Any]:
    """Return normalized daily/7d/30d fee totals for a protocol slug."""
    payload = _fees_summary(slug, "dailyFees", config)
    if not payload:
        return {}
    return {
        "total24h": payload.get("total24h"),
        "total7d": payload.get("total7d"),
        "total30d": payload.get("total30d"),
        "total1y": payload.get("total1y"),
        "change_1d": payload.get("change_1d"),
        "change_7d": payload.get("change_7d"),
        "change_1m": payload.get("change_1m"),
        "category": payload.get("category"),
    }


def get_protocol_revenue(slug: str, config: AppConfig) -> dict[str, Any]:
    """Return normalized daily/7d/30d revenue totals (fees retained by the protocol)."""
    payload = _fees_summary(slug, "dailyRevenue", config)
    if not payload:
        return {}
    return {
        "total24h": payload.get("total24h"),
        "total7d": payload.get("total7d"),
        "total30d": payload.get("total30d"),
        "total1y": payload.get("total1y"),
        "change_1d": payload.get("change_1d"),
        "change_7d": payload.get("change_7d"),
        "change_1m": payload.get("change_1m"),
    }


@cached("defillama.get_yield_pools", lambda config: ("all",))
def get_yield_pools(config: AppConfig) -> list[dict[str, Any]]:
    """Fetch yields.llama.fi/pools — the global pool directory.

    Heavy (~8k entries, ~5MB). Memoised per-process via @cached.
    """
    payload = get_json(
        f"{DEFILLAMA_YIELDS_BASE}/pools",
        timeout_seconds=max(config.request_timeout_seconds, 15.0),
    )
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        pools = [item for item in payload["data"] if isinstance(item, dict)]
        persist_raw_payload(config, "defillama", "yields-pools", "all", {"count": len(pools)})
        return pools
    return []


def find_yield_pools_for_protocol(
    slug: str | None,
    symbol: str | None,
    config: AppConfig,
) -> list[dict[str, Any]]:
    """Filter yields.llama.fi pools by protocol slug (strict) or symbol (loose).

    Match priority:
      1. exact project==slug
      2. symbol appears inside the pool's symbol string (for PT/LP pairs)
    Returned pools are sorted by TVL descending.
    """
    pools = get_yield_pools(config)
    if not pools:
        return []
    slug_lower = (slug or "").strip().lower()
    symbol_upper = (symbol or "").strip().upper()
    matches: list[dict[str, Any]] = []
    for pool in pools:
        project = str(pool.get("project") or "").lower()
        pool_symbol = str(pool.get("symbol") or "").upper()
        by_slug = slug_lower and project == slug_lower
        by_symbol = symbol_upper and symbol_upper in pool_symbol
        if by_slug or by_symbol:
            matches.append(pool)
    matches.sort(key=lambda p: float(p.get("tvlUsd") or 0), reverse=True)
    return matches


def _proto_tvl(proto: dict[str, Any]) -> float:
    value = proto.get("tvl")
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def find_protocols_by_name(
    query: str, config: AppConfig, *, min_tvl_usd: float = 100_000.0
) -> list[dict[str, Any]]:
    """Return every protocol whose name or slug contains the query, ranked by TVL.

    Unlike ``find_protocol`` (which returns a single best match), this is for
    disambiguation use cases where the caller wants to see all candidates —
    pre-launch queries where multiple "Katana" entries exist, for example.
    Results are filtered by ``min_tvl_usd`` to drop dead entries.
    """
    try:
        protocols = get_protocols(config)
    except ProviderError:
        return []
    if not protocols:
        return []
    needle = (query or "").strip().lower()
    if not needle:
        return []
    matches: list[dict[str, Any]] = []
    for proto in protocols:
        name = str(proto.get("name") or "").lower()
        slug = str(proto.get("slug") or "").lower()
        tvl = _proto_tvl(proto)
        if tvl < min_tvl_usd:
            continue
        if needle in name or needle in slug:
            matches.append(proto)
    matches.sort(key=_proto_tvl, reverse=True)
    return matches


def find_protocol(
    symbol: str | None, project_name: str | None, config: AppConfig
) -> dict[str, Any] | None:
    """Find a DeFiLlama protocol entry matching the given token symbol or project name.

    Matches in priority order:
      1. exact symbol → highest-TVL match
      2. exact name   → highest-TVL match
      3. name substring (both directions), but only among protocols with TVL > 0
         so we don't return stub/dead entries.

    Returns None if nothing plausible is found.
    """
    try:
        protocols = get_protocols(config)
    except ProviderError:
        return None
    if not protocols:
        return None

    symbol_lower = (symbol or "").strip().lower()
    name_lower = (project_name or "").strip().lower()

    def best(matches: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not matches:
            return None
        return max(matches, key=_proto_tvl)

    if symbol_lower:
        hits = [p for p in protocols if str(p.get("symbol", "")).lower() == symbol_lower]
        winner = best(hits)
        if winner is not None:
            return winner

    if name_lower:
        hits = [p for p in protocols if str(p.get("name", "")).lower() == name_lower]
        winner = best(hits)
        if winner is not None:
            return winner

        substring_hits: list[dict[str, Any]] = []
        for proto in protocols:
            if _proto_tvl(proto) <= 0:
                continue
            pname = str(proto.get("name", "")).lower()
            if pname and (name_lower in pname or pname in name_lower):
                substring_hits.append(proto)
        winner = best(substring_hits)
        if winner is not None:
            return winner

    return None


# ---------------------------------------------------------------------------
# Hacks + audit helpers
# ---------------------------------------------------------------------------

@cached("defillama.get_hacks", lambda config: ("all",))
def get_hacks(config: AppConfig) -> list[dict[str, Any]]:
    """Fetch DeFiLlama's hacks database. Returns [] on failure."""
    payload = get_json(
        f"{DEFILLAMA_API_BASE}/hacks",
        timeout_seconds=max(config.request_timeout_seconds, 8.0),
    )
    if isinstance(payload, list):
        persist_raw_payload(config, "defillama", "hacks", "all", {"hacks": payload})
        return [item for item in payload if isinstance(item, dict)]
    return []


def find_hacks_for_protocol(
    protocol_name: str | None, protocol_slug: str | None, config: AppConfig
) -> list[dict[str, Any]]:
    """Return hack entries that match the given protocol name or slug."""
    if not protocol_name and not protocol_slug:
        return []
    try:
        all_hacks = get_hacks(config)
    except ProviderError:
        return []
    name_l = (protocol_name or "").strip().lower()
    slug_l = (protocol_slug or "").strip().lower()
    matches = []
    for hack in all_hacks:
        hack_name = str(hack.get("name") or "").strip().lower()
        hack_project = str(hack.get("project") or "").strip().lower()
        if (name_l and name_l == hack_name) or (slug_l and slug_l == hack_project):
            matches.append(hack)
    return matches


def get_protocol_audits(protocol: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract audit metadata from a DeFiLlama protocol dict.

    Returns a list of ``{url, index}`` dicts from the ``audit_links`` field.
    """
    audit_links = protocol.get("audit_links") or []
    if isinstance(audit_links, list):
        return [
            {"url": link, "index": i}
            for i, link in enumerate(audit_links)
            if isinstance(link, str) and link.startswith("http")
        ]
    return []
