"""Tool implementation for browser_tab_close."""

from __future__ import annotations

from typing import Any

TOOL_NAME = "browser_tab_close"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Close an embedded browser tab by tab id, or close the active tab when no id is provided.",
        "parameters": {
            "type": "object",
            "properties": {
                "tab_id": {"type": "string", "description": "Optional tab id returned by browser_tab_list. If omitted, closes the active tab."},
            },
        },
    },
}


async def _tool_browser_tab_close(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.browser import close_tab

    tab_id = str(args.get("tab_id") or args.get("tabId") or "").strip()
    result = await close_tab(tab_id)
    if result.get("ok") is False:
        return f"Close browser tab failed: {result.get('error', 'unknown error')}"
    active = result.get("activeTab") if isinstance(result.get("activeTab"), dict) else None
    if active:
        return f"Closed browser tab. Active tab is now {active.get('id')}.\nURL: {active.get('url', 'about:blank')}"
    return "Closed browser tab. No browser tabs remain open."


handler = _tool_browser_tab_close

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_tab_close"]
