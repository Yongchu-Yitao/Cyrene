"""Library item query and mutation routes."""

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.knowledge.library_services import LibraryApplicationService
from route.workbench.library_routes.common import bool_param


def register_item_routes(router: APIRouter, service: LibraryApplicationService) -> None:
    @router.get("/api/workbench/library/items")
    async def wb_library_items(
        workspace: str = "", q: str = "", collection: str = "", status: str = "",
        tag: str = "", item_type: str = "", file_type: str = "",
        year: int | None = None, starred: str | None = None, trash: bool = False,
        sort: str = "updated_at", order: str = "desc", limit: int = 200, offset: int = 0,
    ):
        return await service.list_items(
            workspace, q=q, collection=collection, status=status, tag=tag,
            item_type=item_type, file_type=file_type, year=year,
            starred=bool_param(starred), trash=trash, sort=sort, order=order,
            limit=limit, offset=offset,
        )

    @router.post("/api/workbench/library/items")
    async def wb_create_library_item(body: dict[str, Any], workspace: str = ""):
        return await service.create_item(workspace, body)

    @router.post("/api/workbench/library/items/batch-delete")
    async def wb_delete_library_items(body: dict[str, Any], workspace: str = ""):
        raw_ids = body.get("item_ids")
        if not isinstance(raw_ids, list):
            return JSONResponse({"error": "item_ids must be a list"}, status_code=400)
        item_ids = [item for item in dict.fromkeys(str(value or "").strip() for value in raw_ids) if item]
        if not item_ids:
            return JSONResponse({"error": "item_ids is required"}, status_code=400)
        if len(item_ids) > 500:
            return JSONResponse({"error": "at most 500 items can be deleted at once"}, status_code=400)
        deleted = await service.delete_items(workspace, item_ids, permanent=bool(bool_param(body.get("permanent"))))
        return {"ok": True, "deleted": deleted}

    @router.get("/api/workbench/library/items/{item_id}")
    async def wb_get_library_item(item_id: str, workspace: str = ""):
        item = await service.get_item(workspace, item_id)
        return item or JSONResponse({"error": "not found"}, status_code=404)

    @router.patch("/api/workbench/library/items/{item_id}")
    async def wb_update_library_item(item_id: str, body: dict[str, Any], workspace: str = ""):
        try:
            item = await service.update_item(workspace, item_id, body)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return item or JSONResponse({"error": "not found"}, status_code=404)

    @router.post("/api/workbench/library/read")
    async def wb_library_mark_read(body: dict[str, Any], workspace: str = ""):
        return await service.mark_read(
            workspace, attachment_url=str(body.get("attachment_url") or ""),
            file_name=str(body.get("file_name") or ""),
        )

    @router.delete("/api/workbench/library/items/{item_id}")
    async def wb_delete_library_item(item_id: str, workspace: str = "", permanent: bool = False):
        return {"ok": await service.delete_item(workspace, item_id, permanent=permanent)}

    @router.post("/api/workbench/library/items/{item_id}/restore")
    async def wb_restore_library_item(item_id: str, workspace: str = ""):
        item = await service.restore_item(workspace, item_id)
        return item or JSONResponse({"error": "not found"}, status_code=404)


__all__ = ["register_item_routes"]
