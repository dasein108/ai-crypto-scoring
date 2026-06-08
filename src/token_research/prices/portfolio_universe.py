"""Curated universe for the bullish-hedged portfolio book.

All members trade on Bybit USDT perpetuals (verified against the public
markets dump in 2026-04). Each entry carries:

    - `name`        — display ticker
    - `category`    — portfolio bucket the optimizer enforces diversification on
    - `bybit_symbol`— CCXT canonical perp symbol (`BASE/USDT:USDT`)
    - `cache_key`   — short name used under `prices/_reference/` for any
                      asset without a meaningful EVM contract (SOL, AVAX,
                      TAO, …). EVM-native tokens fall through to the
                      per-token cache via `chain` + `address`.
    - `chain`/`address` — EVM identity when meaningful, else None

Resolving where the price-history lives is the job of `series_path_for`.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UniverseAsset:
    name: str
    category: str
    bybit_symbol: str
    cache_key: str
    chain: str | None = None
    address: str | None = None
    notes: str = ""


# Categories the optimizer enforces diversification on. The build command
# uses min-categories ≥ 3 by default; the CLI flag `--min-categories`
# overrides. "Hedge" is the BTC-perp slot — never selected as a long.
CATEGORIES: tuple[str, ...] = ("L1", "L2", "DeFi", "RWA", "AI", "Infra", "Hedge")


# Long candidates — institutional-grade tokens with deep Bybit USDT-perp
# liquidity. Memes intentionally excluded per book mandate. Keep the
# universe at ~16 names so a 14-name optimizer has room to drop 1–2 on
# coverage gaps and still hit the 10–15 minimum.
UNIVERSE: tuple[UniverseAsset, ...] = (
    # ── L1 ──
    UniverseAsset(name="ETH", category="L1",
                  bybit_symbol="ETH/USDT:USDT", cache_key="eth",
                  chain="ethereum",
                  address="0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
                  notes="WETH on Ethereum"),
    UniverseAsset(name="SOL", category="L1",
                  bybit_symbol="SOL/USDT:USDT", cache_key="sol",
                  notes="Solana — non-EVM, reference cache"),
    UniverseAsset(name="AVAX", category="L1",
                  bybit_symbol="AVAX/USDT:USDT", cache_key="avax",
                  notes="Avalanche C-chain — reference cache"),
    # ── L2 ──
    UniverseAsset(name="ARB", category="L2",
                  bybit_symbol="ARB/USDT:USDT", cache_key="arb",
                  chain="arbitrum",
                  address="0x912ce59144191c1204e64559fe8253a0e49e6548"),
    UniverseAsset(name="OP", category="L2",
                  bybit_symbol="OP/USDT:USDT", cache_key="op",
                  chain="optimism",
                  address="0x4200000000000000000000000000000000000042"),
    # ── DeFi ──
    UniverseAsset(name="AAVE", category="DeFi",
                  bybit_symbol="AAVE/USDT:USDT", cache_key="aave",
                  chain="ethereum",
                  address="0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9"),
    UniverseAsset(name="UNI", category="DeFi",
                  bybit_symbol="UNI/USDT:USDT", cache_key="uni",
                  chain="ethereum",
                  address="0x1f9840a85d5af5bf1d1762f925bdaddc4201f984"),
    UniverseAsset(name="MORPHO", category="DeFi",
                  bybit_symbol="MORPHO/USDT:USDT", cache_key="morpho",
                  chain="ethereum",
                  address="0x58d97b57bb95320f9a05dc918aef65434969c2b2"),
    UniverseAsset(name="PENDLE", category="DeFi",
                  bybit_symbol="PENDLE/USDT:USDT", cache_key="pendle",
                  chain="ethereum",
                  address="0x808507121b80c02388fad14726482e061b8da827"),
    UniverseAsset(name="GMX", category="DeFi",
                  bybit_symbol="GMX/USDT:USDT", cache_key="gmx",
                  chain="arbitrum",
                  address="0xfc5a1a6eb076a2c7ad06ed22c90d7e710e35ad0a"),
    # ── RWA / yield ──
    UniverseAsset(name="ONDO", category="RWA",
                  bybit_symbol="ONDO/USDT:USDT", cache_key="ondo",
                  chain="ethereum",
                  address="0xfaba6f8e4a5e8ab82f62fe7c39859fa577269be3"),
    UniverseAsset(name="ENA", category="RWA",
                  bybit_symbol="ENA/USDT:USDT", cache_key="ena",
                  chain="ethereum",
                  address="0x57e114b691db790c35207b2e685d4a43181e6061"),
    # ── AI ──
    UniverseAsset(name="FET", category="AI",
                  bybit_symbol="FET/USDT:USDT", cache_key="fet",
                  chain="ethereum",
                  address="0xaea46a60368a7bd060eec7df8cba43b7ef41ad85"),
    UniverseAsset(name="RENDER", category="AI",
                  bybit_symbol="RENDER/USDT:USDT", cache_key="render",
                  notes="Migrated to SPL — reference cache via Bybit"),
    UniverseAsset(name="TAO", category="AI",
                  bybit_symbol="TAO/USDT:USDT", cache_key="tao",
                  notes="Bittensor — non-EVM"),
    # ── Infra ──
    UniverseAsset(name="LINK", category="Infra",
                  bybit_symbol="LINK/USDT:USDT", cache_key="link",
                  chain="ethereum",
                  address="0x514910771af9ca656af840dff83e8264ecf986ca"),
)


# The macro hedge — never selected as a long position. Stored separately
# so the build command can size it deterministically.
HEDGE_ASSET = UniverseAsset(
    name="BTC",
    category="Hedge",
    bybit_symbol="BTC/USDT:USDT",
    cache_key="btc",
    notes="Macro hedge via short Bybit BTC perpetual",
)


def load_asset_series(data_dir, asset: UniverseAsset) -> list[dict]:
    """Resolve where this asset's price-history lives and return the rows.

    EVM tokens with `chain` + `address` are looked up in the per-token cache
    (`prices/<chain>-<addr>.json`) so we share data with `momentum-backtest`.
    Non-EVM tokens (SOL, AVAX, TAO, RENDER) live in the reference cache.

    Returns an empty list when nothing has been fetched yet — caller emits
    coverage gap.
    """
    from .cache import load_reference_series, load_series

    if asset.chain and asset.address:
        rows = load_series(data_dir, asset.chain, asset.address)
        if rows:
            return rows
    return load_reference_series(data_dir, asset.cache_key)
