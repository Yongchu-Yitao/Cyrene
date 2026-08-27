"""Tool implementation for save_project_memory.

Lets a Workbench task agent persist a durable fact into its project's long-term
memory store — the same store shown on the project's Memory page and injected
into future runs. The project scope is resolved from the active session id, so
the agent never has to know (or be trusted with) the storage key.
"""

from __future__ import annotations

from typing import Any

from agent.plugin import PluginContext
from ._native import create_tool, service as memory_service
from .definitions import get_native_tool_def

TOOL_NAME = "save_project_memory"
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_save_project_memory(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    """Persist one durable fact into the current project's memory store."""
    memory = memory_service(context)
    if not memory.is_main:
        return "Not saved: subagents must report durable findings to the main Agent."
    content = str(args.get("content", "") or "").strip()
    if len(content) < 4:
        return "Nothing saved: 'content' is empty or too short."

    category = str(args.get("category", "fact") or "fact").strip().lower()
    tags = args.get("tags")

    project_id = memory.project_id
    if not project_id:
        # Not inside a Workbench project (for example a channel or scheduler run).
        return "Not saved: project memory is only available inside a Workbench project task/chat."

    # Keep the storage implementation lazy so importing the Plugin declaration
    # has no database side effects.
    from .structured import add_agent_memory_checked

    memory.configure_stores()

    # Resolves textual duplicates (reinforce) and asks an LLM whether this fact
    # contradicts/supersedes existing memories — retiring the outdated ones.
    saved, retired = await add_agent_memory_checked(
        project_id,
        content,
        category=category,
        tags=tags,
        model_gateway=memory.model_gateway,
        session_id=memory.session_id,
    )
    if not saved:
        return "Not saved (blank, too short, or out of project scope)."
    cat_label = str(saved.get("category_label") or saved.get("category") or "")
    msg = f"Saved to project memory [{cat_label}]: {saved.get('content') or content}"
    if retired:
        superseded = "; ".join("“" + str(r.get("content") or "")[:60] + "”" for r in retired)
        msg += f" — superseded {len(retired)} now-outdated memory(ies): {superseded}"
    return msg


handler = _tool_save_project_memory
plugin = create_tool(TOOL_DEF, handler)

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "plugin", "_tool_save_project_memory"]
