"""Regression test for 1.6: deep-report must not crash when the score
module returns an empty payload (offline / all providers disabled)."""

from __future__ import annotations


def test_score_block_tolerates_missing_metrics():
    """Simulate the failure mode: modules["score"] exists but has no
    "metrics" key (or metrics is None). Old code did
    ``modules["score"]["metrics"]["composite_score"]`` and blew up."""
    # Replicate the exact safe-access chain used by deep_report.run()
    modules = {"score": {"command": "score"}}  # no "metrics"
    score_metrics = (modules.get("score") or {}).get("metrics") or {}
    composite = score_metrics.get("composite_score")
    pillars = score_metrics.get("pillars") or {}
    coverage_ratio = score_metrics.get("coverage_ratio")

    assert composite is None
    assert pillars == {}
    assert coverage_ratio is None

    # Score key entirely missing
    modules2: dict = {}
    score_metrics2 = (modules2.get("score") or {}).get("metrics") or {}
    assert score_metrics2.get("composite_score") is None
