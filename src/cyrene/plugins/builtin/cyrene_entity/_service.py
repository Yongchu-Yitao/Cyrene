"""Runtime service and project-scope helpers for entity Plugins."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cyrene.core.plugin import PluginContext


def entity_service(context: PluginContext) -> Any:
    """Return the entity service published by this Plugin pack."""

    service = context.services.get("entities")
    if service is None:
        raise RuntimeError(
            "The cyrene_entity Plugin pack is not attached to this Agent session"
        )
    return service


def current_session_id(context: PluginContext) -> str:
    direct = str(context.data.get("session_id") or "").strip()
    if direct:
        return direct
    run_context = context.data.get("run_context")
    if isinstance(run_context, Mapping):
        return str(run_context.get("session_id") or "").strip()
    return ""


def current_project_id(context: PluginContext) -> str:
    direct = str(context.data.get("project_id") or "").strip()
    if direct:
        return direct
    session_id = current_session_id(context)
    if not session_id:
        return ""
    from cyrene.workbench.sessions.context import resolve_workbench_project_id_for_session

    return str(resolve_workbench_project_id_for_session(session_id) or "")


async def resolve_entity(
    service: Any,
    *,
    entity_id: Any = None,
    title: Any = None,
    type: Any = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Resolve a safe single target without ever guessing through fuzzy search."""

    requested_id = str(entity_id or "").strip()
    requested_title = str(title or "").strip()
    requested_type = str(type or "").strip() or None

    entity = None
    if requested_id:
        entity = await service.get(requested_id)
        if entity is not None and project_id is not None:
            if str(entity.get("project_id") or "default") != project_id:
                entity = None
        if entity is None:
            matches = await service.find_by_id_prefix(
                requested_id,
                project_id=project_id,
            )
            if len(matches) > 1:
                return {"entity": None, "matches": matches, "matched_by": "id_prefix"}
            if matches:
                entity = matches[0]
        if entity is None and not requested_title:
            requested_title = requested_id

    if entity is None and requested_title:
        matches = await service.find_by_title(
            requested_title,
            type=requested_type,
            project_id=project_id,
        )
        if len(matches) > 1:
            return {"entity": None, "matches": matches, "matched_by": "title"}
        if matches:
            entity = matches[0]

    return {"entity": entity, "matches": [], "matched_by": ""}


__all__ = [
    "current_project_id",
    "current_session_id",
    "entity_service",
    "resolve_entity",
]
