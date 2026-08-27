"""Workbench chat upload/download HTTP adapters."""

from __future__ import annotations

from fastapi import APIRouter, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from cyrene.workbench.chat_attachment_service import (
    ChatAttachmentError,
    ChatAttachmentService,
)


def register_attachment_routes(router: APIRouter) -> None:
    service = ChatAttachmentService()

    @router.post("/api/workbench/uploads")
    async def api_workbench_uploads(files: list[UploadFile]):
        try:
            return await service.upload(files)
        except ChatAttachmentError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status_code)

    @router.get("/api/workbench/uploads/{attachment_id}")
    async def api_workbench_upload(attachment_id: str):
        try:
            return FileResponse(service.resolve_upload(attachment_id))
        except ChatAttachmentError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status_code)

    @router.get("/api/workbench/exports/{attachment_id}")
    async def api_workbench_export(attachment_id: str):
        try:
            return FileResponse(service.resolve_export(attachment_id))
        except ChatAttachmentError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status_code)


__all__ = ["register_attachment_routes"]
