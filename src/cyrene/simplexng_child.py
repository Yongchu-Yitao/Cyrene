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


_PARENT_PID_ENV = "CYRENE_SIMPLEXNG_PARENT_PID"


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _windows_pid_exists(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_pid_exists(pid: int) -> bool:
    """Probe without signals: os.kill(pid, 0) terminates processes on Windows."""
    import _winapi

    try:
        # SYNCHRONIZE is sufficient to wait on a process; no terminate access.
        handle = _winapi.OpenProcess(0x00100000, False, pid)
    except PermissionError:
        return True
    except OSError as exc:
        # ERROR_INVALID_PARAMETER means the PID no longer exists. Other errors
        # cannot establish death, so keep the child running and retry later.
        return getattr(exc, "winerror", None) != 87
    try:
        return _winapi.WaitForSingleObject(handle, 0) != _winapi.WAIT_OBJECT_0
    except OSError:
        return True
    finally:
        _winapi.CloseHandle(handle)


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
    if sys.platform != "win32":
        return
    from cyrene.platform.simplexng_calculator import install, prepare_windows_runtime

    prepare_windows_runtime()
    install()


def main() -> None:
    # PyInstaller windowed executables set Python's standard streams to None,
    # even when Popen supplied redirected OS handles. Restore the managed log
    # before upstream log_setup(), which writes startup errors to stdout.
    log_path = os.environ.get("CYRENE_SIMPLEXNG_LOG_PATH")
    if log_path and (sys.stdout is None or sys.stderr is None):
        stream = open(log_path, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream

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
