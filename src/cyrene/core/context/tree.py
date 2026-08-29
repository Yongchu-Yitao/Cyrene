"""Core value objects for context trees."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class ContextTree:
    """A tree containing opaque context values."""

    id: str
    root_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ContextNode:
    """One value mounted at a position in a context tree."""

    id: str
    tree_id: str
    parent_id: str | None
    value: Any
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ContextChange:
    """The single notification shape emitted after a committed mutation."""

    tree_id: str
    node_id: str
    action: Literal["mount", "update", "delete"]
    time: datetime
    deleted_node_ids: tuple[str, ...] = field(default_factory=tuple)
    parent_id: str | None = None
