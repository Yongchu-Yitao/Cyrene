from __future__ import annotations
from typing import Any

TOOL_NAME = "AppUIInspect"
TOOL_DEF = {"type": "function", "function": {
    "name": TOOL_NAME,
    "description": "Inspect a leased external-app accessibility node and a bounded semantic subtree without screenshots or focus.",
    "parameters": {"type": "object", "properties": {
        "session_id": {"type": "string", "minLength": 1}, "snapshot_id": {"type": "string", "minLength": 1},
        "revision": {"type": "integer", "minimum": 0}, "node_id": {"type": "string", "minLength": 1},
        "max_nodes": {"type": "integer", "minimum": 1, "maximum": 100}, "max_depth": {"type": "integer", "minimum": 1, "maximum": 8},
        "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
    }, "required": ["session_id", "snapshot_id", "revision", "node_id"], "additionalProperties": False},
}}
TOOL_METADATA = {"read_only": True, "resource_keys": ("desktop:app-semantic",), "requires_order": True}

async def handler(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify: Any) -> str:
    from cyrene.tooling.backends.app_semantic import execute_inspect, format_result
    return format_result(await execute_inspect(dict(args or {})))

__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]

