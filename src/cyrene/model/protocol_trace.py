"""Explicit, bounded raw wire tracing for model-provider diagnostics.

Normal Cyrene logs must never contain prompts or model output.  This module is
therefore disabled unless a developer explicitly sets
``CYRENE_MODEL_PROTOCOL_TRACE``.  Enabled traces contain raw provider response
lines and can include private conversation content; they are written to a
dedicated owner-only JSONL file instead of the ordinary application log.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_TRACE_ENV = "CYRENE_MODEL_PROTOCOL_TRACE"
_TRACE_MAX_BYTES_ENV = "CYRENE_MODEL_PROTOCOL_TRACE_MAX_BYTES"
_DEFAULT_MAX_BYTES = 32 * 1024 * 1024
_MAX_EVENT_BYTES = 1024 * 1024
_FALSE_VALUES = frozenset({"", "0", "false", "no", "off", "disabled"})
_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "enabled"})
_SAFE_FILE_PART = re.compile(r"[^A-Za-z0-9._-]+")


def _trace_directory(data_directory: str | Path, configured: str) -> Path:
    normalized = configured.strip()
    if normalized.lower() in _TRUE_VALUES:
        data_root = Path(data_directory).expanduser().resolve()
        return data_root.parent / "logs" / "model-protocol"
    return Path(normalized).expanduser().resolve()


def _safe_session_name(session_id: str) -> str:
    normalized = _SAFE_FILE_PART.sub("-", str(session_id or "").strip()).strip("-.")
    if normalized:
        return normalized[:96]
    digest = hashlib.sha256(str(session_id or "unknown").encode("utf-8")).hexdigest()
    return f"session-{digest[:16]}"


def _bounded_record(event: Mapping[str, Any], *, session_id: str) -> bytes:
    record = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "session_id": str(session_id or ""),
        **dict(event),
    }
    encoded = json.dumps(
        record,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    if len(encoded) <= _MAX_EVENT_BYTES:
        return encoded + b"\n"

    raw_line = str(record.get("line") or "")
    digest = hashlib.sha256(raw_line.encode("utf-8")).hexdigest()
    record["line"] = raw_line[: max(0, _MAX_EVENT_BYTES // 2)]
    record["line_truncated"] = True
    record["line_original_length"] = len(raw_line)
    record["line_sha256"] = digest
    return json.dumps(
        record,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8") + b"\n"


class ModelProtocolTraceWriter:
    """Append explicitly enabled raw provider events to one bounded file."""

    def __init__(self, path: Path, *, session_id: str, max_bytes: int) -> None:
        self.path = Path(path)
        self.session_id = str(session_id or "")
        self.max_bytes = max(1024, int(max_bytes))
        self._lock = threading.Lock()
        self._full = False

    async def __call__(self, event: Mapping[str, Any]) -> None:
        if self._full or not isinstance(event, Mapping):
            return
        payload = _bounded_record(event, session_id=self.session_id)
        with self._lock:
            if self._full:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                current_size = self.path.stat().st_size
            except FileNotFoundError:
                current_size = 0
            if current_size + len(payload) > self.max_bytes:
                self._full = True
                return
            descriptor = os.open(
                self.path,
                os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                0o600,
            )
            try:
                os.chmod(self.path, 0o600)
                os.write(descriptor, payload)
            finally:
                os.close(descriptor)


def create_model_protocol_trace(
    data_directory: str | Path,
    *,
    session_id: str,
) -> ModelProtocolTraceWriter | None:
    """Return a raw trace writer only after explicit developer opt-in."""

    configured = str(os.environ.get(_TRACE_ENV) or "").strip()
    if configured.lower() in _FALSE_VALUES:
        return None
    try:
        max_bytes = int(
            str(os.environ.get(_TRACE_MAX_BYTES_ENV) or _DEFAULT_MAX_BYTES)
        )
    except (TypeError, ValueError):
        max_bytes = _DEFAULT_MAX_BYTES
    directory = _trace_directory(data_directory, configured)
    filename = f"{_safe_session_name(session_id)}.jsonl"
    return ModelProtocolTraceWriter(
        directory / filename,
        session_id=session_id,
        max_bytes=max_bytes,
    )


__all__ = [
    "ModelProtocolTraceWriter",
    "create_model_protocol_trace",
]
