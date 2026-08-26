"""Small, runtime-independent Plugin value objects."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypeAlias
from uuid import uuid4

from .validation import check_input_schema

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@dataclass(frozen=True, slots=True)
class PluginContext:
    """Handles a Plugin may use while deciding how to apply its result.

    The Plugin system does not interpret or mutate these values. In particular,
    mounting a result into ``tree`` remains the Plugin's responsibility.
    """

    workspace: Path | None = None
    tree: Any = None
    tree_id: str | None = None
    node_id: str | None = None
    hooks: Any = None
    data: Mapping[str, Any] = field(default_factory=dict)


PluginHandler: TypeAlias = Callable[
    [dict[str, Any], PluginContext],
    Any | Awaitable[Any],
]


@dataclass(frozen=True, slots=True)
class Plugin:
    """One executable component that returns an opaque result."""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    handler: PluginHandler = field(repr=False, compare=False)
    kind: Literal["tool", "model"] = "tool"
    allow_parallel: bool = False
    timeout_seconds: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        if not _IDENTIFIER.fullmatch(name):
            raise ValueError(f"invalid Plugin name: {self.name!r}")
        if not callable(self.handler):
            raise TypeError("Plugin handler must be callable")
        if self.kind not in {"tool", "model"}:
            raise ValueError("Plugin kind must be 'tool' or 'model'")
        if not isinstance(self.allow_parallel, bool):
            raise TypeError("Plugin allow_parallel must be a boolean")
        if self.timeout_seconds is not None:
            if isinstance(self.timeout_seconds, bool) or not isinstance(
                self.timeout_seconds, (int, float)
            ):
                raise TypeError("Plugin timeout_seconds must be a number or None")
            if float(self.timeout_seconds) <= 0:
                raise ValueError("Plugin timeout_seconds must be greater than zero")
        schema = deepcopy(dict(self.input_schema))
        if not isinstance(self.metadata, Mapping):
            raise TypeError("Plugin metadata must be a mapping")
        metadata = deepcopy(dict(self.metadata))
        if schema.get("type", "object") != "object":
            raise ValueError("Plugin input_schema must describe an object")
        check_input_schema(schema)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", str(self.description).strip())
        object.__setattr__(self, "input_schema", schema)
        object.__setattr__(self, "metadata", metadata)
        if self.timeout_seconds is not None:
            object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))

    def tool_definition(self) -> dict[str, Any]:
        """Return a fresh function definition suitable for a model call."""

        if self.kind != "tool":
            raise ValueError(f"model Plugin has no tool definition: {self.name}")

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": deepcopy(dict(self.input_schema)),
            },
        }


@dataclass(frozen=True, slots=True)
class PluginPack:
    """A user-visible directory grouping related Plugins."""

    id: str
    description: str
    plugins: tuple[Plugin, ...]

    def __post_init__(self) -> None:
        pack_id = str(self.id).strip()
        if not _IDENTIFIER.fullmatch(pack_id):
            raise ValueError(f"invalid Plugin pack id: {self.id!r}")
        plugins = tuple(self.plugins)
        names = [plugin.name for plugin in plugins]
        if len(names) != len(set(names)):
            raise ValueError(f"Plugin pack contains duplicate names: {pack_id}")
        object.__setattr__(self, "id", pack_id)
        object.__setattr__(self, "description", str(self.description).strip())
        object.__setattr__(self, "plugins", plugins)


@dataclass(frozen=True, slots=True)
class PluginCall:
    """One Plugin invocation requested by a model component."""

    name: str
    arguments: Mapping[str, Any]
    id: str = field(default_factory=lambda: f"call_{uuid4().hex}")

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        if not name:
            raise ValueError("PluginCall name cannot be empty")
        call_id = str(self.id).strip()
        if not call_id:
            raise ValueError("PluginCall id cannot be empty")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "id", call_id)
        object.__setattr__(self, "arguments", deepcopy(dict(self.arguments)))


@dataclass(frozen=True, slots=True)
class PluginCallResult:
    """Runtime envelope around the opaque value returned by a Plugin."""

    call_id: str
    name: str
    success: bool
    value: Any
    error: str
    time: datetime


__all__ = [
    "Plugin",
    "PluginCall",
    "PluginCallResult",
    "PluginContext",
    "PluginHandler",
    "PluginPack",
]
