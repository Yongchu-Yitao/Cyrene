"""Tool implementation for retire_short_term_memory."""

from __future__ import annotations

from typing import Any

from cyrene.core.plugin import PluginContext
from .short_term import retire_entry
from ._native import create_tool, service as memory_service
from .definitions import get_native_tool_def
from cyrene.plugins.native_runtime import json_result, plugin_localized

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
            "message": plugin_localized(
                context,
                "Only the main Agent can retire memory.",
                "只有主 Agent 可以将记忆标记为过时。",
            ),
        })
    memory_id = str(args.get("memory_id", "") or "").strip()
    if not memory_id:
        return json_result({
            "status": "error",
            "type": "invalid_arguments",
            "message": plugin_localized(
                context,
                "memory_id is required",
                "必须提供 memory_id",
            ),
        })

    retired, changed = retire_entry(
        memory_id,
        reason=str(args.get("reason", "") or "").strip(),
    )
    if retired is None:
        return json_result({
            "status": "error",
            "type": "not_found",
            "message": plugin_localized(
                context,
                "Short-term memory {memory_id} was not found.",
                "未找到短期记忆 {memory_id}。",
                memory_id=memory_id,
            ),
        })

    return json_result({
        "status": "success",
        "memory_id": memory_id,
        "changed": changed,
        "stale": True,
        "content": str(retired.get("content") or ""),
        "message": plugin_localized(
            context,
            "Short-term memory retired. It will no longer be injected into Agent runs or returned by RecallMemory.",
            "短期记忆已标记为过时。它将不再注入 Agent 运行，RecallMemory 也不会再返回它。",
        ) if changed else plugin_localized(
            context,
            "Short-term memory was already retired.",
            "短期记忆已经处于过时状态。",
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
