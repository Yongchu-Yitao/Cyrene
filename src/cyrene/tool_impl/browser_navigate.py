"""Tool implementation for browser_navigate."""

from __future__ import annotations

import json
from typing import Any

from cyrene import tool_legacy as _legacy

TOOL_NAME = 'browser_navigate'
TOOL_DEF = next(td for td in _legacy.TOOL_DEFS if td["function"]["name"] == TOOL_NAME)


async def _tool_browser_navigate(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.browser import navigate, visible_link_matches
    url = str(args.get("url") or "").strip()
    if not url:
        return "No URL provided."
    reason = str(args.get("reason") or "").strip()
    if reason != "user_exact_url":
        scan = await visible_link_matches(url)
        matches = scan.get("matches") if isinstance(scan.get("matches"), list) else []
        if matches:
            return json.dumps(
                {
                    "ok": False,
                    "code": "VISIBLE_LINK_AVAILABLE",
                    "error": (
                        "Target URL is already available as a visible link on the current page. "
                        "Use browser_click_ref or browser_click_text instead of browser_navigate."
                    ),
                    "target_url": str(scan.get("targetUrl") or url),
                    "matches": [
                        {
                            "ref": str(item.get("ref") or ""),
                            "text": str(item.get("text") or ""),
                            "url": str(item.get("url") or ""),
                        }
                        for item in matches
                        if isinstance(item, dict)
                    ],
                },
                ensure_ascii=False,
            )
    result = await navigate(url, extract_text=True)
    parts = [f"Title: {result.get('title', '—')}", f"URL: {result.get('url', url)}"]
    from cyrene.tool_impl.browser_output import page_link_lines, page_observation_lines
    parts.extend(page_observation_lines(result))
    parts.extend(page_link_lines(result))
    if result.get("text"):
        parts.append(result["text"])
    if result.get("error"):
        parts.append(f"Error: {result['error']}")
    return "\n\n".join(parts)


handler = _tool_browser_navigate

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_navigate"]
