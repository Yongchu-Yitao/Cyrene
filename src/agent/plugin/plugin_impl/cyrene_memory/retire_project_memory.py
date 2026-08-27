"""Tool implementation for retire_project_memory.

Lets the main Workbench agent mark an identified project memory as stale
without permanently deleting it. Project scope is resolved from the active
session, so callers cannot mutate another project's memory store.
"""

from __future__ import annotations

from typing import Any

from agent.plugin import PluginContext
from ._native import create_tool, service as memory_service
from .definitions import get_native_tool_def
from agent.plugin.native_runtime import json_result

TOOL_NAME = "retire_project_memory"
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_retire_project_memory(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    """Retire one durable memory in the current Workbench project."""
    memory_id = str(args.get("memory_id", "") or "").strip()
    if not memory_id:
        return json_result({
            "status": "error",
            "type": "invalid_arguments",
            "message": "memory_id is required",
        })

    memory = memory_service(context)
    if not memory.is_main:
        return json_result({
            "status": "error",
            "type": "permission_denied",
            "message": "Only the main Agent can retire project memory.",
        })
    project_id = memory.project_id
    if project_id is None:
        return json_result({
            "status": "error",
            "type": "not_found",
            "message": (
                "Retiring project memory is only available inside a Workbench "
                "project task/chat."
            ),
        })

    from .structured import (
        retire_project_memory,
    )

    memory.configure_stores()
    retired, changed = retire_project_memory(
        project_id,
        memory_id,
        reason=str(args.get("reason", "") or "").strip(),
    )
    if retired is None:
        return json_result({
            "status": "error",
            "type": "not_found",
            "message": f"Project memory {memory_id} was not found.",
        })

    return json_result({
        "status": "success",
        "memory_id": memory_id,
        "changed": changed,
        "stale": True,
        "content": str(retired.get("content") or ""),
        "message": (
            "Project memory retired. It will no longer be injected into agent "
            "runs, but remains recoverable on the Memory page."
            if changed
            else "Project memory was already retired."
        ),
    })


handler = _tool_retire_project_memory
plugin = create_tool(TOOL_DEF, handler)

__all__ = [
    "TOOL_NAME",
    "TOOL_DEF",
    "handler",
    "plugin",
    "_tool_retire_project_memory",
]
