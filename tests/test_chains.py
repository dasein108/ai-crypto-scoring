"""Regression test for 1.5: monad must stay out of SUPPORTED_EVM_CHAINS until wiring lands."""

from __future__ import annotations

from token_research.chains import (
    BLOCKSCOUT_BASE_URLS,
    SUPPORTED_EVM_CHAINS,
    get_blockscout_base_url,
    normalize_chain_name,
)


def test_supported_chains_match_blockscout_coverage():
    """Every supported chain must have a Blockscout URL. Fail loudly if not."""
    missing = [c for c in SUPPORTED_EVM_CHAINS if c not in BLOCKSCOUT_BASE_URLS]
    assert missing == [], f"chains without Blockscout URL: {missing}"


def test_monad_excluded_from_resolver_filter():
    assert "monad" not in SUPPORTED_EVM_CHAINS
    # Alias normalization still works so users can pass monad as a chain
    # without surprise errors — it just won't be picked up by the resolver.
    assert normalize_chain_name("monad") == "monad"


def test_blockscout_url_none_for_monad():
    """monad lookup returns None, not a broken URL."""
    assert get_blockscout_base_url("monad") is None
