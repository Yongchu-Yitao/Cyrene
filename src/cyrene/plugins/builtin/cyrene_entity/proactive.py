"""Authoritative work-item selection for proactive Agent runs.

Proactive execution must not infer a work queue from memories.  Only an
explicitly tracked, still-active task/problem is an autonomous work item.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


_ACTIONABLE_TYPES = frozenset({"task", "problem"})
_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def select_proactive_entities(
    entities: Sequence[Mapping[str, Any]],
    *,
    project_id: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Return the explicit active work queue for one exact project scope."""

    scoped: list[dict[str, Any]] = []
    normalized_project = str(project_id or "default")
    for raw in entities:
        if str(raw.get("project_id") or "default") != normalized_project:
            continue
        if str(raw.get("status") or "") != "active":
            continue
        if str(raw.get("source") or "") != "explicit":
            continue
        if str(raw.get("type") or "").strip().lower() not in _ACTIONABLE_TYPES:
            continue
        if not str(raw.get("id") or "").strip() or not str(raw.get("title") or "").strip():
            continue
        scoped.append(dict(raw))
    scoped.sort(
        key=lambda item: (
            0 if str(item.get("due_date") or "").strip() else 1,
            str(item.get("due_date") or "9999-12-31"),
            _PRIORITY_ORDER.get(str(item.get("priority") or "medium"), 1),
            str(item.get("updated_at") or item.get("last_referenced_at") or ""),
            str(item.get("id") or ""),
        )
    )
    return scoped[: max(1, int(limit))]


def proactive_work_signature(entities: Sequence[Mapping[str, Any]]) -> str:
    """Version the exact work-state snapshot evaluated by one heartbeat."""

    payload = [
        {
            "id": str(item.get("id") or ""),
            "status": str(item.get("status") or ""),
            "updated_at": str(item.get("updated_at") or ""),
            "title": str(item.get("title") or ""),
            "content": str(item.get("content") or ""),
            "due_date": str(item.get("due_date") or ""),
            "priority": str(item.get("priority") or ""),
        }
        for item in entities
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def render_proactive_entities(entities: Sequence[Mapping[str, Any]]) -> str:
    """Render machine-identifiable work items without turning other entities into tasks."""

    records = [
        {
            "id": str(item.get("id") or ""),
            "type": str(item.get("type") or ""),
            "title": str(item.get("title") or ""),
            "content": str(item.get("content") or ""),
            "priority": str(item.get("priority") or "medium"),
            "due_date": item.get("due_date"),
            "updated_at": str(item.get("updated_at") or ""),
        }
        for item in entities
    ]
    return json.dumps(records, ensure_ascii=False, indent=2)


__all__ = [
    "proactive_work_signature",
    "render_proactive_entities",
    "select_proactive_entities",
]
