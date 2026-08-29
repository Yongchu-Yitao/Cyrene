"""Tool implementation for send_message."""

from __future__ import annotations

from typing import Any
from datetime import datetime, timezone
from uuid import uuid4

from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import plugin_localized, publish_runtime_event, run_context_value

from .definitions import get_native_tool_def

TOOL_NAME = 'send_message'
TOOL_DEF = get_native_tool_def(TOOL_NAME)
TOOL_METADATA = {
    "agent_exposure": "direct",
    # This only publishes the Agent's in-progress reply into the current chat.
    # Reviewing it with another model call adds no meaningful permission
    # boundary and can race the real tool review in the same model batch.
    "permission_review": False,
}


async def _tool_send_user_message(args: dict[str, Any], context: PluginContext) -> str:
    text = str(args.get("text", "") or "").strip()
    if not text:
        return plugin_localized(context, "Error: 'text' is required.", "错误：必须提供 text。")
    sender = str(run_context_value(context, "agent_id") or "").strip()
    if sender not in {"main", "scheduler"}:
        return plugin_localized(
            context,
            "Only the main Agent can send a user-visible Web UI message. Subagents must return a final response or use send_agent_message.",
            "只有主 Agent 可以发送用户可见的 Web UI 消息。子 Agent 必须返回最终答复或使用 send_agent_message。",
        )

    if sender == "scheduler":
        await publish_runtime_event(context, {
            "type": "assistant_message",
            "system_initiated": True,
            "scheduled": True,
            "message": {"role": "assistant", "content": text, "scheduled": True},
        })
        notify_state = context.data.get("notify_state")
        if isinstance(notify_state, dict):
            notify_state["sent"] = True
            notify_state["delivered_text"] = text
        return plugin_localized(context, "Scheduled message sent to the user.", "定时消息已发送给用户。")

    round_id = str(run_context_value(context, "round_id") or "").strip()
    if not round_id:
        await publish_runtime_event(context, {
            "type": "assistant_message",
            "system_initiated": True,
            "message": {"role": "assistant", "content": text},
        })
        notify_state = context.data.get("notify_state")
        if isinstance(notify_state, dict):
            notify_state["sent"] = True
            notify_state["delivered_text"] = text
        return plugin_localized(context, "System message sent to the user.", "系统消息已发送给用户。")

    client_request_id = str(run_context_value(context, "client_request_id") or "").strip()
    public_message = {
        "id": f"assistant_{uuid4().hex}",
        "role": "assistant",
        "content": text,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "intermediate": True,
        "roundId": round_id,
    }
    await publish_runtime_event(context, {"type": "intermediate_message", "message": public_message})
    await publish_runtime_event(context, {
        "type": "assistant_message",
        "round_id": round_id,
        "client_request_id": client_request_id,
        "intermediate": True,
        "message_id": public_message["id"],
        "message": public_message,
    })
    notify_state = context.data.get("notify_state")
    if isinstance(notify_state, dict):
        notify_state["sent"] = True
    return plugin_localized(context, "Mid-run message sent to the user.", "运行中消息已发送给用户。")


handler = _tool_send_user_message

__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler", "_tool_send_user_message"]
