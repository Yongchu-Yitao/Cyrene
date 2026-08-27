"""Tool implementation for browser_request_takeover."""

from __future__ import annotations

from typing import Any

from agent.plugin import PluginContext
from agent.plugin.execution import require_plugin_execution
from .definitions import get_native_tool_def
from agent.plugin.native_runtime import (
    json_result,
    publish_runtime_event,
    run_context_value,
)

TOOL_NAME = 'browser_request_takeover'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_browser_request_takeover(args: dict[str, Any], context: PluginContext) -> str:
    from cyrene import browser as _browser
    from cyrene.browser import get_session

    if str(run_context_value(context, "agent_id", "main")) != "main":
        return "Only the main agent can request a browser takeover."
    round_id = str(run_context_value(context, "round_id") or "").strip()
    if not round_id:
        return "Cannot request a browser takeover outside an active chat round."

    reason = str(args.get("reason") or "").strip() or "请在浏览器窗口完成登录，然后点「我已完成登录」。"

    session = None
    if _browser.electron_browser_available():
        try:
            current_url = await _browser.electron_current_url()
        except Exception:
            current_url = ""
    else:
        try:
            session = await get_session()
        except Exception as exc:
            return f"Browser takeover unavailable (Playwright/Chromium not ready): {exc}"
        current_url = await session.current_url()

    # Ask in the app FIRST (the standard question popup), then open the real
    # browser window. The browser side panel also receives the question id so it
    # can offer the same "finished login" confirmation in place.
    question_id = f"question_{require_plugin_execution().call.id[:24]}"
    await publish_runtime_event(context, {
        "type": "browser_takeover_request",
        "session_id": str(run_context_value(context, "session_id") or ""),
        "round_id": round_id,
        "url": current_url,
        "reason": reason,
        "question_id": question_id,
    })
    if not _browser.electron_browser_available():
        try:
            await session.switch_to_headed(current_url)
        except Exception as exc:
            await publish_runtime_event(context, {
                "type": "browser_takeover_cancelled",
                "session_id": str(run_context_value(context, "session_id") or ""),
                "round_id": round_id,
            })
            return f"Failed to open the browser window for takeover: {exc}"
    return json_result({
        "status": "awaiting_user",
        "question_id": question_id,
        "takeover": True,
        "embedded": _browser.electron_browser_available(),
    })


handler = _tool_browser_request_takeover

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_request_takeover"]
