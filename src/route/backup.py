"""Backup HTTP adapters."""

import json

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from cyrene.runtime.backup import BackupDownloadError, BackupRepository


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
            return {"ok": False, "error": "path is required"}
        return await backups.restore(path)

    @router.post("/api/backup/delete")
    async def api_backup_delete(request: Request):
        body = await request.json()
        name = str(body.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "name is required"}
        ok = await backups.delete(name)
        return {"ok": ok}

    @router.get("/api/backup/download/{backup_name}")
    async def api_backup_download(backup_name: str):
        try:
            target = backups.download(backup_name)
        except BackupDownloadError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status_code)
        return FileResponse(target, filename=backup_name, media_type="application/zip")
