"""OHLCV fetcher — pages bars off any `ExchangeAdapter`, retries network errors.

Pure-Python; CCXT is only imported by the high-level helpers in `batch.py`
when a real adapter is needed. Tests inject stubs that match the
`ExchangeAdapter` protocol so this layer runs without the optional extra.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from typing import Any

from .exchanges import ExchangeAdapter


# CCXT timeframe → milliseconds. Only the bars we actually use today.
TIMEFRAME_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}

DEFAULT_PAGE_LIMIT = 1000
MAX_RETRIES = 3
BACKOFF_SECONDS = (0.5, 1.5, 4.0)


@dataclass(frozen=True, slots=True)
class Candle:
    """One OHLCV bar (CCXT order: ts_ms, open, high, low, close, volume)."""

    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def date_iso(self) -> str:
        return datetime.fromtimestamp(self.timestamp_ms / 1000, UTC).strftime("%Y-%m-%d")

    @classmethod
    def from_row(cls, row: list[Any]) -> "Candle":
        # CCXT may pack as [ts, open, high, low, close, volume]. Anything
        # malformed becomes a ValueError so the caller's retry / skip logic
        # kicks in cleanly.
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            raise ValueError(f"bad ohlcv row: {row!r}")
        return cls(
            timestamp_ms=int(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]) if len(row) >= 6 and row[5] is not None else 0.0,
        )


@dataclass(frozen=True, slots=True)
class OHLCVRequest:
    exchange_id: str
    symbol: str
    timeframe: str
    since_ms: int
    until_ms: int
    page_limit: int = DEFAULT_PAGE_LIMIT


def _to_ms(d: datetime | str) -> int:
    if isinstance(d, str):
        d = datetime.fromisoformat(d.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp() * 1000)


def _sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def _fetch_page(
    adapter: ExchangeAdapter,
    symbol: str,
    timeframe: str,
    since_ms: int,
    limit: int,
) -> list[list[Any]]:
    """Single CCXT call with retry + exponential backoff."""
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            rows = adapter.fetch_ohlcv(symbol, timeframe=timeframe, since=since_ms, limit=limit)
            return list(rows or [])
        except Exception as exc:
            last_exc = exc
            _sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
    if last_exc:
        raise last_exc
    return []


def fetch_ohlcv(
    adapter: ExchangeAdapter,
    request: OHLCVRequest,
    *,
    rate_limit_sleep: float = 0.0,
) -> list[Candle]:
    """Page bars from `since_ms` up to (but not past) `until_ms`.

    The loop advances `since` past the last bar's timestamp on each page so
    the next call doesn't re-fetch the same bar. We stop early when:

        - the page is empty, OR
        - the last bar's timestamp is >= `until_ms`, OR
        - the page is shorter than `page_limit` (exchange has no more data).
    """
    tf_ms = TIMEFRAME_MS.get(request.timeframe)
    if tf_ms is None:
        raise ValueError(f"Unsupported timeframe: {request.timeframe!r}")

    out: list[Candle] = []
    since = request.since_ms
    seen: set[int] = set()  # guards against exchanges that return overlapping pages

    while since < request.until_ms:
        page = _fetch_page(adapter, request.symbol, request.timeframe, since, request.page_limit)
        if not page:
            break

        for row in page:
            try:
                candle = Candle.from_row(row)
            except ValueError:
                continue
            if candle.timestamp_ms in seen or candle.timestamp_ms >= request.until_ms:
                continue
            if candle.timestamp_ms < request.since_ms:
                continue
            seen.add(candle.timestamp_ms)
            out.append(candle)

        last_ts = int(page[-1][0])
        if last_ts <= since:
            # Exchange returned a page that didn't advance — bail to avoid loop.
            break
        since = last_ts + tf_ms
        if len(page) < request.page_limit:
            break
        _sleep(rate_limit_sleep)

    out.sort(key=lambda c: c.timestamp_ms)
    return out


def candles_to_rows(candles: list[Candle]) -> list[dict[str, Any]]:
    """Project Candle list to the {date_iso, price_usd} cache schema."""
    out: list[dict[str, Any]] = []
    for c in candles:
        out.append({"date_iso": c.date_iso, "price_usd": round(c.close, 8)})
    # Multiple intra-day candles collapse to one row keyed by date_iso —
    # downstream is daily, so we keep the latest close.
    by_date: dict[str, dict[str, Any]] = {}
    for r in out:
        by_date[r["date_iso"]] = r
    return [by_date[k] for k in sorted(by_date)]


# Convenience for callers that want absolute timestamps.
def parse_iso_to_ms(s: str) -> int:
    return _to_ms(s)
