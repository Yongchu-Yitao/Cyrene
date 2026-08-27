"""Tool implementation for browser_click_ref."""

from __future__ import annotations

from typing import Any
from agent.plugin import PluginContext
from agent.plugin.native_runtime import plugin_localized

TOOL_NAME = "browser_click_ref"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Click an element returned by browser_snapshot using its ref (for example e12). Prefer this over CSS selectors on SPA pages with dynamic classes.",
        "parameters": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "Element ref from browser_snapshot, such as e12."},
            },
            "required": ["ref"],
        },
    },
}


async def _tool_browser_click_ref(args: dict[str, Any], context: PluginContext) -> str:
    from .runtime import click_ref

    ref = str(args.get("ref") or "").strip()
    if not ref:
        return plugin_localized(
            context,
            "Click failed: no element ref was provided.",
            "点击失败：未提供元素 ref。",
        )
    result = await click_ref(ref)
    if result.get("ok"):
        from .browser_output import page_observation_lines

        parts = [
            plugin_localized(context, "Clicked {ref}.", "已点击 {ref}。", ref=ref),
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


handler = _tool_browser_click_ref

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_click_ref"]
