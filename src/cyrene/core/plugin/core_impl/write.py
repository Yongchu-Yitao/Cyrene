"""Fixed Write Plugin."""

from __future__ import annotations

import asyncio
from typing import Any

from ..plugin import Plugin, PluginContext
from .permission_boundaries import path_boundary, resolved_path


async def write(arguments: dict[str, Any], context: PluginContext) -> str:
    path = resolved_path(arguments.get("path"), context)
    content = str(arguments.get("content", ""))

    def write_file() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    await asyncio.to_thread(write_file)
    return f"Wrote {path}"


def write_permission_boundary(
    arguments: dict[str, Any],
    context: PluginContext,
) -> dict[str, Any] | None:
    return path_boundary(
        arguments.get("path"),
        context,
        kind="write_permission_request",
        operation="写入/删除操作",
    )


WRITE_PLUGIN = Plugin(
    name="Write",
    description="Write a UTF-8 text file.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    },
    handler=write,
    metadata={
        "resource_effects": ({
            "argument_path": ("path",),
            "kind": "file",
            "access": "write",
            "phase": "both",
        },),
    },
    permission_boundary=write_permission_boundary,
    allow_parallel=False,
    timeout_seconds=30.0,
)


__all__ = ["WRITE_PLUGIN", "write", "write_permission_boundary"]
