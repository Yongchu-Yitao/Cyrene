"""Cyrene HTTP and WebSocket route adapters.

The :mod:`route` package is the single composition boundary between FastAPI
and Cyrene's application/runtime services.
"""

from route.registry import register_routes

__all__ = ["register_routes"]
