"""Validation and accessors for Workbench-specific plugin contributions."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cyrene.core.plugin import PluginPack

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def frontend_views(pack: PluginPack) -> tuple[Mapping[str, Any], ...]:
    return tuple(pack.metadata.get("frontend_views", ()))


def project_tools(pack: PluginPack) -> tuple[Mapping[str, Any], ...]:
    return tuple(pack.metadata.get("project_tools", ()))


def validate_workbench_contributions(pack: PluginPack) -> None:
    """Validate metadata interpreted by the Workbench application adapter."""

    views = pack.metadata.get("frontend_views", ())
    tools = pack.metadata.get("project_tools", ())
    if not isinstance(views, (list, tuple)):
        raise TypeError("Plugin pack metadata.frontend_views must be an array")
    if not isinstance(tools, (list, tuple)):
        raise TypeError("Plugin pack metadata.project_tools must be an array")
    view_ids: set[str] = set()
    for raw in views:
        if not isinstance(raw, Mapping):
            raise TypeError("Plugin frontend view must be an object")
        view_id = str(raw.get("id") or "").strip()
        if not _IDENTIFIER.fullmatch(view_id):
            raise ValueError(f"invalid Plugin frontend view id: {view_id!r}")
        if view_id in view_ids:
            raise ValueError(f"duplicate Plugin frontend view id: {view_id}")
        view_ids.add(view_id)
        entry = str(raw.get("entry") or "").strip().replace("\\", "/")
        entry_path = Path(entry)
        if not entry or entry_path.is_absolute() or ".." in entry_path.parts:
            raise ValueError(
                f"Plugin frontend view entry must stay inside the pack: {entry!r}"
            )
        item_i18n = raw.get("i18n", {})
        if not isinstance(item_i18n, Mapping) or any(
            not isinstance(value, Mapping) for value in item_i18n.values()
        ):
            raise TypeError("Plugin frontend view i18n must map locales to objects")
    tool_ids: set[str] = set()
    for raw in tools:
        if not isinstance(raw, Mapping):
            raise TypeError("Plugin project tool must be an object")
        tool_id = str(raw.get("id") or "").strip()
        if not _IDENTIFIER.fullmatch(tool_id):
            raise ValueError(f"invalid Plugin project tool id: {tool_id!r}")
        if tool_id in tool_ids:
            raise ValueError(f"duplicate Plugin project tool id: {tool_id}")
        tool_ids.add(tool_id)
        view_id = str(raw.get("view") or "").strip()
        if view_id not in view_ids:
            raise ValueError(
                f"Plugin project tool {tool_id} references missing view: {view_id}"
            )
        item_i18n = raw.get("i18n", {})
        if not isinstance(item_i18n, Mapping) or any(
            not isinstance(value, Mapping) for value in item_i18n.values()
        ):
            raise TypeError("Plugin project tool i18n must map locales to objects")


__all__ = [
    "frontend_views",
    "project_tools",
    "validate_workbench_contributions",
]
