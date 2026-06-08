"""Tests for the prices/ module — symbol resolution, OHLCV paging, cache,
batch driver. CCXT is never imported: tests inject a stub adapter.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from token_research.prices import (
    BatchSpec,
    Candle,
    OHLCVRequest,
    SymbolMatch,
    cache_path,
    fetch_ohlcv,
    fetch_universe,
    load_metadata,
    load_series,
    resolve_symbol,
    write_negative_cache,
    write_series,
)
from token_research.prices.cache import (
    NEGATIVE_CACHE_TTL_DAYS,
    merge_rows,
    negative_cache_active,
)
from token_research.prices.exceptions import MissingExtraError
from token_research.prices.ohlcv import candles_to_rows


# ---------------------------------------------------------------------------
# Stub adapter
# ---------------------------------------------------------------------------


class StubAdapter:
    """Minimal ExchangeAdapter shape — feed pre-built markets + OHLCV pages."""

    def __init__(
        self,
        exchange_id: str,
        markets: dict[str, dict],
        pages: list[list[list]] | None = None,
        raise_on_load: bool = False,
    ):
        self.id = exchange_id
        self._markets = markets
        self._pages = pages or []
        self._raise_on_load = raise_on_load
        self.fetch_calls: list[tuple] = []

    def load_markets(self, reload: bool = False):
        if self._raise_on_load:
            raise RuntimeError("simulated load_markets failure")
        return self._markets

    def fetch_ohlcv(self, symbol, timeframe="1d", since=None, limit=None):
        self.fetch_calls.append((symbol, timeframe, since, limit))
        if not self._pages:
            return []
        return self._pages.pop(0)


# ---------------------------------------------------------------------------
# Symbol resolution
# ---------------------------------------------------------------------------


def test_resolve_uses_alias_when_present():
    eth_addr = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
    binance = StubAdapter("binance", {
        "ETH/USDT": {"base": "ETH", "quote": "USDT", "spot": True, "active": True},
    })
    match = resolve_symbol("ethereum", eth_addr, "WETH",
                           adapters={"binance": binance})
    assert match is not None
    assert match.exchange_id == "binance"
    assert match.symbol == "ETH/USDT"
    assert match.via == "alias"


def test_resolve_alias_none_skips_exchange():
    """USDC has alias=None for Binance — resolver must skip Binance."""
    usdc = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    binance = StubAdapter("binance", {
        "USDC/USDT": {"base": "USDC", "quote": "USDT", "spot": True, "active": True},
    })
    coinbase = StubAdapter("coinbase", {
        "USDC/USD": {"base": "USDC", "quote": "USD", "spot": True, "active": True},
    })
    match = resolve_symbol("ethereum", usdc, "USDC",
                           adapters={"binance": binance, "coinbase": coinbase})
    assert match is not None
    assert match.exchange_id == "coinbase"
    assert match.via == "alias"


def test_resolve_falls_back_to_probe_for_unknown_token():
    binance = StubAdapter("binance", {
        "FOO/USDT": {"base": "FOO", "quote": "USDT", "spot": True, "active": True},
    })
    match = resolve_symbol("ethereum", "0xdead", "FOO",
                           adapters={"binance": binance})
    assert match is not None
    assert match.symbol == "FOO/USDT"
    assert match.via == "probe"
    assert match.kind == "spot"


def test_resolve_falls_back_to_perp_when_no_spot():
    binance = StubAdapter("binance", {
        "FOO/USDT:USDT": {"base": "FOO", "quote": "USDT", "swap": True, "active": True},
    })
    match = resolve_symbol("ethereum", "0xdead", "FOO",
                           adapters={"binance": binance})
    assert match is not None
    assert match.kind == "perp"
    assert match.via == "perp_fallback"


def test_resolve_returns_none_when_no_listing():
    binance = StubAdapter("binance", {})
    match = resolve_symbol("ethereum", "0xdead", "OBSCURE",
                           adapters={"binance": binance})
    assert match is None


def test_resolve_only_exchange_skips_others():
    binance = StubAdapter("binance", {
        "FOO/USDT": {"base": "FOO", "quote": "USDT", "spot": True, "active": True},
    })
    okx = StubAdapter("okx", {
        "FOO/USDT": {"base": "FOO", "quote": "USDT", "spot": True, "active": True},
    })
    match = resolve_symbol("ethereum", "0xdead", "FOO",
                           adapters={"binance": binance, "okx": okx},
                           only_exchange="okx")
    assert match is not None
    assert match.exchange_id == "okx"


def test_resolve_load_markets_failure_does_not_crash():
    bad = StubAdapter("binance", {}, raise_on_load=True)
    good = StubAdapter("okx", {
        "FOO/USDT": {"base": "FOO", "quote": "USDT", "spot": True, "active": True},
    })
    match = resolve_symbol("ethereum", "0xdead", "FOO",
                           adapters={"binance": bad, "okx": good})
    assert match is not None
    assert match.exchange_id == "okx"


def test_resolve_outcome_marks_transient_vs_definitive():
    """resolve_symbol_outcome separates "no listing exists" from "couldn't reach exchange"."""
    from token_research.prices import resolve_symbol_outcome

    # Case A: every adapter raises → transient miss, NOT definitive.
    flaky = StubAdapter("binance", {}, raise_on_load=True)
    a = resolve_symbol_outcome(
        "ethereum", "0xdead", "FOO", adapters={"binance": flaky},
    )
    assert a.match is None
    assert a.probed == ()
    assert a.transient_failures == ("binance",)
    assert a.is_definitive_miss is False

    # Case B: adapter loads markets but token isn't listed → definitive miss.
    empty = StubAdapter("binance", markets={
        "OTHER/USDT": {"base": "OTHER", "quote": "USDT", "spot": True, "active": True},
    })
    b = resolve_symbol_outcome(
        "ethereum", "0xdead", "FOO", adapters={"binance": empty},
    )
    assert b.match is None
    assert b.probed == ("binance",)
    assert b.transient_failures == ()
    assert b.is_definitive_miss is True


def test_batch_does_not_poison_cache_on_transient_failure(tmp_path):
    """Bug regression: timeout on every exchange must NOT write a 30-day negative cache."""
    flaky = StubAdapter("binance", {}, raise_on_load=True)
    specs = [BatchSpec("ethereum", "0xdead", ticker="FOO", only_exchange="binance")]
    report = fetch_universe(
        specs, data_dir=tmp_path, since="2026-01-01", until="2026-01-05",
        adapters={"binance": flaky},
    )
    assert report.cached == 0
    assert len(report.failed) == 1
    assert "transient_failure" in report.failed[0].reason
    # Critical: NO negative-cache sidecar should have been written.
    meta = load_metadata(tmp_path, "ethereum", "0xdead")
    assert meta is None, f"transient failure poisoned the cache: {meta!r}"


def test_resolve_alias_bypasses_load_markets():
    """Reliability: an alias must resolve even when load_markets is broken."""
    from token_research.prices import resolve_symbol_outcome

    eth_addr = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
    flaky_binance = StubAdapter("binance", {}, raise_on_load=True)
    outcome = resolve_symbol_outcome(
        "ethereum", eth_addr, "WETH",
        adapters={"binance": flaky_binance},
        only_exchange="binance",
    )
    assert outcome.match is not None
    assert outcome.match.symbol == "ETH/USDT"
    assert outcome.match.via == "alias"
    # Critically: load_markets should NOT have been called.
    assert flaky_binance.fetch_calls == []


def test_resolve_outcome_alias_none_counts_as_probed():
    """An alias that says 'not listed here' is authoritative — counts as probed."""
    from token_research.prices import resolve_symbol_outcome

    # USDC has alias=None for Binance.
    usdc = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    flaky_binance = StubAdapter("binance", {}, raise_on_load=True)
    outcome = resolve_symbol_outcome(
        "ethereum", usdc, "USDC",
        adapters={"binance": flaky_binance},
        only_exchange="binance",
    )
    # Even though Binance can't load_markets, the alias=None tells us
    # authoritatively USDC isn't on Binance — that's a definitive answer.
    assert "binance" in outcome.probed
    assert "binance" in outcome.skipped_by_alias
    assert outcome.is_definitive_miss is True


# ---------------------------------------------------------------------------
# OHLCV paging
# ---------------------------------------------------------------------------


def _day_ms(d: datetime) -> int:
    return int(d.replace(tzinfo=UTC).timestamp() * 1000)


def _make_candles(start: datetime, count: int, base_price: float = 100.0) -> list[list]:
    out = []
    for i in range(count):
        ts = _day_ms(start + timedelta(days=i))
        price = base_price + i
        out.append([ts, price, price + 1, price - 1, price + 0.5, 1000.0])
    return out


def test_fetch_ohlcv_pages_until_limit():
    """Two pages of 3 bars each → 6 deduped, sorted candles."""
    start = datetime(2026, 1, 1)
    page1 = _make_candles(start, 3)
    page2 = _make_candles(start + timedelta(days=3), 3)
    adapter = StubAdapter("binance", {}, pages=[page1, page2, []])
    request = OHLCVRequest(
        exchange_id="binance",
        symbol="ETH/USDT", timeframe="1d",
        since_ms=_day_ms(start),
        until_ms=_day_ms(start + timedelta(days=10)),
        page_limit=3,
    )
    candles = fetch_ohlcv(adapter, request)
    assert len(candles) == 6
    assert candles[0].timestamp_ms < candles[-1].timestamp_ms


def test_fetch_ohlcv_dedupe_overlapping_pages():
    start = datetime(2026, 1, 1)
    page1 = _make_candles(start, 3)
    page2 = _make_candles(start + timedelta(days=2), 3)  # 1 day overlap
    adapter = StubAdapter("binance", {}, pages=[page1, page2, []])
    request = OHLCVRequest(
        exchange_id="binance", symbol="ETH/USDT", timeframe="1d",
        since_ms=_day_ms(start),
        until_ms=_day_ms(start + timedelta(days=10)),
        page_limit=3,
    )
    candles = fetch_ohlcv(adapter, request)
    timestamps = [c.timestamp_ms for c in candles]
    assert len(timestamps) == len(set(timestamps))


def test_fetch_ohlcv_stops_at_until():
    start = datetime(2026, 1, 1)
    page = _make_candles(start, 10)
    adapter = StubAdapter("binance", {}, pages=[page])
    request = OHLCVRequest(
        exchange_id="binance", symbol="ETH/USDT", timeframe="1d",
        since_ms=_day_ms(start),
        until_ms=_day_ms(start + timedelta(days=5)),
        page_limit=10,
    )
    candles = fetch_ohlcv(adapter, request)
    # Bars from days 0..4 inclusive (5 of them) — day 5 is at `until_ms` exactly.
    assert len(candles) == 5


def test_fetch_ohlcv_handles_empty_response():
    start = datetime(2026, 1, 1)
    adapter = StubAdapter("binance", {}, pages=[[]])
    request = OHLCVRequest(
        exchange_id="binance", symbol="X/USDT", timeframe="1d",
        since_ms=_day_ms(start),
        until_ms=_day_ms(start + timedelta(days=5)),
        page_limit=100,
    )
    assert fetch_ohlcv(adapter, request) == []


def test_candle_from_row_validates():
    with pytest.raises(ValueError):
        Candle.from_row([])
    c = Candle.from_row([1_700_000_000_000, 1, 2, 0.5, 1.5, 10])
    assert c.close == 1.5
    assert c.date_iso  # populated


def test_candles_to_rows_collapses_intra_day():
    start = datetime(2026, 1, 1)
    candles = [
        Candle.from_row(_make_candles(start, 1)[0]),
        Candle.from_row([_day_ms(start) + 3_600_000, 1, 1, 1, 999.0, 0]),  # same day
    ]
    rows = candles_to_rows(candles)
    assert len(rows) == 1
    assert rows[0]["price_usd"] == 999.0  # last close wins


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_cache_round_trip(tmp_path):
    rows = [
        {"date_iso": "2026-01-01", "price_usd": 100.0},
        {"date_iso": "2026-01-02", "price_usd": 101.0},
    ]
    write_series(tmp_path, "ethereum", "0xABC", rows,
                 metadata={"exchange": "binance", "symbol": "X/USDT", "kind": "spot"})
    loaded = load_series(tmp_path, "ethereum", "0xabc")
    assert loaded == rows
    meta = load_metadata(tmp_path, "ethereum", "0xabc")
    assert meta["exchange"] == "binance"


def test_cache_path_lowercases(tmp_path):
    p = cache_path(tmp_path, "Ethereum", "0xABC")
    assert p.name == "ethereum-0xabc.json"


def test_merge_rows_dedupes_by_date():
    a = [{"date_iso": "2026-01-01", "price_usd": 100.0},
         {"date_iso": "2026-01-02", "price_usd": 101.0}]
    b = [{"date_iso": "2026-01-02", "price_usd": 999.0},
         {"date_iso": "2026-01-03", "price_usd": 103.0}]
    merged = merge_rows(a, b)
    assert len(merged) == 3
    assert merged[1]["price_usd"] == 999.0  # b wins on collision


def test_negative_cache_lifecycle(tmp_path):
    write_negative_cache(tmp_path, "ethereum", "0xdead", reason="no_listing_found")
    meta = load_metadata(tmp_path, "ethereum", "0xdead")
    assert meta["kind"] == "no_listing"
    assert negative_cache_active(meta) is True


def test_negative_cache_expired():
    expired = {
        "kind": "no_listing",
        "expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
    }
    assert negative_cache_active(expired) is False


def test_negative_cache_ttl_constant():
    """Sanity: TTL should be a positive non-trivial number of days."""
    assert NEGATIVE_CACHE_TTL_DAYS > 0


# ---------------------------------------------------------------------------
# Batch driver
# ---------------------------------------------------------------------------


def _stub_pool() -> dict:
    """Adapters dict with one usable Binance stub serving FOO + BAR."""
    pages_foo = [_make_candles(datetime(2026, 1, 1), 3)]
    pages_bar = [_make_candles(datetime(2026, 1, 1), 3, base_price=50.0)]
    binance = StubAdapter("binance", markets={
        "FOO/USDT": {"base": "FOO", "quote": "USDT", "spot": True, "active": True},
        "BAR/USDT": {"base": "BAR", "quote": "USDT", "spot": True, "active": True},
    }, pages=pages_foo + pages_bar)
    return {"binance": binance}


def test_batch_writes_cache(tmp_path):
    adapters = _stub_pool()
    specs = [BatchSpec("ethereum", "0xfoo", ticker="FOO", only_exchange="binance")]
    report = fetch_universe(
        specs, data_dir=tmp_path, since="2026-01-01", until="2026-01-05",
        timeframe="1d", adapters=adapters,
    )
    assert report.cached == 1
    assert load_series(tmp_path, "ethereum", "0xfoo")
    meta = load_metadata(tmp_path, "ethereum", "0xfoo")
    assert meta and meta["exchange"] == "binance"


def test_batch_no_listing_writes_negative_cache(tmp_path):
    adapters = {"binance": StubAdapter("binance", {})}
    specs = [BatchSpec("ethereum", "0xnonexistent", ticker="ZZZ", only_exchange="binance")]
    report = fetch_universe(
        specs, data_dir=tmp_path, since="2026-01-01", until="2026-01-05",
        adapters=adapters,
    )
    assert report.cached == 0
    assert len(report.failed) == 1
    assert "no_listing_found" in report.failed[0].reason
    meta = load_metadata(tmp_path, "ethereum", "0xnonexistent")
    assert meta and meta["kind"] == "no_listing"


def test_batch_negative_cache_short_circuits(tmp_path):
    """Second run should skip without calling the adapter."""
    write_negative_cache(tmp_path, "ethereum", "0xdead", reason="prior_run")
    counter = StubAdapter("binance", {})
    adapters = {"binance": counter}
    specs = [BatchSpec("ethereum", "0xdead", ticker="X", only_exchange="binance")]
    report = fetch_universe(
        specs, data_dir=tmp_path, since="2026-01-01", until="2026-01-05",
        adapters=adapters,
    )
    assert report.skipped_existing == 1
    # Adapter should NOT have been called — short-circuit before resolve.
    assert counter.fetch_calls == []


def test_batch_force_overrides_existing(tmp_path):
    """--force re-fetches even when cache already has rows."""
    write_series(tmp_path, "ethereum", "0xfoo",
                 [{"date_iso": "2026-01-01", "price_usd": 1.0}])
    adapters = {"binance": StubAdapter("binance", markets={
        "FOO/USDT": {"base": "FOO", "quote": "USDT", "spot": True, "active": True},
    }, pages=[_make_candles(datetime(2026, 1, 2), 3, base_price=200.0)])}
    specs = [BatchSpec("ethereum", "0xfoo", ticker="FOO", only_exchange="binance")]
    report = fetch_universe(
        specs, data_dir=tmp_path, since="2026-01-01", until="2026-01-10",
        adapters=adapters, force=True,
    )
    assert report.cached == 1
    rows = load_series(tmp_path, "ethereum", "0xfoo")
    # Old day from existing file should be gone (force discards prior rows).
    dates = [r["date_iso"] for r in rows]
    assert "2026-01-01" not in dates


def test_batch_multiple_specs(tmp_path):
    adapters = _stub_pool()
    specs = [
        BatchSpec("ethereum", "0xfoo", ticker="FOO", only_exchange="binance"),
        BatchSpec("ethereum", "0xbar", ticker="BAR", only_exchange="binance"),
    ]
    report = fetch_universe(
        specs, data_dir=tmp_path, since="2026-01-01", until="2026-01-05",
        adapters=adapters,
    )
    assert report.requested == 2
    assert report.cached == 2
    assert report.failed == []


def test_batch_report_serializes(tmp_path):
    adapters = _stub_pool()
    specs = [BatchSpec("ethereum", "0xfoo", ticker="FOO", only_exchange="binance")]
    report = fetch_universe(
        specs, data_dir=tmp_path, since="2026-01-01", until="2026-01-05",
        adapters=adapters,
    )
    d = report.to_dict()
    # Must round-trip through json with no surprises.
    json.dumps(d)
    assert d["requested"] == 1
    assert d["cached"] == 1
    assert d["outcomes"][0]["match"]["symbol"] == "FOO/USDT"


# ---------------------------------------------------------------------------
# Missing-extra error
# ---------------------------------------------------------------------------


def test_missing_extra_error_message():
    with pytest.raises(MissingExtraError) as excinfo:
        raise MissingExtraError()
    assert "ccxt" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# Beta computation
# ---------------------------------------------------------------------------


def _walk(start_iso: str, n: int, daily_log_drift: float) -> list[dict]:
    """Synthetic series with known log-drift per day starting from $100."""
    import math
    from datetime import date, timedelta
    out = []
    base = date.fromisoformat(start_iso)
    price = 100.0
    for i in range(n):
        if i > 0:
            price *= math.exp(daily_log_drift)
        out.append({"date_iso": (base + timedelta(days=i)).isoformat(),
                    "price_usd": round(price, 8)})
    return out


def _series_from_log_returns(start_iso: str, log_returns: list[float], base_price: float = 100.0):
    """Build a price series from a sequence of daily log-returns."""
    import math
    from datetime import date, timedelta
    base = date.fromisoformat(start_iso)
    rows = [{"date_iso": base.isoformat(), "price_usd": base_price}]
    price = base_price
    for i, r in enumerate(log_returns, start=1):
        price = price * math.exp(r)
        rows.append({"date_iso": (base + timedelta(days=i)).isoformat(),
                     "price_usd": round(price, 10)})
    return rows


def test_beta_perfect_correlation_equals_one():
    """Token series ≡ BTC series with non-trivial returns → β = 1, ρ = 1."""
    import math
    from token_research.prices import compute_beta
    btc_returns = [0.01 * math.sin(i * 0.4) for i in range(60)]
    btc = _series_from_log_returns("2026-01-01", btc_returns)
    res = compute_beta(btc, btc, window_days=60, min_observations=20)
    assert res.beta is not None
    assert abs(res.beta - 1.0) < 1e-9
    assert abs((res.correlation or 0) - 1.0) < 1e-9
    assert res.fallback_used is False


def test_beta_double_amplitude_returns_two():
    """Token returns = 2 × BTC returns → β = 2."""
    import math
    from token_research.prices import compute_beta
    btc_returns = [0.01 * math.sin(i * 0.4) for i in range(80)]
    token_returns = [2.0 * r for r in btc_returns]
    btc = _series_from_log_returns("2026-01-01", btc_returns)
    token = _series_from_log_returns("2026-01-01", token_returns)
    res = compute_beta(token, btc, window_days=60, min_observations=20)
    assert res.beta is not None
    assert abs(res.beta - 2.0) < 1e-6
    assert abs((res.correlation or 0) - 1.0) < 1e-6


def test_beta_missing_overlap_falls_back():
    """Series in disjoint date ranges → fallback with reason."""
    from token_research.prices import compute_beta
    a = _walk("2025-01-01", 30, 0.001)
    b = _walk("2026-06-01", 30, 0.001)
    res = compute_beta(a, b, window_days=60, min_observations=20)
    assert res.beta is None
    assert res.fallback_used is True
    assert "insufficient" in (res.reason or "")


def test_beta_btc_zero_variance_falls_back():
    """Constant BTC price → variance 0 → β undefined."""
    from token_research.prices import compute_beta
    flat_btc = [{"date_iso": f"2026-01-{i+1:02d}", "price_usd": 100.0} for i in range(40)]
    moving = _walk("2026-01-01", 40, 0.005)
    res = compute_beta(moving, flat_btc, window_days=30, min_observations=10)
    assert res.beta is None
    assert res.fallback_used is True
    assert "variance" in (res.reason or "")


def test_beta_window_truncates():
    """Window of 10 days → uses last 10 daily returns even when more available."""
    from token_research.prices import compute_beta
    btc = _walk("2026-01-01", 100, 0.005)
    res = compute_beta(btc, btc, window_days=10, min_observations=5)
    assert res.observations == 10  # 11 dates → 10 returns
    assert res.beta is not None


# ---------------------------------------------------------------------------
# Reference cache + fetcher
# ---------------------------------------------------------------------------


def test_reference_cache_round_trip(tmp_path):
    from token_research.prices import (
        load_reference_metadata,
        load_reference_series,
        write_reference_series,
    )
    rows = [{"date_iso": "2026-01-01", "price_usd": 50000.0}]
    write_reference_series(tmp_path, "btc", rows,
                           metadata={"exchange": "binance", "symbol": "BTC/USDT"})
    assert load_reference_series(tmp_path, "btc") == rows
    meta = load_reference_metadata(tmp_path, "btc")
    assert meta and meta["symbol"] == "BTC/USDT"


def test_fetch_reference_writes_cache_with_stub(tmp_path):
    """Inject a stub adapter — fetch_reference uses it instead of make_adapter."""
    from datetime import datetime
    from token_research.prices.reference import fetch_reference

    start = datetime(2026, 1, 1)
    page = _make_candles(start, 5, base_price=50000.0)
    stub = StubAdapter("binance", markets={}, pages=[page])
    outcome = fetch_reference(
        "btc",
        data_dir=tmp_path,
        since="2026-01-01", until="2026-01-10",
        adapter=stub,
    )
    assert outcome["exchange"] == "binance"
    assert outcome["symbol"] == "BTC/USDT"
    assert outcome["rows_in_cache"] == 5

    from token_research.prices import load_reference_series
    rows = load_reference_series(tmp_path, "btc")
    assert len(rows) == 5
    assert all("price_usd" in r for r in rows)


def test_fetch_reference_unknown_name_raises(tmp_path):
    from token_research.prices.reference import fetch_reference
    with pytest.raises(KeyError):
        fetch_reference(
            "doge_unknown",
            data_dir=tmp_path,
            since="2026-01-01", until="2026-01-10",
            adapter=StubAdapter("binance", {}),
        )


def test_missing_extra_when_no_factory_no_adapters(tmp_path, monkeypatch):
    """fetch_universe with no adapters and no CCXT installed → MissingExtraError."""
    # Force make_adapter to behave as if ccxt is missing.
    from token_research.prices import batch as bm

    def boom(_id):
        raise MissingExtraError()

    with pytest.raises(MissingExtraError):
        bm.fetch_universe(
            [BatchSpec("ethereum", "0xfoo", ticker="FOO")],
            data_dir=tmp_path, since="2026-01-01", until="2026-01-05",
            adapter_factory=boom,
        )
