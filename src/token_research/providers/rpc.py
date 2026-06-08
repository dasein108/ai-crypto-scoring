"""JSON-RPC client + ERC-20 primitives for supported EVM chains.

Scope kept small:

- ``_call_rpc`` / ``_eth_call`` — low-level request helpers
- ``get_total_supply`` / ``get_decimals`` / ``get_balance_of`` — ERC-20 reads
- ``get_code`` / ``is_contract`` — EOA vs contract detection
- ``get_total_assets`` — ERC-4626 ``totalAssets()`` (needed by supply.py)

Contract-pattern fingerprinting (``inspect_vesting_wallet`` &c.) lives in
``providers.patterns`` and is re-exported here for back-compat so callers
can keep doing ``rpc.inspect_vesting_wallet(...)``.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from token_research.config import AppConfig
from token_research.providers._memoize import cached
from token_research.providers.http import ProviderError


# ERC-20 function selectors
TOTAL_SUPPLY_SELECTOR = "0x18160ddd"
DECIMALS_SELECTOR = "0x313ce567"
BALANCE_OF_SELECTOR = "0x70a08231"

# ERC-4626 totalAssets() — re-used by supply.py for yield-bearing wrappers
TOTAL_ASSETS_SELECTOR = "0x01e1d114"

# Ownership / admin selectors
OWNER_SELECTOR = "0x8da5cb5b"  # owner()

# EIP-1967 proxy admin storage slot
# bytes32(uint256(keccak256('eip1967.proxy.admin')) - 1)
EIP1967_ADMIN_SLOT = "0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103"
# EIP-1967 implementation storage slot
EIP1967_IMPL_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"


def _rpc_url_for_chain(chain: str, config: AppConfig) -> str | None:
    mapping = {
        "ethereum": config.eth_rpc_url,
        "base": config.base_rpc_url,
        "arbitrum": config.arb_rpc_url,
        "optimism": config.op_rpc_url,
        "bsc": config.bsc_rpc_url,
    }
    return mapping.get(chain)


def _call_rpc(rpc_url: str, method: str, params: list[Any], config: AppConfig) -> Any:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    request = Request(rpc_url, data=body, headers=headers)
    try:
        with urlopen(request, timeout=config.request_timeout_seconds) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise ProviderError(f"RPC HTTP {exc.code} for {rpc_url}") from exc
    except URLError as exc:
        raise ProviderError(f"RPC network error for {rpc_url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ProviderError(f"RPC timeout for {rpc_url}") from exc

    if "error" in result:
        error = result["error"]
        raise ProviderError(f"RPC error: {error.get('message', error)}")
    return result.get("result")


def _eth_call(rpc_url: str, to: str, data: str, config: AppConfig) -> str | None:
    result = _call_rpc(rpc_url, "eth_call", [{"to": to, "data": data}, "latest"], config)
    if result and isinstance(result, str) and result != "0x":
        return result
    return None


def _decode_uint256(hex_value: str) -> int:
    return int(hex_value, 16)


def _decode_address(hex_value: str | None) -> str | None:
    if not hex_value or len(hex_value) < 66:
        return None
    return "0x" + hex_value[-40:]


def _safe_eth_call_uint(rpc_url: str, to: str, selector: str, config: AppConfig) -> int | None:
    """Call a no-arg function returning uint256, returning None on revert."""
    try:
        result = _eth_call(rpc_url, to, selector, config)
        if result:
            return _decode_uint256(result)
    except ProviderError:
        pass
    return None


def get_total_supply(chain: str, token_address: str, config: AppConfig) -> int | None:
    """Fetch ERC-20 totalSupply via eth_call. Returns raw uint256 (no decimal adjustment)."""
    rpc_url = _rpc_url_for_chain(chain, config)
    if not rpc_url:
        return None
    result = _eth_call(rpc_url, token_address, TOTAL_SUPPLY_SELECTOR, config)
    if result:
        return _decode_uint256(result)
    return None


def get_decimals(chain: str, token_address: str, config: AppConfig) -> int | None:
    """Fetch ERC-20 decimals via eth_call."""
    rpc_url = _rpc_url_for_chain(chain, config)
    if not rpc_url:
        return None
    result = _eth_call(rpc_url, token_address, DECIMALS_SELECTOR, config)
    if result:
        return _decode_uint256(result)
    return None


def get_balance_of(chain: str, token_address: str, holder: str, config: AppConfig) -> int | None:
    """Fetch ERC-20 balanceOf for a holder address. Returns raw uint256."""
    rpc_url = _rpc_url_for_chain(chain, config)
    if not rpc_url:
        return None
    padded = holder.lower().replace("0x", "").zfill(64)
    data = BALANCE_OF_SELECTOR + padded
    result = _eth_call(rpc_url, token_address, data, config)
    if result:
        return _decode_uint256(result)
    return None


def get_total_assets(chain: str, vault_address: str, config: AppConfig) -> int | None:
    """Fetch ERC-4626 totalAssets() for a vault. Returns raw uint256."""
    rpc_url = _rpc_url_for_chain(chain, config)
    if not rpc_url:
        return None
    result = _eth_call(rpc_url, vault_address, TOTAL_ASSETS_SELECTOR, config)
    if result:
        return _decode_uint256(result)
    return None


@cached(
    "rpc.get_code",
    lambda chain, address, config: (chain, address.lower()),
)
def get_code(chain: str, address: str, config: AppConfig) -> str | None:
    """Fetch contract bytecode via eth_getCode. Returns '0x' for EOAs."""
    rpc_url = _rpc_url_for_chain(chain, config)
    if not rpc_url:
        return None
    try:
        return _call_rpc(rpc_url, "eth_getCode", [address, "latest"], config)
    except ProviderError:
        return None


def is_contract(chain: str, address: str, config: AppConfig) -> bool:
    """True if address has non-empty bytecode on-chain."""
    code = get_code(chain, address, config)
    return bool(code) and code not in ("0x", "0x0", "")


def get_owner(chain: str, contract_address: str, config: AppConfig) -> str | None:
    """Call ``owner()`` on a contract. Returns checksummed address or None."""
    rpc_url = _rpc_url_for_chain(chain, config)
    if not rpc_url:
        return None
    try:
        result = _eth_call(rpc_url, contract_address, OWNER_SELECTOR, config)
        return _decode_address(result)
    except ProviderError:
        return None


def get_storage_at(chain: str, address: str, slot: str, config: AppConfig) -> str | None:
    """Read a single storage slot via ``eth_getStorageAt``."""
    rpc_url = _rpc_url_for_chain(chain, config)
    if not rpc_url:
        return None
    try:
        return _call_rpc(rpc_url, "eth_getStorageAt", [address, slot, "latest"], config)
    except ProviderError:
        return None


def get_proxy_admin(chain: str, contract_address: str, config: AppConfig) -> str | None:
    """Read EIP-1967 proxy admin slot. Returns address or None if not a proxy."""
    raw = get_storage_at(chain, contract_address, EIP1967_ADMIN_SLOT, config)
    if not raw:
        return None
    addr = _decode_address(raw)
    if addr and addr != "0x" + "0" * 40:
        return addr
    return None


def get_proxy_implementation(chain: str, contract_address: str, config: AppConfig) -> str | None:
    """Read EIP-1967 implementation slot. Returns address or None."""
    raw = get_storage_at(chain, contract_address, EIP1967_IMPL_SLOT, config)
    if not raw:
        return None
    addr = _decode_address(raw)
    if addr and addr != "0x" + "0" * 40:
        return addr
    return None


def probe_mint_privilege(chain: str, token_address: str, config: AppConfig) -> dict[str, object]:
    """Check whether the token has an ``owner()`` and if that owner is an EOA or contract.

    Returns ``{owner, owner_is_contract, proxy_admin, proxy_impl, is_upgradeable}``.
    """
    owner = get_owner(chain, token_address, config)
    owner_is_contract = is_contract(chain, owner, config) if owner else None
    proxy_admin = get_proxy_admin(chain, token_address, config)
    proxy_impl = get_proxy_implementation(chain, token_address, config)
    return {
        "owner": owner,
        "owner_is_contract": owner_is_contract,
        "proxy_admin": proxy_admin,
        "proxy_impl": proxy_impl,
        "is_upgradeable": proxy_admin is not None or proxy_impl is not None,
    }


# Re-export contract-pattern fingerprinters from providers.patterns. This
# keeps callers (``commands/locks.py``, ``commands/staking.py``) using
# ``rpc.inspect_vesting_wallet(...)`` without change while the pattern
# logic lives in its own module.
from token_research.providers.patterns import (  # noqa: E402,F401  (re-exported as rpc.inspect_*)
    inspect_erc4626_vault,
    inspect_generic_staking,
    inspect_token_timelock,
    inspect_ve_lock,
    inspect_vesting_wallet,
)

__all__ = [
    "TOTAL_SUPPLY_SELECTOR",
    "DECIMALS_SELECTOR",
    "BALANCE_OF_SELECTOR",
    "TOTAL_ASSETS_SELECTOR",
    "get_total_supply",
    "get_decimals",
    "get_balance_of",
    "get_total_assets",
    "get_code",
    "is_contract",
    "inspect_vesting_wallet",
    "inspect_token_timelock",
    "inspect_erc4626_vault",
    "inspect_generic_staking",
    "inspect_ve_lock",
]
