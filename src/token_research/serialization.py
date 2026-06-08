"""Lightweight output helpers.

Historically this module also contained the ~700-line Markdown renderer
for deep-report payloads. That has moved to
``token_research.commands.deep_report_markdown`` so this module only
holds the tiny JSON pretty-printer used by the CLI. The markdown renderer
is re-exported here for back-compat.
"""

from __future__ import annotations

import json
from typing import Any

from token_research.commands.deep_report_markdown import deep_report_to_markdown

__all__ = ["to_pretty_json", "deep_report_to_markdown"]


def to_pretty_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)
