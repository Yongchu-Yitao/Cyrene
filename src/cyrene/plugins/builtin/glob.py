"""Standalone Glob Plugin."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from cyrene.core.plugin import Plugin, PluginContext

_IGNORED_PARTS = {".git", ".hg", ".svn", ".venv", "node_modules", "__pycache__"}
_SCAN_SECONDS = 20.0
_MAX_CANDIDATES = 50_000
_MAX_MATCHES = 200


def _workspace(context: PluginContext) -> Path:
    if context.workspace is None:
        raise ValueError("a workspace is required")
    workspace = Path(context.workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"workspace is not a directory: {workspace}")
    return workspace


def _scan(workspace: Path, pattern: str) -> list[str]:
    matches: list[str] = []
    deadline = time.monotonic() + _SCAN_SECONDS
    for index, candidate in enumerate(workspace.glob(pattern), start=1):
        if index > _MAX_CANDIDATES or time.monotonic() >= deadline:
            break
        try:
            relative = candidate.relative_to(workspace)
            candidate.resolve().relative_to(workspace)
        except (OSError, ValueError):
            continue
        if any(part in _IGNORED_PARTS for part in relative.parts):
            continue
        matches.append(str(relative))
        if len(matches) >= _MAX_MATCHES:
            break
    return sorted(matches)


async def glob(arguments: dict[str, Any], context: PluginContext) -> str:
    workspace = _workspace(context)
    pattern = str(arguments["pattern"])
    matches = await asyncio.to_thread(_scan, workspace, pattern)
    return "\n".join(matches) if matches else "No matches."


plugin = Plugin(
    name="Glob",
    description="Find files in the workspace using a glob pattern.",
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "minLength": 1,
                "description": "Workspace-relative glob pattern, for example **/*.py.",
            }
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
    handler=glob,
    allow_parallel=True,
    timeout_seconds=30.0,
)


__all__ = ["plugin"]
