"""Tool implementation for browser_network_log."""

from __future__ import annotations

from typing import Any
from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import plugin_localized

TOOL_NAME = "browser_network_log"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Return recent resource, fetch, and XHR URLs observed by the current browser page. Useful for SPAs where content IDs are loaded through API requests.",
        "parameters": {
            "type": "object",
            "properties": {
                "max_entries": {"type": "integer", "description": "Maximum entries to return. Default 40, max 200."},
            },
        },
    },
}


async def _tool_browser_network_log(args: dict[str, Any], context: PluginContext) -> str:
    from .runtime import network_log

    try:
        max_entries = int(args.get("max_entries") or 40)
    except (TypeError, ValueError):
        max_entries = 40
    result = await network_log(max_entries=max_entries)
    if result.get("ok") is False:
        from .browser_output import browser_error_text

        return plugin_localized(
            context,
            "Network log failed: {error}",
            "网络日志读取失败：{error}",
            error=browser_error_text(
                result,
                context,
                "The browser network log could not be read.",
                "无法读取浏览器网络日志。",
            ),
        )
    entries = result.get("entries") if isinstance(result.get("entries"), list) else []
    parts = [
        plugin_localized(context, "Title: {title}", "标题：{title}", title=result.get("title", "—")),
        plugin_localized(context, "URL: {url}", "网址：{url}", url=result.get("url", "—")),
        plugin_localized(context, "Recent network entries:", "最近的网络条目："),
    ]
    if not entries:
        parts.append(
            plugin_localized(
                context,
                "No recent resource entries were found.",
                "未找到最近的资源条目。",
            )
        )
    else:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "")
            typ = str(entry.get("type") or "")
            duration = entry.get("durationMs", 0)
            size = entry.get("transferSize", 0)
            kind = typ or plugin_localized(context, "resource", "资源")
            parts.append(
                plugin_localized(
                    context,
                    "- {kind} {name} ({duration} ms, {size} bytes)",
                    "- {kind} {name}（{duration} 毫秒，{size} 字节）",
                    kind=kind,
                    name=name,
                    duration=duration,
                    size=size,
                )
            )
    return "\n".join(parts)


handler = _tool_browser_network_log

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_network_log"]
