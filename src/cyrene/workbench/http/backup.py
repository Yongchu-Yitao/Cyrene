"""Backup HTTP adapters."""

import json

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from cyrene.runtime.backup import BackupDownloadError, BackupRepository
from cyrene.workbench.http.errors import localized_error_payload, localized_error_response


def register_backup_routes(
    router: APIRouter,
    backups: BackupRepository,
) -> None:
    # ---- Backup API ----

    @router.get("/api/backup/list")
    async def api_backup_list():
        return {"ok": True, "backups": backups.list()}

    @router.post("/api/backup/export")
    async def api_backup_export(request: Request):
        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError):
            body = {}
        target_path = str(body.get("path") or "").strip()
        return await backups.export(target_path)

    @router.post("/api/backup/restore")
    async def api_backup_restore(request: Request):
        body = await request.json()
        path = str(body.get("path") or "").strip()
        if not path:
            return {
                "ok": False,
                **localized_error_payload(
                    "Backup path is required.",
                    "请选择备份路径。",
                    "backup_path_required",
                ),
            }
        return await backups.restore(path)

    @router.post("/api/backup/delete")
    async def api_backup_delete(request: Request):
        body = await request.json()
        name = str(body.get("name") or "").strip()
        if not name:
            return {
                "ok": False,
                **localized_error_payload(
                    "Backup name is required.",
                    "请选择要删除的备份。",
                    "backup_name_required",
                ),
            }
        ok = await backups.delete(name)
        return {"ok": ok}

    @router.get("/api/backup/download/{backup_name}")
    async def api_backup_download(backup_name: str):
        try:
            target = backups.download(backup_name)
        except BackupDownloadError as exc:
            return localized_error_response(
                "The backup could not be downloaded.",
                "无法下载该备份。",
                exc.status_code,
                "backup_download_failed",
            )
        return FileResponse(target, filename=backup_name, media_type="application/zip")
