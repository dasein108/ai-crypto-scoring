"""Tests for user-supplied aliases.json loading."""

from __future__ import annotations

import json

import pytest

from token_research.config import AppConfig
from token_research.resolver import (
    KNOWN_ALIASES,
    _load_user_aliases,
    _reset_aliases_cache,
    resolve_with_sources,
)


@pytest.fixture(autouse=True)
def _clear_aliases_cache():
    _reset_aliases_cache()
    yield
    _reset_aliases_cache()


def _fresh_config(tmp_path) -> AppConfig:
    import os
    os.environ["TOKEN_RESEARCH_DATA_DIR"] = str(tmp_path)
    cfg = AppConfig.from_env()
    cfg.ensure_directories()
    return cfg


def test_no_aliases_file_returns_builtin_only(tmp_path):
    cfg = _fresh_config(tmp_path)
    aliases = _load_user_aliases(cfg)
    assert aliases == KNOWN_ALIASES


def test_user_alias_file_merges_with_builtins(tmp_path):
    cfg = _fresh_config(tmp_path)
    (cfg.data_dir / "aliases.json").write_text(json.dumps([
        {
            "chain": "base",
            "query": "FOO",
            "symbol": "FOO",
            "project_name": "FooProject",
            "token_address": "0x" + "a" * 40,
        },
    ]))
    aliases = _load_user_aliases(cfg)
    # built-in survived
    assert ("ethereum", "ETH") in aliases
    # user entry merged
    assert ("base", "FOO") in aliases
    assert aliases[("base", "FOO")]["token_address"] == "0x" + "a" * 40


def test_malformed_aliases_file_falls_back_silently(tmp_path):
    cfg = _fresh_config(tmp_path)
    (cfg.data_dir / "aliases.json").write_text("{not valid json")
    # Should not raise; builtins still served
    aliases = _load_user_aliases(cfg)
    assert aliases == KNOWN_ALIASES


def test_user_alias_applied_via_resolve(tmp_path):
    cfg = _fresh_config(tmp_path)
    (cfg.data_dir / "aliases.json").write_text(json.dumps([
        {
            "chain": "ethereum",
            "query": "MYTOK",
            "symbol": "MYTOK",
            "project_name": "My Token",
            "token_address": "0x" + "b" * 40,
            "warning": "user alias for test",
        },
    ]))
    identity, _, warnings, _ = resolve_with_sources("MYTOK", chain="ethereum", address=None, config=cfg)
    assert identity.token_address == "0x" + "b" * 40
    assert any("user alias for test" in w.message for w in warnings)
