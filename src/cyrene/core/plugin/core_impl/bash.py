"""Fixed Bash Plugin."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from ..plugin import Plugin, PluginContext
from .permission_boundaries import bash_boundary


async def bash(arguments: dict[str, Any], context: PluginContext) -> dict[str, Any]:
    command = str(arguments.get("command") or "").strip()
    if not command:
        raise ValueError("command cannot be empty")
    timeout_ms = int(arguments.get("timeout_ms", 120_000))
    if timeout_ms <= 0:
        raise ValueError("timeout_ms must be greater than zero")
    workspace = (
        Path(context.workspace).expanduser().resolve()
        if context.workspace is not None
        else Path.cwd()
    )
    # Optional packs may extend the child PATH through a generic service port.
    # The fixed Bash tool remains usable when that pack is absent or disabled.
    extension_service = context.services.get("extensions")
    environment_builder = getattr(extension_service, "process_environment", None)
    environment = (
        environment_builder()
        if callable(environment_builder)
        else dict(os.environ)
    )

    process = await asyncio.create_subprocess_shell(
        command,
        cwd=str(workspace),
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_ms / 1000,
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise TimeoutError(f"command timed out after {timeout_ms} ms") from None
    except asyncio.CancelledError:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    return {
        "exit_code": int(process.returncode or 0),
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
    }


BASH_PLUGIN = Plugin(
    name="Bash",
    description="Run a shell command in the workspace.",
    input_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout_ms": {"type": "integer"},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
    handler=bash,
    permission_boundary=bash_boundary,
    allow_parallel=True,
    timeout_seconds=310.0,
)


__all__ = ["BASH_PLUGIN", "bash"]
