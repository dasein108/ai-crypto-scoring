"""price-history — fetch CEX OHLCV into the prices/ cache.

Two input modes:
    1. Single token via `query` + `--chain` + `--address`.
    2. Bulk via `--from-basket <tag|path|latest>` (reads a momentum-screen basket).

Writes daily candles into `$TOKEN_RESEARCH_DATA_DIR/prices/<chain>-<addr>.json`
plus a sidecar metadata JSON. `momentum-backtest` consumes the cache as-is.

Requires the optional `ccxt` extra:  pip install -e ".[ccxt]"
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from token_research.config import AppConfig
from token_research.models import (
    CommandResult,
    CoverageGap,
    CoverageMetric,
    WarningCode,
    WarningItem,
    dataclass_list,
)
from token_research.prices import (
    BatchSpec,
    MissingExtraError,
    fetch_universe,
)
from token_research.prices.reference import (
    REFERENCE_ASSETS,
    fetch_reference,
)
from token_research.providers.registry import build_source_refs
from token_research.resolver import resolve_with_sources


# ---------------------------------------------------------------------------
# Basket loader
# ---------------------------------------------------------------------------


def _load_basket(config: AppConfig, ref: str) -> tuple[dict[str, Any] | None, str | None]:
    if not ref:
        return None, None
    candidate = Path(ref)
    if candidate.is_file():
        try:
            return json.loads(candidate.read_text()), str(candidate)
        except (json.JSONDecodeError, OSError):
            return None, str(candidate)

    d = config.data_dir / "baskets"
    if not d.exists():
        return None, None
    files = sorted(d.glob("*.json"))
    if not files:
        return None, None

    if ref in ("latest", ""):
        target = files[-1]
    else:
        prefix_matches = [f for f in files if f.name.startswith(ref)]
        if not prefix_matches:
            return None, None
        target = prefix_matches[-1]
    try:
        return json.loads(target.read_text()), str(target)
    except (json.JSONDecodeError, OSError):
        return None, str(target)


def _basket_specs(basket: dict[str, Any]) -> list[BatchSpec]:
    inner = basket.get("metrics", {}).get("basket") if "metrics" in basket else basket
    longs = list(inner.get("longs") or [])
    shorts = list(inner.get("shorts") or [])
    seen: set[tuple[str, str]] = set()
    out: list[BatchSpec] = []
    for pos in longs + shorts:
        chain = (pos.get("chain") or "").lower()
        address = (pos.get("address") or "").lower()
        if not chain or not address:
            continue
        key = (chain, address)
        if key in seen:
            continue
        seen.add(key)
        out.append(BatchSpec(
            chain=chain, address=address, ticker=pos.get("symbol"),
        ))
    return out


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def _default_since(end: str | None) -> str:
    base = datetime.fromisoformat(end + "T00:00:00+00:00") if end else datetime.now(UTC)
    return (base - timedelta(days=2 * 365)).strftime("%Y-%m-%d")


def _default_end() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(
    query: str,
    chain: str | None,
    address: str | None,
    config: AppConfig,
    include_premium: bool,
    *,
    since: str | None = None,
    end: str | None = None,
    timeframe: str = "1d",
    from_basket: str | None = None,
    force: bool = False,
    max_parallel: int = 5,
    only_exchange: str | None = None,
    prefer_perp: bool = False,
    reference: str | None = None,
):
    warnings: list[WarningItem] = []
    coverage_gaps: list[CoverageGap] = []

    end_iso = end or _default_end()
    since_iso = since or _default_since(end_iso)

    # Reference-asset mode short-circuits everything else: write directly to
    # `prices/_reference/<name>.json` from a hard-coded (exchange, symbol).
    if reference:
        try:
            outcome = fetch_reference(
                reference, data_dir=config.data_dir,
                since=since_iso, until=end_iso, timeframe=timeframe, force=force,
            )
        except MissingExtraError as exc:
            warnings.append(WarningItem(
                code=WarningCode.PRICE_FALLBACK_FAILED,
                message=str(exc), severity="error",
            ))
            return _result(query, chain, address, include_premium, config, {
                "verdict": "missing_ccxt", "reference": reference,
            }, warnings, coverage_gaps)
        except KeyError:
            available = ", ".join(sorted(REFERENCE_ASSETS))
            warnings.append(WarningItem(
                code=WarningCode.PRICE_FALLBACK_FAILED,
                message=f"unknown reference {reference!r}; available: {available}",
                severity="error",
            ))
            return _result(query, chain, address, include_premium, config, {
                "verdict": "unknown_reference", "reference": reference,
            }, warnings, coverage_gaps)
        except Exception as exc:
            # Network errors / CCXT failures — surface as a warning instead
            # of a crash. The exception type tells the user what to retry.
            warnings.append(WarningItem(
                code=WarningCode.PRICE_FALLBACK_FAILED,
                message=f"reference fetch failed: {type(exc).__name__}: {exc}",
                severity="warning",
            ))
            return _result(query, chain, address, include_premium, config, {
                "verdict": "fetch_failed",
                "reference": reference,
                "error_type": type(exc).__name__,
            }, warnings, coverage_gaps)
        return _result(query, chain, address, include_premium, config, {
            "mode": "reference",
            "reference": reference,
            "since": since_iso,
            "end": end_iso,
            "timeframe": timeframe,
            "outcome": outcome,
        }, warnings, coverage_gaps)

    # Resolve the work list.
    specs: list[BatchSpec] = []
    basket_source: str | None = None
    if from_basket:
        basket, basket_source = _load_basket(config, from_basket)
        if basket is None:
            coverage_gaps.append(CoverageGap(
                metric=CoverageMetric.PRIMITIVE_INFERENCE,
                reason=f"Basket reference {from_basket!r} did not resolve to a saved basket.",
                suggested_source="Run `momentum-screen` to produce one.",
            ))
            return _result(query, chain, address, include_premium, config, {
                "verdict": "no_basket", "from_basket": from_basket,
            }, warnings, coverage_gaps)
        specs = _basket_specs(basket)
        # Apply override flags to every spec when set explicitly.
        if only_exchange or prefer_perp:
            specs = [
                BatchSpec(s.chain, s.address, s.ticker, only_exchange=only_exchange,
                          prefer_perp=prefer_perp)
                for s in specs
            ]
        if not specs:
            coverage_gaps.append(CoverageGap(
                metric=CoverageMetric.PRIMITIVE_INFERENCE,
                reason="Basket has no positions with chain+address.",
                suggested_source="Re-run `momentum-screen`.",
            ))
            return _result(query, chain, address, include_premium, config, {
                "verdict": "empty_basket", "basket_source": basket_source,
            }, warnings, coverage_gaps)
    else:
        identity, _, ident_warnings, ident_gaps = resolve_with_sources(
            query, chain, address, config,
        )
        warnings.extend(ident_warnings)
        coverage_gaps.extend(ident_gaps)
        if not identity.token_address or not identity.chain:
            coverage_gaps.append(CoverageGap(
                metric=CoverageMetric.TOKEN_ADDRESS,
                reason="Could not resolve a (chain, address) pair for the query.",
                suggested_source="Pass --chain and --address explicitly.",
            ))
            return _result(query, chain, address, include_premium, config, {
                "verdict": "unresolved",
            }, warnings, coverage_gaps)
        specs = [BatchSpec(
            chain=identity.chain.lower(),
            address=identity.token_address.lower(),
            ticker=identity.symbol,
            only_exchange=only_exchange,
            prefer_perp=prefer_perp,
        )]

    # Drive the fetch.
    try:
        report = fetch_universe(
            specs,
            data_dir=config.data_dir,
            since=since_iso,
            until=end_iso,
            timeframe=timeframe,
            max_parallel=max_parallel,
            force=force,
        )
    except MissingExtraError as exc:
        warnings.append(WarningItem(
            code=WarningCode.PRICE_FALLBACK_FAILED,
            message=str(exc),
            severity="error",
        ))
        return _result(query, chain, address, include_premium, config, {
            "verdict": "missing_ccxt",
        }, warnings, coverage_gaps)

    metrics: dict[str, Any] = {
        "since": since_iso,
        "end": end_iso,
        "timeframe": timeframe,
        "force": force,
        "from_basket": from_basket,
        "basket_source": basket_source,
        "report": report.to_dict(),
    }
    if report.failed:
        warnings.append(WarningItem(
            code=WarningCode.PRICE_FALLBACK_FAILED,
            message=f"{len(report.failed)} of {report.requested} positions failed",
            severity="info",
        ))
    return _result(query, chain, address, include_premium, config, metrics, warnings, coverage_gaps)


def _result(
    query: str,
    chain: str | None,
    address: str | None,
    include_premium: bool,
    config: AppConfig,
    metrics: dict[str, Any],
    warnings: list[WarningItem],
    coverage_gaps: list[CoverageGap],
) -> CommandResult:
    return CommandResult(
        command="price-history",
        input={
            "query": query,
            "chain": chain,
            "address": address,
            "include_premium": include_premium,
        },
        resolved_identity={"query": query, "mode": "price-history"},
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
