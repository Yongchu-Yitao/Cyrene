"""Standalone Grep Plugin."""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

from agent.plugin import Plugin, PluginContext

_IGNORED_PARTS = {".git", ".hg", ".svn", ".venv", "node_modules", "__pycache__"}
_SCAN_SECONDS = 20.0
_MAX_CANDIDATES = 50_000
_MAX_FILE_BYTES = 4 * 1024 * 1024
_MAX_MATCHES = 200


def _workspace(context: PluginContext) -> Path:
    if context.workspace is None:
        raise ValueError("a workspace is required")
    workspace = Path(context.workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"workspace is not a directory: {workspace}")
    return workspace


def _search_root(workspace: Path, raw_path: Any) -> Path:
    value = str(raw_path or ".").strip() or "."
    path = Path(value).expanduser()
    resolved = path.resolve() if path.is_absolute() else (workspace / path).resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("path must stay within the workspace") from exc
    return resolved


def _scan(
    workspace: Path,
    search_root: Path,
    file_pattern: str,
    content_pattern: re.Pattern[str],
) -> list[str]:
    matches: list[str] = []
    deadline = time.monotonic() + _SCAN_SECONDS
    for index, candidate in enumerate(search_root.glob(file_pattern), start=1):
        if index > _MAX_CANDIDATES or time.monotonic() >= deadline:
            break
        try:
            relative = candidate.relative_to(workspace)
            candidate.resolve().relative_to(workspace)
            if any(part in _IGNORED_PARTS for part in relative.parts):
                continue
            if not candidate.is_file() or candidate.stat().st_size > _MAX_FILE_BYTES:
                continue
            content = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ValueError):
            continue
        for line_number, line in enumerate(content.splitlines(), start=1):
            if content_pattern.search(line):
                matches.append(f"{relative}:{line_number}:{line}")
                if len(matches) >= _MAX_MATCHES:
                    return matches
    return matches


async def grep(arguments: dict[str, Any], context: PluginContext) -> str:
    workspace = _workspace(context)
    search_root = _search_root(workspace, arguments.get("path"))
    content_pattern = re.compile(str(arguments["pattern"]))
    file_pattern = str(arguments.get("glob") or "**/*")
    matches = await asyncio.to_thread(
        _scan,
        workspace,
        search_root,
        file_pattern,
        content_pattern,
    )
    return "\n".join(matches) if matches else "No matches."


plugin = Plugin(
    name="Grep",
    description="Search file contents by regex pattern inside the workspace.",
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Python regular expression to search for.",
            },
            "path": {
                "type": "string",
                "description": "Workspace-relative directory or file root. Defaults to the workspace.",
            },
            "glob": {
                "type": "string",
                "minLength": 1,
                "description": "Glob used to select files below path. Defaults to **/*.",
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
    handler=grep,
    allow_parallel=True,
    timeout_seconds=30.0,
)


__all__ = ["plugin"]
