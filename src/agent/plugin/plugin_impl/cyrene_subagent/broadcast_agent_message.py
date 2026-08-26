"""Tool implementation for broadcast_agent_message."""

from __future__ import annotations

from typing import Any

from .definitions import get_native_tool_def
from cyrene.tooling.runtime_api import (
    send_inbox,
    can_receive,
    datetime,
    timezone,
)

TOOL_NAME = 'broadcast_agent_message'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_broadcast_agent_message(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    """Broadcast a message to all peer sub-agents in the current round."""
    content = str(args.get("content", ""))
    if not content:
        return "Error: 'content' is required."
    from cyrene.agent.context import (
        get_current_agent_id,
        get_current_round_id,
        get_current_session_id,
    )
    from cyrene.subagent import (
        DISCUSSION_MODE,
        get_discussion_id,
        get_mode,
        get_round_id,
        get_session_id,
        list_discussion_peer_ids,
    )
    from_agent = get_current_agent_id()
    current_round_id = await get_round_id(from_agent) or get_current_round_id()
    if await get_mode(from_agent) != DISCUSSION_MODE:
        return "Error: peer communication requires discussion mode."
    discussion_id = await get_discussion_id(from_agent)
    session_id = await get_session_id(from_agent) or get_current_session_id()

    peers = await list_discussion_peer_ids(from_agent)

    if not peers:
        return "No peer sub-agents are available to receive the broadcast."

    sent_count = 0
    errors: list[str] = []
    for peer_id in peers:
        if await can_receive(
            peer_id,
            round_id=current_round_id,
            discussion_id=discussion_id,
            session_id=session_id,
            strict_session=True,
        ):
            msg_id = await send_inbox(from_agent, peer_id, "progress", content, round_id=current_round_id)
            if msg_id:
                sent_count += 1
            else:
                errors.append(f"{peer_id}: failed to deliver")
        else:
            errors.append(f"{peer_id}: not available")

    result = f"Broadcast sent to {sent_count}/{len(peers)} peers."
    if errors:
        result += f" Skipped: {', '.join(errors)}"

    # Publish SSE event for real-time flow diagram updates
    from cyrene.observability import debug as _debug_comm
    await _debug_comm.publish_event({
        "type": "agent_comm",
        "from": from_agent,
        "to": "all",
        "content": content,  # full content for group chat
        "summary": content[:100].replace("\n", " ").strip() + ("..." if len(content) > 100 else ""),
        "msg_type": "progress",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "round_id": current_round_id,
        "discussion_id": discussion_id,
        "broadcast": True,
    })
    return result


handler = _tool_broadcast_agent_message

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_broadcast_agent_message"]
