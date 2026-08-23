"""Zotero Local API status, collection, and sync routes."""

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.knowledge import zotero
from cyrene.knowledge.library_services import ZoteroSyncService


def register_zotero_routes(router: APIRouter, service: ZoteroSyncService) -> None:
    @router.get("/api/workbench/library/zotero/status")
    async def wb_zotero_status(workspace: str = ""):
        try:
            return await service.status(workspace)
        except zotero.ZoteroLocalError as exc:
            return {"available": False, "error": str(exc), "sync_sources": []}

    @router.get("/api/workbench/library/zotero/collections")
    async def wb_zotero_collections(workspace: str = "", library_id: str = "0", library_type: str = "user"):
        del workspace
        try:
            return await service.collections(library_id, library_type)
        except zotero.ZoteroLocalError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)

    @router.post("/api/workbench/library/zotero/sync")
    async def wb_zotero_sync(body: dict[str, Any], workspace: str = ""):
        try:
            return await service.sync(workspace, body)
        except zotero.ZoteroLocalError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)


__all__ = ["register_zotero_routes"]
