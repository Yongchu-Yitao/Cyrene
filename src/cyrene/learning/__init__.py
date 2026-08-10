"""Lazy public API for behavior learning and learned skills."""

from __future__ import annotations

from importlib import import_module
from typing import Any


__all__ = [
    "activate_learned_skill",
    "apply_skill_patch",
    "decide_skill_candidate",
    "delete_learned_skill",
    "deprecate_learned_skill",
    "get_learned_skill",
    "init",
    "learn_from_turn",
    "list_learned_skill_patches",
    "list_learned_skill_runs",
    "list_learned_skill_versions",
    "list_learned_skills",
    "list_skill_candidates",
    "list_tool_chains",
    "rebuild_learning_state",
    "record_action",
    "reject_skill_patch",
    "rollback_learned_skill",
    "run_learned_skill",
    "scan_for_manual_learn",
    "scan_for_session_start",
    "tick",
    "update_learned_skill",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module("cyrene.learning.facade"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted((*globals(), *__all__))
