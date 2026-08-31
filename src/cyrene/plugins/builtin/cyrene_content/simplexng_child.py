"""Compatibility entry point for the isolated SimpleXNG child runtime."""

from __future__ import annotations

from cyrene.simplexng_child import (
    _PARENT_PID_ENV,
    _install_windows_compat_patches,
    _parent_is_alive,
    _pid_exists,
    _watch_parent,
    main,
)


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
