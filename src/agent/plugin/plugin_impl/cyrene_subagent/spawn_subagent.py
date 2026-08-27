"""Tool implementation for spawn_subagent."""

from __future__ import annotations

from typing import Any

from agent.plugin import PluginContext

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
        return "Error: agent_id and task are required."
    requester_id = current_agent_id(context)
    if requester_id != "main":
        return "Only the main agent can spawn subagents."

    await subagent_manager(context).spawn(
        requester_id,
        agent_id,
        task,
        effect_key=current_effect_key(),
    )
    return f"Sub-agent '{agent_id}' spawned. Task: {task[:80]}"


handler = _tool_spawn_subagent

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_spawn_subagent"]
