"""Tool implementation for spawn_subagent."""

from __future__ import annotations

from typing import Any

from agent.plugin import PluginContext
from agent.plugin.native_runtime import plugin_localized

from ._service import current_agent_id, current_effect_key, subagent_manager
from .definitions import get_native_tool_def

TOOL_NAME = 'spawn_subagent'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_spawn_subagent(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    """Spawn a sub-agent to handle a specific task."""
    agent_id = str(args.get("agent_id", ""))
    task = str(args.get("task", ""))
    if not agent_id or not task:
        return plugin_localized(
            context,
            "Error: agent_id and task are required.",
            "错误：必须提供 agent_id 和 task。",
        )
    requester_id = current_agent_id(context)
    if requester_id != "main":
        return plugin_localized(
            context,
            "Only the main agent can spawn subagents.",
            "只有主 Agent 可以创建子 Agent。",
        )

    await subagent_manager(context).spawn(
        requester_id,
        agent_id,
        task,
        effect_key=current_effect_key(),
    )
    return plugin_localized(
        context,
        "Sub-agent '{agent_id}' started. Task: {task}",
        "子 Agent '{agent_id}' 已启动。任务：{task}",
        agent_id=agent_id,
        task=task[:80],
    )


handler = _tool_spawn_subagent

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_spawn_subagent"]
