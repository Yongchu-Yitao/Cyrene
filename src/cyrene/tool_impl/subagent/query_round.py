"""Tool implementation for query_round."""

from __future__ import annotations

from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def

TOOL_NAME = 'query_round'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_query_round(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    """Query live round status for the main agent."""
    from cyrene.agent.context import get_current_agent_id

    if get_current_agent_id() != "main":
        return "Only the main agent can inspect live round status."
    from cyrene.agent.round import query_live_rounds

    return query_live_rounds(round_id=str(args.get("round_id", "")).strip())


handler = _tool_query_round

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_query_round"]
