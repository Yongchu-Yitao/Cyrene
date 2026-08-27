"""Tool implementation for send_agent_message."""

from __future__ import annotations

from typing import Any

from agent.plugin import PluginContext

from ._service import current_agent_id, current_effect_key, subagent_manager
from .definitions import get_native_tool_def

TOOL_NAME = 'send_agent_message'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_send_agent_message(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    """Send a message to another sub-agent via inbox."""
    target = str(args.get("to", ""))
    content = str(args.get("content", ""))
    if not target or not content:
        return "Error: both 'to' and 'content' are required."
    result = await subagent_manager(context).send(
        current_agent_id(context),
        target,
        content,
        effect_key=current_effect_key(),
    )
    return f"Message sent to {result['to']}."


handler = _tool_send_agent_message

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_send_agent_message"]
