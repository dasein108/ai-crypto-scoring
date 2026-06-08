"""Resolve a token (chain, address, ticker) to a CCXT exchange symbol.

Three layers in order:
    1. Hand-seeded alias registry (`symbol_aliases.py`).
    2. Probe each exchange's `markets` for the first quote currency in
       its priority list.
    3. Perp fallback — same base, settle currency from the spec.

Returns `None` when no listing is found anywhere; caller surfaces that
as a coverage gap.
"""
from __future__ import annotations

from dataclasses import dataclass

from .exchanges import EXCHANGES, ExchangeAdapter, ExchangeSpec, find_spec
from .symbol_aliases import _MISS, get_alias


@dataclass(frozen=True, slots=True)
class SymbolMatch:
    """One resolved (exchange, symbol) listing."""

    exchange_id: str
    symbol: str
    kind: str          # "spot" | "perp"
    base: str
    quote: str
    via: str           # "alias" | "probe" | "perp_fallback"


@dataclass(frozen=True, slots=True)
class ResolveOutcome:
    """Diagnostic-rich result from `resolve_symbol_outcome`.

    Distinguishes a definitive "no listing exists anywhere" miss from a
    transient one ("couldn't reach any exchange this run"). The caller
    needs that distinction to avoid poisoning the negative-cache TTL on
    flaky-network runs.
    """

    match: SymbolMatch | None
    probed: tuple[str, ...]              # exchange ids whose load_markets() returned data
    transient_failures: tuple[str, ...]  # exchange ids whose load_markets() raised
    skipped_by_alias: tuple[str, ...]    # exchange ids the alias registry said "no listing here"

    @property
    def is_definitive_miss(self) -> bool:
        """True iff we successfully probed ≥1 exchange and still found nothing.

        When `probed` is empty (every adapter raised on `load_markets`),
        the absence of a match is a network artefact, not a real miss.
        """
        return self.match is None and len(self.probed) > 0


def _try_alias(spec: ExchangeSpec, chain: str, address: str) -> str | None | object:
    return get_alias(chain, address, spec.id)


def _probe_spot(
    spec: ExchangeSpec, ticker: str, markets: dict[str, dict]
) -> SymbolMatch | None:
    """Walk quote priority for a spot listing matching `ticker`."""
    base = ticker.upper()
    for quote in spec.quote_priority:
        wanted = f"{base}/{quote}"
        m = markets.get(wanted)
        if m and m.get("spot", True) and m.get("active", True):
            return SymbolMatch(
                exchange_id=spec.id, symbol=wanted, kind="spot",
                base=base, quote=quote, via="probe",
            )
    return None


def _probe_perp(
    spec: ExchangeSpec, ticker: str, markets: dict[str, dict]
) -> SymbolMatch | None:
    """Walk perp quote priority for a USDT-settled (or equivalent) perp."""
    if not spec.has_perp or not spec.perp_settle:
        return None
    base = ticker.upper()
    for quote in spec.perp_quote_priority:
        # CCXT canonical perp symbol shape: "BASE/QUOTE:SETTLE".
        wanted = f"{base}/{quote}:{spec.perp_settle}"
        m = markets.get(wanted)
        if m and (m.get("swap") or m.get("contract")) and m.get("active", True):
            return SymbolMatch(
                exchange_id=spec.id, symbol=wanted, kind="perp",
                base=base, quote=quote, via="perp_fallback",
            )
    return None


def resolve_on(
    spec: ExchangeSpec,
    *,
    chain: str,
    address: str,
    ticker: str | None,
    markets: dict[str, dict],
    prefer_perp: bool = False,
) -> SymbolMatch | None:
    """Run the alias→probe→perp pipeline against one exchange's markets.

    `markets` is whatever `adapter.load_markets()` returns. The resolver is
    pure given that input — caller is responsible for actually loading.
    """
    # 1. Alias registry beats everything.
    alias = _try_alias(spec, chain, address)
    if alias is _MISS:
        pass
    elif alias is None:
        # Explicit "not listed here" — skip without further probing.
        return None
    else:
        # Alias points at a concrete symbol; trust it but only if the
        # exchange actually still lists it (defensive — aliases drift).
        symbol = str(alias)
        m = markets.get(symbol)
        if m and m.get("active", True):
            base = str(m.get("base") or symbol.split("/")[0]).upper()
            quote = str(m.get("quote") or (symbol.split("/")[1].split(":")[0])).upper()
            kind = "perp" if (m.get("swap") or m.get("contract")) else "spot"
            return SymbolMatch(
                exchange_id=spec.id, symbol=symbol, kind=kind,
                base=base, quote=quote, via="alias",
            )

    # 2/3. Probe — perp first if explicitly preferred.
    if not ticker:
        return None
    if prefer_perp:
        m = _probe_perp(spec, ticker, markets)
        if m:
            return m
    spot = _probe_spot(spec, ticker, markets)
    if spot:
        return spot
    return _probe_perp(spec, ticker, markets)


def resolve_symbol_outcome(
    chain: str,
    address: str,
    ticker: str | None,
    *,
    adapters: dict[str, ExchangeAdapter] | None = None,
    only_exchange: str | None = None,
    prefer_perp: bool = False,
) -> ResolveOutcome:
    """Same as `resolve_symbol` but returns the full outcome including
    which exchanges responded vs failed. The batch driver uses this to
    decide whether a miss is definitive (write negative cache) or
    transient (don't poison the cache)."""
    if not adapters:
        return ResolveOutcome(match=None, probed=(), transient_failures=(), skipped_by_alias=())

    candidates = [find_spec(only_exchange)] if only_exchange else list(EXCHANGES)
    probed: list[str] = []
    transient: list[str] = []
    skipped: list[str] = []

    for spec in candidates:
        if spec is None:
            continue
        adapter = adapters.get(spec.id)
        if adapter is None:
            # No adapter configured for this exchange — neither a probe nor
            # a transient failure. Quiet skip.
            continue
        alias_value = _try_alias(spec, chain, address)
        # Alias = None: explicit "not listed here". Authoritative answer
        # without touching the network — counts as probed.
        if alias_value is None:
            probed.append(spec.id)
            skipped.append(spec.id)
            continue
        # Alias = concrete symbol: trust it WITHOUT calling load_markets.
        # The full markets payload is multi-MB on some exchanges (Binance
        # /exchangeInfo) and routinely times out on flaky links. Aliases
        # that point at a stale symbol will fail later in fetch_ohlcv with
        # a clear BadSymbol from CCXT — that's fine; the alternative is
        # locking out every alias-resolvable token whenever load_markets
        # times out.
        if alias_value is not _MISS:
            symbol = str(alias_value)
            base, _, quote_part = symbol.partition("/")
            quote = quote_part.split(":")[0] if quote_part else ""
            kind = "perp" if ":" in symbol else "spot"
            probed.append(spec.id)
            return ResolveOutcome(
                match=SymbolMatch(
                    exchange_id=spec.id, symbol=symbol, kind=kind,
                    base=base.upper(), quote=quote.upper(), via="alias",
                ),
                probed=tuple(probed),
                transient_failures=tuple(transient),
                skipped_by_alias=tuple(skipped),
            )

        # No alias — need load_markets to probe.
        try:
            markets = adapter.load_markets()
        except Exception:
            transient.append(spec.id)
            continue
        if not isinstance(markets, dict):
            transient.append(spec.id)
            continue
        probed.append(spec.id)
        match = resolve_on(
            spec,
            chain=chain, address=address, ticker=ticker,
            markets=markets, prefer_perp=prefer_perp,
        )
        if match is not None:
            return ResolveOutcome(
                match=match,
                probed=tuple(probed),
                transient_failures=tuple(transient),
                skipped_by_alias=tuple(skipped),
            )
    return ResolveOutcome(
        match=None,
        probed=tuple(probed),
        transient_failures=tuple(transient),
        skipped_by_alias=tuple(skipped),
    )


def resolve_symbol(
    chain: str,
    address: str,
    ticker: str | None,
    *,
    adapters: dict[str, ExchangeAdapter] | None = None,
    only_exchange: str | None = None,
    prefer_perp: bool = False,
) -> SymbolMatch | None:
    """Backwards-compatible thin wrapper — returns just `.match`.

    Most callers only care about the listing; `resolve_symbol_outcome`
    is for the batch driver / anything that needs to distinguish
    transient from definitive misses.
    """
    outcome = resolve_symbol_outcome(
        chain, address, ticker,
        adapters=adapters, only_exchange=only_exchange, prefer_perp=prefer_perp,
    )
    return outcome.match
