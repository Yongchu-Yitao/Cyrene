"""Fixed Bash Plugin."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from typing import Any

from cyrene.platform.subprocess_environment import external_process_environment

from ..plugin import Plugin, PluginContext
from .permission_boundaries import bash_boundary


async def _kill_process_tree(process: asyncio.subprocess.Process) -> None:
    """Stop descendants as well as the shell, including inherited output pipes."""
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill", "/PID", str(process.pid), "/T", "/F",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
        if process.returncode is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    await process.wait()


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
    environment = external_process_environment(environment)

    process = await asyncio.create_subprocess_shell(
        command,
        cwd=str(workspace),
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **({"start_new_session": True} if os.name != "nt" else {}),
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_ms / 1000,
        )
    except asyncio.TimeoutError:
        await _kill_process_tree(process)
        raise TimeoutError(f"command timed out after {timeout_ms} ms") from None
    except asyncio.CancelledError:
        await asyncio.shield(_kill_process_tree(process))
        raise
    return {
        "exit_code": int(process.returncode or 0),
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
    }


BASH_PLUGIN = Plugin(
    name="Bash",
    description=(
        "Run a shell command in the workspace. Shell file-printing output does "
        "not satisfy a request to open or show a named file in the workspace UI."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout_ms": {"type": "integer", "minimum": 1, "description": "Command timeout in milliseconds; defaults to 120000. Longer explicit timeouts are honored."},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
    handler=bash,
    permission_boundary=bash_boundary,
    allow_parallel=True,
    # The command owns its deadline; a second plugin timer would override it.
    timeout_seconds=None,
)


__all__ = ["BASH_PLUGIN", "bash"]
