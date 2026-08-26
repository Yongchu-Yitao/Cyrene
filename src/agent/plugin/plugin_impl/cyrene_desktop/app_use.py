"""Stable App Use gateway tool with runtime capability disclosure."""

from __future__ import annotations

from typing import Any

TOOL_NAME = "app_use"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Purely visual desktop control for macOS or Windows. This scheme uses window captures, calibrated coordinates, "
            "OS pointer/keyboard input, and visual effect checks; it never reads or invokes an accessibility tree. "
            "Start with operation='list_targets', then operation='connect'. Connect is always mode='visual' and returns the "
            "runtime visual capabilities for that window. Invoke a disclosed capability with operation='call'. "
            "For a named target, call visual_describe, inspect the fresh screenshot, and call measure_coordinates with the selected "
            "captured-image point. Inspect the marked crop, then pass the returned window_point unchanged. Use focus_window followed by "
            "click_at as the primary activation path; visual_click may re-localize visually after a definite pre-action failure. "
            "Coordinate input may visibly move the real cursor or temporarily change focus, and every dispatched action must be verified. "
            "This tool never invokes semantic refs, AX/UIA actions, or accessibility-tree fallbacks. If the visual scheme produces a hard failure "
            "before dispatching input, disconnect it and explicitly start AppUISnapshot as a separate semantic session. "
            "For a visible macOS text input, visual_type keeps capture localization, PID-targeted coordinate input, and exact-text verification in one visual operation. Use low-level virtual_type_at only when current visual evidence already supplies coordinates. If visual_type returns unsupported_background_text_input with isolation_required=true, do not retry or ask to take over the user's foreground; report that a separate desktop/VM worker must be configured. "
            "Reconnect when a visual session becomes stale."
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
            "additionalProperties": False,
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
    from cyrene.tooling.backends.app_use import execute_app_use, format_app_use_result

    return format_app_use_result(await execute_app_use(args))


handler = _tool_app_use

__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler", "_tool_app_use"]
