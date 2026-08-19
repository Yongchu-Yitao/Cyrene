"""Public records for file-backed, in-process custom tools."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

CustomToolHandler = Callable[
    [dict[str, Any], Any, int, str, dict[str, bool] | None],
    Awaitable[str],
]


@dataclass(frozen=True, slots=True)
class CustomToolLoadError:
    """One source file that could not contribute a tool."""

    package_id: str
    source_path: Path
    error_type: str
    message: str

    def public(self, *, root: Path) -> dict[str, str]:
        try:
            relative = self.source_path.relative_to(root).as_posix()
        except ValueError:
            relative = str(self.source_path)
        return {
            "package_id": self.package_id,
            "path": relative,
            "error_type": self.error_type,
            "error": self.message,
        }


@dataclass(frozen=True, slots=True)
class CustomToolDefinition:
    """A validated native-style tool exported by one user Python module."""

    package_id: str
    source_path: Path
    module_name: str
    tool_def: dict[str, Any]
    handler: CustomToolHandler
    metadata: dict[str, Any]
    generation: int
    revision: str

    @property
    def name(self) -> str:
        return str((self.tool_def.get("function") or {}).get("name") or "")

    @property
    def description(self) -> str:
        return str((self.tool_def.get("function") or {}).get("description") or "")

    @property
    def input_schema(self) -> dict[str, Any]:
        return deepcopy(
            (self.tool_def.get("function") or {}).get("parameters")
            or {"type": "object"}
        )

    @property
    def concrete_name(self) -> str:
        return f"{self.stable_name}@{self.revision}"

    @property
    def stable_name(self) -> str:
        return f"custom:{self.package_id}/{self.name}"

    @property
    def capability_id(self) -> str:
        return f"custom.{self.package_id}.{self.name}"

    def function_definition(self, *, public_name: bool = False) -> dict[str, Any]:
        result = deepcopy(self.tool_def)
        function = result.setdefault("function", {})
        function["name"] = self.name if public_name else self.concrete_name
        return result

    def public(self, *, root: Path) -> dict[str, Any]:
        try:
            relative = self.source_path.relative_to(root).as_posix()
        except ValueError:
            relative = str(self.source_path)
        return {
            "package_id": self.package_id,
            "name": self.name,
            "description": self.description,
            "capability_id": self.capability_id,
            "concrete_name": self.concrete_name,
            "stable_name": self.stable_name,
            "input_schema": self.input_schema,
            "metadata": deepcopy(self.metadata),
            "path": relative,
            "module": self.module_name,
            "generation": self.generation,
            "revision": self.revision,
        }


@dataclass(frozen=True, slots=True)
class CustomToolPackage:
    """Current load result for one top-level directory (or root module)."""

    package_id: str
    root: Path
    tools: tuple[CustomToolDefinition, ...] = ()
    # Pure presentation records survive a restart while a package is disabled.
    # They never contain a handler and are never used for discovery/execution.
    display_tools: tuple[dict[str, Any], ...] = ()
    errors: tuple[CustomToolLoadError, ...] = ()
    source_files: tuple[Path, ...] = ()
    generation: int = 0
    revision: str = ""
    configured_enabled: bool = True
    enabled: bool = True
    module_namespace: str = ""

    @property
    def status(self) -> str:
        if not self.enabled:
            return "disabled"
        if self.errors:
            return "error"
        if self.tools:
            return "ready"
        return "empty"

    def public(self, *, custom_tools_root: Path | None = None) -> dict[str, Any]:
        manager_root = custom_tools_root or self.root.parent
        public_tools = (
            [tool.public(root=manager_root) for tool in self.tools]
            if self.tools
            else deepcopy(list(self.display_tools))
        )
        return {
            "id": self.package_id,
            "root": str(self.root),
            "status": self.status,
            "configured_enabled": self.configured_enabled,
            "effective_enabled": self.enabled,
            "enabled": self.enabled,
            "generation": self.generation,
            "revision": self.revision,
            "source_count": len(self.source_files),
            "tool_count": len(public_tools),
            "error_count": len(self.errors),
            "tools": public_tools,
            "errors": [error.public(root=manager_root) for error in self.errors],
        }


__all__ = [
    "CustomToolDefinition",
    "CustomToolHandler",
    "CustomToolLoadError",
    "CustomToolPackage",
]
