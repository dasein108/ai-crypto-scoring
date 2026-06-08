"""In-process request memoization for provider hot paths.

`deep_report` runs ~7 sub-commands; several of them (compare, score,
relations, risk) re-run the same underlying provider calls because of
cross-module dependencies. For a single token research session this can
mean the same Blockscout address-info endpoint is hit 5+ times. For the
Morpho re-run this was the dominant source of wall-clock time.

This module provides a tiny `cached` decorator that memoises by a tuple
key (chain, address, other args) and retains results for the process's
lifetime. Callers can clear the cache between runs with `clear_all()` —
the CLI doesn't need to because each CLI invocation is a fresh process.

Intentionally simple:
  - No TTL (process-scoped)
  - No size limit (result count is bounded by the number of unique
    (token, chain, ...) tuples per run, which is small)
  - No serialization — dicts are stored by reference
  - Not thread-safe (the pipeline is synchronous)

Apply via: `@cached("<name>", lambda chain, addr, config: (chain, addr))`
"""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable


_CACHES: dict[str, dict[tuple, Any]] = {}


def cached(namespace: str, key_fn: Callable[..., tuple]):
    """Decorator that memoises results keyed by `key_fn(*args, **kwargs)`.

    The `namespace` exists so different decorated functions don't collide
    if they happen to produce the same key tuple.
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            cache = _CACHES.setdefault(namespace, {})
            key = key_fn(*args, **kwargs)
            if key in cache:
                return cache[key]
            value = fn(*args, **kwargs)
            cache[key] = value
            return value

        wrapper.__cache_namespace__ = namespace  # type: ignore[attr-defined]
        return wrapper

    return decorator


def clear_all() -> None:
    """Drop every cached entry across every namespace."""
    _CACHES.clear()
