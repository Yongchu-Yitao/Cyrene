"""Tool implementation for PromptClaudeCode."""

from __future__ import annotations

from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_api import (
    CC_PROJECT_DIR,
    json_result,
)

TOOL_NAME = 'PromptClaudeCode'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_prompt_claude_code(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    task = str(args.get("task", "") or "").strip()
    if not task:
        return "Error: 'task' is required."

    from cyrene.agent.context import get_current_agent_id, get_current_client_request_id, get_current_round_id
    from cyrene.agent.session import upsert_pending_question, get_session_labels
    from cyrene.agent.prompts import build_claude_code_question_payload, optimize_claude_code_prompt
    from cyrene.tooling.backends.claude_code_bridge import get_cc_status

    if get_current_agent_id() != "main":
        return "Only the main agent can prepare a Claude Code prompt for user confirmation."

    round_id = str(get_current_round_id() or "").strip()
    if not round_id:
        return "Cannot prepare a Claude Code prompt outside an active chat round."

    status = get_cc_status(CC_PROJECT_DIR)
    if not bool(status.get("available")):
        reason = str(status.get("reason") or "Claude Code is not running.").strip()
        return json_result({
            "status": "error",
            "reason": reason,
            "can_launch": bool(status.get("can_launch")),
        })

    optimized_prompt = await optimize_claude_code_prompt(task)
    payload = build_claude_code_question_payload(
        task,
        optimized_prompt,
        tmux_session=str(status.get("tmux_session") or "").strip(),
    )
    labels = get_session_labels(round_id)
    question = await upsert_pending_question({
        "text": payload["text"],
        "round_id": round_id,
        "round_title": labels.get("round_title", ""),
        "client_request_id": str(get_current_client_request_id() or "").strip(),
        "options": payload["options"],
        "allow_custom": bool(payload.get("allow_custom", True)),
        "meta": payload.get("meta", {}),
    })
    return json_result({
        "status": "awaiting_user",
        "question_id": question.get("id", ""),
        "prompt": optimized_prompt,
        "tmux_session": str(status.get("tmux_session") or "").strip(),
    })


handler = _tool_prompt_claude_code

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_prompt_claude_code"]
