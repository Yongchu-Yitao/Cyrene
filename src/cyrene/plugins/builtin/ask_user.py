"""Standalone user-clarification Plugin, directly exposed to the main Agent."""

from __future__ import annotations

from typing import Any

from cyrene.core.plugin import Plugin, PluginContext
from cyrene.core.plugin.execution import require_plugin_execution
from cyrene.plugins.native_runtime import json_result, plugin_localized, run_context_value

TOOL_NAME = 'ask_user'
TOOL_DEF = {'type': 'function',
 'function': {'name': 'ask_user',
              'description': 'Ask the user a clarification question and pause until they answer. '
                             'Use this liberally — asking is better than assuming. Trigger when: '
                             'the request is ambiguous, details are missing, multiple reasonable '
                             'approaches exist, or you need sign-off before a risky action. If you '
                             'need to ask the user anything, use this tool instead of putting a '
                             'question in assistant text. Use freeform text for open questions, or '
                             'add a short options array for structured choices. The UI always '
                             'allows custom answers even with options.',
              'parameters': {'type': 'object',
                             'properties': {'text': {'type': 'string',
                                                     'description': 'The clarification question to '
                                                                    'show the user.'},
                                            'options': {'type': 'array',
                                                        'description': 'Optional short option '
                                                                       'labels when structured '
                                                                       'choices would help.',
                                                        'items': {'type': 'string'}}},
                             'required': ['text']}}}
TOOL_METADATA = {"agent_exposure": "direct", "main_only": True}


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

plugin = Plugin(
    name=TOOL_NAME,
    description=TOOL_DEF["function"]["description"],
    input_schema=TOOL_DEF["function"]["parameters"],
    handler=handler,
    allow_parallel=False,
    timeout_seconds=180.0,
    metadata=TOOL_METADATA,
)

__all__ = ["plugin", "TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler", "_tool_ask_user"]
