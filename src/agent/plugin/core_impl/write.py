"""Fixed Write Plugin."""

from __future__ import annotations

import asyncio
from typing import Any

from ..plugin import Plugin, PluginContext
from .read import _resolve_path


async def write(arguments: dict[str, Any], context: PluginContext) -> str:
    path = _resolve_path(arguments.get("path"), context)
    content = str(arguments.get("content", ""))

    def write_file() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    await asyncio.to_thread(write_file)
    return f"Wrote {path}"


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
    allow_parallel=False,
    timeout_seconds=30.0,
)


__all__ = ["WRITE_PLUGIN", "write"]
