"""Tree-local Hook bindings for Cyrene's component agent kernel."""

from .errors import HookBlocked, HookError
from .hook import (
    CONTEXT_CHANGE,
    CONTEXT_USED,
    HOOK_EVENTS,
    POST_TOOL_USE,
    PRE_TOOL_USE,
    SESSION_END,
    SESSION_START,
    STOP,
    ContextUsed,
    Hook,
    HookEvent,
    HookMatcher,
    HookPlugin,
    HookRegistration,
)
from .plugin import PluginRegistry
from .registry import HookSet

__all__ = [
    "CONTEXT_CHANGE",
    "CONTEXT_USED",
    "HOOK_EVENTS",
    "POST_TOOL_USE",
    "PRE_TOOL_USE",
    "SESSION_END",
    "SESSION_START",
    "STOP",
    "ContextUsed",
    "Hook",
    "HookBlocked",
    "HookError",
    "HookEvent",
    "HookMatcher",
    "HookPlugin",
    "HookRegistration",
    "HookSet",
    "PluginRegistry",
]
