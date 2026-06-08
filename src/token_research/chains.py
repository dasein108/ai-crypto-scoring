from __future__ import annotations


CHAIN_ALIASES = {
    "eth": "ethereum",
    "ethereum": "ethereum",
    "mainnet": "ethereum",
    "base": "base",
    "arb": "arbitrum",
    "arbitrum": "arbitrum",
    "op": "optimism",
    "optimism": "optimism",
    "bsc": "bsc",
    "binance": "bsc",
    "bnb": "bsc",
    "monad": "monad",
}

# Chains the pipeline can actually collect against. The resolver filters
# DexScreener search results against this so that high-liquidity tokens on
# unsupported chains (Solana memecoins, Tron, etc.) never get picked as the
# canonical identity for an EVM query.
# monad is listed in CHAIN_ALIASES (CLAUDE.md tier "planned") but intentionally
# excluded here until a Blockscout endpoint + RPC URL land. Listing it here
# without those would return None lookups downstream.
SUPPORTED_EVM_CHAINS = frozenset({"ethereum", "base", "arbitrum", "optimism", "bsc"})


BLOCKSCOUT_BASE_URLS = {
    "ethereum": "https://eth.blockscout.com",
    "base": "https://base.blockscout.com",
    "optimism": "https://optimism.blockscout.com",
    "arbitrum": "https://arbitrum.blockscout.com",
    "bsc": "https://bsc.blockscout.com",
}


def normalize_chain_name(chain: str | None) -> str:
    if not chain:
        return "ethereum"
    lowered = chain.strip().lower()
    return CHAIN_ALIASES.get(lowered, lowered)


def get_blockscout_base_url(chain: str | None) -> str | None:
    return BLOCKSCOUT_BASE_URLS.get(normalize_chain_name(chain))
