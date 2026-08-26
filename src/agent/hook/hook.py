"""Value objects and protocols for tree-local Hooks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, TypeAlias

CONTEXT_CHANGE = "ContextChange"
CONTEXT_USED = "ContextUsed"
PRE_TOOL_USE = "PreToolUse"
POST_TOOL_USE = "PostToolUse"
SESSION_START = "SessionStart"
SESSION_END = "SessionEnd"
STOP = "Stop"

HOOK_EVENTS = frozenset(
    {
        CONTEXT_CHANGE,
        CONTEXT_USED,
        PRE_TOOL_USE,
        POST_TOOL_USE,
        SESSION_START,
        SESSION_END,
        STOP,
    }
)


@dataclass(frozen=True, slots=True)
class ContextUsed:
    """Token usage contributed by one context-tree path to a model call."""

    tree_id: str
    node_id: str
    tokens: int
    token_limit: int
    usage_ratio: float
    node_tokens: Mapping[str, int]
    time: datetime


@dataclass(frozen=True, slots=True)
class HookEvent:
    """One invocation delivered to a Hook Plugin.

    ``payload`` stays opaque to the Hook system.  Context events carry a
    ``ContextChange`` or ``ContextUsed`` instance; compatibility events carry
    small dictionaries matching their legacy semantics.
    """

    name: str
    tree_id: str
    time: datetime
    payload: Any = None
    node_id: str | None = None
    is_root: bool = False


HookPlugin: TypeAlias = Callable[[HookEvent], Any | Awaitable[Any]]
HookMatcher: TypeAlias = Callable[[HookEvent], bool]
FailurePolicy: TypeAlias = Literal["open", "block"]


@dataclass(frozen=True, slots=True)
class Hook:
    """A persistent tree-local binding to a Plugin implementation."""

    id: str
    event: str
    plugin_id: str
    root_only: bool = False
    matcher: str | None = None
    failure_policy: FailurePolicy = "open"
    config: Mapping[str, Any] = field(default_factory=dict)
    enabled: bool = True
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class HookRegistration:
    """A Hook supplied while a tree is created, before its root event is queued."""

    event: str
    plugin_id: str
    plugin: HookPlugin = field(repr=False, compare=False)
    hook_id: str | None = None
    root_only: bool = False
    matcher: str | None = None
    failure_policy: FailurePolicy = "open"
    config: Mapping[str, Any] = field(default_factory=dict)
    enabled: bool = True
