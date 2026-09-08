"""Small independent report store; failures must not replace application errors."""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .evidence import error_code


class ReportRepository:
    def __init__(self, directory: Path, *, retain: int | None = 200, max_size: int = 2_000_000):
        self.directory = Path(directory)
        self.retain, self.max_size = retain, max_size

    def save(self, value: dict) -> dict:
        identifier = str(value["id"])
        if not re.fullmatch(r"[a-z0-9_-]{1,80}", identifier):
            raise ValueError("Invalid report ID")
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=self.directory, prefix=".report-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            Path(name).replace(self.directory / (identifier + ".json"))
        finally:
            Path(name).unlink(missing_ok=True)
        if self.retain is not None:
            for path in sorted(self.directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[self.retain:]:
                path.unlink(missing_ok=True)
        return value

    def get(self, identifier: str) -> dict:
        if not re.fullmatch(r"[a-z0-9_-]{1,80}", identifier):
            raise ValueError("Invalid report ID")
        path = self.directory / (identifier + ".json")
        if path.stat().st_size > self.max_size:
            raise ValueError("Report exceeds size limit")
        return json.loads(path.read_text(encoding="utf-8"))


def record_incident(exc: BaseException, *, chat_id: str = "", run_id: str = "", project_id: str = "", stage: str = "agent_run", operation: str = "", directory: Path | None = None, plugin_root: Path | None = None) -> str:
    if isinstance(exc, asyncio.CancelledError):
        return ""
    try:
        if directory is None:
            from cyrene.platform.paths import PATHS
            directory = PATHS.data / "doctor" / "incidents"
        details = {}
        current = exc
        for _ in range(6):
            if current is None:
                break
            exporter = getattr(current, "as_error_details", None)
            if callable(exporter):
                raw = exporter()
                from cyrene.model.error_details import public_stream_diagnostics
                details = {key: raw[key] for key in ("retryable", "retry_scope") if key in raw}
                details["stream_diagnostics"] = public_stream_diagnostics(raw.get("stream_diagnostics"))
                break
            current = current.__cause__ or current.__context__
        identifier = "incident_" + uuid4().hex
        frames = []
        current, seen = exc, set()
        while current is not None and id(current) not in seen and len(seen) < 6:
            seen.add(id(current))
            for frame in traceback.extract_tb(current.__traceback__)[-12:]:
                entry = {'module': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
                if plugin_root is not None:
                    path, root = Path(frame.filename).resolve(), plugin_root.resolve()
                    if path.is_relative_to(root) and path != root:
                        entry['plugin'] = path.relative_to(root).parts[0]
                frames.append(entry)
            current = current.__cause__ or current.__context__
        ReportRepository(directory).save({
            "id": identifier, "created_at": datetime.now(timezone.utc).isoformat(),
            "chat_id": chat_id, "run_id": run_id, "project_id": project_id,
            "stage": stage, "operation": operation[:240], "model_error": details, "code": error_code(exc), "exception_type": type(exc).__name__,
            "frames": frames[-36:],
        })
        return identifier
    except Exception:
        return ""


def record_runtime_incident(exc, *, chat_id="", run_id="", project_id="", stage="agent_run", operation=""):
    try:
        from cyrene.plugins.application import application_plugin_scope
        host = application_plugin_scope()
        service = host.services.get("doctor") if host is not None else None
        if service is None:
            return ""
        identifier = record_incident(exc, chat_id=chat_id, run_id=run_id, project_id=project_id,
                                    stage=stage, operation=operation, directory=service.data / "doctor" / "incidents", plugin_root=service.plugins)
        setattr(exc, "incident_id", identifier)
        return identifier
    except Exception:
        return ""
