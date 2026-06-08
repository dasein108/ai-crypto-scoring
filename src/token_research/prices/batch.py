"""Batch driver — resolve + fetch + cache for many tokens at once.

Used by the `price-history` CLI command. Sequential by default; opt into
threaded parallelism via `max_parallel > 1`. CCXT adapters are loaded
lazily so the import path stays clean when the extra is missing.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .cache import (
    latest_date,
    load_metadata,
    load_series,
    merge_rows,
    negative_cache_active,
    write_negative_cache,
    write_series,
)
from .exceptions import MissingExtraError, PriceHistoryError
from .exchanges import EXCHANGES, ExchangeAdapter, find_spec, make_adapter, seed_market_for_alias
from .ohlcv import (
    DEFAULT_PAGE_LIMIT,
    Candle,
    OHLCVRequest,
    candles_to_rows,
    fetch_ohlcv,
    parse_iso_to_ms,
)
from .symbols import SymbolMatch, resolve_symbol_outcome


@dataclass(frozen=True, slots=True)
class BatchSpec:
    chain: str
    address: str
    ticker: str | None = None
    only_exchange: str | None = None
    prefer_perp: bool = False


@dataclass(frozen=True, slots=True)
class BatchFailure:
    spec: BatchSpec
    reason: str


@dataclass(frozen=True, slots=True)
class BatchOutcome:
    spec: BatchSpec
    cached: bool
    skipped: bool
    failure: BatchFailure | None
    match: SymbolMatch | None
    candles_appended: int


@dataclass(frozen=True, slots=True)
class BatchReport:
    requested: int
    cached: int
    skipped_existing: int
    failed: list[BatchFailure] = field(default_factory=list)
    outcomes: list[BatchOutcome] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "cached": self.cached,
            "skipped_existing": self.skipped_existing,
            "failed": [{"spec": _spec_dict(f.spec), "reason": f.reason} for f in self.failed],
            "outcomes": [
                {
                    "spec": _spec_dict(o.spec),
                    "cached": o.cached,
                    "skipped": o.skipped,
                    "candles_appended": o.candles_appended,
                    "match": _match_dict(o.match) if o.match else None,
                    "failure_reason": o.failure.reason if o.failure else None,
                }
                for o in self.outcomes
            ],
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


def _spec_dict(s: BatchSpec) -> dict[str, Any]:
    return {
        "chain": s.chain, "address": s.address, "ticker": s.ticker,
        "only_exchange": s.only_exchange, "prefer_perp": s.prefer_perp,
    }


def _match_dict(m: SymbolMatch) -> dict[str, Any]:
    return {
        "exchange": m.exchange_id, "symbol": m.symbol, "kind": m.kind,
        "base": m.base, "quote": m.quote, "via": m.via,
    }


# ---------------------------------------------------------------------------
# Adapter factory
# ---------------------------------------------------------------------------


AdapterFactory = Callable[[str], ExchangeAdapter]


def _default_adapter_factory(exchange_id: str) -> ExchangeAdapter:
    return make_adapter(exchange_id)


def _build_adapter_pool(
    *,
    factory: AdapterFactory,
    exchange_ids: list[str] | None = None,
) -> dict[str, ExchangeAdapter]:
    """Eagerly build an adapter for each exchange we plan to query.

    `make_adapter` raises `MissingExtraError` when CCXT is absent — the
    factory pattern lets tests inject stubs and skip CCXT entirely.
    """
    target_ids = exchange_ids or [s.id for s in EXCHANGES]
    pool: dict[str, ExchangeAdapter] = {}
    for eid in target_ids:
        try:
            pool[eid] = factory(eid)
        except MissingExtraError:
            raise
        except Exception:
            # Non-MissingExtra failures (auth, network at construction
            # time) just mean this exchange is unavailable for this run.
            continue
    return pool


# ---------------------------------------------------------------------------
# Single-spec worker
# ---------------------------------------------------------------------------


def fetch_one(
    spec: BatchSpec,
    *,
    data_dir: Path,
    since: str,
    until: str,
    timeframe: str,
    adapters: dict[str, ExchangeAdapter],
    force: bool,
) -> BatchOutcome:
    """Resolve symbol, page bars, write cache. Pure given the inputs."""
    chain = spec.chain.lower()
    address = spec.address.lower()

    # Negative cache short-circuit (skip when "no listing" sidecar still TTL-fresh).
    if not force:
        meta = load_metadata(data_dir, chain, address)
        if negative_cache_active(meta):
            return BatchOutcome(
                spec=spec, cached=False, skipped=True,
                failure=BatchFailure(spec=spec, reason="negative_cache_active"),
                match=None, candles_appended=0,
            )

    outcome = resolve_symbol_outcome(
        chain, address, spec.ticker,
        adapters=adapters,
        only_exchange=spec.only_exchange,
        prefer_perp=spec.prefer_perp,
    )
    match = outcome.match
    if match is None:
        # Only poison the negative-cache TTL when we authoritatively
        # confirmed the absence — i.e. at least one exchange's market list
        # was successfully loaded and didn't contain the token. A run where
        # every adapter raised a network error must NOT lock the token out
        # for the next 30 days.
        if outcome.is_definitive_miss:
            write_negative_cache(data_dir, chain, address, reason="no_listing_found")
            reason = "no_listing_found"
        else:
            reason = (
                f"transient_failure: probed={list(outcome.probed)} "
                f"transient={list(outcome.transient_failures)}"
            )
        return BatchOutcome(
            spec=spec, cached=False, skipped=False,
            failure=BatchFailure(spec=spec, reason=reason),
            match=None, candles_appended=0,
        )

    adapter = adapters.get(match.exchange_id)
    if adapter is None:
        return BatchOutcome(
            spec=spec, cached=False, skipped=False,
            failure=BatchFailure(spec=spec, reason="resolved exchange has no adapter loaded"),
            match=match, candles_appended=0,
        )

    # Incremental: pick up from cache's last bar unless --force.
    existing = [] if force else load_series(data_dir, chain, address)
    last = latest_date(existing) if existing else None
    effective_since = since
    if last and last >= since:
        # +1 day to avoid re-fetching the last cached bar.
        effective_since = (datetime.fromisoformat(last + "T00:00:00+00:00")
                           ).strftime("%Y-%m-%d")

    spec_obj = find_spec(match.exchange_id)
    rate_sleep = (spec_obj.rate_limit_ms / 1000.0) if spec_obj else 0.0
    # When the symbol came via the alias registry we already know the
    # exchange-side market id deterministically. Seeding it sidesteps
    # CCXT's implicit load_markets() call inside fetch_ohlcv (Binance's
    # /exchangeInfo is multi-MB and a frequent timeout culprit on flaky
    # links). For probe-resolved symbols the markets dict was already
    # populated by load_markets, so seeding is a no-op.
    if match.via == "alias":
        seed_market_for_alias(
            adapter,
            exchange_id=match.exchange_id, symbol=match.symbol,
            base=match.base, quote=match.quote, kind=match.kind,
        )
    try:
        request = OHLCVRequest(
            exchange_id=match.exchange_id,
            symbol=match.symbol,
            timeframe=timeframe,
            since_ms=parse_iso_to_ms(effective_since),
            until_ms=parse_iso_to_ms(until),
            page_limit=DEFAULT_PAGE_LIMIT,
        )
        candles = fetch_ohlcv(adapter, request, rate_limit_sleep=rate_sleep)
    except PriceHistoryError:
        raise
    except Exception as exc:
        return BatchOutcome(
            spec=spec, cached=False, skipped=False,
            failure=BatchFailure(spec=spec, reason=f"fetch_failed: {exc}"),
            match=match, candles_appended=0,
        )

    new_rows = candles_to_rows(candles)
    merged = merge_rows(existing, new_rows)
    metadata = _build_metadata(match, timeframe, merged, candles)
    write_series(data_dir, chain, address, merged, metadata=metadata)

    appended = len(merged) - len(existing)
    return BatchOutcome(
        spec=spec, cached=appended > 0, skipped=appended == 0,
        failure=None, match=match, candles_appended=max(appended, 0),
    )


def _build_metadata(
    match: SymbolMatch,
    timeframe: str,
    rows: list[dict[str, Any]],
    candles: list[Candle],
) -> dict[str, Any]:
    first = rows[0]["date_iso"] if rows else None
    last = rows[-1]["date_iso"] if rows else None
    return {
        "kind": match.kind,
        "exchange": match.exchange_id,
        "symbol": match.symbol,
        "base": match.base,
        "quote": match.quote,
        "via": match.via,
        "timeframe": timeframe,
        "first_date": first,
        "last_date": last,
        "fetched_at": datetime.now(UTC).isoformat(),
        "candle_count": len(rows),
        "this_run_candles": len(candles),
    }


# ---------------------------------------------------------------------------
# Public driver
# ---------------------------------------------------------------------------


def fetch_universe(
    specs: list[BatchSpec],
    *,
    data_dir: Path,
    since: str,
    until: str,
    timeframe: str = "1d",
    max_parallel: int = 1,
    force: bool = False,
    adapter_factory: AdapterFactory | None = None,
    adapters: dict[str, ExchangeAdapter] | None = None,
) -> BatchReport:
    """Resolve and fetch many tokens. Tests pass `adapters=` directly to
    skip CCXT; production passes nothing and the default factory builds
    real CCXT adapters lazily.

    `max_parallel` is capped at 10 to stay safe with exchange rate limits.
    """
    started = time.monotonic()
    if max_parallel < 1:
        max_parallel = 1
    if max_parallel > 10:
        max_parallel = 10

    if adapters is None:
        factory = adapter_factory or _default_adapter_factory
        # Build only the exchanges we might actually need — if every spec
        # pins one exchange, don't construct the rest.
        forced = sorted({s.only_exchange for s in specs if s.only_exchange})
        target_ids: list[str] | None = list(forced) if forced else None
        adapters = _build_adapter_pool(factory=factory, exchange_ids=target_ids)

    outcomes: list[BatchOutcome] = []
    failures: list[BatchFailure] = []
    cached = 0
    skipped = 0

    def _work(spec: BatchSpec) -> BatchOutcome:
        return fetch_one(
            spec,
            data_dir=data_dir, since=since, until=until,
            timeframe=timeframe, adapters=adapters, force=force,
        )

    if max_parallel == 1:
        results = [_work(s) for s in specs]
    else:
        results = []
        with ThreadPoolExecutor(max_workers=max_parallel) as pool:
            futures = {pool.submit(_work, s): s for s in specs}
            for fut in as_completed(futures):
                results.append(fut.result())

    for outcome in results:
        outcomes.append(outcome)
        if outcome.cached:
            cached += 1
        elif outcome.skipped:
            skipped += 1
        if outcome.failure:
            failures.append(outcome.failure)

    return BatchReport(
        requested=len(specs),
        cached=cached,
        skipped_existing=skipped,
        failed=failures,
        outcomes=outcomes,
        elapsed_seconds=time.monotonic() - started,
    )
