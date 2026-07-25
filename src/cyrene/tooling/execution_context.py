"""Task-local execution context shared by native tools and the dispatcher."""

from __future__ import annotations


def is_system_initiated_round() -> bool:
    """Return whether the active tool call belongs to a proactive system round."""
    try:
        from cyrene.agent.context import current_assistant_meta

        meta = current_assistant_meta()
        return isinstance(meta, dict) and bool(meta.get("system_initiated"))
    except Exception:
        return False


__all__ = ["is_system_initiated_round"]
