"""Tool implementation for browser_tab_close."""

from __future__ import annotations

from typing import Any
from agent.plugin import PluginContext
from agent.plugin.native_runtime import plugin_localized

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


async def _tool_browser_tab_close(args: dict[str, Any], context: PluginContext) -> str:
    from .runtime import close_tab

    tab_id = str(args.get("tab_id") or args.get("tabId") or "").strip()
    result = await close_tab(tab_id)
    if result.get("ok") is False:
        from .browser_output import browser_error_text

        return plugin_localized(
            context,
            "Closing the browser tab failed: {error}",
            "关闭浏览器标签页失败：{error}",
            error=browser_error_text(
                result,
                context,
                "The browser tab could not be closed.",
                "无法关闭浏览器标签页。",
            ),
        )
    active = result.get("activeTab") if isinstance(result.get("activeTab"), dict) else None
    if active:
        return plugin_localized(
            context,
            "Closed the browser tab. The active tab is now {tab}.\nURL: {url}",
            "已关闭浏览器标签页。当前活动标签页为 {tab}。\n网址：{url}",
            tab=active.get("id"),
            url=active.get("url", "about:blank"),
        )
    return plugin_localized(
        context,
        "Closed the browser tab. No browser tabs remain open.",
        "已关闭浏览器标签页。当前没有其他打开的浏览器标签页。",
    )


handler = _tool_browser_tab_close

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_tab_close"]
