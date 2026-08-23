"""Library collection, tag, and statistics routes."""

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.knowledge.library_services import LibraryApplicationService


def register_collection_routes(router: APIRouter, service: LibraryApplicationService) -> None:
    @router.get("/api/workbench/library/stats")
    async def wb_library_stats(workspace: str = ""):
        return await service.stats(workspace)

    @router.get("/api/workbench/library/tags")
    async def wb_library_tags(workspace: str = ""):
        return {"tags": await service.tags(workspace)}

    @router.get("/api/workbench/library/collections")
    async def wb_library_collections(workspace: str = ""):
        return {"collections": await service.collections(workspace)}

    @router.post("/api/workbench/library/collections")
    async def wb_create_library_collection(body: dict[str, Any], workspace: str = ""):
        if not str(body.get("name") or "").strip():
            return JSONResponse({"error": "name is required"}, status_code=400)
        return await service.create_collection(workspace, body)

    @router.patch("/api/workbench/library/collections/{collection_id}")
    async def wb_update_library_collection(collection_id: str, body: dict[str, Any], workspace: str = ""):
        value = await service.update_collection(workspace, collection_id, body)
        return value or JSONResponse({"error": "not found"}, status_code=404)

    @router.delete("/api/workbench/library/collections/{collection_id}")
    async def wb_delete_library_collection(collection_id: str, workspace: str = ""):
        return {"ok": await service.delete_collection(workspace, collection_id)}


__all__ = ["register_collection_routes"]
