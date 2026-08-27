"""Permission-mode vocabulary owned by the Plugin Agent kernel."""

from __future__ import annotations

PERMISSION_MODES = frozenset(
    {"default", "auto", "plan", "full_access", "workspace_only"}
)


def normalize_permission_mode(value: object) -> str:
    mode = str(value or "default").strip().lower()
    if mode not in PERMISSION_MODES:
        return "default"
    return mode


def runtime_permission_mode(value: object) -> str:
    mode = normalize_permission_mode(value)
    return "default" if mode == "workspace_only" else mode


__all__ = [
    "PERMISSION_MODES",
    "normalize_permission_mode",
    "runtime_permission_mode",
]
