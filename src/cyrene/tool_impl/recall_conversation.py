"""Tool implementation for RecallConversation."""

from __future__ import annotations

from typing import Any

from cyrene import tool_legacy as _legacy
from cyrene.conversations import recall_conversations
from cyrene.tool_legacy import _json_result

TOOL_NAME = "RecallConversation"
TOOL_DEF = next(td for td in _legacy.TOOL_DEFS if td["function"]["name"] == TOOL_NAME)


async def _tool_recall_conversation(
    args: dict[str, Any],
    _bot: Any,
    _chat_id: int,
    _db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    """Search archived conversation rounds by keyword, session, or date."""
    query = str(args.get("query", "") or "").strip()
    session_id = str(args.get("session_id", "") or "").strip()
    date = str(args.get("date", "") or "").strip()
    limit = max(1, min(int(args.get("limit", 5) or 5), 10))

    matches = recall_conversations(
        query=query,
        session_id=session_id,
        date=date,
        limit=limit,
    )
    payload: dict[str, Any] = {
        "query": query,
        "session_id": session_id,
        "date": date,
        "matches": [
            {
                "date": item.get("date", ""),
                "timestamp": item.get("timestamp", ""),
                "archive_session_id": item.get("archive_session_id", ""),
                "session_title": item.get("session_title", ""),
                "round_id": item.get("round_id", ""),
                "round_title": item.get("round_title", ""),
                "user": item.get("user_body", ""),
                "assistant": item.get("assistant_body", ""),
            }
            for item in matches
        ],
    }
    if not payload["matches"]:
        payload["note"] = "No archived conversation matches found for the given filters."
    return _json_result(payload)


handler = _tool_recall_conversation

__all__ = [
    "TOOL_NAME",
    "TOOL_DEF",
    "handler",
    "_tool_recall_conversation",
]
