"""Lazy public facade for Cyrene's tool control plane."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORT_GROUPS: dict[str, tuple[str, ...]] = {
    "cyrene.tooling.catalog": (
        "Capability",
        "all_capabilities",
        "describe_capabilities",
        "discover_capabilities",
        "get_active_tool_defs",
        "get_active_tool_defs_for_actor",
        "get_capability",
        "get_tool_execution_metadata",
        "is_tool_allowed_for_actor",
    ),
    "cyrene.tooling.gateway": (
        "WireCallResolution",
        "WireToolError",
        "execute_capability",
        "execute_wire_tool",
        "execute_wire_tool_in_context",
        "get_wire_tool_execution_metadata",
        "resolve_wire_call",
    ),
    "cyrene.tooling.snapshot": ("build_catalog_snapshot",),
    "cyrene.tooling.wire": (
        "DIRECT_TOOL_NAMES",
        "MODULE_TOOL_NAMES",
        "TOOLBOX_TOOL_NAME",
        "get_main_wire_tool_defs",
        "get_subagent_wire_tool_defs",
        "get_wire_bundle_hash",
        "get_wire_bundle_version",
        "get_wire_tool_bundle",
    ),
}

_EXPORTS = {
    name: module_name
    for module_name, names in _EXPORT_GROUPS.items()
    for name in names
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted((*globals(), *__all__))
