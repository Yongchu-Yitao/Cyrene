"""Application routes and services owned by the editable entity Plugin."""

from agent.plugin import PluginApplicationContext
from fastapi import APIRouter, HTTPException

from .service import EntityService

_CREATE_FIELDS = {
    "type", "title", "content", "status", "tags", "priority", "effort",
    "due_date", "parent_id", "linked_ids", "people", "source",
    "source_round_id", "confidence", "metadata", "project_id",
}
_UPDATE_FIELDS = {
    "status", "priority", "due_date", "content", "tags", "people",
    "title", "effort", "metadata", "linked_ids", "parent_id",
}


def setup_application(context: PluginApplicationContext) -> None:
    router: APIRouter = context.router
    entities = EntityService(context.db_path)

    @router.get("/api/entities")
    async def api_list_entities(
        type: str = None,
        status: str = None,
        has_due_date: bool = False,
        q: str = None,
        project_id: str = None,
        limit: int = 100,
    ):
        if q:
            return await entities.query(
                q,
                type=type,
                status=status,
                project_id=project_id,
                limit=limit,
            )
        return await entities.list(
            type=type,
            status=status,
            has_due_date=has_due_date,
            project_id=project_id,
            limit=limit,
        )

    @router.post("/api/entities")
    async def api_create_entity(body: dict):
        filtered = {k: v for k, v in body.items() if k in _CREATE_FIELDS}
        try:
            return await entities.create(**filtered)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/api/entities/candidates")
    async def api_list_candidates(project_id: str = None, limit: int = 50):
        """List all candidate entities."""
        return await entities.list_candidates(project_id=project_id, limit=limit)

    @router.post("/api/entities/candidates/{candidate_id}/approve")
    async def api_approve_candidate(candidate_id: str):
        """Promote a candidate to a full entity."""
        result = await entities.promote_candidate(candidate_id)
        return result or {"error": "not found"}

    @router.delete("/api/entities/candidates/{candidate_id}")
    async def api_reject_candidate(candidate_id: str):
        """Reject a candidate entity."""
        success = await entities.reject_candidate(candidate_id)
        return {"ok": success}

    @router.get("/api/entities/{entity_id}")
    async def api_get_entity(entity_id: str):
        """Get a single entity by ID."""
        return await entities.get(entity_id) or {"error": "not found"}

    @router.put("/api/entities/{entity_id}")
    async def api_update_entity(entity_id: str, body: dict):
        filtered = {k: v for k, v in body.items() if k in _UPDATE_FIELDS}
        try:
            return await entities.update(entity_id, **filtered) or {"error": "not found"}
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.delete("/api/entities/{entity_id}")
    async def api_delete_entity(entity_id: str, permanent: bool = False):
        """Delete or archive an entity."""
        success = await entities.delete(entity_id, permanent=permanent)
        return {"ok": success}

    context.provide("entities", entities)
    context.on_startup(entities.startup)


__all__ = ["setup_application"]
