"""Regression test for 1.4: defillama must not swallow non-ProviderError exceptions."""

from __future__ import annotations

import pytest

from token_research.config import AppConfig
from token_research.providers import defillama
from token_research.providers._memoize import clear_all
from token_research.providers.http import ProviderError


def _fresh_config() -> AppConfig:
    return AppConfig.from_env()


def test_find_protocol_propagates_value_error(monkeypatch):
    """Unexpected exceptions (ValueError, TypeError, etc.) must propagate,
    not be silently converted to None like ProviderError is."""
    clear_all()

    def _boom(config):
        raise ValueError("corrupt payload")

    monkeypatch.setattr(defillama, "get_protocols", _boom)

    with pytest.raises(ValueError, match="corrupt payload"):
        defillama.find_protocol("ETH", "Ethereum", _fresh_config())


def test_find_protocol_swallows_provider_error(monkeypatch):
    """ProviderError (real outage) is still expected to be swallowed — this is
    the intentional fallback so callers can distinguish outage from other bugs."""
    clear_all()

    def _unavailable(config):
        raise ProviderError("DeFiLlama 503")

    monkeypatch.setattr(defillama, "get_protocols", _unavailable)

    assert defillama.find_protocol("ETH", "Ethereum", _fresh_config()) is None


def test_find_protocols_by_name_propagates_value_error(monkeypatch):
    clear_all()

    def _boom(config):
        raise RuntimeError("bad state")

    monkeypatch.setattr(defillama, "get_protocols", _boom)

    with pytest.raises(RuntimeError):
        defillama.find_protocols_by_name("katana", _fresh_config())
