"""Tool implementation for ask_user."""

from __future__ import annotations

from typing import Any

from .definitions import get_native_tool_def
from agent.plugin import PluginContext
from agent.plugin.execution import require_plugin_execution
from agent.plugin.native_runtime import json_result, run_context_value

TOOL_NAME = 'ask_user'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_ask_user(args: dict[str, Any], context: PluginContext) -> str:
    text = str(args.get("text", "") or "").strip()
    if not text:
        return "Error: 'text' is required."

    if str(run_context_value(context, "agent_id", "main")) != "main":
        return "Only the main agent can ask the user a clarification question."

    round_id = str(run_context_value(context, "round_id") or "").strip()
    if not round_id:
        return "Cannot ask the user a question outside an active chat round."

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
        "option_count": len(options[:6]),
        "round_id": round_id,
        "client_request_id": str(run_context_value(context, "client_request_id") or ""),
    })


handler = _tool_ask_user

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_ask_user"]
