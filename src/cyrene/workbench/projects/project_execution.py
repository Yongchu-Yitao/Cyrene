"""Validation for optional project-owned workspace execution actions."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_ACTION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_ACTION_KINDS = frozenset({"build", "run", "test", "preview"})
_MAX_ACTIONS = 40


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _relative_path(value: Any, *, label: str) -> str:
    raw = _text(value, 1024).replace("\\", "/") or "."
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must stay inside the project workspace")
    return path.as_posix() or "."


def normalize_execution_action(value: Mapping[str, Any], index: int = 0) -> dict[str, Any]:
    """Return the bounded public representation stored in project metadata."""

    action_id = _text(value.get("id"), 80) or f"custom-{index + 1}"
    if not _ACTION_ID.fullmatch(action_id):
        raise ValueError("workspace action id is invalid")
    kind = _text(value.get("kind"), 20).lower() or "run"
    if kind not in _ACTION_KINDS:
        raise ValueError("workspace action kind is invalid")
    program = _text(value.get("program"), 512)
    if not program:
        raise ValueError("workspace action program is required")
    if any(character in program for character in ("\n", "\r", "\0")):
        raise ValueError("workspace action program is invalid")
    raw_args = value.get("args") or []
    if not isinstance(raw_args, Sequence) or isinstance(raw_args, (str, bytes)):
        raise ValueError("workspace action args must be an array")
    args = [_text(item, 2048) for item in raw_args[:100]]
    raw_artifacts = value.get("artifactPatterns") or []
    if not isinstance(raw_artifacts, Sequence) or isinstance(raw_artifacts, (str, bytes)):
        raise ValueError("workspace action artifactPatterns must be an array")
    artifacts = [
        _relative_path(item, label="artifact pattern")
        for item in raw_artifacts[:20]
        if _text(item, 1024)
    ]
    preview_port = value.get("previewPort")
    if preview_port in (None, ""):
        normalized_port = None
    else:
        normalized_port = int(preview_port)
        if normalized_port < 1 or normalized_port > 65535:
            raise ValueError("workspace action preview port is invalid")
    return {
        "id": action_id,
        "label": _text(value.get("label"), 160) or action_id,
        "kind": kind,
        "program": program,
        "args": args,
        "cwd": _relative_path(value.get("cwd"), label="workspace action cwd"),
        "longRunning": bool(value.get("longRunning")),
        "previewPort": normalized_port,
        "readyPattern": _text(value.get("readyPattern"), 500),
        "artifactPatterns": artifacts,
        "disabled": bool(value.get("disabled")),
        "source": "user",
    }


def normalize_execution_actions(value: Any) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("executionActions must be an array")
    actions = [
        normalize_execution_action(item, index)
        for index, item in enumerate(value[:_MAX_ACTIONS])
        if isinstance(item, Mapping)
    ]
    ids = [item["id"] for item in actions]
    if len(ids) != len(set(ids)):
        raise ValueError("workspace action ids must be unique")
    return actions


__all__ = ["normalize_execution_action", "normalize_execution_actions"]
