"""Tool implementation for Glob."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def

TOOL_NAME = 'Glob'
TOOL_DEF = get_native_tool_def(TOOL_NAME)

_IGNORED_PARTS = {".git", ".hg", ".svn", ".venv", "node_modules", "__pycache__"}
_SCAN_SECONDS = 20.0
_MAX_CANDIDATES = 50_000


async def _tool_glob(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.settings_store import is_workspace_active
    if not is_workspace_active():
        return "Workspace access is disabled. Ask the user to add workspace via '+ add context' in the chat input, or set a workspace directory in Settings."
    from cyrene.agent.state import active_workspace_dir
    pattern = str(args["pattern"])
    workspace = active_workspace_dir()
    def scan() -> list[str]:
        matches: list[str] = []
        deadline = time.monotonic() + _SCAN_SECONDS
        for index, path in enumerate(workspace.glob(pattern), start=1):
            if index > _MAX_CANDIDATES or time.monotonic() >= deadline:
                break
            try:
                relative = path.relative_to(workspace)
            except ValueError:
                continue
            if any(part in _IGNORED_PARTS for part in relative.parts):
                continue
            matches.append(str(relative))
            if len(matches) >= 200:
                break
        return sorted(matches)

    matches = await asyncio.to_thread(scan)
    return "\n".join(matches[:200]) if matches else "No matches."


handler = _tool_glob

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_glob"]
