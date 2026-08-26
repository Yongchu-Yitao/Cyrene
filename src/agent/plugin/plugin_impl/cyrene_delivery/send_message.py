"""Tool implementation for send_message."""

from __future__ import annotations

from typing import Any

from .definitions import get_native_tool_def

TOOL_NAME = 'send_message'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_send_user_message(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    text = str(args.get("text", "") or "").strip()
    if not text:
        return "Error: 'text' is required."
    from cyrene.agent.context import get_current_agent_id, get_current_client_request_id, get_current_round_id
    from cyrene.agent.session import append_system_message
    from cyrene.agent.message import insert_intermediate_user_reply

    sender = str(get_current_agent_id() or "").strip()
    if sender not in {"main", "scheduler"}:
        return "Only the main agent can send a user-visible WebUI message. Subagents must report via quit or send_agent_message."

    if sender == "scheduler":
        await append_system_message(
            text,
            message_meta={"scheduled": True},
            publish_event={"scheduled": True},
        )
        if _notify_state is not None:
            _notify_state["sent"] = True
            _notify_state["delivered_text"] = text
        return "Scheduled message sent to the user."

    round_id = str(get_current_round_id() or "").strip()
    if not round_id:
        await append_system_message(text)
        if _notify_state is not None:
            _notify_state["sent"] = True
            _notify_state["delivered_text"] = text
        return "System message sent to the user."

    client_request_id = str(get_current_client_request_id() or "").strip()
    await insert_intermediate_user_reply(
        text,
        round_id=round_id,
        client_request_id=client_request_id,
    )
    if _notify_state is not None:
        _notify_state["sent"] = True
    return "Mid-run message sent to the user."


handler = _tool_send_user_message

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_send_user_message"]
