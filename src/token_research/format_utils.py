"""Shared number/currency formatting helpers.

Single source of truth for human-readable number formatting used by both
the Markdown serializer (`serialization.py`) and the deep-report narrative
generator (`commands/deep_report.py`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def format_number(value: Any, digits: int = 2) -> str:
    """Format a number with K/M/B suffixes. Returns "n/a" on None."""
    if value is None:
        return "n/a"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    sign = "-" if f < 0 else ""
    a = abs(f)
    if a >= 1_000_000_000:
        return f"{sign}{a / 1_000_000_000:.{digits}f}B"
    if a >= 1_000_000:
        return f"{sign}{a / 1_000_000:.{digits}f}M"
    if a >= 1_000:
        return f"{sign}{a / 1_000:.{digits}f}K"
    return f"{sign}{a:.{digits}f}"


def format_usd(value: Any) -> str:
    """Format a USD amount with $ prefix and K/M/B suffixes.

    Uses the narrative-style precision: 2 decimals for B/M, 1 for K, 0 for <1K.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:.0f}"


def iso_from_unix(ts: float | None, *, date_only: bool = False) -> str | None:
    """Convert a unix timestamp (seconds) to ISO-8601, or "YYYY-MM-DD" when
    ``date_only``. Returns None for falsy/invalid input. Single source of truth
    for the three command modules that previously each had their own copy.
    """
    if not ts:
        return None
    try:
        dt = datetime.fromtimestamp(float(ts), UTC)
    except (OverflowError, OSError, ValueError, TypeError):
        return None
    return dt.strftime("%Y-%m-%d") if date_only else dt.replace(microsecond=0).isoformat()
