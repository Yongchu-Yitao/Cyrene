"""Tool implementation for browser_request_takeover."""

from __future__ import annotations

import logging
from typing import Any

from cyrene.core.plugin import PluginContext
from cyrene.core.plugin.execution import require_plugin_execution
from .definitions import get_native_tool_def
from cyrene.plugins.native_runtime import (
    json_result,
    plugin_localized,
    publish_runtime_event,
    run_context_value,
)

TOOL_NAME = 'browser_request_takeover'
TOOL_DEF = get_native_tool_def(TOOL_NAME)
logger = logging.getLogger(__name__)


async def _tool_browser_request_takeover(args: dict[str, Any], context: PluginContext) -> str:
    from . import runtime

    if str(run_context_value(context, "agent_id", "main")) != "main":
        return plugin_localized(
            context,
            "Only the main Agent can request browser takeover.",
            "只有主 Agent 可以请求接管浏览器。",
        )
    round_id = str(run_context_value(context, "round_id") or "").strip()
    if not round_id:
        return plugin_localized(
            context,
            "Browser takeover requires an active chat turn.",
            "只能在进行中的聊天轮次请求接管浏览器。",
        )

    reason = str(args.get("reason") or "").strip() or plugin_localized(
        context,
        'Complete the sign-in in the browser window, then choose "Done".',
        "请在浏览器窗口完成登录，然后选择“我已完成”。",
    )

    session = None
    if runtime.electron_browser_available():
        try:
            current_url = await runtime.electron_current_url()
        except Exception:
            current_url = ""
    else:
        try:
            session = await runtime.get_session()
            current_url = await session.current_url()
        except Exception:
            logger.warning("Browser takeover runtime is unavailable", exc_info=True)
            return plugin_localized(
                context,
                "Browser takeover is unavailable because Playwright/Chromium is not ready.",
                "浏览器接管不可用，因为 Playwright/Chromium 尚未就绪。",
            )

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
    if not runtime.electron_browser_available():
        try:
            await session.switch_to_headed(current_url)
        except Exception:
            logger.warning("Failed to open browser takeover window", exc_info=True)
            await publish_runtime_event(context, {
                "type": "browser_takeover_cancelled",
                "session_id": str(run_context_value(context, "session_id") or ""),
                "round_id": round_id,
            })
            return plugin_localized(
                context,
                "Failed to open the browser window for takeover.",
                "无法打开浏览器窗口进行接管。",
            )
    return json_result({
        "status": "awaiting_user",
        "question_id": question_id,
        "kind": "browser_takeover",
        "text": reason,
        "options": [plugin_localized(context, "Done", "我已完成")],
        "allow_custom": False,
        "takeover": True,
        "embedded": runtime.electron_browser_available(),
    })


handler = _tool_browser_request_takeover

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_browser_request_takeover"]
