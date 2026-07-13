"""Stable App Use gateway tool with runtime capability disclosure."""

from __future__ import annotations

from typing import Any

TOOL_NAME = "app_use"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Discover and control macOS or Windows desktop application windows through one cache-stable gateway. "
            "Start with operation='list_targets', then operation='connect'. Connect returns the runtime capabilities "
            "for that window. Invoke a disclosed capability with operation='call'. The target may be foreground or background. "
            "Prefer semantic snapshots and element refs; reconnect when a session or ref becomes stale."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["list_targets", "connect", "call", "status", "disconnect"],
                    "description": "Gateway operation. Use list_targets before connecting unless recent_external selection is intended.",
                },
                "target_id": {
                    "type": "string",
                    "description": "Target id returned by list_targets. Used by connect.",
                },
                "session_id": {
                    "type": "string",
                    "description": "App session id returned by connect. Required by call, status, and disconnect.",
                },
                "capability": {
                    "type": "string",
                    "description": "Runtime capability name disclosed by connect. Required by call.",
                },
                "parameters": {
                    "type": "object",
                    "description": "Arguments for connect or for the disclosed runtime capability.",
                },
            },
            "required": ["operation"],
        },
    },
}

TOOL_METADATA = {
    "read_only": False,
    "resource_keys": ("desktop:app-use",),
    "requires_order": True,
}


async def _tool_app_use(
    args: dict[str, Any],
    _bot: Any,
    _chat_id: int,
    _db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    from cyrene.app_use import execute_app_use, format_app_use_result

    return format_app_use_result(await execute_app_use(args))


handler = _tool_app_use

__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler", "_tool_app_use"]
