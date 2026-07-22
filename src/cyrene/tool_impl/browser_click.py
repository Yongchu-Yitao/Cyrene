"""Tool implementation for browser_click."""

from __future__ import annotations

from typing import Any

from cyrene import tool_legacy as _legacy

TOOL_NAME = 'browser_click'
TOOL_DEF = next(td for td in _legacy.TOOL_DEFS if td["function"]["name"] == TOOL_NAME)


async def _tool_browser_click(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.browser import click
    selector = str(args.get("selector") or "").strip()
    if not selector:
        return "No CSS selector provided."
    result = await click(selector)
    if result.get("ok"):
        from cyrene.tool_impl.browser_output import page_observation_lines

        parts = [f"Clicked {selector}.", f"URL: {result.get('url', '—')}", f"Title: {result.get('title', '—')}" ]
        if result.get("opened_new_tab"):
            parts.append(
                f"Opened new active tab: {result.get('active_tab_id') or result.get('tabId') or '—'} "
                f"(source tab: {result.get('source_tab_id', '—')}, source URL: {result.get('source_url', '—')})"
            )
        parts.extend(page_observation_lines(result))
        return "\n".join(parts)
    from cyrene.tool_impl.browser_output import file_chooser_instruction
    chooser = file_chooser_instruction(result)
    if chooser:
        return chooser
    return f"Click failed: {result.get('error', 'unknown error')}"


handler = _tool_browser_click

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_click"]
