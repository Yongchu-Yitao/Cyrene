"""Dependency-neutral invalidation boundary for derived tool catalogs."""

from __future__ import annotations

from collections.abc import Callable


_INVALIDATORS: set[Callable[[], None]] = set()


def register_tool_cache_invalidator(callback: Callable[[], None]) -> None:
    """Register one idempotent derived-cache invalidator."""
    _INVALIDATORS.add(callback)


def invalidate_tool_caches() -> None:
    """Invalidate every currently initialized derived tool cache."""
    for callback in tuple(_INVALIDATORS):
        callback()


__all__ = ["invalidate_tool_caches", "register_tool_cache_invalidator"]
