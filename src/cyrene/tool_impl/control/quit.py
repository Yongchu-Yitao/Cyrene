"""Tool definition entry for quit.

The handler is registered lazily by ``cyrene.agent`` to avoid importing the
agent package from the tool registry during startup.
"""

from __future__ import annotations

from cyrene.tooling.native_definitions import get_native_tool_def

TOOL_NAME = "quit"
TOOL_DEF = get_native_tool_def(TOOL_NAME)
handler = None

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler"]
