"""Tool implementation for browser_tab_new."""

from __future__ import annotations

from typing import Any

TOOL_NAME = "browser_tab_new"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Open a new tab (only when the user explicitly asks to keep a page open while browsing something else). After this, browser_navigate will navigate the NEW tab. Do NOT call this just to visit a different URL — reuse the same tab with browser_navigate instead.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Optional URL for the new tab. Defaults to about:blank."},
            },
        },
    },
}


async def _tool_browser_tab_new(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.browser import new_tab

    result = await new_tab(str(args.get("url") or "about:blank"))
    if result.get("ok") is False:
        return f"New browser tab failed: {result.get('error', 'unknown error')}"
    active = result.get("activeTab") if isinstance(result.get("activeTab"), dict) else {}
    return f"Opened browser tab {active.get('id', '—')}.\nURL: {active.get('url', 'about:blank')}\nTitle: {active.get('title', '—')}"


handler = _tool_browser_tab_new

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_tab_new"]
