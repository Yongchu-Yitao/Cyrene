"""Tool implementation for browser_tab_select."""

from __future__ import annotations

from typing import Any
from agent.plugin import PluginContext
from agent.plugin.native_runtime import plugin_localized

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


async def _tool_browser_tab_select(args: dict[str, Any], context: PluginContext) -> str:
    from .runtime import select_tab

    tab_id = str(args.get("tab_id") or args.get("tabId") or "").strip()
    if not tab_id:
        return plugin_localized(context, "No tab_id was provided.", "未提供 tab_id。")
    result = await select_tab(tab_id)
    if result.get("ok") is False:
        from .browser_output import browser_error_text

        return plugin_localized(
            context,
            "Selecting the browser tab failed: {error}",
            "选择浏览器标签页失败：{error}",
            error=browser_error_text(
                result,
                context,
                "The browser tab could not be selected.",
                "无法选择浏览器标签页。",
            ),
        )
    active = result.get("activeTab") if isinstance(result.get("activeTab"), dict) else {}
    return plugin_localized(
        context,
        "Selected browser tab {tab}.\nURL: {url}\nTitle: {title}",
        "已选择浏览器标签页 {tab}。\n网址：{url}\n标题：{title}",
        tab=active.get("id", tab_id),
        url=active.get("url", "about:blank"),
        title=active.get("title", "—"),
    )


handler = _tool_browser_tab_select

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_tab_select"]
