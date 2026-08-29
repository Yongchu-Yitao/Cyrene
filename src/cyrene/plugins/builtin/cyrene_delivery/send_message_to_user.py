"""Tool implementation for send_message_to_user."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import plugin_localized, publish_runtime_event, run_context_value
from .definitions import get_native_tool_def

TOOL_NAME = 'send_message_to_user'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_send_message_to_user(args: dict[str, Any], context: PluginContext) -> str:
    """Send a message directly to the user. Only available to subagents responding to @mentions."""
    text = str(args.get("text", "") or "").strip()
    if not text:
        return plugin_localized(context, "Error: 'text' is required.", "错误：必须提供 text。")

    agent_id = str(run_context_value(context, "agent_id") or "subagent")
    if agent_id == "main":
        return plugin_localized(
            context,
            "Error: send_message_to_user is only available when responding to a direct user message via @mention. Return your result normally for other turns.",
            "错误：send_message_to_user 仅可用于回复通过 @ 提及发来的直接用户消息。其他轮次请正常返回结果。",
        )

    round_id = str(run_context_value(context, "round_id") or "").strip()
    await publish_runtime_event(context, {
        "type": "agent_comm",
        "from": agent_id,
        "to": "user",
        "content": text,
        "summary": text[:100].replace("\n", " ").strip() + ("..." if len(text) > 100 else ""),
        "msg_type": "reply",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "round_id": round_id,
        "message_id": f"reply_{agent_id}_{int(time.time() * 1000)}",
    })
    notify_state = context.data.get("notify_state")
    if isinstance(notify_state, dict):
        notify_state["sent"] = True
    return plugin_localized(
        context,
        "Message sent. Now act on the user's guidance, adjust your approach, and continue with your other tools.",
        "消息已发送。现在请根据用户的指导调整方案，并继续使用其他工具工作。",
    )


handler = _tool_send_message_to_user

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_send_message_to_user"]
