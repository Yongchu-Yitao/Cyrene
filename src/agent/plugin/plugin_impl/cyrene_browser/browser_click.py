"""Tool implementation for browser_click."""

from __future__ import annotations

from typing import Any
from agent.plugin import PluginContext
from agent.plugin.native_runtime import plugin_localized

from .definitions import get_native_tool_def

TOOL_NAME = 'browser_click'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_browser_click(args: dict[str, Any], context: PluginContext) -> str:
    from .runtime import click
    selector = str(args.get("selector") or "").strip()
    if not selector:
        return plugin_localized(
            context, "No CSS selector was provided.", "未提供 CSS 选择器。"
        )
    result = await click(selector)
    if result.get("ok"):
        from .browser_output import page_observation_lines

        parts = [
            plugin_localized(context, "Clicked {selector}.", "已点击 {selector}。", selector=selector),
            plugin_localized(context, "URL: {url}", "网址：{url}", url=result.get("url", "—")),
            plugin_localized(context, "Title: {title}", "标题：{title}", title=result.get("title", "—")),
        ]
        if result.get("opened_new_tab"):
            parts.append(
                plugin_localized(
                    context,
                    "Opened new active tab: {tab} (source tab: {source_tab}, source URL: {source_url})",
                    "已打开新的活动标签页：{tab}（来源标签页：{source_tab}，来源网址：{source_url}）",
                    tab=result.get("active_tab_id") or result.get("tabId") or "—",
                    source_tab=result.get("source_tab_id", "—"),
                    source_url=result.get("source_url", "—"),
                )
            )
        parts.extend(page_observation_lines(result, context))
        return "\n".join(parts)
    from .browser_output import browser_error_text, file_chooser_instruction
    chooser = file_chooser_instruction(result, context)
    if chooser:
        return chooser
    return plugin_localized(
        context,
        "Click failed: {error}",
        "点击失败：{error}",
        error=browser_error_text(
            result,
            context,
            "The browser element could not be clicked.",
            "无法点击浏览器元素。",
        ),
    )


handler = _tool_browser_click

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_click"]
