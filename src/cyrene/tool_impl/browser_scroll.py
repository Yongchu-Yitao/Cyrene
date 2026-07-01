"""Tool implementation for browser_scroll."""

from __future__ import annotations

from typing import Any

TOOL_NAME = "browser_scroll"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Scroll the current page vertically. Use when the page has content below the visible area that you need to reach (e.g. load more results, see comments, find a button).",
        "parameters": {
            "type": "object",
            "properties": {
                "delta_y": {"type": "integer", "description": "Pixels to scroll vertically. Positive=down, negative=up. Default 500 (about half a viewport)."},
            },
        },
    },
}


async def _tool_browser_scroll(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.browser import scroll_page

    raw = args.get("delta_y")
    if raw is None:
        delta_y = 500
    else:
        try:
            delta_y = int(raw)
        except (ValueError, TypeError):
            return f"Scroll failed: invalid delta_y value '{raw}'."
    result = await scroll_page(delta_y=delta_y)
    if result.get("ok") is False:
        return f"Scroll failed: {result.get('error', 'unknown error')}"
    if delta_y == 0:
        return "Scroll position unchanged (delta_y=0)."
    return f"Scrolled {delta_y}px."


handler = _tool_browser_scroll

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_scroll"]
