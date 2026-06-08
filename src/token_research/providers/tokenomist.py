from __future__ import annotations

from typing import Any
from urllib.parse import quote

from token_research.cache import persist_raw_payload
from token_research.config import AppConfig
from token_research.providers._memoize import cached
from token_research.providers.http import ProviderError, get_json


TOKENOMIST_BASE = "https://api.unlocks.app/v1"


def _headers(config: AppConfig) -> dict[str, str]:
    if not config.tokenomist_api_key:
        raise ProviderError("Tokenomist API key not configured (TOKEN_RESEARCH_TOKENOMIST_API_KEY).")
    return {"Authorization": f"Bearer {config.tokenomist_api_key}"}


@cached("tokenomist_list", lambda config: ("all",))
@cached("tokenomist_unlocks", lambda symbol, config: (symbol.strip().lower(),))
def get_token_unlocks(symbol: str, config: AppConfig) -> dict[str, Any]:
    """Fetch unlock schedule for a specific token by symbol.

    Returns the token entry with allocation and unlock event details.
    """
    payload = get_json(
        f"{TOKENOMIST_BASE}/token/{quote(symbol.upper())}",
        timeout_seconds=config.request_timeout_seconds,
        headers=_headers(config),
    )
    if isinstance(payload, dict):
        persist_raw_payload(config, "tokenomist", "token-unlocks", symbol.lower(), payload)
        return payload
    return {}
