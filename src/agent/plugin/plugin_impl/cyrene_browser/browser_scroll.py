"""Tool implementation for browser_scroll."""

from __future__ import annotations

from typing import Any
from agent.plugin import PluginContext

TOOL_NAME = "browser_scroll"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Scroll the page or a nested scrollable area under the target. Use when content is outside the visible area (e.g. load more results, see comments, or scroll a modal/sidebar).",
        "parameters": {
            "type": "object",
            "properties": {
                "delta_y": {"type": "integer", "description": "Pixels to scroll vertically. Positive=down, negative=up. Default 500 (about half a viewport)."},
                "ref": {"type": "string", "description": "Optional element ref from browser_snapshot. Scroll the nearest scrollable area under this element."},
                "x": {"type": "integer", "description": "Optional viewport x coordinate for the scroll gesture."},
                "y": {"type": "integer", "description": "Optional viewport y coordinate for the scroll gesture."},
            },
        },
    },
}


async def _tool_browser_scroll(args: dict[str, Any], _context: PluginContext) -> str:
    from cyrene.browser import scroll_page

    raw = args.get("delta_y")
    if raw is None:
        delta_y = 500
    else:
        try:
            delta_y = int(raw)
        except (ValueError, TypeError):
            return f"Scroll failed: invalid delta_y value '{raw}'."
    def _optional_int(name: str) -> int | None:
        value = args.get(name)
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            raise ValueError(f"invalid {name} value '{value}'") from None

    try:
        x = _optional_int("x")
        y = _optional_int("y")
    except ValueError as exc:
        return f"Scroll failed: {exc}."
    ref = str(args.get("ref") or "").strip()
    result = await scroll_page(delta_y=delta_y, x=x, y=y, ref=ref)
    if result.get("ok") is False:
        return f"Scroll failed: {result.get('error', 'unknown error')}"
    if delta_y == 0:
        return "Scroll position unchanged (delta_y=0)."
    if result.get("moved") is False:
        return "Scroll had no effect. The targeted area may already be at its boundary; choose a ref or coordinates inside another scrollable area."
    actual = result.get("actualDeltaY")
    target = result.get("target") if isinstance(result.get("target"), dict) else {}
    target_name = str(target.get("id") or (f"e{target.get('ref')}" if target.get("ref") else "") or target.get("tag") or "targeted area")
    if isinstance(actual, (int, float)) and actual != 0:
        return f"Scrolled {target_name} by {round(actual)}px."
    return f"Scrolled down at ({result.get('x')}, {result.get('y')})." if delta_y > 0 else f"Scrolled up at ({result.get('x')}, {result.get('y')})."


handler = _tool_browser_scroll

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_scroll"]
