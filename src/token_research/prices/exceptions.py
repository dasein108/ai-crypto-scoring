"""Exceptions raised by the prices module."""
from __future__ import annotations


class PriceHistoryError(Exception):
    """Base for everything raised inside `token_research.prices`."""


class MissingExtraError(PriceHistoryError):
    """Raised when CCXT is needed but the optional extra isn't installed.

    The CLI catches this and prints the install hint instead of a stack trace.
    """

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or 'Price-history requires the ccxt extra. Install with: pip install -e ".[ccxt]"'
        )


class BadSymbolError(PriceHistoryError):
    """Symbol couldn't be resolved on any candidate exchange."""
