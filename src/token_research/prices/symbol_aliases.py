"""Hand-seeded (chain, token_address) → exchange-symbol overrides.

These trump the auto symbol-probe in `symbols.py` for cases where a
ticker collision or non-standard naming would mislead the resolver.
Top tokens only — long tail relies on the probe path. Regenerate the
larger registry via the future `scripts/refresh_symbols.py` helper.

Address keys are lowercase, exchange ids match `EXCHANGES` in
`exchanges.py`. A `None` value explicitly says "no spot pair on this
exchange" so the resolver skips probing.
"""
from __future__ import annotations

# (chain, address) → {exchange_id: symbol_or_None}
SYMBOL_ALIASES: dict[tuple[str, str], dict[str, str | None]] = {
    # WETH on Ethereum — most exchanges quote ETH/USDT.
    ("ethereum", "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"): {
        "binance": "ETH/USDT",
        "okx": "ETH/USDT",
        "bybit": "ETH/USDT",
        "coinbase": "ETH/USD",
        "kraken": "ETH/USD",
    },
    # WBTC on Ethereum.
    ("ethereum", "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"): {
        "binance": "BTC/USDT",
        "okx": "BTC/USDT",
        "bybit": "BTC/USDT",
        "coinbase": "BTC/USD",
        "kraken": "BTC/USD",
    },
    # USDC on Ethereum — listed on Coinbase + Kraken; Binance pulled USDC pairs.
    ("ethereum", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"): {
        "binance": None,
        "okx": "USDC/USDT",
        "coinbase": "USDC/USD",
        "kraken": "USDC/USD",
    },
    # MORPHO on Ethereum.
    ("ethereum", "0x58d97b57bb95320f9a05dc918aef65434969c2b2"): {
        "binance": "MORPHO/USDT",
        "okx": "MORPHO/USDT",
        "bybit": "MORPHO/USDT",
    },
    # ONDO on Ethereum.
    ("ethereum", "0xfaba6f8e4a5e8ab82f62fe7c39859fa577269be3"): {
        "binance": "ONDO/USDT",
        "okx": "ONDO/USDT",
        "bybit": "ONDO/USDT",
        "coinbase": "ONDO/USD",
    },
    # PENDLE on Ethereum.
    ("ethereum", "0x808507121b80c02388fad14726482e061b8da827"): {
        "binance": "PENDLE/USDT",
        "okx": "PENDLE/USDT",
        "bybit": "PENDLE/USDT",
    },
    # ARB on Arbitrum.
    ("arbitrum", "0x912ce59144191c1204e64559fe8253a0e49e6548"): {
        "binance": "ARB/USDT",
        "okx": "ARB/USDT",
        "bybit": "ARB/USDT",
        "coinbase": "ARB/USD",
    },
    # OP on Optimism.
    ("optimism", "0x4200000000000000000000000000000000000042"): {
        "binance": "OP/USDT",
        "okx": "OP/USDT",
        "bybit": "OP/USDT",
        "coinbase": "OP/USD",
    },
    # AAVE on Ethereum.
    ("ethereum", "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9"): {
        "binance": "AAVE/USDT",
        "okx": "AAVE/USDT",
        "bybit": "AAVE/USDT",
    },
    # UNI on Ethereum.
    ("ethereum", "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984"): {
        "binance": "UNI/USDT",
        "okx": "UNI/USDT",
        "bybit": "UNI/USDT",
    },
    # GMX on Arbitrum.
    ("arbitrum", "0xfc5a1a6eb076a2c7ad06ed22c90d7e710e35ad0a"): {
        "binance": "GMX/USDT",
        "okx": "GMX/USDT",
        "bybit": "GMX/USDT",
    },
    # ENA on Ethereum.
    ("ethereum", "0x57e114b691db790c35207b2e685d4a43181e6061"): {
        "binance": "ENA/USDT",
        "okx": "ENA/USDT",
        "bybit": "ENA/USDT",
    },
    # FET on Ethereum.
    ("ethereum", "0xaea46a60368a7bd060eec7df8cba43b7ef41ad85"): {
        "binance": "FET/USDT",
        "okx": "FET/USDT",
        "bybit": "FET/USDT",
    },
    # LINK on Ethereum.
    ("ethereum", "0x514910771af9ca656af840dff83e8264ecf986ca"): {
        "binance": "LINK/USDT",
        "okx": "LINK/USDT",
        "bybit": "LINK/USDT",
    },
}


def get_alias(chain: str, address: str, exchange_id: str) -> str | None | object:
    """Return the alias entry, or the sentinel `_MISS` when no alias is set.

    Three cases:
        - Alias = symbol str: use it directly.
        - Alias = None: explicit "not listed" — skip the probe.
        - _MISS: no opinion — fall through to the probe.
    """
    record = SYMBOL_ALIASES.get((chain.lower(), address.lower()))
    if record is None:
        return _MISS
    if exchange_id not in record:
        return _MISS
    return record[exchange_id]


_MISS = object()
"""Sentinel for "no alias entry exists for this (chain, address, exchange)"."""
