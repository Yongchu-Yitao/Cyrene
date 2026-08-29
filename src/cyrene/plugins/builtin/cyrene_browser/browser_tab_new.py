"""Tool implementation for browser_tab_new."""

from __future__ import annotations

from typing import Any
from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import plugin_localized

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


async def _tool_browser_tab_new(args: dict[str, Any], context: PluginContext) -> str:
    from .runtime import new_tab

    result = await new_tab(str(args.get("url") or "about:blank"))
    if result.get("ok") is False:
        from .browser_output import browser_error_text

        return plugin_localized(
            context,
            "New browser tab failed: {error}",
            "新建浏览器标签页失败：{error}",
            error=browser_error_text(
                result,
                context,
                "The browser tab could not be created.",
                "无法创建浏览器标签页。",
            ),
        )
    active = result.get("activeTab") if isinstance(result.get("activeTab"), dict) else {}
    return plugin_localized(
        context,
        "Opened browser tab {tab}.\nURL: {url}\nTitle: {title}",
        "已打开浏览器标签页 {tab}。\n网址：{url}\n标题：{title}",
        tab=active.get("id", "—"),
        url=active.get("url", "about:blank"),
        title=active.get("title", "—"),
    )


handler = _tool_browser_tab_new

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_tab_new"]
