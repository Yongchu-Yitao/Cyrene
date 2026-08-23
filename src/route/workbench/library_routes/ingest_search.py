"""Library ingest, embedding, search, citation, and raw-content routes."""

from typing import Any

from fastapi import APIRouter, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from cyrene.knowledge.library_services import LibraryApplicationService, LibraryIngestService
from route.workbench.library_routes.common import library_call


def register_embedding_routes(router: APIRouter, ingest: LibraryIngestService) -> None:
    @router.get("/api/workbench/library/embedding/status")
    async def wb_library_embedding_status(workspace: str = ""):
        return await ingest.embedding_status(workspace)

    @router.post("/api/workbench/library/reembed")
    async def wb_library_reembed(workspace: str = ""):
        return await ingest.reembed(workspace)


def register_ingest_search_routes(
    router: APIRouter,
    application: LibraryApplicationService,
    ingest: LibraryIngestService,
) -> None:
    register_embedding_routes(router, ingest)

    @router.post("/api/workbench/library/import")
    async def wb_import_library(body: dict[str, Any], workspace: str = ""):
        if not isinstance(body.get("items"), list):
            return JSONResponse({"error": "items must be a list"}, status_code=400)
        return await ingest.import_records(workspace, body)

    @router.post("/api/workbench/library/upload")
    async def wb_upload_library(files: list[UploadFile], workspace: str = "", item_id: str = ""):
        if not files:
            return JSONResponse({"error": "no files uploaded"}, status_code=400)
        result = await library_call(ingest.upload(workspace, files, item_id))
        return result if isinstance(result, JSONResponse) else {"items": result}

    @router.get("/api/workbench/library/search")
    async def wb_search_library(workspace: str = "", q: str = "", k: int = 20):
        return await application.search(workspace, q, k)

    @router.get("/api/workbench/library/items/{item_id}/citation")
    async def wb_library_citation(item_id: str, workspace: str = "", style: str = "ieee"):
        result = await application.citation(workspace, item_id, style)
        return result or JSONResponse({"error": "not found"}, status_code=404)

    @router.get("/api/workbench/library/items/{item_id}/raw")
    async def wb_library_raw(item_id: str, workspace: str = ""):
        result = await library_call(application.raw(workspace, item_id))
        if isinstance(result, JSONResponse):
            return result
        if result is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(
            str(result.path), media_type=result.media_type, filename=result.filename,
            content_disposition_type="inline",
        )


__all__ = ["register_ingest_search_routes"]
