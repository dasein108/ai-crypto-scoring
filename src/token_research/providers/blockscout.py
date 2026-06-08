from __future__ import annotations

from typing import Any

from token_research.cache import persist_raw_payload
from token_research.chains import get_blockscout_base_url, normalize_chain_name
from token_research.config import AppConfig
from token_research.providers._memoize import cached
from token_research.providers.http import ProviderError, build_url, get_json


@cached(
    "blockscout.get_token_info",
    lambda chain, token_address, config: (normalize_chain_name(chain), token_address.lower()),
)
def get_token_info(chain: str, token_address: str, config: AppConfig) -> dict[str, Any]:
    normalized_chain = normalize_chain_name(chain)
    base_url = get_blockscout_base_url(normalized_chain)
    if not base_url:
        return {}
    headers = {}
    if config.blockscout_api_key:
        headers["x-api-key"] = config.blockscout_api_key
    payload = get_json(
        f"{base_url}/api/v2/tokens/{token_address}",
        timeout_seconds=config.request_timeout_seconds,
        headers=headers,
    )
    if isinstance(payload, dict):
        persist_raw_payload(config, "blockscout", "token-info", f"{normalized_chain}-{token_address.lower()}", payload)
        return payload
    return {}


@cached(
    "blockscout.get_address_info",
    lambda chain, address, config: (normalize_chain_name(chain), address.lower()),
)
def get_address_info(chain: str, address: str, config: AppConfig) -> dict[str, Any]:
    """Fetch Blockscout's address metadata (public_tags, watchlist_names, implementation).

    Returns {} for unsupported chains, missing addresses, or transport errors.
    """
    normalized_chain = normalize_chain_name(chain)
    base_url = get_blockscout_base_url(normalized_chain)
    if not base_url or not address:
        return {}
    headers = {}
    if config.blockscout_api_key:
        headers["x-api-key"] = config.blockscout_api_key
    try:
        payload = get_json(
            f"{base_url}/api/v2/addresses/{address}",
            timeout_seconds=config.request_timeout_seconds,
            headers=headers,
        )
    except ProviderError:
        return {}
    if isinstance(payload, dict):
        persist_raw_payload(
            config,
            "blockscout",
            "address-info",
            f"{normalized_chain}-{address.lower()}",
            payload,
        )
        return payload
    return {}


@cached(
    "blockscout.get_transaction",
    lambda chain, tx_hash, config: (normalize_chain_name(chain), tx_hash.lower()),
)
def get_transaction(chain: str, tx_hash: str, config: AppConfig) -> dict[str, Any]:
    """Fetch a single transaction by hash via Blockscout. Returns {} on error.

    Primary use: looking up contract creation timestamps to derive protocol
    age bands when DeFiLlama `listedAt` is missing.
    """
    normalized_chain = normalize_chain_name(chain)
    base_url = get_blockscout_base_url(normalized_chain)
    if not base_url or not tx_hash:
        return {}
    headers = {}
    if config.blockscout_api_key:
        headers["x-api-key"] = config.blockscout_api_key
    try:
        payload = get_json(
            f"{base_url}/api/v2/transactions/{tx_hash}",
            timeout_seconds=config.request_timeout_seconds,
            headers=headers,
        )
    except ProviderError:
        return {}
    if isinstance(payload, dict):
        persist_raw_payload(
            config,
            "blockscout",
            "transaction",
            f"{normalized_chain}-{tx_hash.lower()}",
            payload,
        )
        return payload
    return {}


def collect_name_hints(info: dict[str, Any]) -> list[str]:
    """Return every human-readable name/tag found on a Blockscout address payload.

    Returned strings are not deduped — callers typically just need to run
    keyword checks over the union. Order: verified contract name,
    implementation_name, token.name, public_tags, private_tags, watchlist.
    """
    if not info:
        return []
    hints: list[str] = []
    name = info.get("name")
    if isinstance(name, str) and name:
        hints.append(name)
    impl = info.get("implementation_name")
    if isinstance(impl, str) and impl:
        hints.append(impl)
    token = info.get("token") or {}
    if isinstance(token, dict):
        tname = token.get("name")
        if isinstance(tname, str) and tname:
            hints.append(tname)
        tsymbol = token.get("symbol")
        if isinstance(tsymbol, str) and tsymbol:
            hints.append(tsymbol)
    for tag_list_key in ("public_tags", "private_tags", "watchlist_names"):
        for tag in info.get(tag_list_key) or []:
            if isinstance(tag, dict):
                display = tag.get("display_name") or tag.get("tag_name")
                if isinstance(display, str) and display:
                    hints.append(display)
    return hints


def extract_address_labels(info: dict[str, Any]) -> list[dict[str, str]]:
    """Flatten a Blockscout address-info payload into a list of label rows."""
    if not info:
        return []
    rows: list[dict[str, str]] = []

    for tag in info.get("public_tags") or []:
        if isinstance(tag, dict) and tag.get("display_name"):
            rows.append(
                {
                    "source": "blockscout_public_tag",
                    "label": str(tag.get("display_name")),
                    "type": "public_tag",
                }
            )
    for tag in info.get("private_tags") or []:
        if isinstance(tag, dict) and tag.get("display_name"):
            rows.append(
                {
                    "source": "blockscout_private_tag",
                    "label": str(tag.get("display_name")),
                    "type": "private_tag",
                }
            )
    for name in info.get("watchlist_names") or []:
        if isinstance(name, dict) and name.get("display_name"):
            rows.append(
                {
                    "source": "blockscout_watchlist",
                    "label": str(name.get("display_name")),
                    "type": "watchlist",
                }
            )
    token = info.get("token") or {}
    if isinstance(token, dict) and token.get("name"):
        rows.append(
            {
                "source": "blockscout_token_contract",
                "label": str(token.get("name")),
                "type": "token_contract",
            }
        )
    impl = info.get("implementation_name")
    if impl:
        rows.append(
            {
                "source": "blockscout_implementation",
                "label": str(impl),
                "type": "proxy_implementation",
            }
        )
    name = info.get("name")
    if name:
        rows.append(
            {
                "source": "blockscout_contract_name",
                "label": str(name),
                "type": "contract_name",
            }
        )
    return rows


@cached(
    "blockscout.get_token_holders",
    lambda chain, token_address, config, page=1, offset=20: (
        normalize_chain_name(chain), token_address.lower(), page, offset,
    ),
)
def get_token_holders(chain: str, token_address: str, config: AppConfig, page: int = 1, offset: int = 20) -> list[dict[str, Any]]:
    normalized_chain = normalize_chain_name(chain)
    base_url = get_blockscout_base_url(normalized_chain)
    if not base_url:
        return []
    params = {
        "module": "token",
        "action": "getTokenHolders",
        "contractaddress": token_address,
        "page": page,
        "offset": offset,
    }
    if config.blockscout_api_key:
        params["apikey"] = config.blockscout_api_key

    url = build_url(f"{base_url}/api", params)
    payload = get_json(url, timeout_seconds=config.request_timeout_seconds)
    if isinstance(payload, dict):
        persist_raw_payload(config, "blockscout", "token-holders", f"{normalized_chain}-{token_address.lower()}", payload)
        result = payload.get("result") or []
        return [item for item in result if isinstance(item, dict)]
    return []
