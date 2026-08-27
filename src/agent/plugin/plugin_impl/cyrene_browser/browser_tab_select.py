"""Tool implementation for browser_tab_select."""

from __future__ import annotations

from typing import Any
from agent.plugin import PluginContext

TOOL_NAME = "browser_tab_select"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Select an existing embedded browser tab by tab id. Use browser_tab_list first when you do not know the id.",
        "parameters": {
            "type": "object",
            "properties": {
                "tab_id": {"type": "string", "description": "The tab id returned by browser_tab_list."},
            },
            "required": ["tab_id"],
        },
    },
}


async def _tool_browser_tab_select(args: dict[str, Any], _context: PluginContext) -> str:
    from cyrene.browser import select_tab

    tab_id = str(args.get("tab_id") or args.get("tabId") or "").strip()
    if not tab_id:
        return "No tab_id provided."
    result = await select_tab(tab_id)
    if result.get("ok") is False:
        return f"Select browser tab failed: {result.get('error', 'unknown error')}"
    active = result.get("activeTab") if isinstance(result.get("activeTab"), dict) else {}
    return f"Selected browser tab {active.get('id', tab_id)}.\nURL: {active.get('url', 'about:blank')}\nTitle: {active.get('title', '—')}"


handler = _tool_browser_tab_select

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_tab_select"]
