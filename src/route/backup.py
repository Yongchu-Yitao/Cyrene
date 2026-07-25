"""Backup routes."""

# ruff: noqa: F403,F405

from cyrene.workbench.runtime import *


def register_backup_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    global _bot, _db_path
    _bot = bot
    _db_path = db_path

    # ---- Backup API ----

    @router.get("/api/backup/list")
    async def api_backup_list():
        from cyrene.runtime.backup import list_backups
        return {"ok": True, "backups": list_backups()}

    @router.post("/api/backup/export")
    async def api_backup_export():
        from cyrene.runtime.backup import export_backup
        result = await export_backup()
        return result

    @router.post("/api/backup/restore")
    async def api_backup_restore(request: Request):
        from cyrene.runtime.backup import restore_backup
        body = await request.json()
        path = str(body.get("path") or "").strip()
        if not path:
            return {"ok": False, "error": "path is required"}
        result = await restore_backup(path)
        return result

    @router.post("/api/backup/delete")
    async def api_backup_delete(request: Request):
        from cyrene.runtime.backup import delete_backup
        body = await request.json()
        name = str(body.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "name is required"}
        ok = await delete_backup(name)
        return {"ok": ok}

    @router.get("/api/backup/download/{backup_name}")
    async def api_backup_download(backup_name: str):
        from cyrene.runtime.backup import _BACKUP_DIR
        target = (_BACKUP_DIR / backup_name).resolve()
        backups_root = _BACKUP_DIR.resolve()
        if backups_root not in target.parents:
            return JSONResponse({"error": "invalid backup path"}, status_code=400)
        if not target.exists() or not target.is_file():
            return JSONResponse({"error": "backup not found"}, status_code=404)
        return FileResponse(target, filename=backup_name, media_type="application/zip")
