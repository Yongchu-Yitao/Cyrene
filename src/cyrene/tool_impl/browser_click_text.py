"""Tool implementation for browser_click_text."""

from __future__ import annotations

from typing import Any

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


async def _tool_browser_click_text(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.browser import click_text

    text = str(args.get("text") or "").strip()
    exact = bool(args.get("exact", False))
    if not text:
        return "Click failed: no text provided."
    result = await click_text(text, exact=exact)
    if result.get("ok"):
        from cyrene.tool_impl.browser_output import page_observation_lines

        parts = [f"Clicked text {text!r}.", f"URL: {result.get('url', '—')}", f"Title: {result.get('title', '—')}" ]
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


handler = _tool_browser_click_text

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_click_text"]
