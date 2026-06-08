"""On-chain contract-fingerprint patterns.

Pure fingerprinters for known custody-contract shapes:

- OpenZeppelin ``VestingWallet`` (and 5.x ``VestingWalletCliff`` variants)
- OpenZeppelin ``TokenTimelock``
- ERC-4626 tokenized vaults
- Synthetix-style ``StakingRewards`` (and forks)

Each ``inspect_*`` function returns a dict of observed fields when the
contract matches the pattern, otherwise ``None``. Matching is defensive:
selectors that revert get swallowed (via ``_safe_eth_call_uint``), and
pattern-specific token-address checks (TokenTimelock.token(),
ERC4626.asset(), StakingRewards.stakingToken()) filter out contracts
whose underlying token is different from the one under research.

Selectors live here so they can be reused from other analysis paths
(e.g., `commands/risk.py` tier scoring) without pulling in the full
``rpc`` module.
"""

from __future__ import annotations

from typing import Any

from token_research.config import AppConfig
from token_research.providers.http import ProviderError


# ---------------------------------------------------------------------------
# Function selectors (keccak256("signature()")[:4])
# ---------------------------------------------------------------------------

# OpenZeppelin VestingWallet
VESTING_RELEASABLE_SELECTOR = "0x19165587"  # releasable() — parameterless variant is 0xa3f8eace
VESTING_RELEASABLE_TOKEN_SELECTOR = "0xc1fc0dcc"  # releasable(address token) on OZ 5.x
VESTING_RELEASED_SELECTOR = "0x96132521"  # released()
VESTING_START_SELECTOR = "0xbe9a6555"  # start()
VESTING_DURATION_SELECTOR = "0x0fb5a6b4"  # duration()
VESTING_BENEFICIARY_SELECTOR = "0x38af3eed"  # beneficiary()

# ERC-4626
TOTAL_ASSETS_SELECTOR = "0x01e1d114"  # totalAssets()
ERC4626_ASSET_SELECTOR = "0x38d52e0f"  # asset()

# OpenZeppelin TokenTimelock
TOKEN_TIMELOCK_RELEASE_TIME_SELECTOR = "0xb91d4001"  # releaseTime()
TOKEN_TIMELOCK_TOKEN_SELECTOR = "0xfc0c546a"  # token()

# Synthetix-style StakingRewards and forks
STAKING_TOKEN_SELECTOR = "0x72f702f3"  # stakingToken()
STAKING_TOTAL_SUPPLY_SELECTOR = "0x18160ddd"  # totalSupply() — share token

# veCRV-style vote-escrow lock contracts
VE_LOCKED_SELECTOR = "0xcbf9fe5f"  # locked(address) → (int128 amount, uint256 end)
VE_TOKEN_SELECTOR = "0xfc0c546a"   # token() — underlying token
VE_SUPPLY_SELECTOR = "0x18160ddd"  # totalSupply() — total voting power


def inspect_vesting_wallet(
    chain: str, contract_address: str, token_address: str, config: AppConfig
) -> dict[str, Any] | None:
    """Return OZ VestingWallet metrics, or ``None`` if the contract doesn't match."""
    from token_research.providers.rpc import (
        _eth_call,
        _rpc_url_for_chain,
        _safe_eth_call_uint,
        get_balance_of,
    )

    rpc_url = _rpc_url_for_chain(chain, config)
    if not rpc_url:
        return None

    start = _safe_eth_call_uint(rpc_url, contract_address, VESTING_START_SELECTOR, config)
    duration = _safe_eth_call_uint(rpc_url, contract_address, VESTING_DURATION_SELECTOR, config)
    if start is None and duration is None:
        return None  # not a vesting wallet

    released = _safe_eth_call_uint(rpc_url, contract_address, VESTING_RELEASED_SELECTOR, config)
    token_balance = get_balance_of(chain, token_address, contract_address, config)

    beneficiary = None
    try:
        result = _eth_call(rpc_url, contract_address, VESTING_BENEFICIARY_SELECTOR, config)
        if result and len(result) >= 66:
            beneficiary = "0x" + result[-40:]
    except ProviderError:
        pass

    return {
        "contract": contract_address,
        "type": "vesting_wallet",
        "start": start,
        "duration": duration,
        "released": released,
        "token_balance": token_balance,
        "beneficiary": beneficiary,
    }


def inspect_token_timelock(
    chain: str, contract_address: str, token_address: str, config: AppConfig
) -> dict[str, Any] | None:
    """Return OZ TokenTimelock metrics, or ``None`` if it doesn't match.

    Filters out contracts whose ``token()`` is a different ERC-20 than the
    one under research.
    """
    from token_research.providers.rpc import (
        _decode_address,
        _decode_uint256,
        _eth_call,
        _rpc_url_for_chain,
        get_balance_of,
    )

    rpc_url = _rpc_url_for_chain(chain, config)
    if not rpc_url:
        return None

    try:
        release_time_raw = _eth_call(rpc_url, contract_address, TOKEN_TIMELOCK_RELEASE_TIME_SELECTOR, config)
        token_raw = _eth_call(rpc_url, contract_address, TOKEN_TIMELOCK_TOKEN_SELECTOR, config)
    except ProviderError:
        return None

    if not release_time_raw or not token_raw:
        return None

    locked_token = _decode_address(token_raw)
    if not locked_token or locked_token.lower() != token_address.lower():
        return None

    release_time = _decode_uint256(release_time_raw)
    token_balance = get_balance_of(chain, token_address, contract_address, config)

    beneficiary = None
    try:
        result = _eth_call(rpc_url, contract_address, VESTING_BENEFICIARY_SELECTOR, config)
        beneficiary = _decode_address(result)
    except ProviderError:
        pass

    return {
        "contract": contract_address,
        "type": "token_timelock",
        "release_time": release_time,
        "locked_token": locked_token,
        "token_balance": token_balance,
        "beneficiary": beneficiary,
    }


def inspect_erc4626_vault(
    chain: str, contract_address: str, token_address: str, config: AppConfig
) -> dict[str, Any] | None:
    """Return ERC-4626 vault metrics, or ``None`` if ``asset() != token_address``."""
    from token_research.providers.rpc import (
        _decode_address,
        _eth_call,
        _rpc_url_for_chain,
        _safe_eth_call_uint,
        get_balance_of,
    )

    rpc_url = _rpc_url_for_chain(chain, config)
    if not rpc_url:
        return None

    try:
        asset_raw = _eth_call(rpc_url, contract_address, ERC4626_ASSET_SELECTOR, config)
    except ProviderError:
        return None

    asset_address = _decode_address(asset_raw)
    if not asset_address or asset_address.lower() != token_address.lower():
        return None

    total_assets = _safe_eth_call_uint(rpc_url, contract_address, TOTAL_ASSETS_SELECTOR, config)
    token_balance = get_balance_of(chain, token_address, contract_address, config)

    return {
        "contract": contract_address,
        "type": "erc4626_vault",
        "asset": asset_address,
        "total_assets": total_assets,
        "token_balance": token_balance,
    }


def inspect_generic_staking(
    chain: str, contract_address: str, token_address: str, config: AppConfig
) -> dict[str, Any] | None:
    """Return Synthetix-style StakingRewards metrics, or ``None`` if it doesn't match."""
    from token_research.providers.rpc import (
        _decode_address,
        _eth_call,
        _rpc_url_for_chain,
        _safe_eth_call_uint,
        get_balance_of,
    )

    rpc_url = _rpc_url_for_chain(chain, config)
    if not rpc_url:
        return None

    try:
        staking_token_raw = _eth_call(rpc_url, contract_address, STAKING_TOKEN_SELECTOR, config)
    except ProviderError:
        return None

    staking_token = _decode_address(staking_token_raw)
    if not staking_token or staking_token.lower() != token_address.lower():
        return None

    total_share_supply = _safe_eth_call_uint(rpc_url, contract_address, STAKING_TOTAL_SUPPLY_SELECTOR, config)
    token_balance = get_balance_of(chain, token_address, contract_address, config)

    return {
        "contract": contract_address,
        "type": "generic_staking",
        "staking_token": staking_token,
        "total_share_supply": total_share_supply,
        "token_balance": token_balance,
    }


def inspect_ve_lock(
    chain: str, contract_address: str, token_address: str, config: AppConfig
) -> dict[str, Any] | None:
    """Detect veCRV-style vote-escrow lock contracts.

    Pattern: ``locked(address)`` returns a struct with amount + unlock end,
    and ``token()`` returns the underlying ERC-20.
    """
    from token_research.providers.rpc import (
        _decode_address,
        _eth_call,
        _rpc_url_for_chain,
        _safe_eth_call_uint,
        get_balance_of,
    )

    rpc_url = _rpc_url_for_chain(chain, config)
    if not rpc_url:
        return None

    # Check token() matches the token under research
    try:
        token_raw = _eth_call(rpc_url, contract_address, VE_TOKEN_SELECTOR, config)
    except ProviderError:
        return None

    underlying = _decode_address(token_raw)
    if not underlying or underlying.lower() != token_address.lower():
        return None

    # Probe locked(address(0)) to confirm the selector responds — the actual
    # lock data per-user is not useful in aggregate, but the selector
    # responding at all confirms this is a vote-escrow contract.
    padded_zero = "0" * 64
    try:
        locked_raw = _eth_call(rpc_url, contract_address, VE_LOCKED_SELECTOR + padded_zero, config)
    except ProviderError:
        locked_raw = None

    if locked_raw is None:
        return None

    total_voting_supply = _safe_eth_call_uint(rpc_url, contract_address, VE_SUPPLY_SELECTOR, config)
    token_balance = get_balance_of(chain, token_address, contract_address, config)

    return {
        "contract": contract_address,
        "type": "ve_lock",
        "underlying_token": underlying,
        "total_voting_supply": total_voting_supply,
        "token_balance": token_balance,
    }
