"""Regression tests for unlock projection (1.2) and flow validation (1.7)."""

from __future__ import annotations

from token_research.commands.unlocks import (
    _derive_onchain_schedule,
    _upcoming_unlocks,
    _validate_against_flows,
    _verdict,
)


def _make_vesting_record(balance: int, duration_seconds: int, start_offset_seconds: int) -> dict:
    """Construct a detected VestingWallet-shaped record for `_derive_onchain_schedule`."""
    from token_research.commands.unlocks import _now_ts

    now = _now_ts()
    return {
        "contract": "0x" + "1" * 40,
        "type": "vesting_wallet",
        "start": now + start_offset_seconds,
        "duration": duration_seconds,
        "released": 0,
        "token_balance": balance,
    }


def test_upcoming_unlocks_large_balance_no_float_loss():
    """A 10^24-raw balance vested over 30 days must slice without float truncation.

    Prior implementation used ``int(balance * overlap / duration)`` which loses
    precision once ``balance * overlap`` exceeds 2^53. Integer floor-div is
    exact and must produce the mathematically correct slice.
    """
    balance = 10**24           # 1M tokens at 18 decimals
    duration = 30 * 86_400     # 30 days

    # Start vesting 1 day ago so a 29-day forward slice is active within the
    # 90-day default horizon.
    record = _make_vesting_record(balance, duration, start_offset_seconds=-86_400)
    schedule = _derive_onchain_schedule([record])
    events = _upcoming_unlocks(schedule, horizon_days=90)

    assert events, "linear vesting should project an upcoming slice"
    ev = events[0]
    assert ev["type"] == "linear_slice"
    # Total amount cannot exceed balance; with 1 day elapsed the forward slice
    # is (29/30) * balance ≈ 9.666e23. Must be an exact int.
    assert isinstance(ev["amount_raw"], int)
    assert ev["amount_raw"] <= balance
    # Expect somewhere between 28 and 30 days worth — i.e. ≥ 0.9 * balance.
    assert ev["amount_raw"] > int(balance * 0.9), (
        f"expected ~29/30 of balance, got {ev['amount_raw']}"
    )
    # Float path would have clamped significant digits at ~15.95 decimal
    # digits. Make sure we preserved well beyond that.
    assert ev["amount_raw"] % 1 == 0  # it's literally an int
    # Explicit correctness check via the exact formula:
    overlap = ev["to_ts"] - ev["from_ts"]
    assert ev["amount_raw"] == (balance * overlap) // duration


def test_verdict_directionality():
    """_verdict must distinguish outflow-driven distribution from two-way churn."""
    # Outflow matches expected → aligned
    assert _verdict(100.0, observed_outflow=50.0, observed_activity=60.0) == "distribution_aligned"
    # Activity present but no net outflow → explicit new verdict
    assert _verdict(100.0, observed_outflow=0.0, observed_activity=30.0) == "activity_present_no_net_outflow"
    # Nothing observed
    assert _verdict(100.0, observed_outflow=0.0, observed_activity=0.0) == "no_flow_observed"
    # Missing expected amount
    assert _verdict(None, 0.0, 0.0) == "no_expected_amount"


def test_validate_against_flows_separates_in_and_out():
    """Netflows with +1000 / -1000 in non-unknown bucket must not read as 2000 distributed."""
    onchain_events = [{
        "contract": "0x" + "a" * 40,
        "type": "cliff",
        "ts": 0,
        "amount_raw": 500,
    }]
    flow_metrics = {
        "netflows": [
            {"category": "treasury", "net_raw": 1000},   # inflow to treasury
            {"category": "exchange", "net_raw": -1000},  # outflow to exchange
        ]
    }
    windows = _validate_against_flows(
        curated_events=[],
        onchain_events=onchain_events,
        flow_metrics=flow_metrics,
    )
    assert len(windows) == 1
    win = windows[0]
    assert win["observed_inflow"] == 1000.0
    assert win["observed_outflow"] == 1000.0
    # The old bug: activity_abs summed abs(net) → 2000. New code keeps that
    # but the VERDICT is driven by outflow, so it correctly says aligned.
    assert win["observed_activity_abs"] == 2000.0
    assert win["verdict"] == "distribution_aligned"


def test_validate_against_flows_inflow_only_does_not_falsely_align():
    """All-inflow activity must not trigger the 'distribution happened' verdict."""
    onchain_events = [{
        "contract": "0x" + "b" * 40,
        "type": "cliff",
        "ts": 0,
        "amount_raw": 500,
    }]
    flow_metrics = {
        "netflows": [
            {"category": "treasury", "net_raw": 600},  # only inflow — above 25% threshold
        ]
    }
    windows = _validate_against_flows(
        curated_events=[],
        onchain_events=onchain_events,
        flow_metrics=flow_metrics,
    )
    assert windows[0]["verdict"] == "activity_present_no_net_outflow"
