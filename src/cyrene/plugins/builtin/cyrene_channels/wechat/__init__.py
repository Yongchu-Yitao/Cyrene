"""WeChat iLink transport primitives used by ``cyrene_channels``."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from .auth import WeChatAuth, WeChatAuthError
from .client import WeChatClient, WeChatConfig
__all__ = [
    "get_current_client",
    "set_current_client",
    "WeChatAuth",
    "WeChatAuthError",
    "WeChatClient",
    "WeChatConfig",
    "WeChatUpdater",
]

# Module-level reference so other modules (e.g. scheduler) can send
# proactive WeChat notifications without coupling to the FastAPI app.
_current_client: WeChatClient | None = None


def get_current_client() -> WeChatClient | None:
    """Return the currently active WeChatClient, or ``None``."""
    return _current_client


def set_current_client(client: WeChatClient | None) -> None:
    """Set the current WeChatClient used by the HTTP adapter and scheduler."""
    global _current_client
    _current_client = client


def __getattr__(name: str) -> Any:
    if name != "WeChatUpdater":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = import_module(f"{__package__}.bot").WeChatUpdater
    globals()[name] = value
    return value
