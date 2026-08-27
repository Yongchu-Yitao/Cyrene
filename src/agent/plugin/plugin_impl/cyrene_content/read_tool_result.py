"""System tool for paged access to model-projected tool results."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.plugin import PluginContext
from agent.plugin.native_runtime import plugin_localized

from .tool_result_store import ToolResultReferenceError

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
    context: PluginContext,
) -> str:
    run_context = context.data.get("run_context")
    nested_session_id = (
        str(run_context.get("session_id") or "")
        if isinstance(run_context, Mapping)
        else ""
    )
    session_id = str(
        context.data.get("session_id")
        or context.data.get("chat_id")
        or nested_session_id
        or ""
    )
    service = context.services.get("tool_results")
    reader = getattr(service, "read", None)
    if not callable(reader) or context.services.get("content") is None:
        raise RuntimeError("cyrene_content application service is unavailable")
    try:
        options = {
            "session_id": session_id,
            "offset": int(args.get("offset") or 0),
            "limit": int(args.get("limit") or 4000),
            "query": str(args.get("query") or ""),
        }
        return str(reader(str(args.get("content_ref") or ""), **options))
    except (TypeError, ValueError, ToolResultReferenceError):
        return plugin_localized(
            context,
            "Tool failed: the result reference or paging arguments are invalid.",
            "工具失败：结果引用或分页参数无效。",
        )


handler = _tool_read_tool_result

__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler", "_tool_read_tool_result"]
