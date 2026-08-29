"""Fixed Read Plugin."""

from __future__ import annotations

import asyncio
from typing import Any

from ..plugin import Plugin, PluginContext
from .permission_boundaries import path_boundary, resolved_path


_resolve_path = resolved_path


def read_permission_boundary(
    arguments: dict[str, Any],
    context: PluginContext,
) -> dict[str, Any] | None:
    return path_boundary(
        arguments.get("path"),
        context,
        kind="read_elevation",
        operation="读取操作",
    )


async def read(arguments: dict[str, Any], context: PluginContext) -> str:
    path = _resolve_path(arguments.get("path"), context)
    return await asyncio.to_thread(path.read_text, encoding="utf-8")


READ_PLUGIN = Plugin(
    name="Read",
    description="Read a UTF-8 text file.",
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    },
    handler=read,
    permission_boundary=read_permission_boundary,
    allow_parallel=True,
    timeout_seconds=30.0,
)


__all__ = ["READ_PLUGIN", "read", "read_permission_boundary"]
