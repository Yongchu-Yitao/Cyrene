"""Tool implementation for save_project_memory.

Lets a Workbench task agent persist a durable fact into its project's long-term
memory store — the same store shown on the project's Memory page and injected
into future runs. The project scope is resolved from the active session id, so
the agent never has to know (or be trusted with) the storage key.
"""

from __future__ import annotations

from typing import Any

from .definitions import get_native_tool_def
from cyrene.workbench.context import resolve_workbench_project_id_for_session

TOOL_NAME = 'save_project_memory'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_save_project_memory(
    args: dict[str, Any],
    _bot: Any,
    _chat_id: int,
    _db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    """Persist one durable fact into the current project's memory store."""
    from cyrene.agent.context import get_current_session_id

    content = str(args.get("content", "") or "").strip()
    if len(content) < 4:
        return "Nothing saved: 'content' is empty or too short."

    category = str(args.get("category", "fact") or "fact").strip().lower()
    tags = args.get("tags")

    project_id = resolve_workbench_project_id_for_session(get_current_session_id())
    if not project_id:
        # Not inside a Workbench project (e.g. legacy chat / scheduler run).
        return "Not saved: project memory is only available inside a Workbench project task/chat."

    # Lazy import: the store lives in the webui layer (loaded in the server
    # process); importing it here at module load would invert package layering.
    from cyrene.workbench.memory import add_agent_memory_checked, configure_store

    configure_store(_db_path)

    # Resolves textual duplicates (reinforce) and asks an LLM whether this fact
    # contradicts/supersedes existing memories — retiring the outdated ones.
    saved, retired = await add_agent_memory_checked(project_id, content, category=category, tags=tags)
    if not saved:
        return "Not saved (blank, too short, or out of project scope)."
    cat_label = str(saved.get("category_label") or saved.get("category") or "")
    msg = f"Saved to project memory [{cat_label}]: {saved.get('content') or content}"
    if retired:
        superseded = "; ".join("“" + str(r.get("content") or "")[:60] + "”" for r in retired)
        msg += f" — superseded {len(retired)} now-outdated memory(ies): {superseded}"
    return msg


handler = _tool_save_project_memory

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_save_project_memory"]
