"""Compatibility entry point for the isolated SimpleXNG child runtime."""

from __future__ import annotations

from cyrene import simplexng_child as _runtime


_PARENT_PID_ENV = _runtime._PARENT_PID_ENV
_install_windows_compat_patches = _runtime._install_windows_compat_patches
_parent_is_alive = _runtime._parent_is_alive
_pid_exists = _runtime._pid_exists
_watch_parent = _runtime._watch_parent
main = _runtime.main


__all__ = [
    "_PARENT_PID_ENV",
    "_install_windows_compat_patches",
    "_parent_is_alive",
    "_pid_exists",
    "_watch_parent",
    "main",
]


if __name__ == "__main__":
    main()
