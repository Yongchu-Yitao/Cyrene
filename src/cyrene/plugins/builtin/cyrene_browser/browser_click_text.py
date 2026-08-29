"""Tool implementation for browser_click_text."""

from __future__ import annotations

from typing import Any
from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import plugin_localized

TOOL_NAME = "browser_click_text"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Click the first visible element whose text or accessible label matches the provided text.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Visible text or accessible label to click."},
                "exact": {"type": "boolean", "description": "Require an exact normalized text match. Default false."},
            },
            "required": ["text"],
        },
    },
}


async def _tool_browser_click_text(args: dict[str, Any], context: PluginContext) -> str:
    from .runtime import click_text

    text = str(args.get("text") or "").strip()
    exact = bool(args.get("exact", False))
    if not text:
        return plugin_localized(
            context,
            "Click failed: no text was provided.",
            "点击失败：未提供文本。",
        )
    result = await click_text(text, exact=exact)
    if result.get("ok"):
        from .browser_output import page_observation_lines

        parts = [
            plugin_localized(context, "Clicked text {text!r}.", "已点击文本 {text!r}。", text=text),
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


handler = _tool_browser_click_text

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_click_text"]
