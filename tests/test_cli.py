from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile


def run_cli(*args: str) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    env["TOKEN_RESEARCH_OFFLINE"] = "1"
    env["TOKEN_RESEARCH_DATA_DIR"] = tempfile.mkdtemp(prefix="token-research-test-")
    completed = subprocess.run(
        [sys.executable, "-m", "token_research", *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return json.loads(completed.stdout)


def test_resolve_runs() -> None:
    payload = run_cli("resolve", "ETH")
    assert payload["command"] == "resolve"
    assert payload["resolved_identity"]["symbol"] == "ETH"


def test_deep_report_runs_and_persists() -> None:
    payload = run_cli("deep-report", "Monad")
    assert payload["command"] == "deep-report"
    assert "modules" in payload["metrics"]
    assert "saved_to" in payload
