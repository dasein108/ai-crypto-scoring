from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from token_research.config import AppConfig


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def persist_raw_payload(
    config: AppConfig,
    provider: str,
    command: str,
    slug: str,
    payload: dict[str, Any],
) -> Path:
    path = config.raw_dir / provider / command / f"{slug}.json"
    write_json(path, payload)
    return path


def persist_report(config: AppConfig, slug: str, payload: dict[str, Any]) -> Path:
    path = config.reports_dir / f"{slug}.json"
    write_json(path, payload)
    return path
