"""Tool implementation for browser_screenshot."""

from __future__ import annotations

from typing import Any

TOOL_NAME = 'browser_screenshot'
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Take a screenshot of the current browser page, or navigate to a URL first if one is provided.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Optional URL to screenshot. Omit to screenshot the current page."},
            },
        },
    },
}


async def _tool_browser_screenshot(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.browser import screenshot
    url = str(args.get("url") or "").strip()
    result = await screenshot(url)
    if result.get("ok"):
        return f"Screenshot taken.\nPath: {result.get('path', '—')}\nTitle: {result.get('title', '—')}"
    return f"Screenshot failed: {result.get('error', 'unknown error')}"


handler = _tool_browser_screenshot

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_screenshot"]
