"""Static registry of well-known EVM addresses used during classification.

Kept intentionally small and conservative. Only addresses that are widely
documented and stable should be added here — false positives here directly
bias LP lock, vesting, and relations output.
"""

from __future__ import annotations

from token_research.chains import normalize_chain_name


BURN_ADDRESSES = frozenset(
    a.lower()
    for a in (
        "0x0000000000000000000000000000000000000000",
        "0x000000000000000000000000000000000000dEaD",
        "0x0000000000000000000000000000000000000001",
    )
)


# LP / token locker contracts keyed by normalized chain name. Only include
# addresses with strong public attestation (project docs, Dune spellbook,
# or Etherscan verified labels).
#
# SCOPE: This is a trusted allow-list, not an exhaustive index. Current
# entries cover Ethereum lockers seen most often in reviews; Base / Arbitrum /
# Optimism dicts are intentionally empty. Unknown lockers are still captured
# by the name-heuristic fallbacks in `commands/locks.py` and `commands/staking.py`
# (keyword matching on verified contract names), so absence here does not
# mean the locker goes unreported — it just means the classification signal
# is weaker. Prefer adding a Dune-backed auto-discovery over hand-maintaining
# this list long-term.
KNOWN_LP_LOCKERS: dict[str, dict[str, str]] = {
    "ethereum": {
        "0xe2fe530c047f2d85298b07d9333c05737f1435fb": "Team Finance Lock",
        "0x663a5c229c09b049e36dcc11a9b0d4a8eb9db214": "Unicrypt V2 Lock",
        "0x71b5759d73262fbb223956913ecf4ecc51057641": "PinkSale Lock",
    },
    "base": {},
    "arbitrum": {},
    "optimism": {},
}


# Known CEX hot/deposit wallets on Ethereum. Used by the `screen` command
# to detect whether a token is listed on major centralized exchanges.
# Source: Etherscan verified labels + Arkham Intelligence public tags.
KNOWN_CEX_WALLETS: dict[str, str] = {
    # Binance
    "0x28c6c06298d514780030bb0fb8a73eec6d98e6d7": "Binance 14",
    "0xf977814e90da44bfa03b4253de767361f4e04526": "Binance 8",
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549": "Binance 36",
    "0xdfd5293d8e347dfe59e90efd55b2956a1343963d": "Binance 16",
    "0x56eddb7aa87536c09ccc2793473599fd21a8b17f": "Binance 17",
    "0x9696f59e4d72e237be84ffd425dcad154bf96976": "Binance 19",
    # Bybit
    "0xf89d7b9c864f589bbf53a82105107622b35eaa40": "Bybit",
    "0x1db92e2eebc8e0c075a02bea49a2935bcd2dfcf4": "Bybit 2",
    # OKX
    "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b": "OKX",
    "0x98ec059dc3adfbdd63429227d09cb3e8a787e7a1": "OKX 2",
    # Coinbase
    "0x503828976d22510aad0201ac7ec88293211d23da": "Coinbase 2",
    "0xddfabcdc4d8ffc6d5beaf154f18b778f892a0740": "Coinbase 3",
    "0x3cd751e6b0078be393132286c442345e68ff0045": "Coinbase 4",
    "0xa9d1e08c7793af67e9d92fe308d5697fb81d3e43": "Coinbase 10",
    # Kraken
    "0x2910543af39aba0cd09dbb2d50200b3e800a63d2": "Kraken 13",
    "0x267be1c1d684f78cb4f6a176c4911b741e4ffdc0": "Kraken",
    # Gate.io
    "0x0d0707963952f2fba59dd06f2b425ace40b492fe": "Gate.io",
    # Kucoin
    "0xf16e9b0d03470827a95cdfd0cb8a8a3b46969b91": "Kucoin",
    # HTX (Huobi)
    "0x46340b20830761efd32832a74d7169b29feb9758": "HTX",
}


def identify_cex(address: str) -> str | None:
    """Return exchange name if address is a known CEX wallet, else None."""
    if not address:
        return None
    return KNOWN_CEX_WALLETS.get(address.lower())


def is_burn_address(address: str) -> bool:
    return bool(address) and address.lower() in BURN_ADDRESSES


def known_locker_label(chain: str | None, address: str) -> str | None:
    if not address:
        return None
    lockers = KNOWN_LP_LOCKERS.get(normalize_chain_name(chain), {})
    return lockers.get(address.lower())


def classify_lp_holder(chain: str | None, address: str) -> tuple[str, str | None]:
    """Classify an LP-token holder as burn, locker, or other.

    Returns (classification, label). Label is None for 'other'.
    """
    if is_burn_address(address):
        return "burn", "burn_address"
    label = known_locker_label(chain, address)
    if label:
        return "locker", label
    return "other", None


# Keyword fingerprints for verified-contract names. These are intentionally
# broad — they only fire as a *fallback* after ABI-based fingerprinting has
# been tried, and the resulting records are flagged as heuristic (confidence
# should be lower than strict RPC matches).
_VESTING_NAME_KEYWORDS = (
    "vest",
    "timelock",
    "cliff",
    "escrow",
    "lockup",
    "token_lock",
    "tokenlock",
    "lockbox",
)

_STAKING_NAME_KEYWORDS = (
    "stak",
    "reward",
    "rewards",
    "gauge",
    "vault",
    "xtoken",
    "ve_",
    "vetoken",
    "veescrow",
    "pool",
    "masterchef",
)


def _normalize_name(name: str) -> str:
    return "".join(c for c in name.lower() if c.isalnum() or c == "_")


def name_suggests_vesting(name: str | None) -> bool:
    if not name:
        return False
    normalized = _normalize_name(name)
    return any(keyword in normalized for keyword in _VESTING_NAME_KEYWORDS)


def name_suggests_staking(name: str | None) -> bool:
    if not name:
        return False
    normalized = _normalize_name(name)
    return any(keyword in normalized for keyword in _STAKING_NAME_KEYWORDS)
