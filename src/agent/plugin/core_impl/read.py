"""Fixed Read Plugin."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..plugin import Plugin, PluginContext


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
    allow_parallel=True,
    timeout_seconds=30.0,
)


__all__ = ["READ_PLUGIN", "read"]
