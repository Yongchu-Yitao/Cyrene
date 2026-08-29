"""Errors raised by the tree-local Hook system."""

from __future__ import annotations


class HookError(RuntimeError):
    """Base error for Hook registration and dispatch."""


class HookBlocked(HookError):
    """Raised when a blocking ``PreToolUse`` Hook denies a tool call."""


class HookAwaitingUser(HookError):
    """Raised when a ``PreToolUse`` Hook needs one durable user decision."""

    def __init__(self, question: object) -> None:
        super().__init__("User confirmation is required")
        self.question = question
