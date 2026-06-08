"""CEX adapter registry — priority order + capability flags.

Adapters are looked up by `id` on the underlying CCXT package; this module
just owns the priority list, the spot/perp policy, and a thin `ExchangeAdapter`
protocol so tests can inject mocks without importing CCXT.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class ExchangeAdapter(Protocol):
    """Minimum surface the OHLCV fetcher uses.

    Real-world implementation is a CCXT exchange instance; tests pass a
    hand-rolled stub matching this shape so they run without CCXT.
    """

    id: str

    def load_markets(self, reload: bool = False) -> dict[str, dict[str, Any]]: ...

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1d",
        since: int | None = None,
        limit: int | None = None,
    ) -> list[list[Any]]: ...


@dataclass(frozen=True, slots=True)
class ExchangeSpec:
    """Static config for one supported exchange."""

    id: str               # CCXT exchange id, e.g. "binance"
    display_name: str
    quote_priority: tuple[str, ...]  # preferred quote ccys, in order
    perp_quote_priority: tuple[str, ...] = ()
    perp_settle: str | None = None   # CCXT settle ccy for perps, e.g. "USDT"
    has_spot: bool = True
    has_perp: bool = True
    rate_limit_ms: int = 200          # min delay between calls (defensive)


# Priority order encodes "where to look first". Binance has the deepest
# history and the broadest listings; OKX picks up tokens Binance has
# delisted; Bybit covers long-tail alts and is strong on perps; Coinbase
# and Kraken add USD pairs and a few US-only listings.
EXCHANGES: tuple[ExchangeSpec, ...] = (
    ExchangeSpec(
        id="binance",
        display_name="Binance",
        quote_priority=("USDT", "BUSD", "USDC", "BTC", "ETH"),
        perp_quote_priority=("USDT",),
        perp_settle="USDT",
        rate_limit_ms=120,
    ),
    ExchangeSpec(
        id="okx",
        display_name="OKX",
        quote_priority=("USDT", "USDC", "USD"),
        perp_quote_priority=("USDT", "USD"),
        perp_settle="USDT",
        rate_limit_ms=120,
    ),
    ExchangeSpec(
        id="bybit",
        display_name="Bybit",
        quote_priority=("USDT", "USDC"),
        perp_quote_priority=("USDT", "USDC"),
        perp_settle="USDT",
        rate_limit_ms=120,
    ),
    ExchangeSpec(
        id="coinbase",
        display_name="Coinbase",
        quote_priority=("USD", "USDC", "USDT"),
        has_perp=False,
        rate_limit_ms=350,
    ),
    ExchangeSpec(
        id="kraken",
        display_name="Kraken",
        quote_priority=("USD", "USDT", "EUR"),
        has_perp=False,
        rate_limit_ms=350,
    ),
)


def find_spec(exchange_id: str) -> ExchangeSpec | None:
    needle = exchange_id.lower().strip()
    for spec in EXCHANGES:
        if spec.id == needle:
            return spec
    return None


def make_adapter(exchange_id: str) -> ExchangeAdapter:
    """Construct a CCXT adapter for `exchange_id`. Raises MissingExtraError
    when the optional extra isn't installed."""
    try:
        import ccxt  # type: ignore[import-not-found]
    except ImportError as exc:
        from .exceptions import MissingExtraError

        raise MissingExtraError() from exc

    cls = getattr(ccxt, exchange_id, None)
    if cls is None:
        raise ValueError(f"Unknown CCXT exchange id: {exchange_id!r}")
    # Binance /exchangeInfo is multi-MB and routinely takes 15-30s on slow
    # links. CCXT calls it implicitly inside fetch_ohlcv via load_markets,
    # so a stingy timeout here cascades into the alias path failing despite
    # a known-good symbol. 60s is generous but bounded.
    return cls({"enableRateLimit": True, "timeout": 60_000})


# Per-exchange exchange-side `id` format. Used by `seed_market_for_alias` to
# pre-populate `adapter.markets` so CCXT's automatic load_markets() inside
# fetch_ohlcv becomes a no-op. When an exchange isn't here, the alias path
# still works but pays the load_markets cost on the first call.
_MARKET_ID_FORMAT: dict[str, str] = {
    "binance": "{base}{quote}",
    "bybit": "{base}{quote}",
    "okx": "{base}-{quote}",
    "coinbase": "{base}-{quote}",
    "kraken": "{base}{quote}",
}


def seed_market_for_alias(
    adapter: ExchangeAdapter,
    *,
    exchange_id: str,
    symbol: str,
    base: str,
    quote: str,
    kind: str,
) -> bool:
    """Inject a single-market dict into `adapter.markets` so CCXT skips
    its implicit `load_markets()` call. Returns True when the seed was
    applied, False when we don't know the exchange's id format and the
    caller should fall back to the slow path."""
    fmt = _MARKET_ID_FORMAT.get(exchange_id)
    if not fmt:
        return False
    market_id = fmt.format(base=base, quote=quote)
    market = {
        "id": market_id,
        "symbol": symbol,
        "base": base,
        "quote": quote,
        "baseId": base,
        "quoteId": quote,
        "type": kind,
        "spot": kind == "spot",
        "swap": kind == "perp",
        "future": False,
        "option": False,
        "active": True,
        "contract": kind == "perp",
        "linear": kind == "perp",
        "inverse": False,
        "settle": quote if kind == "perp" else None,
        "settleId": quote if kind == "perp" else None,
        "info": {},
        "precision": {"price": 8, "amount": 8},
        "limits": {"amount": {"min": None, "max": None},
                   "price": {"min": None, "max": None},
                   "cost": {"min": None, "max": None}},
    }
    try:
        # CCXT exposes both `markets` and `markets_by_id`; population of
        # both makes downstream lookups (`exchange.market(symbol)`) succeed.
        adapter.markets = {symbol: market}  # type: ignore[attr-defined]
        adapter.markets_by_id = {market_id: [market]}  # type: ignore[attr-defined]
        adapter.symbols = [symbol]  # type: ignore[attr-defined]
        adapter.ids = [market_id]  # type: ignore[attr-defined]
    except Exception:
        return False
    return True
