"""Tool implementation for browser_type."""

from __future__ import annotations

from typing import Any
from agent.plugin import PluginContext
from agent.plugin.native_runtime import plugin_localized

from .definitions import get_native_tool_def

TOOL_NAME = 'browser_type'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_browser_type(args: dict[str, Any], context: PluginContext) -> str:
    from .runtime import type_text
    selector = str(args.get("selector") or "").strip()
    text = str(args.get("text") or "").strip()
    submit = bool(args.get("submit", False))
    if not selector:
        return plugin_localized(
            context, "No CSS selector was provided.", "未提供 CSS 选择器。"
        )
    result = await type_text(selector, text, submit=submit)
    if result.get("ok"):
        return plugin_localized(
            context,
            "Typed into {selector}.\nURL: {url}\nTitle: {title}",
            "已在 {selector} 中输入文本。\n网址：{url}\n标题：{title}",
            selector=selector,
            url=result.get("url", "—"),
            title=result.get("title", "—"),
        )
    from .browser_output import browser_error_text

    return plugin_localized(
        context,
        "Typing failed: {error}",
        "输入失败：{error}",
        error=browser_error_text(
            result,
            context,
            "The browser could not enter text in that element.",
            "浏览器无法在该元素中输入文本。",
        ),
    )


handler = _tool_browser_type

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_type"]
