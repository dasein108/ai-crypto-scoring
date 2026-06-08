"""Regression tests for 1.3: compare.py must emit a note on 100% protocol custody
rather than silently returning a None ratio.
"""

from __future__ import annotations

from token_research.commands.compare import _compute_ratios


def _wrap(name: str, metrics: dict) -> dict:
    """Shape inputs like `_compute_ratios` expects — dicts with a `metrics` key."""
    return {"command": name, "metrics": metrics}


def test_full_protocol_custody_emits_note():
    """When non-EOA contract holders own ~100% of supply, emit an explicit note
    rather than leaving `effective_overhang_ratio` silently None."""
    # Construct a token where a single non-protocol holder set is empty AND
    # all supply is in contract-held addresses that match the locks detection.
    # Easiest path: lock 100% of supply.
    total_supply = 1_000_000
    decimals = 18
    supply_result = _wrap("supply", {
        "total_supply_adjusted": float(total_supply),
        "decimals": decimals,
    })
    # locks: all supply locked in one timelock contract, detected as protocol-owned.
    locks_result = _wrap("locks", {
        "locked_onchain_total": str(total_supply * 10**decimals),
        "timelock_balances": [
            {"address": "0x" + "c" * 40, "balance_raw": str(total_supply * 10**decimals)},
        ],
    })
    # holders: the single contract holder IS the only holder
    holders_result = _wrap("holders", {
        "top_holders": [
            {"address": "0x" + "c" * 40, "balance_raw": str(total_supply * 10**decimals), "percent": 100.0},
        ],
        "top10_share_of_supply": 1.0,
    })
    staking_result = _wrap("staking", {"staked_total": None})
    liquidity_result = _wrap("liquidity", {})
    yields_result = _wrap("yields", {})
    price_result = _wrap("price", {})

    metrics, notes = _compute_ratios(
        supply_result,
        holders_result,
        liquidity_result,
        locks_result,
        staking_result,
        yields_result,
        price_result,
    )

    full_custody_notes = [n for n in notes if "Full protocol custody" in n]
    assert full_custody_notes, f"expected a 'Full protocol custody' note, got: {notes}"
    # Ratio is correctly None (undefined) rather than a garbage number.
    assert metrics["supply"]["effective_overhang_ratio"] is None
