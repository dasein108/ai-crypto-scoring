"""Historical OHLCV price-history fetcher (CCXT-backed, opt-in extra).

Self-contained module that resolves an EVM token to a CEX listing, pages
OHLCV bars off the chosen exchange via CCXT, and writes them into the
`prices/` cache that `momentum-backtest` consumes. CCXT is an optional
extra (`pip install -e ".[ccxt]"`) so the rest of the pipeline keeps
working without it.

Public surface:
    fetch_ohlcv         — single (exchange, symbol) → list[Candle]
    fetch_universe      — batch driver, fills the cache for many tokens
    resolve_symbol      — token-address → exchange-symbol mapping
    load_series         — cache reader (used by momentum-backtest)
    write_series        — cache writer
    cache_path          — canonical cache file path
    EXCHANGES           — priority-ordered list of supported CEX adapters
"""
from __future__ import annotations

from .batch import BatchFailure, BatchReport, BatchSpec, fetch_universe
from .beta import (
    DEFAULT_BTC_REFERENCE,
    DEFAULT_MIN_OBSERVATIONS,
    DEFAULT_WINDOW_DAYS,
    BetaResult,
    compute_beta,
)
from .cache import (
    NEGATIVE_CACHE_TTL_DAYS,
    cache_path,
    load_metadata,
    load_reference_metadata,
    load_reference_series,
    load_series,
    reference_path,
    write_negative_cache,
    write_reference_series,
    write_series,
)
from .exceptions import BadSymbolError, MissingExtraError, PriceHistoryError
from .exchanges import EXCHANGES, ExchangeSpec
from .ohlcv import Candle, OHLCVRequest, fetch_ohlcv
from .symbols import ResolveOutcome, SymbolMatch, resolve_symbol, resolve_symbol_outcome

__all__ = [
    "BatchFailure",
    "BatchReport",
    "BatchSpec",
    "BadSymbolError",
    "BetaResult",
    "Candle",
    "DEFAULT_BTC_REFERENCE",
    "DEFAULT_MIN_OBSERVATIONS",
    "DEFAULT_WINDOW_DAYS",
    "compute_beta",
    "load_reference_metadata",
    "load_reference_series",
    "reference_path",
    "write_reference_series",
    "EXCHANGES",
    "ExchangeSpec",
    "MissingExtraError",
    "NEGATIVE_CACHE_TTL_DAYS",
    "OHLCVRequest",
    "PriceHistoryError",
    "ResolveOutcome",
    "SymbolMatch",
    "cache_path",
    "fetch_ohlcv",
    "fetch_universe",
    "load_metadata",
    "load_series",
    "resolve_symbol",
    "resolve_symbol_outcome",
    "write_negative_cache",
    "write_series",
]
