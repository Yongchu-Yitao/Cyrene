"""Small SDK helpers for project-type detector Plugins."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any


def scope_id(scope: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", scope.strip("./") or "root")[:40]


def relative_scope(workspace: Path, value: Path) -> str:
    relative = value.resolve().relative_to(workspace.resolve()).as_posix()
    return relative or "."


def nearest_scope(
    workspace: Path,
    current_path: str,
    *markers: str,
) -> Path | None:
    root = workspace.resolve()
    candidate = (root / str(current_path or ".")).resolve()
    if candidate.is_file() or candidate.suffix:
        candidate = candidate.parent
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    for directory in (candidate, *candidate.parents):
        if any((directory / marker).exists() for marker in markers):
            return directory
        if directory == root:
            break
    return None


def workspace_action(
    action_id: str,
    label: str,
    kind: str,
    program: str,
    args: list[str],
    *,
    cwd: str = ".",
    long_running: bool = False,
    preview_port: int | None = None,
    artifacts: list[str] | None = None,
    ready_pattern: str = "",
    i18n: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "id": action_id,
        "label": label,
        "i18n": dict(i18n or {}),
        "kind": kind,
        "program": program,
        "args": list(args),
        "cwd": cwd,
        "longRunning": long_running,
        "previewPort": preview_port,
        "readyPattern": ready_pattern,
        "artifactPatterns": list(artifacts or []),
        "disabled": False,
    }


def preferred_program(*names: str) -> str:
    """Resolve a runtime using the same PATH as Cyrene-launched processes."""

    from cyrene.plugins.builtin.cyrene_extensions.extension_service import (
        agent_process_environment,
    )

    search_path = agent_process_environment().get("PATH", "")
    for name in names:
        resolved = shutil.which(str(name or "").strip(), path=search_path)
        if resolved:
            return resolved
    return str(names[0] if names else "")


__all__ = [
    "nearest_scope",
    "preferred_program",
    "relative_scope",
    "scope_id",
    "workspace_action",
]
