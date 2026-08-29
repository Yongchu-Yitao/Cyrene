"""Errors raised by the tree-local Hook system."""

from __future__ import annotations


class HookError(RuntimeError):
    """Base error for Hook registration and dispatch."""


class HookBlocked(HookError):
    """Raised when a blocking ``PreToolUse`` Hook denies a tool call."""
