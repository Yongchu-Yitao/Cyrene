"""Tool implementation for listing the short-term memory inventory."""

from __future__ import annotations

from typing import Any

from cyrene import short_term
from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_support import _json_result

TOOL_NAME = "ListMemories"
TOOL_DEF = get_native_tool_def(TOOL_NAME)
_MAX_CONTENT_CHARS = 800


def _bounded_content(value: Any) -> tuple[str, bool]:
    text = str(value or "")
    if len(text) <= _MAX_CONTENT_CHARS:
        return text, False
    return text[:_MAX_CONTENT_CHARS] + "…", True


def _short_term_payload(entry: dict[str, Any]) -> dict[str, Any]:
    content, content_truncated = _bounded_content(entry.get("content", ""))
    memory = {
        "memory_id": short_term.entry_id(entry),
        "scope": "short_term",
        "content": content,
        "type": entry.get("type", ""),
        "status": "retired" if entry.get("stale") else "active",
        "first_seen": entry.get("first_seen", ""),
        "last_mentioned": entry.get("last_mentioned", ""),
        "mention_count": int(entry.get("mention_count") or 1),
        "emotional_valence": entry.get("emotional_valence", 0),
    }
    if content_truncated:
        memory["content_truncated"] = True
    if entry.get("stale"):
        memory["retired_at"] = entry.get("retired_at", "")
        memory["retire_reason"] = entry.get("retire_reason", "")
    return memory


def _project_payload(entry: dict[str, Any]) -> dict[str, Any]:
    content, content_truncated = _bounded_content(entry.get("content", ""))
    memory = {
        "memory_id": entry.get("id", ""),
        "scope": "project",
        "content": content,
        "type": entry.get("category", ""),
        "status": "retired" if entry.get("stale") else "active",
        "first_seen": entry.get("created_at", ""),
        "last_mentioned": entry.get("updated_at", ""),
        "mention_count": int(entry.get("citation_count") or 1),
        "emotional_valence": entry.get("emotional_valence", 0),
        "source": entry.get("source", ""),
        "confidence": entry.get("confidence", ""),
        "tags": entry.get("tags", []),
    }
    if content_truncated:
        memory["content_truncated"] = True
    return memory


async def _tool_list_memories(
    args: dict[str, Any],
    _bot: Any,
    _chat_id: int,
    _db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    """List filtered memories and report exact totals for every available store."""
    scope = str(args.get("scope", "all") or "all").strip().lower()
    if scope not in {"all", "short_term", "project"}:
        scope = "all"
    memory_type = str(args.get("type", "") or "").strip().lower()
    status = str(args.get("status", "active") or "active").strip().lower()
    if status not in {"active", "retired", "all"}:
        status = "active"
    limit = max(1, min(int(args.get("limit", 100) or 100), 500))
    offset = max(0, int(args.get("offset", 0) or 0))

    memories: list[dict[str, Any]] = []
    project_available = False

    if scope in {"all", "short_term"}:
        memories.extend(
            _short_term_payload(entry)
            for entry in short_term.load_entries()
            if isinstance(entry, dict)
        )

    if scope in {"all", "project"}:
        from cyrene.agent.state import _current_session_id
        from cyrene.workbench_context import (
            resolve_workbench_project_id_for_session,
        )

        project_id = resolve_workbench_project_id_for_session(
            _current_session_id.get()
        )
        if project_id is not None:
            from cyrene.workbench_memory_service import (
                _build_payload,
                configure_store,
            )

            configure_store(_db_path)
            project_available = True
            memories.extend(
                _project_payload(entry)
                for entry in _build_payload(project_id)["memories"]
                if isinstance(entry, dict)
            )
        elif scope == "project":
            return _json_result({
                "status": "error",
                "type": "not_found",
                "message": (
                    "Project memory is only available inside a Workbench "
                    "project task/chat."
                ),
            })

    memories = [
        memory
        for memory in memories
        if (
            not memory_type
            or str(memory.get("type") or "").strip().lower() == memory_type
        )
        and (
            status == "all"
            or str(memory.get("status") or "") == status
        )
    ]
    memories.sort(
        key=lambda memory: (
            str(memory.get("last_mentioned") or memory.get("first_seen") or ""),
            int(memory.get("mention_count") or 1),
        ),
        reverse=True,
    )
    total_by_scope = {
        memory_scope: sum(
            memory.get("scope") == memory_scope for memory in memories
        )
        for memory_scope in ("short_term", "project")
    }
    total = len(memories)
    page = memories[offset:offset + limit]

    payload: dict[str, Any] = {
        "scope": scope,
        "type": memory_type,
        "status": status,
        "total": total,
        "total_by_scope": total_by_scope,
        "project_memory_available": project_available,
        "offset": offset,
        "limit": limit,
        "returned": len(page),
        "has_more": offset + len(page) < total,
        "memories": page,
    }
    if not page:
        payload["note"] = "No memories match the requested filters."
    return _json_result(payload)


handler = _tool_list_memories

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_list_memories"]
