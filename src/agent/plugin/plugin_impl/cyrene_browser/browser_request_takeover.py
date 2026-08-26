"""Tool implementation for browser_request_takeover."""

from __future__ import annotations

from typing import Any

from .definitions import get_native_tool_def
from cyrene.tooling.runtime_api import (
    json_result,
)

TOOL_NAME = 'browser_request_takeover'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_browser_request_takeover(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene import browser as _browser
    from cyrene.observability import debug
    from cyrene.browser import get_session
    from cyrene.agent.context import get_current_agent_id, get_current_client_request_id, get_current_round_id, get_current_session_id
    from cyrene.agent.session import clear_pending_question, upsert_pending_question, get_session_labels

    if get_current_agent_id() != "main":
        return "Only the main agent can request a browser takeover."
    round_id = str(get_current_round_id() or "").strip()
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
    labels = get_session_labels(round_id)
    question = await upsert_pending_question({
        "text": reason,
        "round_id": round_id,
        "round_title": labels.get("round_title", ""),
        "client_request_id": str(get_current_client_request_id() or "").strip(),
        "options": ["我已完成登录"],
        "allow_custom": False,
        "meta": {"kind": "browser_takeover", "url": current_url},
    })
    await debug.publish_event({
        "type": "browser_takeover_request",
        "session_id": str(get_current_session_id() or ""),
        "round_id": round_id,
        "url": current_url,
        "reason": reason,
        "question_id": question.get("id", ""),
    })
    if not _browser.electron_browser_available():
        try:
            await session.switch_to_headed(current_url)
        except Exception as exc:
            # Couldn't open the window — undo the pending question and clear the panel.
            try:
                await clear_pending_question(str(question.get("id", "")))
            except Exception:
                pass
            await debug.publish_event({
                "type": "browser_takeover_cancelled",
                "session_id": str(get_current_session_id() or ""),
                "round_id": round_id,
            })
            return f"Failed to open the browser window for takeover: {exc}"
    return json_result({
        "status": "awaiting_user",
        "question_id": question.get("id", ""),
        "takeover": True,
        "embedded": _browser.electron_browser_available(),
    })


handler = _tool_browser_request_takeover

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_request_takeover"]
