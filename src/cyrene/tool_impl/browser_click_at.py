"""Tool implementation for browser_click_at."""

from __future__ import annotations

from typing import Any

TOOL_NAME = "browser_click_at"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Click the current browser page at viewport coordinates. Use only when snapshot refs/selectors are insufficient and a screenshot provides reliable coordinates.",
        "parameters": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Viewport x coordinate."},
                "y": {"type": "integer", "description": "Viewport y coordinate."},
            },
            "required": ["x", "y"],
        },
    },
}


async def _tool_browser_click_at(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.browser import click_at

    try:
        x = int(args.get("x"))
        y = int(args.get("y"))
    except (TypeError, ValueError):
        return "Click failed: invalid coordinates."
    result = await click_at(x, y)
    if result.get("ok"):
        from cyrene.tool_impl.browser_output import page_observation_lines

        parts = [f"Clicked at {x},{y}.", f"URL: {result.get('url', '—')}", f"Title: {result.get('title', '—')}" ]
        parts.extend(page_observation_lines(result))
        return "\n".join(parts)
    from cyrene.tool_impl.browser_output import file_chooser_instruction
    chooser = file_chooser_instruction(result)
    if chooser:
        return chooser
    return f"Click failed: {result.get('error', 'unknown error')}"


handler = _tool_browser_click_at

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_click_at"]
