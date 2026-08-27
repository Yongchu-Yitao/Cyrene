"""Main-Agent tool that queues project-memory learning from live context."""

from __future__ import annotations

from typing import Any

from agent.plugin import PluginContext
from ._native import create_tool, service as memory_service
from .definitions import get_native_tool_def
from agent.plugin.native_runtime import json_result

TOOL_NAME = "trigger_project_memory_learning"
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_trigger_project_memory_learning(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    memory = memory_service(context)
    result = memory.trigger_project_learning(
        str(args.get("reason") or "high_value_evidence"),
        node_id=str(context.node_id or ""),
    )
    return json_result(result)


handler = _tool_trigger_project_memory_learning
plugin = create_tool(TOOL_DEF, handler)

__all__ = [
    "TOOL_DEF",
    "TOOL_NAME",
    "_tool_trigger_project_memory_learning",
    "handler",
    "plugin",
]
