"""Tool implementation for Grep."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_support import (
    _resolve_workspace_path,
    re,
)

TOOL_NAME = 'Grep'
TOOL_DEF = get_native_tool_def(TOOL_NAME)

_IGNORED_PARTS = {".git", ".hg", ".svn", ".venv", "node_modules", "__pycache__"}
_SCAN_SECONDS = 20.0
_MAX_CANDIDATES = 50_000
_MAX_FILE_BYTES = 4 * 1024 * 1024


async def _tool_grep(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.settings_store import is_workspace_active
    if not is_workspace_active():
        return "Workspace access is disabled. Ask the user to add workspace via '+ add context' in the chat input, or set a workspace directory in Settings."
    from cyrene.agent.state import active_workspace_dir
    pattern = re.compile(str(args["pattern"]))
    search_root = _resolve_workspace_path(str(args.get("path", ".")))
    glob_pattern = str(args.get("glob", "**/*"))
    workspace = active_workspace_dir()
    def scan() -> list[str]:
        lines: list[str] = []
        deadline = time.monotonic() + _SCAN_SECONDS
        for candidate_index, path in enumerate(search_root.glob(glob_pattern), start=1):
            if candidate_index > _MAX_CANDIDATES or time.monotonic() >= deadline:
                break
            try:
                rel = path.relative_to(workspace)
                if any(part in _IGNORED_PARTS for part in rel.parts):
                    continue
                if not path.is_file() or path.stat().st_size > _MAX_FILE_BYTES:
                    continue
                content = path.read_text(encoding="utf-8")
            except Exception:
                continue
            for line_index, line in enumerate(content.splitlines(), start=1):
                if pattern.search(line):
                    lines.append(f"{rel}:{line_index}:{line}")
                    if len(lines) >= 200:
                        return lines
        return lines

    lines = await asyncio.to_thread(scan)
    return "\n".join(lines) if lines else "No matches."


handler = _tool_grep

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_grep"]
