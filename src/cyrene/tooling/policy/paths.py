"""Workspace path-policy helpers."""

from cyrene.tooling import runtime_support as _implementation

__all__ = [
    "_resolve_tool_path",
    "_resolve_workspace_path",
    "_resolve_workspace_write_target",
    "resolve_tool_path",
    "resolve_workspace_path",
    "resolve_workspace_write_target",
]

resolve_tool_path = _implementation._resolve_tool_path
resolve_workspace_path = _implementation._resolve_workspace_path
resolve_workspace_write_target = _implementation._resolve_workspace_write_target

_resolve_tool_path = resolve_tool_path
_resolve_workspace_path = resolve_workspace_path
_resolve_workspace_write_target = resolve_workspace_write_target
