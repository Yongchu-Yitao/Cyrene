"""Tool implementation for Glob."""

from __future__ import annotations

from typing import Any

from cyrene import tool_legacy as _legacy

TOOL_NAME = 'Glob'
TOOL_DEF = next(td for td in _legacy.TOOL_DEFS if td["function"]["name"] == TOOL_NAME)


async def _tool_glob(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.settings_store import is_workspace_active
    if not is_workspace_active():
        return "Workspace access is disabled. Ask the user to add workspace via '+ add context' in the chat input, or set a workspace directory in Settings."
    from cyrene.agent.state import active_workspace_dir
    pattern = str(args["pattern"])
    workspace = active_workspace_dir()
    matches = sorted(str(path.relative_to(workspace)) for path in workspace.glob(pattern))
    return "\n".join(matches[:200]) if matches else "No matches."


handler = _tool_glob

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_glob"]
