"""Main-Agent tool that queues project-memory learning from live context."""

from __future__ import annotations

from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_api import json_result
from cyrene.workbench.context import resolve_workbench_project_id_for_session

TOOL_NAME = "trigger_project_memory_learning"
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_trigger_project_memory_learning(
    args: dict[str, Any],
    _bot: Any,
    _chat_id: int,
    _db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    from cyrene.agent.context import get_current_session_id
    from cyrene.workbench.project_memory_prompt import (
        configure_store,
        schedule_learning_from_live_session,
    )

    session_id = get_current_session_id()
    project_id = resolve_workbench_project_id_for_session(session_id)
    if not project_id:
        return json_result({
            "status": "error",
            "type": "not_found",
            "message": "Project-memory learning is only available in a Workbench project chat.",
        })
    from cyrene.workbench.chat import completed_turn_count, get_workbench_chat

    chat = get_workbench_chat(session_id)
    if not chat or str(chat.get("kind") or "chat") != "chat":
        return json_result({
            "status": "error",
            "type": "unsupported_chat_kind",
            "message": "Only a root Workbench conversation can learn project memory.",
        })
    configure_store(_db_path)
    result = schedule_learning_from_live_session(
        project_id,
        session_id,
        source="agent_tool",
        reason=str(args.get("reason") or "high_value_evidence"),
        # This tool runs after the round has gathered its durable evidence but
        # before the public final reply is persisted.
        completed_turn_count=completed_turn_count(chat) + 1,
    )
    return json_result(result)


handler = _tool_trigger_project_memory_learning

__all__ = [
    "TOOL_DEF",
    "TOOL_NAME",
    "_tool_trigger_project_memory_learning",
    "handler",
]
