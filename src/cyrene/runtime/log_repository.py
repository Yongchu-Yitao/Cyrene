"""Repository for rolling-log discovery and export packaging."""

from __future__ import annotations

import os
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class LogArchive:
    path: Path
    filename: str


@dataclass(slots=True)
class LogRepositoryError(Exception):
    message: str
    status_code: int
    code: str

    def __str__(self) -> str:
        return self.message


class LogRepository:
    """Read persistent rolling logs and build a temporary zip archive."""

    def __init__(self, data_dir: Path):
        self.log_dir = Path(data_dir) / "logs"

    def create_export(self) -> LogArchive:
        files = self._log_files()
        descriptor, raw_path = tempfile.mkstemp(
            prefix="cyrene-logs-", suffix=".zip"
        )
        os.close(descriptor)
        target = Path(raw_path)
        try:
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in files:
                    archive.write(path, arcname=path.name)
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            target.unlink(missing_ok=True)
            raise LogRepositoryError(
                "Failed to package log files", 500, "log_package_failed"
            ) from exc
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return LogArchive(target, f"cyrene-logs-{timestamp}.zip")

    @staticmethod
    def remove_export(path: Path) -> None:
        path.unlink(missing_ok=True)

    def _log_files(self) -> list[Path]:
        if not self.log_dir.is_dir():
            raise LogRepositoryError(
                "No log files available", 404, "no_logs"
            )
        try:
            files = sorted(
                (
                    path
                    for path in self.log_dir.glob("cyrene.log*")
                    if path.is_file()
                ),
                key=lambda path: path.stat().st_mtime,
            )
        except OSError as exc:
            raise LogRepositoryError(
                "Failed to read log files", 500, "log_scan_failed"
            ) from exc
        if not files:
            raise LogRepositoryError(
                "No log files available", 404, "no_logs"
            )
        return files


__all__ = ["LogArchive", "LogRepository", "LogRepositoryError"]
