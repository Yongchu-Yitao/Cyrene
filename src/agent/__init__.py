"""Cyrene's component-based agent kernel.

The package is intentionally independent from the legacy :mod:`cyrene.agent`
implementation.  Components are added here incrementally while the old backend
remains in service.
"""

from .context import (
    ContextChange,
    ContextError,
    ContextNode,
    ContextStoreRouter,
    ContextTree,
    ContextTreeStore,
    ContextValueError,
    NodeHasChildrenError,
    NodeNotFoundError,
    RootDeletionError,
    TreeNotFoundError,
)
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
    HookBlocked,
    HookError,
    HookEvent,
    HookRegistration,
    HookSet,
    PluginRegistry,
)
from .session import AgentEventListener, AgentSession, AgentSessionEvent

__all__ = [
    "ContextChange",
    "ContextError",
    "ContextNode",
    "ContextStoreRouter",
    "ContextTree",
    "ContextTreeStore",
    "ContextValueError",
    "CONTEXT_CHANGE",
    "CONTEXT_USED",
    "HOOK_EVENTS",
    "Hook",
    "HookBlocked",
    "HookError",
    "HookEvent",
    "HookRegistration",
    "HookSet",
    "PluginRegistry",
    "AgentEventListener",
    "AgentSession",
    "AgentSessionEvent",
    "ContextUsed",
    "NodeHasChildrenError",
    "NodeNotFoundError",
    "RootDeletionError",
    "TreeNotFoundError",
    "POST_TOOL_USE",
    "PRE_TOOL_USE",
    "SESSION_END",
    "SESSION_START",
    "STOP",
]
