"""Tool implementation for browser_tab_list."""

from __future__ import annotations

from typing import Any
from agent.plugin import PluginContext
from agent.plugin.native_runtime import plugin_localized

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


async def _tool_browser_tab_list(args: dict[str, Any], context: PluginContext) -> str:
    from .runtime import list_tabs

    result = await list_tabs()
    if result.get("ok") is False:
        from .browser_output import browser_error_text

        return plugin_localized(
            context,
            "Browser tabs are unavailable: {error}",
            "浏览器标签页不可用：{error}",
            error=browser_error_text(
                result,
                context,
                "The browser tabs could not be listed.",
                "无法列出浏览器标签页。",
            ),
        )
    tabs = result.get("tabs") if isinstance(result.get("tabs"), list) else []
    if not tabs:
        return plugin_localized(
            context, "No browser tabs are open.", "当前没有打开的浏览器标签页。"
        )
    lines = []
    active_id = str(result.get("activeTabId") or "")
    for tab in tabs:
        if not isinstance(tab, dict):
            continue
        marker = "*" if str(tab.get("id") or "") == active_id else "-"
        flags = []
        if tab.get("loading"):
            flags.append(plugin_localized(context, "loading", "加载中"))
        if tab.get("audible"):
            flags.append(plugin_localized(context, "audible", "正在播放声音"))
        if tab.get("muted"):
            flags.append(plugin_localized(context, "muted", "已静音"))
        suffix = f" ({', '.join(flags)})" if flags else ""
        title = tab.get("title") or plugin_localized(context, "Untitled", "无标题")
        lines.append(
            f"{marker} {tab.get('id')}: {title}\n  {tab.get('url') or 'about:blank'}{suffix}"
        )
    return "\n".join(lines)


handler = _tool_browser_tab_list

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_tab_list"]
