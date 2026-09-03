"""Environment preparation for host executables launched by Cyrene."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping


def external_process_environment(
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return an environment safe for launching host executables.

    PyInstaller prepends its private library directory to ``LD_LIBRARY_PATH``
    so the bundled application can load its own native dependencies.  Passing
    that value to a host executable can make it load Cyrene's bundled OpenSSL
    instead of the system copy.  PyInstaller preserves the original value in
    ``LD_LIBRARY_PATH_ORIG``; restore it for child processes, or remove the
    override when the original value was empty.
    """

    env = dict(os.environ if base is None else base)
    if not sys.platform.startswith("linux"):
        return env

    frozen = bool(getattr(sys, "frozen", False))
    if not frozen and "LD_LIBRARY_PATH_ORIG" not in env:
        return env

    original = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if original:
        env["LD_LIBRARY_PATH"] = original
    else:
        env.pop("LD_LIBRARY_PATH", None)
    return env


__all__ = ["external_process_environment"]
