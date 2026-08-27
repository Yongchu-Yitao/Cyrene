"""Tool implementation for query_round."""

from __future__ import annotations

from typing import Any

from agent.plugin import PluginContext
from agent.plugin.native_runtime import plugin_localized

from ._service import current_agent_id, result_text, subagent_manager
from .definitions import get_native_tool_def

TOOL_NAME = 'query_round'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


def _tool_query_round(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    """Query live round status for the main agent."""
    if current_agent_id(context) != "main":
        return plugin_localized(
            context,
            "Only the main agent can inspect live round status.",
            "只有主 Agent 可以查看实时轮次状态。",
        )
    result = subagent_manager(context).query(
        round_id=str(args.get("round_id", "")).strip()
    )
    return result_text(result)


handler = _tool_query_round

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_query_round"]
