"""Tool implementation for browser_navigate."""

from __future__ import annotations

import json
from typing import Any
from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import plugin_localized

from .definitions import get_native_tool_def

TOOL_NAME = 'browser_navigate'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_browser_navigate(args: dict[str, Any], context: PluginContext) -> str:
    from .runtime import navigate, navigation_guard
    url = str(args.get("url") or "").strip()
    if not url:
        return plugin_localized(context, "No URL was provided.", "未提供网址。")
    reason = str(args.get("reason") or "").strip()
    snapshot_token = str(args.get("snapshot_token") or "").strip()
    guard = await navigation_guard(url, reason, snapshot_token)
    if guard.get("allowed") is not True:
        from .browser_output import browser_error_text

        public_guard = dict(guard)
        public_guard["error"] = browser_error_text(
            guard,
            context,
            "The browser navigation request was rejected.",
            "浏览器导航请求已被拒绝。",
        )
        return json.dumps(public_guard, ensure_ascii=False)
    result = await navigate(url, extract_text=True)
    parts = [
        plugin_localized(
            context, "Title: {title}", "标题：{title}", title=result.get("title", "—")
        ),
        plugin_localized(
            context, "URL: {url}", "网址：{url}", url=result.get("url", url)
        ),
    ]
    from .browser_output import page_link_lines, page_observation_lines
    parts.extend(page_observation_lines(result, context))
    parts.extend(page_link_lines(result, context))
    if result.get("text"):
        parts.append(result["text"])
    if result.get("error"):
        from .browser_output import browser_error_text

        parts.append(
            plugin_localized(
                context,
                "Error: {error}",
                "错误：{error}",
                error=browser_error_text(
                    result,
                    context,
                    "The browser could not navigate to that URL.",
                    "浏览器无法导航到该网址。",
                ),
            )
        )
    return "\n\n".join(parts)


handler = _tool_browser_navigate

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_navigate"]
