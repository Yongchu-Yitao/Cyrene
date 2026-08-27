"""Cross-platform shell discovery shared by terminal UI and code Plugins."""

from __future__ import annotations

import ntpath
import os
import shutil
import subprocess
import sys
from pathlib import Path

_launch_cache: dict[str, bool] = {}


def _existing_file(value: str | os.PathLike[str] | None) -> str | None:
    if not value:
        return None
    path = Path(value).expanduser()
    return str(path) if path.is_file() else None


def _which(name: str) -> str | None:
    return shutil.which(name)


def _is_wsl_launcher(path: str) -> bool:
    normalized = str(path).replace("/", "\\").lower()
    return normalized.endswith("\\system32\\bash.exe") or normalized.endswith(
        "\\sysnative\\bash.exe"
    )


def _can_launch(executable: str) -> bool:
    if executable in _launch_cache:
        return _launch_cache[executable]
    try:
        completed = subprocess.run(
            [executable, "-c", "exit 0"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        ok = completed.returncode == 0
    except Exception:
        ok = False
    _launch_cache[executable] = ok
    return ok


def _windows_bash_candidates() -> list[str]:
    candidates: list[str] = []
    configured = os.environ.get("SHELL", "").strip().strip('"')
    if configured and ntpath.basename(configured).lower() in {"bash", "bash.exe"}:
        candidates.append(configured)
    discovered = _which("bash.exe") or _which("bash")
    if discovered:
        candidates.append(discovered)
    for env_name in (
        "ProgramFiles",
        "ProgramW6432",
        "ProgramFiles(x86)",
        "LOCALAPPDATA",
    ):
        base = os.environ.get(env_name)
        if not base:
            continue
        for git_root in ("Git", "Programs\\Git"):
            candidates.extend(
                [
                    str(Path(base) / git_root / "bin" / "bash.exe"),
                    str(Path(base) / git_root / "usr" / "bin" / "bash.exe"),
                ]
            )
    return [
        candidate
        for candidate in candidates
        if not _is_wsl_launcher(candidate)
    ]


def resolve_shell(unix_fallback: str = "/bin/sh") -> tuple[str, str]:
    """Return ``(kind, executable)`` for the best available local shell."""

    if sys.platform != "win32":
        return "bash", os.environ.get("SHELL") or unix_fallback

    configured = os.environ.get("SHELL", "").strip().strip('"')
    configured_name = ntpath.basename(configured).lower()
    if configured_name in {"pwsh", "pwsh.exe", "powershell", "powershell.exe"}:
        executable = _existing_file(configured) or _which(configured)
        if executable:
            return "powershell", executable
    if configured_name in {"cmd", "cmd.exe"}:
        executable = _existing_file(configured) or _which(configured)
        if executable:
            return "cmd", executable

    for candidate in _windows_bash_candidates():
        executable = _existing_file(candidate)
        if executable and _can_launch(executable):
            return "bash", executable

    for name in ("pwsh.exe", "pwsh", "powershell.exe", "powershell"):
        executable = _which(name)
        if executable:
            return "powershell", executable

    comspec = os.environ.get("COMSPEC", "").strip().strip('"')
    executable = _existing_file(comspec) or _which("cmd.exe") or _which("cmd")
    if executable:
        return "cmd", executable
    raise FileNotFoundError(
        "No supported shell found. Install Git Bash or PowerShell, or ensure "
        "cmd.exe is available through COMSPEC/PATH."
    )


def command_argv(command: str) -> list[str]:
    """Build argv for a one-shot command using the best available shell."""

    kind, executable = resolve_shell()
    if kind == "bash":
        return [executable, "-lc", command]
    if kind == "powershell":
        return [
            executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ]
    return [executable, "/d", "/s", "/c", command]


def interactive_argv() -> tuple[str, list[str]]:
    """Build argv for a persistent interactive shell."""

    kind, executable = resolve_shell(unix_fallback="/bin/bash")
    if kind == "bash":
        return kind, [executable, "-i"]
    if kind == "powershell":
        return kind, [
            executable,
            "-NoLogo",
            "-NoProfile",
            "-NoExit",
            "-Command",
            "-",
        ]
    return kind, [executable, "/d", "/q"]


__all__ = ["command_argv", "interactive_argv", "resolve_shell"]
