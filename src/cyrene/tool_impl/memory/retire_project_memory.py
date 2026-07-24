"""Tool implementation for retire_project_memory.

Lets the main Workbench agent mark an identified project memory as stale
without permanently deleting it. Project scope is resolved from the active
session, so callers cannot mutate another project's memory store.
"""

from __future__ import annotations

from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_support import _json_result
from cyrene.workbench_context import resolve_workbench_project_id_for_session

TOOL_NAME = "retire_project_memory"
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_retire_project_memory(
    args: dict[str, Any],
    _bot: Any,
    _chat_id: int,
    _db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    """Retire one durable memory in the current Workbench project."""
    from cyrene.agent.state import _current_session_id

    memory_id = str(args.get("memory_id", "") or "").strip()
    if not memory_id:
        return _json_result({
            "status": "error",
            "type": "invalid_arguments",
            "message": "memory_id is required",
        })

    project_id = resolve_workbench_project_id_for_session(
        _current_session_id.get()
    )
    if project_id is None:
        return _json_result({
            "status": "error",
            "type": "not_found",
            "message": (
                "Retiring project memory is only available inside a Workbench "
                "project task/chat."
            ),
        })

    from webui.routes_workbench_memory import (
        configure_store,
        retire_project_memory,
    )

    configure_store(_db_path)
    retired, changed = retire_project_memory(
        project_id,
        memory_id,
        reason=str(args.get("reason", "") or "").strip(),
    )
    if retired is None:
        return _json_result({
            "status": "error",
            "type": "not_found",
            "message": f"Project memory {memory_id} was not found.",
        })

    return _json_result({
        "status": "success",
        "memory_id": memory_id,
        "changed": changed,
        "stale": True,
        "content": str(retired.get("content") or ""),
        "message": (
            "Project memory retired. It will no longer be injected into agent "
            "runs, but remains recoverable on the Memory page."
            if changed
            else "Project memory was already retired."
        ),
    })


handler = _tool_retire_project_memory

__all__ = [
    "TOOL_NAME",
    "TOOL_DEF",
    "handler",
    "_tool_retire_project_memory",
]
