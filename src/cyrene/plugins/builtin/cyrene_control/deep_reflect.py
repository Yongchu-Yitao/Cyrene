"""Tool implementation for DeepReflect."""

from __future__ import annotations

from typing import Any

from cyrene.core.plugin import PluginContext

from .definitions import get_native_tool_def

TOOL_NAME = 'DeepReflect'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_deep_reflect(_args: dict[str, Any], _context: PluginContext) -> str:
    return (
        "DeepReflect is handled inside the main chat loop so it can access the live visible transcript. "
        "If you see this fallback, continue without changing persisted history."
    )


handler = _tool_deep_reflect

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_deep_reflect"]
