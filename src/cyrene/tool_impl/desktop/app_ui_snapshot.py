from __future__ import annotations
from typing import Any

TOOL_NAME = "AppUISnapshot"
TOOL_DEF = {"type": "function", "function": {
    "name": TOOL_NAME,
    "description": "Discover targets, connect a semantic-only application session, read or reprobe its paginated accessibility tree, report status, or disconnect. Never captures pixels or changes focus.",
    "parameters": {"type": "object", "properties": {
        "operation": {"type": "string", "enum": ["list_targets", "connect", "snapshot", "reprobe", "status", "disconnect"]},
        "target_id": {"type": "string"}, "selection": {"type": "string", "enum": ["", "foreground", "quick_chat_origin"]},
        "session_id": {"type": "string"}, "max_nodes": {"type": "integer", "minimum": 1, "maximum": 200},
        "max_depth": {"type": "integer", "minimum": 1, "maximum": 16},
        "page_size": {"type": "integer", "minimum": 1, "maximum": 200}, "cursor": {"type": "string"},
    }, "required": ["operation"], "additionalProperties": False},
}}
TOOL_METADATA = {"read_only": True, "resource_keys": ("desktop:app-semantic",), "requires_order": True}

async def handler(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify: Any) -> str:
    from cyrene.tooling.backends.app_semantic import execute_snapshot, format_result
    return format_result(await execute_snapshot(dict(args or {})))

__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]

