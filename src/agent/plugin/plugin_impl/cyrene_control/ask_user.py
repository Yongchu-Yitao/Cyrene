"""Tool implementation for ask_user."""

from __future__ import annotations

from typing import Any

from .definitions import get_native_tool_def
from cyrene.tooling.runtime_api import (
    json_result,
)

TOOL_NAME = 'ask_user'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_ask_user(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    text = str(args.get("text", "") or "").strip()
    if not text:
        return "Error: 'text' is required."

    from cyrene.agent.context import get_current_agent_id, get_current_client_request_id, get_current_command, get_current_round_id
    from cyrene.agent.session import upsert_pending_question

    if get_current_agent_id() != "main":
        return "Only the main agent can ask the user a clarification question."

    round_id = str(get_current_round_id() or "").strip()
    if not round_id:
        return "Cannot ask the user a question outside an active chat round."

    # Pass options through as-is; _normalize_pending_question (via
    # upsert_pending_question) handles both plain strings and the option objects
    # models sometimes emit, extracting the label and capping the count. Calling
    # str() on a dict here would leak `{'id':.., 'label':..}` into the UI labels.
    raw_options = args.get("options", [])
    options = raw_options if isinstance(raw_options, list) else []

    from cyrene.agent.session import get_session_labels

    labels = get_session_labels(round_id)
    question = await upsert_pending_question({
        "text": text,
        "round_id": round_id,
        "round_title": labels.get("round_title", ""),
        "client_request_id": str(get_current_client_request_id() or "").strip(),
        "options": options[:6],
        "allow_custom": True,
        "meta": {"command": get_current_command() or ""},
    })
    return json_result({
        "status": "awaiting_user",
        "question_id": question.get("id", ""),
        "option_count": len(question.get("options", []) or []),
    })


handler = _tool_ask_user

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_ask_user"]
