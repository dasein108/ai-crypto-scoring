"""Read / write the price-history cache + per-token metadata sidecar.

Cache layout:
    $TOKEN_RESEARCH_DATA_DIR/prices/<chain>-<address>.json
    $TOKEN_RESEARCH_DATA_DIR/prices/_meta/<chain>-<address>.json

Series schema is `[{"date_iso": "YYYY-MM-DD", "price_usd": float}, ...]`
sorted ascending. Negative cache (no listing found) lives only in the
metadata sidecar with `kind: "no_listing"` and a TTL so newly listed
tokens get re-probed eventually.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


NEGATIVE_CACHE_TTL_DAYS = 30


def _safe_name(chain: str, address: str) -> str:
    return f"{chain.lower()}-{address.lower()}.json"


def cache_path(data_dir: Path, chain: str, address: str) -> Path:
    return data_dir / "prices" / _safe_name(chain, address)


def meta_path(data_dir: Path, chain: str, address: str) -> Path:
    return data_dir / "prices" / "_meta" / _safe_name(chain, address)


def reference_path(data_dir: Path, name: str) -> Path:
    """Cache file for a "reference asset" (BTC, ETH) keyed by short name."""
    return data_dir / "prices" / "_reference" / f"{name.lower()}.json"


def reference_meta_path(data_dir: Path, name: str) -> Path:
    return data_dir / "prices" / "_reference" / "_meta" / f"{name.lower()}.json"


def load_reference_series(data_dir: Path, name: str) -> list[dict[str, Any]]:
    """Reference equivalent of `load_series`. Empty list when missing."""
    p = reference_path(data_dir, name)
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(raw, list):
        return []
    rows = [
        r for r in raw
        if isinstance(r, dict)
        and r.get("date_iso") and r.get("price_usd") is not None
    ]
    rows.sort(key=lambda r: str(r["date_iso"]))
    return rows


def write_reference_series(
    data_dir: Path,
    name: str,
    rows: list[dict[str, Any]],
    *,
    metadata: dict[str, Any] | None = None,
) -> Path:
    target = reference_path(data_dir, name)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(rows, separators=(",", ":")))
    tmp.replace(target)
    if metadata is not None:
        mt = reference_meta_path(data_dir, name)
        mt.parent.mkdir(parents=True, exist_ok=True)
        mt_tmp = mt.with_suffix(mt.suffix + ".tmp")
        mt_tmp.write_text(json.dumps(metadata, indent=2, sort_keys=True))
        mt_tmp.replace(mt)
    return target


def load_reference_metadata(data_dir: Path, name: str) -> dict[str, Any] | None:
    p = reference_meta_path(data_dir, name)
    if not p.exists():
        return None
    try:
        m = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(m, dict):
        return None
    return m


def load_series(data_dir: Path, chain: str, address: str) -> list[dict[str, Any]]:
    """Return cache rows sorted by date_iso. Empty list when missing or unreadable."""
    p = cache_path(data_dir, chain, address)
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(raw, list):
        return []
    rows = [
        r for r in raw
        if isinstance(r, dict)
        and r.get("date_iso") and r.get("price_usd") is not None
    ]
    rows.sort(key=lambda r: str(r["date_iso"]))
    return rows


def load_metadata(data_dir: Path, chain: str, address: str) -> dict[str, Any] | None:
    p = meta_path(data_dir, chain, address)
    if not p.exists():
        return None
    try:
        m = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(m, dict):
        return None
    return m


def write_series(
    data_dir: Path,
    chain: str,
    address: str,
    rows: list[dict[str, Any]],
    *,
    metadata: dict[str, Any] | None = None,
) -> Path:
    """Atomically write `rows` and (optional) sidecar metadata.

    Rows must already be deduped + sorted by date_iso. The writer doesn't
    re-sort because the OHLCV path needs to know whether duplicates were
    actually present (counts mattered upstream).
    """
    target = cache_path(data_dir, chain, address)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(rows, separators=(",", ":")))
    tmp.replace(target)

    if metadata is not None:
        mt = meta_path(data_dir, chain, address)
        mt.parent.mkdir(parents=True, exist_ok=True)
        mt_tmp = mt.with_suffix(mt.suffix + ".tmp")
        mt_tmp.write_text(json.dumps(metadata, indent=2, sort_keys=True))
        mt_tmp.replace(mt)
    return target


def merge_rows(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge two ascending-sorted series, last-write-wins on date collisions."""
    out: dict[str, dict[str, Any]] = {str(r["date_iso"]): r for r in existing}
    for r in incoming:
        out[str(r["date_iso"])] = r
    return [out[k] for k in sorted(out)]


def write_negative_cache(
    data_dir: Path,
    chain: str,
    address: str,
    *,
    reason: str,
) -> Path:
    """Record "no listing exists for this token" so we don't re-probe every run."""
    expires = (datetime.now(UTC) + timedelta(days=NEGATIVE_CACHE_TTL_DAYS)).isoformat()
    metadata = {
        "kind": "no_listing",
        "reason": reason,
        "checked_at": datetime.now(UTC).isoformat(),
        "expires_at": expires,
        "ttl_days": NEGATIVE_CACHE_TTL_DAYS,
    }
    mt = meta_path(data_dir, chain, address)
    mt.parent.mkdir(parents=True, exist_ok=True)
    mt.write_text(json.dumps(metadata, indent=2, sort_keys=True))
    return mt


def negative_cache_active(metadata: dict[str, Any] | None) -> bool:
    """True iff the sidecar says "no listing" AND the TTL hasn't expired."""
    if not metadata or metadata.get("kind") != "no_listing":
        return False
    expires_at = metadata.get("expires_at")
    if not expires_at:
        return False
    try:
        expiry = datetime.fromisoformat(str(expires_at))
    except ValueError:
        return False
    return datetime.now(UTC) < expiry


def latest_date(rows: list[dict[str, Any]]) -> str | None:
    if not rows:
        return None
    return str(rows[-1].get("date_iso"))
