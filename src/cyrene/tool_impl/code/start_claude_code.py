"""Tool implementation for StartClaudeCode."""

from __future__ import annotations

from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_api import (
    CC_PROJECT_DIR,
    json,
)

TOOL_NAME = 'StartClaudeCode'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_cc_launch(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.tooling.backends.claude_code_bridge import launch_cc_tmux
    session_name = str(args.get("session_name", "") or "").strip()
    return json.dumps(launch_cc_tmux(cwd=CC_PROJECT_DIR, session_name=session_name), ensure_ascii=False)


handler = _tool_cc_launch

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_cc_launch"]
