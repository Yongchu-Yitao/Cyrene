"""Standalone Edit Plugin."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from cyrene.core.plugin import Plugin, PluginContext
from cyrene.core.plugin.core_impl.permission_boundaries import path_boundary
from cyrene.plugins.native_runtime import plugin_localized


def _resolve_path(raw_path: Any, context: PluginContext) -> Path:
    value = str(raw_path or "").strip()
    if not value:
        raise ValueError("path cannot be empty")
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    if context.workspace is None:
        raise ValueError("a workspace is required for relative paths")
    return (Path(context.workspace).expanduser() / path).resolve()


def _edit_file(
    path: Path,
    old_string: str,
    new_string: str,
    replace_all: bool,
) -> int:
    content = path.read_text(encoding="utf-8")
    occurrences = content.count(old_string)
    if occurrences == 0:
        raise ValueError("old_string not found")
    if occurrences > 1 and not replace_all:
        raise ValueError("old_string matched multiple times; set replace_all=true")
    updated = (
        content.replace(old_string, new_string)
        if replace_all
        else content.replace(old_string, new_string, 1)
    )
    path.write_text(updated, encoding="utf-8")
    return occurrences if replace_all else 1


async def edit(arguments: dict[str, Any], context: PluginContext) -> str:
    path = _resolve_path(arguments["path"], context)
    replacements = await asyncio.to_thread(
        _edit_file,
        path,
        str(arguments["old_string"]),
        str(arguments["new_string"]),
        bool(arguments.get("replace_all", False)),
    )


def edit_permission_boundary(
    arguments: dict[str, Any],
    context: PluginContext,
) -> dict[str, Any] | None:
    return path_boundary(
        arguments.get("path"),
        context,
        kind="write_permission_request",
        operation="写入/删除操作",
    )
    return plugin_localized(
        context,
        "Edited {path}. Replacements: {count}",
        "已编辑 {path}。替换次数：{count}",
        path=path,
        count=replacements,
    )


plugin = Plugin(
    name="Edit",
    description="Replace an exact string in a text file.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "old_string": {"type": "string", "minLength": 1},
            "new_string": {"type": "string"},
            "replace_all": {
                "type": "boolean",
                "description": "Replace every occurrence instead of requiring exactly one.",
            },
        },
        "required": ["path", "old_string", "new_string"],
        "additionalProperties": False,
    },
    handler=edit,
    permission_boundary=edit_permission_boundary,
    allow_parallel=False,
    timeout_seconds=30.0,
)


__all__ = ["plugin"]
