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
    content = await asyncio.to_thread(path.read_text, encoding="utf-8")
    start_line = arguments.get("start_line")
    end_line = arguments.get("end_line")
    if start_line is None and end_line is None:
        return content

    start = int(start_line or 1)
    end = int(end_line) if end_line is not None else None
    if end is not None and end < start:
        raise ValueError("end_line must be greater than or equal to start_line")
    return "".join(content.splitlines(keepends=True)[start - 1:end])


READ_PLUGIN = Plugin(
    name="Read",
    description=(
        "Read a UTF-8 text file, optionally selecting a 1-based inclusive "
        "line range."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start_line": {
                "type": "integer",
                "minimum": 1,
                "description": "First line to return (1-based, inclusive).",
            },
            "end_line": {
                "type": "integer",
                "minimum": 1,
                "description": "Last line to return (1-based, inclusive).",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    handler=read,
    metadata={
        "read_only": True,
        "resource_effects": ({
            "argument_path": ("path",),
            "kind": "file",
            "access": "read",
            "phase": "both",
        },),
    },
    permission_boundary=read_permission_boundary,
    allow_parallel=True,
    timeout_seconds=30.0,
)


__all__ = ["READ_PLUGIN", "read", "read_permission_boundary"]
