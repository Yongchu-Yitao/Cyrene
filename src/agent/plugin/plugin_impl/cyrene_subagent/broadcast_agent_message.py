"""Tool implementation for broadcast_agent_message."""

from __future__ import annotations

from typing import Any

from agent.plugin import PluginContext

from ._service import current_agent_id, current_effect_key, subagent_manager
from .definitions import get_native_tool_def

TOOL_NAME = 'broadcast_agent_message'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_broadcast_agent_message(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    """Broadcast a message to all peer sub-agents in the current round."""
    content = str(args.get("content", ""))
    if not content:
        return "Error: 'content' is required."
    result = await subagent_manager(context).broadcast(
        current_agent_id(context),
        content,
        effect_key=current_effect_key(),
    )
    delivered = list(result.get("delivered") or ())
    errors = dict(result.get("errors") or {})
    total = len(delivered) + len(errors)
    text = f"Broadcast sent to {len(delivered)}/{total} peers."
    if errors:
        text += " Skipped: " + ", ".join(
            f"{agent_id}: {error}" for agent_id, error in sorted(errors.items())
        )
    return text


handler = _tool_broadcast_agent_message

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_broadcast_agent_message"]
