"""Tool implementation for browser_network_log."""

from __future__ import annotations

from typing import Any

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


async def _tool_browser_network_log(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.browser import network_log

    try:
        max_entries = int(args.get("max_entries") or 40)
    except (TypeError, ValueError):
        max_entries = 40
    result = await network_log(max_entries=max_entries)
    if result.get("ok") is False:
        return f"Network log failed: {result.get('error', 'unknown error')}"
    entries = result.get("entries") if isinstance(result.get("entries"), list) else []
    parts = [
        f"Title: {result.get('title', '—')}",
        f"URL: {result.get('url', '—')}",
        "Recent network entries:",
    ]
    if not entries:
        parts.append("No recent resource entries found.")
    else:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "")
            typ = str(entry.get("type") or "")
            duration = entry.get("durationMs", 0)
            size = entry.get("transferSize", 0)
            parts.append(f"- {typ or 'resource'} {name} ({duration}ms, {size} bytes)")
    return "\n".join(parts)


handler = _tool_browser_network_log

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_network_log"]
