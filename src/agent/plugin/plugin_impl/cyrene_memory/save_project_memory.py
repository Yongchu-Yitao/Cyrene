"""Tool implementation for save_project_memory.

Lets a Workbench task agent persist a durable fact into its project's long-term
memory store — the same store shown on the project's Memory page and injected
into future runs. The project scope is resolved from the active session id, so
the agent never has to know (or be trusted with) the storage key.
"""

from __future__ import annotations

from typing import Any

from agent.plugin import PluginContext
from ._native import create_tool, service as memory_service
from .definitions import get_native_tool_def
from agent.plugin.native_runtime import plugin_localized, plugin_localized_plural

TOOL_NAME = "save_project_memory"
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_save_project_memory(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    """Persist one durable fact into the current project's memory store."""
    memory = memory_service(context)
    if not memory.is_main:
        return plugin_localized(
            context,
            "Not saved: subagents must report durable findings to the main Agent.",
            "未保存：子 Agent 必须将持久性发现报告给主 Agent。",
        )
    content = str(args.get("content", "") or "").strip()
    if len(content) < 4:
        return plugin_localized(
            context,
            "Nothing saved: 'content' is empty or too short.",
            "未保存：'content' 为空或过短。",
        )

    category = str(args.get("category", "fact") or "fact").strip().lower()
    tags = args.get("tags")

    project_id = memory.project_id
    if not project_id:
        # Not inside a Workbench project (for example a channel or scheduler run).
        return plugin_localized(
            context,
            "Not saved: project memory is only available inside a Workbench project task/chat.",
            "未保存：项目记忆仅可在 Workbench 项目任务或对话中使用。",
        )

    # Keep the storage implementation lazy so importing the Plugin declaration
    # has no database side effects.
    from .structured import add_agent_memory_checked

    memory.configure_stores()

    # Resolves textual duplicates (reinforce) and asks an LLM whether this fact
    # contradicts/supersedes existing memories — retiring the outdated ones.
    saved, retired = await add_agent_memory_checked(
        project_id,
        content,
        category=category,
        tags=tags,
        model_gateway=memory.model_gateway,
        session_id=memory.session_id,
    )
    if not saved:
        return plugin_localized(
            context,
            "Not saved (blank, too short, or outside project scope).",
            "未保存（内容为空、过短或不在项目范围内）。",
        )
    category = str(saved.get("category") or "fact")
    category_en = {
        "preference": "personal preference",
        "project": "project context",
        "habit": "work habit",
        "fact": "fact",
        "conversation": "conversation habit",
        "task_report": "task report",
        "reflection": "reflection",
    }.get(category, category)
    category_zh = {
        "preference": "个人偏好",
        "project": "项目背景",
        "habit": "工作习惯",
        "fact": "事实信息",
        "conversation": "对话习惯",
        "task_report": "任务报告",
        "reflection": "反思",
    }.get(category, category)
    cat_label = plugin_localized(context, category_en, category_zh)
    msg = plugin_localized(
        context,
        "Saved to project memory [{category}]: {content}",
        "已保存到项目记忆【{category}】：{content}",
        category=cat_label,
        content=saved.get("content") or content,
    )
    if retired:
        superseded = "; ".join("“" + str(r.get("content") or "")[:60] + "”" for r in retired)
        msg += " " + plugin_localized_plural(
            context,
            "— superseded {count} now-outdated memory: {superseded}",
            "— superseded {count} now-outdated memories: {superseded}",
            "— 已取代 {count} 条过时记忆：{superseded}",
            count=len(retired),
            superseded=superseded,
        )
    return msg


handler = _tool_save_project_memory
plugin = create_tool(TOOL_DEF, handler)

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "plugin", "_tool_save_project_memory"]
