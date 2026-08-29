"""Cyrene HTTP and WebSocket route adapters.

The :mod:`route` package is the single composition boundary between FastAPI
and Cyrene's application/runtime services.
"""

from __future__ import annotations

from importlib import import_module

__all__ = ["register_routes"]


def register_routes(*args, **kwargs):
    """Load the FastAPI composition root only when the app requests it."""
    return import_module("cyrene.workbench.http.registry").register_routes(*args, **kwargs)
