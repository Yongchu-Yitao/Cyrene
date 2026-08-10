"""Tool implementation for send_agent_message."""

from __future__ import annotations

from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_api import (
    send_inbox,
    can_receive,
    datetime,
    timezone,
)

TOOL_NAME = 'send_agent_message'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_send_agent_message(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    """Send a message to another sub-agent via inbox."""
    target = str(args.get("to", ""))
    content = str(args.get("content", ""))
    if not target or not content:
        return "Error: both 'to' and 'content' are required."
    from cyrene.agent.context import get_current_agent_id, get_current_round_id, get_current_session_id
    from cyrene.subagent import (
        DISCUSSION_MODE,
        get_discussion_id,
        get_mode,
        get_round_id,
        get_session_id,
    )
    from_agent = get_current_agent_id()
    current_round_id = await get_round_id(from_agent) or get_current_round_id()
    if target.lower() in {"main", "main_agent", "cyrene", "danny", "host", "coordinator", "parent"}:
        return "The main-agent inbox is reserved for user guidance. Put your final conclusion in your next quit response; the parent agent will collect it automatically."
    discussion_id = await get_discussion_id(from_agent)
    session_id = await get_session_id(from_agent) or get_current_session_id()
    if not await can_receive(
        target,
        round_id=current_round_id,
        discussion_id=discussion_id,
        session_id=session_id,
        strict_session=True,
    ):
        if current_round_id:
            return f"Cannot deliver: agent '{target}' is not available in the current round ({current_round_id})."
        return f"Cannot deliver: agent '{target}' is not available (finished or timed out)."
    if await get_mode(from_agent) != DISCUSSION_MODE:
        return "Error: peer communication requires discussion mode."
    await send_inbox(from_agent, target, "chat", content, round_id=current_round_id)
    # Publish SSE event for real-time flow diagram updates
    from cyrene.observability import debug as _debug_comm
    await _debug_comm.publish_event({
        "type": "agent_comm",
        "from": from_agent,
        "to": target,
        "content": content,  # full content for group chat
        "summary": content[:100].replace("\n", " ").strip() + ("..." if len(content) > 100 else ""),
        "msg_type": "chat",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "round_id": current_round_id,
        "discussion_id": discussion_id,
    })
    return f"Message sent to {target}."


handler = _tool_send_agent_message

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_send_agent_message"]
