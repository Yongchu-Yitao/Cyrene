"""System tool for paged access to model-projected tool results."""

from __future__ import annotations

from typing import Any

from cyrene.tooling.result_store import ToolResultReferenceError, read_tool_result

TOOL_NAME = "read_tool_result"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Read or search a previously truncated tool result using its "
            "tool-result:// content_ref. References are scoped to this session."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content_ref": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100000,
                    "default": 4000,
                },
                "query": {
                    "type": "string",
                    "description": "Optional case-insensitive search text.",
                },
            },
            "required": ["content_ref"],
            "additionalProperties": False,
        },
    },
}
TOOL_METADATA = {"read_only": True, "resource_keys": ("tool-result:{content_ref}",)}


async def _tool_read_tool_result(
    args: dict[str, Any],
    _bot: Any,
    _chat_id: int,
    _db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    try:
        return read_tool_result(
            str(args.get("content_ref") or ""),
            offset=int(args.get("offset") or 0),
            limit=int(args.get("limit") or 4000),
            query=str(args.get("query") or ""),
        )
    except (TypeError, ValueError, ToolResultReferenceError) as exc:
        return f"Tool failed: {exc}"


handler = _tool_read_tool_result

__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler", "_tool_read_tool_result"]
