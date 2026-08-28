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
    TURN_START,
    ContextUsed,
    Hook,
    HookEvent,
    HookMatcher,
    HookPlugin,
    HookRegistration,
    SessionStartCacheFingerprint,
    with_session_start_cache_fingerprint,
)
from .plugin import PluginRegistry
from .registry import (
    HookSet,
    configure_hook_action_provider,
    configure_hook_override_provider,
    refresh_active_hook_overrides,
)

__all__ = [
    "CONTEXT_CHANGE",
    "CONTEXT_USED",
    "HOOK_EVENTS",
    "POST_TOOL_USE",
    "PRE_TOOL_USE",
    "SESSION_END",
    "SESSION_START",
    "STOP",
    "TURN_START",
    "ContextUsed",
    "Hook",
    "HookBlocked",
    "HookError",
    "HookEvent",
    "HookMatcher",
    "HookPlugin",
    "HookRegistration",
    "SessionStartCacheFingerprint",
    "HookSet",
    "configure_hook_action_provider",
    "configure_hook_override_provider",
    "refresh_active_hook_overrides",
    "PluginRegistry",
    "with_session_start_cache_fingerprint",
]
