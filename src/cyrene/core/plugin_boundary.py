"""Host-level exception contract for executing Plugin-owned code."""

from __future__ import annotations

from typing import TypeAlias


# Plugin code may use libraries that raise ``SystemExit`` instead of an ordinary
# exception (for example, a server failing to bind). Exiting is local to the
# contribution that initiated it; cancellation and operator interrupts remain
# host concerns and are intentionally not included here.
PluginBoundaryError: TypeAlias = Exception | SystemExit
PLUGIN_BOUNDARY_ERRORS = (Exception, SystemExit)


__all__ = ["PLUGIN_BOUNDARY_ERRORS", "PluginBoundaryError"]
