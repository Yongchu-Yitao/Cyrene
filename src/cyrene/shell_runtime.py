"""Cross-platform shell discovery and invocation helpers."""

from __future__ import annotations

import os
import ntpath
import shutil
import subprocess
import sys
from pathlib import Path

# Cache of "does this executable actually launch?" probe results, keyed by the
# resolved executable path. The shell environment does not change during a
# process's lifetime, so probing each candidate at most once is safe.
_launch_cache: dict[str, bool] = {}


def _existing_file(value: str | os.PathLike[str] | None) -> str | None:
    if not value:
        return None
    path = Path(value).expanduser()
    return str(path) if path.is_file() else None


def _which(name: str) -> str | None:
    return shutil.which(name)


def _is_wsl_launcher(path: str) -> bool:
    """``C:\\Windows\\System32\\bash.exe`` is the WSL launcher, not a usable bash.

    It runs commands inside the WSL VM (a different filesystem), so invoking it
    with ``-lc`` and a Windows ``cwd`` fails or behaves wrong. A trivial
    ``-c 'exit 0'`` probe would still succeed, so it must be excluded by path.
    """
    normalized = str(path).replace("/", "\\").lower()
    return normalized.endswith("\\system32\\bash.exe") or normalized.endswith("\\sysnative\\bash.exe")


def _can_launch(executable: str) -> bool:
    """Probe whether a bash executable actually starts.

    Skips entries that exist on disk but are broken (e.g. a stale PATH entry).
    Only meaningful on Windows; the Unix path trusts ``$SHELL`` / ``/bin/bash``.
    """
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

    for env_name in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env_name)
        if not base:
            continue
        # ``Git`` = system install (Program Files); ``Programs\Git`` = per-user
        # install (LOCALAPPDATA), which the previous list missed.
        for git_root in ("Git", "Programs\\Git"):
            candidates.extend([
                str(Path(base) / git_root / "bin" / "bash.exe"),
                str(Path(base) / git_root / "usr" / "bin" / "bash.exe"),
            ])
    # Drop the WSL launcher stub — it cannot run with a Windows cwd via -lc.
    return [candidate for candidate in candidates if not _is_wsl_launcher(candidate)]


def resolve_shell(unix_fallback: str = "/bin/sh") -> tuple[str, str]:
    """Return ``(kind, executable)`` for the best available local shell.

    ``unix_fallback`` is used on non-Windows hosts when ``$SHELL`` is unset.
    """
    if sys.platform != "win32":
        return "bash", os.environ.get("SHELL") or unix_fallback

    for candidate in _windows_bash_candidates():
        executable = _existing_file(candidate)
        # Probe each candidate so a broken entry falls through to the next one
        # instead of locking us onto an unusable bash.
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
        "No supported shell found. Install Git Bash or PowerShell, "
        "or ensure cmd.exe is available through COMSPEC/PATH."
    )


def command_argv(command: str) -> list[str]:
    """Build argv for a one-shot command using the best available shell."""
    kind, executable = resolve_shell()
    if kind == "bash":
        return [executable, "-lc", command]
    if kind == "powershell":
        return [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command]
    return [executable, "/d", "/s", "/c", command]


def interactive_argv() -> tuple[str, list[str]]:
    """Build argv for a persistent interactive shell.

    Defaults to ``/bin/bash`` (not ``/bin/sh``) when ``$SHELL`` is unset, matching
    the persistent-shell behavior prior to the shell_runtime refactor.
    """
    kind, executable = resolve_shell(unix_fallback="/bin/bash")
    if kind == "bash":
        return kind, [executable, "-i"]
    if kind == "powershell":
        return kind, [executable, "-NoLogo", "-NoProfile", "-NoExit", "-Command", "-"]
    return kind, [executable, "/d", "/q"]
