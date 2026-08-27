"""Tool implementation for retire_short_term_memory."""

from __future__ import annotations

from typing import Any

from agent.plugin import PluginContext
from .short_term import retire_entry
from ._native import create_tool, service as memory_service
from .definitions import get_native_tool_def
from agent.plugin.native_runtime import json_result

TOOL_NAME = "retire_short_term_memory"
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_retire_short_term_memory(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    """Retire one short-term memory entry by exact id."""
    if not memory_service(context).is_main:
        return json_result({
            "status": "error",
            "type": "permission_denied",
            "message": "Only the main Agent can retire memory.",
        })
    memory_id = str(args.get("memory_id", "") or "").strip()
    if not memory_id:
        return json_result({
            "status": "error",
            "type": "invalid_arguments",
            "message": "memory_id is required",
        })

    retired, changed = retire_entry(
        memory_id,
        reason=str(args.get("reason", "") or "").strip(),
    )
    if retired is None:
        return json_result({
            "status": "error",
            "type": "not_found",
            "message": f"Short-term memory {memory_id} was not found.",
        })

    return json_result({
        "status": "success",
        "memory_id": memory_id,
        "changed": changed,
        "stale": True,
        "content": str(retired.get("content") or ""),
        "message": (
            "Short-term memory retired. It will no longer be injected into agent "
            "runs or returned by RecallMemory."
            if changed
            else "Short-term memory was already retired."
        ),
    })


handler = _tool_retire_short_term_memory
plugin = create_tool(TOOL_DEF, handler)

__all__ = [
    "TOOL_NAME",
    "TOOL_DEF",
    "handler",
    "plugin",
    "_tool_retire_short_term_memory",
]
