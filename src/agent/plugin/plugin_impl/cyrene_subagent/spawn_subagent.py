"""Tool implementation for spawn_subagent."""

from __future__ import annotations

from typing import Any

from .definitions import get_native_tool_def
from cyrene.tooling.runtime_api import (
    register_subagent,
    run_subagent,
    spawn_subagent_task,
)

TOOL_NAME = 'spawn_subagent'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_spawn_subagent(args: dict[str, Any], bot: Any, chat_id: int, db_path: str, _notify_state: dict[str, bool] | None) -> str:
    """Spawn a sub-agent to handle a specific task."""
    agent_id = str(args.get("agent_id", ""))
    task = str(args.get("task", ""))
    use_secondary = bool(args.get("use_secondary", False))
    role = str(args.get("role", ""))
    mode = str(args.get("mode", "")).strip().lower()
    success_criteria = args.get("success_criteria")
    max_messages = args.get("max_messages")
    discussion_id = str(args.get("discussion_id", "") or "").strip()
    if role and role not in ("moderator", "participant"):
        role = ""
    if mode not in ("", "execution", "discussion"):
        mode = ""
    if role:
        mode = "discussion"
    elif not mode:
        mode = "execution"
    if not isinstance(success_criteria, list):
        success_criteria = []
    if max_messages is not None:
        try:
            max_messages = max(1, min(50, int(max_messages)))
        except (TypeError, ValueError):
            max_messages = None
    if not agent_id or not task:
        return "Error: agent_id and task are required."
    from cyrene.agent.context import get_current_agent_id, get_current_round_id, get_current_session_id
    if get_current_agent_id() != "main":
        return "Only the main agent can spawn subagents."
    session_id = get_current_session_id()
    registered = await register_subagent(
        agent_id,
        task,
        round_id=get_current_round_id(),
        role=role,
        session_id=session_id,
        mode=mode,
        success_criteria=success_criteria,
        discussion_max_messages=max_messages,
        discussion_id=discussion_id,
    )
    if registered is False:
        return (
            f"Error: sub-agent id '{agent_id}' is already active. "
            "Choose a unique id for this session/round."
        )
    spawn_subagent_task(
        run_subagent(
            agent_id,
            task,
            bot,
            chat_id,
            db_path,
            use_secondary=use_secondary,
            role=role,
            mode=mode,
            success_criteria=success_criteria,
        ),
        agent_id,
    )
    suffix = " (secondary model)" if use_secondary else ""
    role_suffix = f" [role={role}]" if role else ""
    return f"Sub-agent '{agent_id}' spawned{suffix}{role_suffix} [mode={mode}]. Task: {task[:80]}"


handler = _tool_spawn_subagent

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_spawn_subagent"]
