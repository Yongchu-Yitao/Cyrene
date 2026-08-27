"""Tool implementation for send_message."""

from __future__ import annotations

from typing import Any
from datetime import datetime, timezone
from uuid import uuid4

from agent.plugin import PluginContext
from agent.plugin.native_runtime import publish_runtime_event, run_context_value

from .definitions import get_native_tool_def

TOOL_NAME = 'send_message'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_send_user_message(args: dict[str, Any], context: PluginContext) -> str:
    text = str(args.get("text", "") or "").strip()
    if not text:
        return "Error: 'text' is required."
    sender = str(run_context_value(context, "agent_id") or "").strip()
    if sender not in {"main", "scheduler"}:
        return "Only the main agent can send a user-visible WebUI message. Subagents must return a final response or use send_agent_message."

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
        return "Scheduled message sent to the user."

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
        return "System message sent to the user."

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
    return "Mid-run message sent to the user."


handler = _tool_send_user_message

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_send_user_message"]
