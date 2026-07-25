"""Tool definition entry for quit."""

from __future__ import annotations

from cyrene.agent.actions import complete_interaction
from cyrene.tooling.native_definitions import get_native_tool_def

TOOL_NAME = "quit"
TOOL_DEF = get_native_tool_def(TOOL_NAME)
handler = complete_interaction

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler"]
