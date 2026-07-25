"""Shared registry for shell-like processes owned by external backends."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any

external_shells: dict[str, dict[str, Any]] = {}


def register_external_shell(
    shell_id: str,
    title: str,
    cwd: str = ".",
    extra: dict[str, Any] | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    entry: dict[str, Any] = {
        "id": shell_id,
        "title": title or "external shell",
        "cwd": cwd,
        "pid": "—",
        "status": "running",
        "round_id": "",
        "created_at": now,
        "updated_at": now,
        "exit_code": None,
        "lines": deque(maxlen=240),
    }
    if extra:
        entry.update(extra)
    external_shells[shell_id] = entry


def unregister_external_shell(shell_id: str) -> bool:
    return external_shells.pop(shell_id, None) is not None


def set_external_shell_status(shell_id: str, status: str) -> None:
    entry = external_shells.get(shell_id)
    if entry is None:
        return
    entry["status"] = status
    entry["updated_at"] = datetime.now(timezone.utc).isoformat()
