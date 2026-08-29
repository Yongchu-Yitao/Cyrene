"""Thin rolling-log export HTTP adapter."""

from fastapi import APIRouter
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from cyrene.runtime.log_repository import LogRepository, LogRepositoryError
from cyrene.workbench.http.errors import error_response


def register_log_routes(router: APIRouter, repository: LogRepository) -> None:
    @router.get("/api/logs/export")
    async def api_export_logs():
        """Package the rolling log files (2h x 3-day window) into a zip.

        Only the persistent rolling log under data/logs/ (cyrene.log + its
        rotated backups) is included. Verbose LLM debug files (debug_*.jsonl)
        are intentionally excluded: they hold full prompt/response payloads
        and can be very large.
        """
        try:
            archive = repository.create_export()
        except LogRepositoryError as exc:
            return error_response(exc.message, exc.status_code, exc.code)
        return FileResponse(
            archive.path,
            filename=archive.filename,
            media_type="application/zip",
            background=BackgroundTask(repository.remove_export, archive.path),
        )


__all__ = ["register_log_routes"]
