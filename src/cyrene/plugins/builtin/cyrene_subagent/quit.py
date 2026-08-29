"""Subagent-only completion protocol tool."""

from __future__ import annotations

from typing import Any

from cyrene.core.plugin import PluginContext

from ._service import current_agent_id, result_text, subagent_manager
from .definitions import get_native_tool_def

TOOL_NAME = "quit"
TOOL_DEF = get_native_tool_def(TOOL_NAME)


def handler(args: dict[str, Any], context: PluginContext) -> str:
    return result_text(
        subagent_manager(context).request_finish(
            current_agent_id(context),
            str(args.get("completion_status") or ""),
            args.get("criteria_evidence"),
        )
    )


__all__ = ["TOOL_NAME", "TOOL_DEF", "handler"]
