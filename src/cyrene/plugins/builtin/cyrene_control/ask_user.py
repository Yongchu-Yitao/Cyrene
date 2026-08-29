"""Tool implementation for ask_user."""

from __future__ import annotations

from typing import Any

from .definitions import get_native_tool_def
from cyrene.core.plugin import PluginContext
from cyrene.core.plugin.execution import require_plugin_execution
from cyrene.plugins.native_runtime import json_result, plugin_localized, run_context_value

TOOL_NAME = 'ask_user'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_ask_user(args: dict[str, Any], context: PluginContext) -> str:
    text = str(args.get("text", "") or "").strip()
    if not text:
        return plugin_localized(
            context,
            "Error: 'text' is required.",
            "错误：必须提供 'text'。",
        )

    if str(run_context_value(context, "agent_id", "main")) != "main":
        return plugin_localized(
            context,
            "Only the main agent can ask the user a clarification question.",
            "只有主 Agent 可以请用户补充说明。",
        )

    round_id = str(run_context_value(context, "round_id") or "").strip()
    if not round_id:
        return plugin_localized(
            context,
            "Cannot ask the user a question outside an active chat round.",
            "只能在活动的对话轮次中向用户提问。",
        )

    # Pass options through as-is; _normalize_pending_question (via
    # upsert_pending_question) handles both plain strings and the option objects
    # models sometimes emit, extracting the label and capping the count. Calling
    # str() on a dict here would leak `{'id':.., 'label':..}` into the UI labels.
    raw_options = args.get("options", [])
    options = raw_options if isinstance(raw_options, list) else []

    question_id = f"question_{require_plugin_execution().call.id[:24]}"
    return json_result({
        "status": "awaiting_user",
        "question_id": question_id,
        "kind": "clarification",
        "text": text,
        "options": options[:6],
        "allow_custom": bool(args.get("allow_custom", True)),
        "option_count": len(options[:6]),
        "round_id": round_id,
        "client_request_id": str(run_context_value(context, "client_request_id") or ""),
    })


handler = _tool_ask_user

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_ask_user"]
