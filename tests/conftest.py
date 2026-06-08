"""Pytest fixtures for the token_research test suite.

Enables offline mode by default so tests never make network calls, and
clears the in-process provider memoization cache between tests so results
don't bleed across test cases.
"""

from __future__ import annotations

import pytest

from token_research.providers import _memoize


@pytest.fixture(autouse=True)
def _offline_mode(monkeypatch):
    """Force `TOKEN_RESEARCH_OFFLINE=1` for every test.

    Subprocess-based tests (`test_cli.py::run_cli`) already set this in
    the child env; this fixture covers any future in-process tests so a
    missing env var can't accidentally hit the live network.
    """
    monkeypatch.setenv("TOKEN_RESEARCH_OFFLINE", "1")


@pytest.fixture(autouse=True)
def _clear_memoize_cache():
    """Reset the provider memoize cache before and after each test."""
    _memoize.clear_all()
    yield
    _memoize.clear_all()
