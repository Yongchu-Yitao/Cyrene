"""Main-Agent tool that queues project-memory learning from live context."""

from __future__ import annotations

from typing import Any

from cyrene.core.plugin import PluginContext
from ._native import create_tool, service as memory_service
from .definitions import get_native_tool_def
from cyrene.plugins.native_runtime import json_result, plugin_localized

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
    if result.get("status") == "error":
        error_type = str(result.get("type") or "")
        messages = {
            "permission_denied": (
                "Only the main Agent can trigger project-memory learning.",
                "只有主 Agent 可以触发项目记忆学习。",
            ),
            "not_found": (
                "Project-memory learning is only available in a Workbench project chat.",
                "项目记忆学习仅可在 Workbench 项目对话中使用。",
            ),
            "unsupported_chat_kind": (
                "Only a root Workbench conversation can learn project memory.",
                "只有 Workbench 根对话可以学习项目记忆。",
            ),
            "context_unavailable": (
                "The Workbench conversation could not be verified.",
                "无法验证当前 Workbench 对话。",
            ),
            "no_completed_context": (
                "No completed Agent context is available for memory learning.",
                "当前没有可用于记忆学习的已完成 Agent 上下文。",
            ),
            "internal_error": (
                "Project-memory learning could not be queued.",
                "无法将项目记忆学习加入队列。",
            ),
        }
        en, zh = messages.get(
            error_type,
            (
                "Project-memory learning failed.",
                "项目记忆学习失败。",
            ),
        )
        result = {**result, "message": plugin_localized(context, en, zh)}
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
