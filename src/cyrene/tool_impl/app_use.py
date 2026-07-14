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
            "When the user names a visible target to activate, first call visual_describe so you can inspect a fresh screenshot. Choose a candidate center in captured-image pixels, then call measure_coordinates with x/y and a surrounding width/height. Inspect the returned marked calibration crop; retry measurement if the crosshair is not centered on the intended control. Once calibrated, use click_at as the primary click tool: call focus_window, then pass window_point unchanged with allow_foreground_input=true. visual_click and virtual_click_at are fallback click tools and must not run before primary click_at explicitly fails. Connect may report semantic_profile.status='unavailable'; in that case semantic tree capabilities are removed and snapshot/find/press/select/toggle must not be attempted. Only after coordinate localization or activation fails may an available semantic tree or menu command be used. Use visual_click for a described "
            "target: it shows a virtual pointer and uses a direct application-scoped coordinate AX/UIA hit-test without a full-tree "
            "scan, moving the real cursor, or requesting focus. It is not an OS mouse event and requires an accessible control at the point. Semantic actions are "
            "the first fallback, followed on macOS by background menu AXPress; foreground pointer or keyboard fallback must be explicitly allowed. "
            "Fallback configuration is not evidence that a fallback ran: describe only executed_action from the result. "
            "For a visible text input that is absent from the AX tree on macOS, use visual_type so capture-coordinate mapping, PID-targeted input, and exact-text verification stay in one operation. Use low-level virtual_type_at only when coordinates already come directly from current tool evidence. If visual_type returns unsupported_background_text_input with isolation_required=true, do not retry or ask to take over the user's foreground; report that a separate desktop/VM worker must be configured. "
            "Reconnect when a session or ref becomes stale."
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
    from cyrene.app_use import execute_app_use, format_app_use_result

    return format_app_use_result(await execute_app_use(args))


handler = _tool_app_use

__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler", "_tool_app_use"]
