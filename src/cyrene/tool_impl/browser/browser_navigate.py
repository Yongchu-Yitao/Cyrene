"""Tool implementation for browser_navigate."""

from __future__ import annotations

import json
from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def

TOOL_NAME = 'browser_navigate'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_browser_navigate(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.browser import navigate, navigation_guard
    url = str(args.get("url") or "").strip()
    if not url:
        return "No URL provided."
    reason = str(args.get("reason") or "").strip()
    snapshot_token = str(args.get("snapshot_token") or "").strip()
    guard = await navigation_guard(url, reason, snapshot_token)
    if guard.get("allowed") is not True:
        return json.dumps(guard, ensure_ascii=False)
    result = await navigate(url, extract_text=True)
    parts = [f"Title: {result.get('title', '—')}", f"URL: {result.get('url', url)}"]
    from cyrene.tool_impl.browser.browser_output import page_link_lines, page_observation_lines
    parts.extend(page_observation_lines(result))
    parts.extend(page_link_lines(result))
    if result.get("text"):
        parts.append(result["text"])
    if result.get("error"):
        parts.append(f"Error: {result['error']}")
    return "\n\n".join(parts)


handler = _tool_browser_navigate

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_navigate"]
