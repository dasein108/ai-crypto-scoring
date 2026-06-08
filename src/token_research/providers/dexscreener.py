from __future__ import annotations

from typing import Any
from urllib.parse import quote

from token_research.cache import persist_raw_payload
from token_research.config import AppConfig
from token_research.providers._memoize import cached
from token_research.providers.http import get_json


DEXSCREENER_BASE = "https://api.dexscreener.com"


def _pairs_from_payload(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        pairs = payload.get("pairs") or []
        return [pair for pair in pairs if isinstance(pair, dict)]
    return [pair for pair in payload if isinstance(pair, dict)]


@cached("dexscreener_search", lambda query, config: (query.strip().lower(),))
def search_pairs(query: str, config: AppConfig) -> list[dict[str, Any]]:
    payload = get_json(
        f"{DEXSCREENER_BASE}/latest/dex/search?q={quote(query)}",
        timeout_seconds=config.request_timeout_seconds,
    )
    if isinstance(payload, dict):
        persist_raw_payload(config, "dexscreener", "search", query.lower().replace(" ", "-"), payload)
    return _pairs_from_payload(payload)


@cached("dexscreener_token_pairs", lambda chain, token_address, config: (chain, token_address.lower()))
def get_token_pairs(chain: str, token_address: str, config: AppConfig) -> list[dict[str, Any]]:
    payload = get_json(
        f"{DEXSCREENER_BASE}/token-pairs/v1/{chain}/{token_address}",
        timeout_seconds=config.request_timeout_seconds,
    )
    if isinstance(payload, list):
        persist_raw_payload(
            config,
            "dexscreener",
            "token-pairs",
            f"{chain}-{token_address.lower()}",
            {"pairs": payload},
        )
    return _pairs_from_payload(payload)


def sort_pairs_by_liquidity(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def liquidity_usd(pair: dict[str, Any]) -> float:
        liquidity = pair.get("liquidity") or {}
        value = liquidity.get("usd") or 0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    return sorted(pairs, key=liquidity_usd, reverse=True)


def summarize_pair(pair: dict[str, Any], token_address: str | None = None) -> dict[str, Any]:
    liquidity = pair.get("liquidity") or {}
    base_token = pair.get("baseToken") or {}
    quote_token = pair.get("quoteToken") or {}
    token_side = "base"
    token_liquidity = liquidity.get("base")
    if token_address:
        lowered = token_address.lower()
        if str(base_token.get("address", "")).lower() == lowered:
            token_side = "base"
            token_liquidity = liquidity.get("base")
        elif str(quote_token.get("address", "")).lower() == lowered:
            token_side = "quote"
            token_liquidity = liquidity.get("quote")

    return {
        "chain": pair.get("chainId"),
        "dex_id": pair.get("dexId"),
        "pair_address": pair.get("pairAddress"),
        "url": pair.get("url"),
        "base_token": base_token,
        "quote_token": quote_token,
        "price_usd": _safe_float(pair.get("priceUsd")),
        "liquidity_usd": _safe_float(liquidity.get("usd")),
        "token_liquidity": _safe_float(token_liquidity),
        "token_side": token_side,
        "fdv": _safe_float(pair.get("fdv")),
        "market_cap": _safe_float(pair.get("marketCap")),
        "pair_created_at": pair.get("pairCreatedAt"),
    }


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
