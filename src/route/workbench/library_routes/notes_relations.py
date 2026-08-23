"""Library note and item-relation routes."""

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.knowledge.library_services import LibraryApplicationService


def register_note_relation_routes(router: APIRouter, service: LibraryApplicationService) -> None:
    @router.post("/api/workbench/library/items/{item_id}/notes")
    async def wb_create_library_note(item_id: str, body: dict[str, Any], workspace: str = ""):
        note = await service.create_note(workspace, item_id, body)
        return note or JSONResponse({"error": "item not found"}, status_code=404)

    @router.patch("/api/workbench/library/notes/{note_id}")
    async def wb_update_library_note(note_id: str, body: dict[str, Any], workspace: str = ""):
        note = await service.update_note(workspace, note_id, body)
        return note or JSONResponse({"error": "not found"}, status_code=404)

    @router.delete("/api/workbench/library/notes/{note_id}")
    async def wb_delete_library_note(note_id: str, workspace: str = ""):
        return {"ok": await service.delete_note(workspace, note_id)}

    @router.post("/api/workbench/library/relations")
    async def wb_create_library_relation(body: dict[str, Any], workspace: str = ""):
        if not body.get("src_item_id") or not body.get("dst_item_id"):
            return JSONResponse({"error": "src_item_id and dst_item_id are required"}, status_code=400)
        return await service.create_relation(workspace, body)

    @router.delete("/api/workbench/library/relations/{relation_id}")
    async def wb_delete_library_relation(relation_id: str, workspace: str = ""):
        return {"ok": await service.delete_relation(workspace, relation_id)}


__all__ = ["register_note_relation_routes"]
