from __future__ import annotations
from typing import Any
from agent.plugin import PluginContext

TOOL_NAME = "AppUIInspect"
TOOL_DEF = {"type": "function", "function": {
    "name": TOOL_NAME,
    "description": "Inspect a leased external-app accessibility node and its next semantic layer without screenshots or focus.",
    "parameters": {"type": "object", "properties": {
        "session_id": {"type": "string", "minLength": 1}, "snapshot_id": {"type": "string", "minLength": 1},
        "revision": {"type": "integer", "minimum": 0}, "node_id": {"type": "string", "minLength": 1},
        "max_nodes": {"type": "integer", "minimum": 1, "maximum": 500},
        "max_depth": {"type": "integer", "minimum": 1, "maximum": 24, "description": "Transparent structural wrappers do not count as semantic layers. Use 12 or more for deeply nested Electron/Chromium UI."},
        "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
    }, "required": ["session_id", "snapshot_id", "revision", "node_id"], "additionalProperties": False},
}}
TOOL_METADATA = {"read_only": True, "resource_keys": ("desktop:app-semantic",), "requires_order": True}

async def handler(args: dict[str, Any], _context: PluginContext) -> str:
    from ._app_semantic_backend import execute_inspect, format_result
    return format_result(await execute_inspect(dict(args or {})))

__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
