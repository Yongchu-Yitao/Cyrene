"""Dependency-neutral invalidation boundary for live model-runtime caches."""

from __future__ import annotations

from collections.abc import Callable


_INVALIDATORS: set[Callable[[], None]] = set()


def register_model_cache_invalidator(callback: Callable[[], None]) -> None:
    """Register one idempotent live-runtime cache invalidator."""
    _INVALIDATORS.add(callback)


def invalidate_model_runtime_caches() -> None:
    """Invalidate caches of every model runtime initialized in this process."""
    for callback in tuple(_INVALIDATORS):
        callback()


__all__ = [
    "invalidate_model_runtime_caches",
    "register_model_cache_invalidator",
]
