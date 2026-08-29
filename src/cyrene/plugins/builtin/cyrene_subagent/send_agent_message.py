"""Tool implementation for send_agent_message."""

from __future__ import annotations

from typing import Any

from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import plugin_localized

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
        return plugin_localized(
            context,
            "Error: both 'to' and 'content' are required.",
            "错误：必须同时提供 'to' 和 'content'。",
        )
    try:
        result = await subagent_manager(context).send(
            current_agent_id(context),
            target,
            content,
            effect_key=current_effect_key(),
        )
    except (ValueError, RuntimeError) as exc:
        return plugin_localized(
            context,
            "Error: {error}",
            "错误：{error}",
            error=str(exc),
        )
    return plugin_localized(
        context,
        "Message sent to {target}.",
        "消息已发送给 {target}。",
        target=result["to"],
    )


handler = _tool_send_agent_message

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_send_agent_message"]
