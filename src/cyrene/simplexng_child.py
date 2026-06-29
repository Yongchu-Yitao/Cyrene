"""SimpleXNG child-process entry point with parent-liveness monitoring."""

from __future__ import annotations

import os
import runpy
import threading
import time


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

    runpy.run_module("simplexng.simplexng", run_name="__main__")


if __name__ == "__main__":
    main()
