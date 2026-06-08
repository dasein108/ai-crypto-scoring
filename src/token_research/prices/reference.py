"""Reference-asset fetcher — BTC / ETH directly from a CEX, no token address.

Backs `token-research price-history --reference btc`. Writes to
`prices/_reference/<name>.json` so beta computation can read the canonical
BTC series without going through a wrapped-token (WBTC) proxy.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .cache import (
    latest_date,
    load_reference_series,
    merge_rows,
    write_reference_series,
)
from .exchanges import find_spec, make_adapter, seed_market_for_alias
from .ohlcv import (
    DEFAULT_PAGE_LIMIT,
    Candle,
    OHLCVRequest,
    candles_to_rows,
    fetch_ohlcv,
    parse_iso_to_ms,
)


# name → (exchange_id, symbol). Binance USDT pairs are the deepest, so
# default to those. Operators can extend this map for ETH, SOL, etc.
REFERENCE_ASSETS: dict[str, tuple[str, str]] = {
    "btc": ("binance", "BTC/USDT"),
    "eth": ("binance", "ETH/USDT"),
    "sol": ("binance", "SOL/USDT"),
    "avax": ("binance", "AVAX/USDT"),
    "tao": ("binance", "TAO/USDT"),
    "render": ("binance", "RENDER/USDT"),
    "link": ("binance", "LINK/USDT"),
}


def fetch_reference(
    name: str,
    *,
    data_dir: Path,
    since: str,
    until: str,
    timeframe: str = "1d",
    force: bool = False,
    exchange_id_override: str | None = None,
    symbol_override: str | None = None,
    adapter: Any = None,
) -> dict[str, Any]:
    """Fetch a reference asset and write it to the reference cache.

    Returns a small outcome dict suitable for embedding in a CommandResult.
    Raises `KeyError` for unknown names, `MissingExtraError` when the ccxt
    extra is missing.
    """
    key = name.lower()
    if exchange_id_override and symbol_override:
        exchange_id = exchange_id_override
        symbol = symbol_override
    else:
        if key not in REFERENCE_ASSETS:
            raise KeyError(name)
        exchange_id, symbol = REFERENCE_ASSETS[key]

    if adapter is None:
        adapter = make_adapter(exchange_id)  # raises MissingExtraError if no ccxt

    # Pre-seed the single market we want so CCXT's implicit load_markets
    # call inside fetch_ohlcv becomes a no-op. For Binance reference pairs
    # like BTC/USDT the exchange-side id is just "BTCUSDT".
    base, _, quote_part = symbol.partition("/")
    quote = quote_part.split(":")[0] if quote_part else ""
    kind = "perp" if ":" in symbol else "spot"
    if base and quote:
        seed_market_for_alias(
            adapter,
            exchange_id=exchange_id, symbol=symbol,
            base=base.upper(), quote=quote.upper(), kind=kind,
        )

    existing = [] if force else load_reference_series(data_dir, key)
    last = latest_date(existing) if existing else None
    effective_since = since
    if last and last >= since:
        effective_since = last

    spec = find_spec(exchange_id)
    rate_sleep = (spec.rate_limit_ms / 1000.0) if spec else 0.0

    request = OHLCVRequest(
        exchange_id=exchange_id,
        symbol=symbol,
        timeframe=timeframe,
        since_ms=parse_iso_to_ms(effective_since),
        until_ms=parse_iso_to_ms(until),
        page_limit=DEFAULT_PAGE_LIMIT,
    )
    candles: list[Candle] = fetch_ohlcv(adapter, request, rate_limit_sleep=rate_sleep)
    new_rows = candles_to_rows(candles)
    merged = merge_rows(existing, new_rows)

    metadata = {
        "kind": "reference",
        "name": key,
        "exchange": exchange_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "first_date": merged[0]["date_iso"] if merged else None,
        "last_date": merged[-1]["date_iso"] if merged else None,
        "fetched_at": datetime.now(UTC).isoformat(),
        "candle_count": len(merged),
        "this_run_candles": len(candles),
    }
    write_reference_series(data_dir, key, merged, metadata=metadata)

    return {
        "exchange": exchange_id,
        "symbol": symbol,
        "rows_in_cache": len(merged),
        "appended": max(len(merged) - len(existing), 0),
        "first_date": metadata["first_date"],
        "last_date": metadata["last_date"],
    }
