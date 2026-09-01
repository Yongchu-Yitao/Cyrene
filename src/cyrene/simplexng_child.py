"""Isolated SimpleXNG child runtime.

This module deliberately lives outside :mod:`cyrene.plugins`.  The Windows on
ARM compatibility sidecar imports it in a minimal x64 environment where the
Workbench/application dependencies are not installed.
"""

from __future__ import annotations

import os
import runpy
import sys
import threading
import time
import types


_PARENT_PID_ENV = "CYRENE_SIMPLEXNG_PARENT_PID"


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _parent_is_alive(parent_pid: int) -> bool:
    """Check both parent identity and PID liveness to avoid PID-reuse mistakes."""
    try:
        if os.getppid() != parent_pid:
            return False
    except (AttributeError, OSError):
        pass
    return _pid_exists(parent_pid)


def _watch_parent(parent_pid: int, interval: float = 1.0) -> None:
    while True:
        time.sleep(interval)
        if not _parent_is_alive(parent_pid):
            os._exit(0)


def _install_windows_compat_patches() -> None:
    """Patch SimpleXNG's vendored SearXNG assumptions for Windows."""
    if sys.platform != "win32":
        return

    try:
        import winloop

        # simplexng._vendor.searx.network.client imports uvloop unconditionally.
        # Windows builds ship winloop instead; exposing it under the uvloop name
        # keeps the vendored import path working without editing site-packages.
        sys.modules.setdefault("uvloop", winloop)
    except Exception:
        pass

    if "pwd" not in sys.modules:
        pwd_stub = types.ModuleType("pwd")

        def getpwuid(uid: int):
            name = os.environ.get("USERNAME", "unknown")
            return type("pw", (), {"pw_name": name, "pw_uid": uid})()

        pwd_stub.getpwuid = getpwuid  # type: ignore[attr-defined]
        sys.modules["pwd"] = pwd_stub

    import multiprocessing

    original_get_context = multiprocessing.get_context

    def get_context(method: str | None = None):
        if method == "fork":
            method = "spawn"
        return original_get_context(method)

    multiprocessing.get_context = get_context


def main() -> None:
    raw_parent_pid = os.environ.get(_PARENT_PID_ENV, "").strip()
    try:
        parent_pid = int(raw_parent_pid)
    except ValueError:
        parent_pid = 0

    if parent_pid > 0:
        threading.Thread(
            target=_watch_parent,
            args=(parent_pid,),
            name="simplexng-parent-watchdog",
            daemon=True,
        ).start()

    _install_windows_compat_patches()
    runpy.run_module("simplexng.simplexng", run_name="__main__")


if __name__ == "__main__":
    main()
