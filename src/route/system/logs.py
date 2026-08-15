"""Log export routes — package the rolling log files for download."""

# ruff: noqa: F403,F405

import logging
import os
import tempfile
import zipfile
from datetime import datetime

from cyrene.config import DATA_DIR
from cyrene.workbench.runtime import *
from fastapi.responses import FileResponse
from route.errors import error_response
from starlette.background import BackgroundTask

logger = logging.getLogger(__name__)


def register_log_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    @router.get("/api/logs/export")
    async def api_export_logs():
        """Package the rolling log files (2h x 3-day window) into a zip.

        Only the persistent rolling log under data/logs/ (cyrene.log + its
        rotated backups) is included. Verbose LLM debug files (debug_*.jsonl)
        are intentionally excluded: they hold full prompt/response payloads
        and can be very large.
        """
        log_dir = DATA_DIR / "logs"
        if not log_dir.is_dir():
            return error_response("No log files available", 404, "no_logs")
        try:
            files = sorted(
                (p for p in log_dir.glob("cyrene.log*") if p.is_file()),
                key=lambda p: p.stat().st_mtime,
            )
        except OSError as exc:
            logger.warning("Failed to scan log directory %s", log_dir, exc_info=True)
            return error_response("Failed to read log files", 500, "log_scan_failed")
        if not files:
            return error_response("No log files available", 404, "no_logs")

        fd, tmp_path = tempfile.mkstemp(prefix="cyrene-logs-", suffix=".zip")
        os.close(fd)
        try:
            with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in files:
                    archive.write(path, arcname=path.name)
        except Exception as exc:
            logger.warning("Failed to package log files into %s", tmp_path, exc_info=True)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            return error_response("Failed to package log files", 500, "log_package_failed")

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return FileResponse(
            tmp_path,
            filename=f"cyrene-logs-{timestamp}.zip",
            media_type="application/zip",
            background=BackgroundTask(os.unlink, tmp_path),
        )
