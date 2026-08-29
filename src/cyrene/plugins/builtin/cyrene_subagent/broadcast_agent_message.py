"""Tool implementation for broadcast_agent_message."""

from __future__ import annotations

from typing import Any

from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import plugin_localized

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
        return plugin_localized(
            context,
            "Error: 'content' is required.",
            "错误：必须提供 'content'。",
        )
    try:
        result = await subagent_manager(context).broadcast(
            current_agent_id(context),
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
    delivered = list(result.get("delivered") or ())
    errors = dict(result.get("errors") or {})
    total = len(delivered) + len(errors)
    text = plugin_localized(
        context,
        "Broadcast sent to {delivered}/{total} peers.",
        "广播已发送给 {delivered}/{total} 个同级 Agent。",
        delivered=len(delivered),
        total=total,
    )
    if errors:
        text += plugin_localized(
            context,
            " Skipped: {agents}.",
            " 已跳过：{agents}。",
            agents=", ".join(sorted(errors)),
        )
    return text


handler = _tool_broadcast_agent_message

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_broadcast_agent_message"]
