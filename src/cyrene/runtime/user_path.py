"""Merge the user's login-shell PATH into the process environment.

GUI-launched Electron apps inherit LaunchServices' minimal PATH
(/usr/bin:/bin:/usr/sbin:/sbin), so subprocesses — ACP agents and the
external CLIs they spawn, toolchains, the Bash tool — cannot see
shell-managed runtimes like nvm, Homebrew, or mise.  ``ensure_user_path``
runs once per process: it probes the login shell for the real PATH and
falls back to scanning common install roots when the probe is unavailable.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_SHELL_PROBE_TIMEOUT_SECONDS = 3.0
_done = False


def merge_path_entries(*groups: str) -> str:
    """Join PATH entry groups, deduplicated, preserving first-seen order."""
    seen: set[str] = set()
    merged: list[str] = []
    for group in groups:
        for raw in group.split(os.pathsep):
            entry = raw.strip()
            if entry and entry not in seen:
                seen.add(entry)
                merged.append(entry)
    return os.pathsep.join(merged)


def _probe_login_shell_path(shell: str) -> str:
    """Return the login shell's PATH, or '' when the probe fails.

    The probe runs non-interactively (``-l -c``) so interactive startup
    blocks are skipped.  Output from shell startup files may precede the
    PATH, so the last non-empty line is used; a PATH never starts with a
    non-slash prefix, so foreign trailing output invalidates the probe.
    """
    try:
        result = subprocess.run(
            [shell, "-l", "-c", 'printf %s "$PATH"'],
            capture_output=True,
            text=True,
            timeout=_SHELL_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        return ""
    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    if not lines:
        return ""
    candidate = lines[-1]
    if os.pathsep not in candidate or not candidate.startswith("/"):
        return ""
    return candidate


def _select_login_shell() -> str | None:
    for name in (os.environ.get("SHELL"), "zsh", "bash"):
        if not name:
            continue
        resolved = name if os.path.sep in name else shutil.which(name)
        if resolved and os.path.isfile(resolved):
            return resolved
    return None


def _nvm_version_key(path: Path) -> tuple[int, ...]:
    """Sort key for nvm version directories (v22.3.0 > v9.11.2 numerically)."""
    digits = tuple(int(part) for part in re.findall(r"\d+", path.name))
    return digits or (0,)


def common_install_dirs() -> list[str]:
    home = Path.home()
    dirs: list[str] = []
    if sys.platform == "darwin":
        dirs.extend(("/opt/homebrew/bin", "/usr/local/bin"))
    elif sys.platform.startswith("linux"):
        dirs.extend(("/usr/local/bin", "/snap/bin", str(home / ".local/bin")))
    nvm_root = home / ".nvm" / "versions" / "node"
    if nvm_root.is_dir():
        # Newest nvm-installed Node first, mirroring shell activation order.
        dirs.extend(str(candidate) for candidate in sorted(nvm_root.glob("*/bin"), key=_nvm_version_key, reverse=True))
    dirs.extend((str(home / ".mise" / "shims"), str(home / ".local" / "bin"), str(home / "bin")))
    return dirs


def _path_has_runtime_dirs(path: str) -> bool:
    """Whether ``path`` already exposes shell-managed runtime directories.

    A terminal/IDE/service-manager launch inherits a complete PATH containing
    at least one common runtime root; only LaunchServices-style minimal PATHs
    (GUI Electron) lack them. Probing the login shell for those would add
    startup latency and reorder entries for no benefit.
    """
    entries = set(entry for entry in path.split(os.pathsep) if entry)
    return any(str(directory) in entries for directory in common_install_dirs())


def ensure_user_path() -> str:
    """Merge the user login-shell PATH into ``os.environ`` once; return it."""
    global _done
    if _done:
        return os.environ.get("PATH", "")
    _done = True
    current = os.environ.get("PATH", "")
    if _path_has_runtime_dirs(current):
        logger.info("Skipping login-shell PATH probe: inherited PATH already exposes runtime directories")
        return current
    shell = _select_login_shell()
    if shell is None:
        logger.info(
            "Skipping login-shell PATH probe: no login shell resolvable%s",
            " on Windows" if sys.platform == "win32" else "",
        )
    probe = _probe_login_shell_path(shell) if shell else ""
    # Inherited entries stay first so a venv/parent-supplied PATH is never
    # shadowed; the login-shell PATH and common install roots only extend it.
    merged = merge_path_entries(current, probe, *common_install_dirs())
    if merged and merged != current:
        os.environ["PATH"] = merged
        logger.info("Augmented subprocess PATH with user shell directories")
    return os.environ.get("PATH", "")
