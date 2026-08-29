"""Workbench chat upload/download HTTP adapters."""

from __future__ import annotations

from fastapi import APIRouter, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from cyrene.workbench.chat.chat_attachment_service import (
    ChatAttachmentError,
    ChatAttachmentService,
)
from cyrene.workbench.http.errors import localized_error_response


def _attachment_error(exc: ChatAttachmentError) -> JSONResponse:
    messages = {
        "no files uploaded": (
            "No files were uploaded.",
            "未上传任何文件。",
            "no_files_uploaded",
        ),
        "invalid attachment path": (
            "The attachment path is invalid.",
            "附件路径无效。",
            "invalid_attachment_path",
        ),
        "upload not found": (
            "The uploaded file was not found.",
            "未找到上传的文件。",
            "upload_not_found",
        ),
        "export not found": (
            "The exported file was not found.",
            "未找到导出的文件。",
            "export_not_found",
        ),
    }
    en, zh, code = messages.get(
        str(exc),
        (
            "The attachment request could not be completed.",
            "无法完成附件请求。",
            "attachment_request_failed",
        ),
    )
    return localized_error_response(en, zh, exc.status_code, code)


def register_attachment_routes(router: APIRouter) -> None:
    service = ChatAttachmentService()

    @router.post("/api/workbench/uploads")
    async def api_workbench_uploads(files: list[UploadFile]):
        try:
            return await service.upload(files)
        except ChatAttachmentError as exc:
            return _attachment_error(exc)

    @router.get("/api/workbench/uploads/{attachment_id}")
    async def api_workbench_upload(attachment_id: str):
        try:
            return FileResponse(service.resolve_upload(attachment_id))
        except ChatAttachmentError as exc:
            return _attachment_error(exc)

    @router.get("/api/workbench/exports/{attachment_id}")
    async def api_workbench_export(attachment_id: str):
        try:
            return FileResponse(service.resolve_export(attachment_id))
        except ChatAttachmentError as exc:
            return _attachment_error(exc)


__all__ = ["register_attachment_routes"]
