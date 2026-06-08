from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from token_research.format_utils import iso_from_unix
from token_research.commands import flows, locks, supply
from token_research.config import AppConfig
from token_research.models import CommandResult, CoverageGap, CoverageMetric, WarningCode, WarningItem, dataclass_list
from token_research.providers import tokenomist
from token_research.providers.http import ProviderError
from token_research.providers.registry import build_source_refs
from token_research.resolver import resolve_with_sources


def _now_ts() -> int:
    return int(datetime.now(UTC).timestamp())


def _derive_onchain_schedule(vesting_contracts: list[dict]) -> list[dict]:
    """Project linear unlock curves from detected OZ VestingWallet instances.

    Each wallet exposes start, duration, released, and current token_balance.
    For any wallet with start/duration present we produce a record with
    derived timestamps and per-period cliff/linear share estimates. Heuristic
    name-matched entries are reported but marked as low-confidence.
    """
    schedule: list[dict] = []
    for entry in vesting_contracts or []:
        if not isinstance(entry, dict):
            continue
        contract = entry.get("contract")
        if not contract:
            continue
        entry_type = entry.get("type") or "vesting"
        start = entry.get("start")
        duration = entry.get("duration")
        released = entry.get("released")
        balance = entry.get("token_balance")
        release_time = entry.get("release_time")  # token_timelock variant

        record: dict = {
            "contract": contract,
            "type": entry_type,
            "confidence": entry.get("confidence") or ("high" if entry_type in {"vesting_wallet", "token_timelock"} else "low"),
            "token_balance_raw": balance,
            "released_raw": released,
        }

        # OpenZeppelin VestingWallet-style (linear vesting over `duration`)
        if isinstance(start, int) and isinstance(duration, int) and duration > 0:
            end = start + duration
            record.update(
                {
                    "start_ts": start,
                    "duration_seconds": duration,
                    "end_ts": end,
                    "start_iso": iso_from_unix(start),
                    "end_iso": iso_from_unix(end),
                    "vesting_model": "linear",
                }
            )
            # Remaining portion still locked right now (clamped 0..1)
            now = _now_ts()
            if now < start:
                remaining_fraction = 1.0
            elif now >= end:
                remaining_fraction = 0.0
            else:
                remaining_fraction = (end - now) / duration
            record["remaining_fraction"] = round(remaining_fraction, 4)
            if isinstance(balance, int):
                record["remaining_locked_raw"] = int(balance * remaining_fraction)

        # OZ TokenTimelock-style (cliff at `release_time`)
        elif isinstance(release_time, int) and release_time > 0:
            record.update(
                {
                    "release_ts": release_time,
                    "release_iso": iso_from_unix(release_time),
                    "vesting_model": "cliff",
                }
            )

        schedule.append(record)
    return schedule


def _upcoming_unlocks(schedule: list[dict], horizon_days: int = 90) -> list[dict]:
    """Flatten the projected schedule into upcoming unlock events within a horizon."""
    horizon_ts = _now_ts() + horizon_days * 86_400
    events: list[dict] = []
    for record in schedule:
        model = record.get("vesting_model")
        if model == "cliff":
            ts = record.get("release_ts")
            if isinstance(ts, int) and ts >= _now_ts() and ts <= horizon_ts:
                events.append(
                    {
                        "contract": record.get("contract"),
                        "type": "cliff",
                        "ts": ts,
                        "iso": iso_from_unix(ts),
                        "amount_raw": record.get("token_balance_raw"),
                        "confidence": record.get("confidence"),
                    }
                )
        elif model == "linear":
            start = record.get("start_ts") or 0
            end = record.get("end_ts") or 0
            balance = record.get("token_balance_raw")
            if not isinstance(balance, int) or balance <= 0:
                continue
            if end <= _now_ts() or start >= horizon_ts:
                continue
            overlap_start = max(_now_ts(), start)
            overlap_end = min(horizon_ts, end)
            overlap = max(0, overlap_end - overlap_start)
            duration = record.get("duration_seconds") or 0
            if duration > 0:
                # Integer floor-div: avoids float64 precision loss on large
                # balances (18–27 decimals exceed 2^53).
                estimated = (balance * overlap) // duration
                if estimated > 0:
                    events.append(
                        {
                            "contract": record.get("contract"),
                            "type": "linear_slice",
                            "from_ts": overlap_start,
                            "to_ts": overlap_end,
                            "from_iso": iso_from_unix(overlap_start),
                            "to_iso": iso_from_unix(overlap_end),
                            "amount_raw": estimated,
                            "confidence": record.get("confidence"),
                        }
                    )
    return events


def _validate_against_flows(
    curated_events: list[dict],
    onchain_events: list[dict],
    flow_metrics: dict,
) -> list[dict]:
    """Produce validation_windows comparing projected unlocks to observed flows.

    Phase 2 goal: mark each unlock window with the observed net movement during the
    window so operators can see whether predicted distribution actually hit the
    market. Current implementation operates on the aggregated flow categories we
    fetch today (7-day window) so validation is coarse.
    """
    netflows = flow_metrics.get("netflows") or []
    # Track inflow and outflow separately so callers can distinguish
    # "tokens distributed to market" (outflow from treasury/team buckets)
    # from neutral two-way churn. Prior version summed abs(net) which
    # masked direction — 1000 in + 1000 out read as 2000 distributed.
    distribution_inflow = 0.0   # sum of positive net_raw across non-unknown buckets
    distribution_outflow = 0.0  # sum of |negative net_raw| across non-unknown buckets
    distribution_buckets: dict[str, float] = {}
    for row in netflows:
        cat = str(row.get("category") or "unknown")
        net = row.get("net_raw") or 0
        try:
            net_f = float(net)
        except (TypeError, ValueError):
            net_f = 0.0
        distribution_buckets[cat] = distribution_buckets.get(cat, 0.0) + net_f
        if cat != "unknown":
            if net_f > 0:
                distribution_inflow += net_f
            elif net_f < 0:
                distribution_outflow += -net_f

    distribution_activity = distribution_inflow + distribution_outflow

    windows: list[dict] = []

    def _observed_payload() -> dict:
        return {
            "observed_activity_abs": round(distribution_activity, 2) if distribution_activity else None,
            "observed_inflow": round(distribution_inflow, 2) if distribution_inflow else None,
            "observed_outflow": round(distribution_outflow, 2) if distribution_outflow else None,
            "observed_buckets": {k: round(v, 2) for k, v in distribution_buckets.items()} or None,
        }

    def _window(label: str, source: str, event: dict) -> None:
        ts = event.get("ts") or event.get("to_ts")
        window = {
            "source": source,
            "event_type": event.get("type") or label,
            "contract": event.get("contract"),
            "expected_amount_raw": event.get("amount_raw"),
            "expected_ts_iso": iso_from_unix(ts) if isinstance(ts, int) else event.get("iso") or event.get("to_iso"),
            **_observed_payload(),
            "verdict": _verdict(event.get("amount_raw"), distribution_outflow, distribution_activity),
        }
        windows.append(window)

    for event in onchain_events or []:
        _window("onchain", "onchain_projection", event)
    for event in curated_events or []:
        amount = event.get("amount") or event.get("amount_raw") or event.get("value")
        ts = event.get("timestamp") or event.get("date") or event.get("ts")
        windows.append(
            {
                "source": "curated_tokenomist",
                "event_type": event.get("type") or event.get("category") or "curated_unlock",
                "contract": None,
                "expected_amount_raw": amount,
                "expected_ts_iso": ts if isinstance(ts, str) else iso_from_unix(ts) if isinstance(ts, int) else None,
                **_observed_payload(),
                "verdict": _verdict(amount, distribution_outflow, distribution_activity),
            }
        )
    return windows


def _cliff_overhang_horizons(
    onchain_schedule: list[dict],
    curated_schedule: list[dict],
    decimals: int | None,
    total_supply_tokens: float | None,
) -> dict:
    """Aggregate upcoming unlock token amounts across 90 / 180 / 365 day windows.

    The score command consumes the resulting %-of-supply numbers as cliff
    overhang penalties — a 90d unlock of 30% of float is far more material
    than the same total spread over 365 days. Curated Tokenomist events are
    folded in alongside on-chain projections when both are present.
    """
    out: dict = {"horizons": {}, "decimals": decimals, "total_supply_tokens": total_supply_tokens}
    horizons = (("90d", 90), ("180d", 180), ("365d", 365))

    def _events_to_total(events: list[dict]) -> int:
        total = 0
        for event in events:
            amount = event.get("amount_raw")
            if isinstance(amount, int):
                total += amount
            elif isinstance(amount, float):
                total += int(amount)
        return total

    def _curated_within(horizon_days: int) -> int:
        cutoff = _now_ts() + horizon_days * 86_400
        total = 0
        for event in curated_schedule or []:
            amount = event.get("amount") or event.get("amount_raw") or event.get("value")
            ts_raw = event.get("timestamp") or event.get("date") or event.get("ts")
            ts: int | None = None
            if isinstance(ts_raw, int):
                ts = ts_raw
            elif isinstance(ts_raw, str):
                try:
                    ts = int(datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp())
                except ValueError:
                    ts = None
            if ts is None or ts < _now_ts() or ts > cutoff:
                continue
            try:
                value = int(float(amount)) if amount is not None else 0
            except (TypeError, ValueError):
                value = 0
            total += value
        return total

    for label, days in horizons:
        onchain_events = _upcoming_unlocks(onchain_schedule, horizon_days=days)
        onchain_total_raw = _events_to_total(onchain_events)
        curated_total_raw = _curated_within(days)
        combined_raw = onchain_total_raw + curated_total_raw

        amount_tokens: float | None = None
        pct_of_supply: float | None = None
        if combined_raw and decimals is not None:
            amount_tokens = combined_raw / (10 ** decimals)
            if total_supply_tokens and total_supply_tokens > 0:
                pct_of_supply = round(amount_tokens / total_supply_tokens * 100, 3)

        out["horizons"][label] = {
            "horizon_days": days,
            "onchain_events_count": len(onchain_events),
            "onchain_amount_raw": onchain_total_raw or None,
            "curated_amount_raw": curated_total_raw or None,
            "total_amount_raw": combined_raw or None,
            "total_amount_tokens": round(amount_tokens, 4) if amount_tokens else None,
            "pct_of_total_supply": pct_of_supply,
        }
    return out


def _verdict(expected, observed_outflow: float, observed_activity: float) -> str:
    """Coarse verdict comparing an expected unlock against observed flow activity.

    Outflow is the primary signal — a real unlock distribution shows up as
    tokens leaving treasury/team buckets. Activity (inflow+outflow) is the
    fallback when category directionality is noisy.
    """
    try:
        exp = float(expected) if expected is not None else None
    except (TypeError, ValueError):
        exp = None
    if exp is None:
        return "no_expected_amount"
    if observed_activity <= 0:
        return "no_flow_observed"
    # Loose tiers — flow window is 7d, unlock horizon is 90d, so ratios are rough.
    if observed_outflow >= exp * 0.25:
        return "distribution_aligned"
    if observed_activity >= exp * 0.25:
        return "activity_present_no_net_outflow"
    return "flow_activity_lower_than_expected"


def run(query: str, chain: str | None, address: str | None, config: AppConfig, include_premium: bool):
    identity, _, warnings, coverage_gaps = resolve_with_sources(query, chain, address, config)
    metrics: dict[str, object] = {
        "curated_schedule": [],
        "allocations": [],
        "onchain_schedule": [],
        "upcoming_onchain_events": [],
        "validation_windows": [],
        "cliff_overhang": None,
    }

    # --- Curated Tokenomist source (premium) ---
    if not include_premium:
        warnings.append(
            WarningItem(
                code=WarningCode.FREE_MODE_UNLOCK_COVERAGE,
                message="Unlock coverage is intentionally limited in free mode. Curated schedule providers are disabled.",
            )
        )
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.CURATED_SCHEDULE,
                reason="Premium mode not enabled.",
                suggested_source="Use --with-premium to query Tokenomist.",
            )
        )
    elif config.offline:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.CURATED_SCHEDULE,
                reason="Offline mode enabled.",
                suggested_source="Disable offline mode.",
            )
        )
    elif not config.tokenomist_api_key:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.CURATED_SCHEDULE,
                reason="Tokenomist API key not configured.",
                suggested_source="Set TOKEN_RESEARCH_TOKENOMIST_API_KEY.",
            )
        )
    else:
        try:
            data = tokenomist.get_token_unlocks(identity.symbol, config)
            if data:
                allocations = data.get("allocations") or data.get("categories") or []
                if isinstance(allocations, list):
                    metrics["allocations"] = allocations
                events = data.get("unlocks") or data.get("events") or data.get("schedule") or []
                if isinstance(events, list):
                    metrics["curated_schedule"] = events
                if not allocations and not events:
                    coverage_gaps.append(
                        CoverageGap(
                            metric=CoverageMetric.CURATED_SCHEDULE,
                            reason="Tokenomist returned no schedule data for this token.",
                            suggested_source="Messari or manual research.",
                        )
                    )
            else:
                coverage_gaps.append(
                    CoverageGap(
                        metric=CoverageMetric.CURATED_SCHEDULE,
                        reason=f"Token '{identity.symbol}' not found in Tokenomist.",
                        suggested_source="Try alternative symbol or Messari.",
                    )
                )
        except ProviderError as exc:
            warnings.append(WarningItem(code=WarningCode.TOKENOMIST_UNAVAILABLE, message=str(exc), severity="warning"))
            coverage_gaps.append(
                CoverageGap(metric=CoverageMetric.CURATED_SCHEDULE, reason="Tokenomist query failed.", suggested_source="Tokenomist")
            )

    # --- Free-mode: derive on-chain schedule from detected vesting contracts ---
    locks_result = locks.run(query, chain, address, config, include_premium).to_dict()
    vesting_contracts = (locks_result.get("metrics") or {}).get("vesting_contracts") or []
    onchain_schedule = _derive_onchain_schedule(vesting_contracts)
    metrics["onchain_schedule"] = onchain_schedule
    upcoming = _upcoming_unlocks(onchain_schedule)
    metrics["upcoming_onchain_events"] = upcoming

    if not onchain_schedule:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.ONCHAIN_SCHEDULE,
                reason="No OZ VestingWallet or TokenTimelock patterns detected among top holders — cannot project on-chain schedule.",
                suggested_source="Custom vesting implementations require project-specific inspection.",
            )
        )

    # --- Cliff-overhang aggregation across 90/180/365d horizons ---
    # Read decimals + total supply from supply.run so token-denominated numbers
    # are well-defined. Falls back gracefully when supply is unavailable.
    decimals_val: int | None = None
    total_supply_tokens: float | None = None
    try:
        supply_metrics = (supply.run(query, chain, address, config, include_premium).to_dict().get("metrics") or {})
        if isinstance(supply_metrics.get("decimals"), int):
            decimals_val = supply_metrics["decimals"]
        if isinstance(supply_metrics.get("total_supply_adjusted"), (int, float)):
            total_supply_tokens = float(supply_metrics["total_supply_adjusted"])
    except Exception:
        pass
    metrics["cliff_overhang"] = _cliff_overhang_horizons(
        onchain_schedule,
        metrics.get("curated_schedule") or [],
        decimals_val,
        total_supply_tokens,
    )

    # --- Unlock-window validation via flows ---
    flows_result = flows.run(query, chain, address, config, include_premium).to_dict()
    flow_metrics = flows_result.get("metrics") or {}
    validation = _validate_against_flows(
        metrics.get("curated_schedule") or [],
        upcoming,
        flow_metrics,
    )
    metrics["validation_windows"] = validation
    if not validation:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.VALIDATION_WINDOWS,
                reason="No curated or on-chain unlock events available to validate against flows.",
                suggested_source="Enable --with-premium or discover on-chain vesting contracts.",
            )
        )

    return CommandResult(
        command="unlocks",
        input={"query": query, "chain": chain, "address": address, "include_premium": include_premium},
        resolved_identity=asdict(identity),
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
