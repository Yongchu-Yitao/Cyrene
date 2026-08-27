"""Tool implementation for browser_tab_list."""

from __future__ import annotations

from typing import Any
from agent.plugin import PluginContext

TOOL_NAME = "browser_tab_list"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "List open tabs in the embedded desktop browser, including the active tab, URL, title, loading, audible, and muted state.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}


async def _tool_browser_tab_list(args: dict[str, Any], _context: PluginContext) -> str:
    from cyrene.browser import list_tabs

    result = await list_tabs()
    if result.get("ok") is False:
        return f"Browser tabs unavailable: {result.get('error', 'unknown error')}"
    tabs = result.get("tabs") if isinstance(result.get("tabs"), list) else []
    if not tabs:
        return "No browser tabs are open."
    lines = []
    active_id = str(result.get("activeTabId") or "")
    for tab in tabs:
        if not isinstance(tab, dict):
            continue
        marker = "*" if str(tab.get("id") or "") == active_id else "-"
        flags = []
        if tab.get("loading"):
            flags.append("loading")
        if tab.get("audible"):
            flags.append("audible")
        if tab.get("muted"):
            flags.append("muted")
        suffix = f" ({', '.join(flags)})" if flags else ""
        lines.append(f"{marker} {tab.get('id')}: {tab.get('title') or 'Untitled'}\n  {tab.get('url') or 'about:blank'}{suffix}")
    return "\n".join(lines)


handler = _tool_browser_tab_list

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_tab_list"]
