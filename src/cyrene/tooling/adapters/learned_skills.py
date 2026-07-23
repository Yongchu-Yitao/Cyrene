"""Learned-skill capability normalization."""

from __future__ import annotations

from typing import Any


def normalize_learned_step(step: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    reference = step.get("implementation_reference") or {}
    return (
        str(
            step.get("capability_id")
            or step.get("tool")
            or reference.get("tool_name")
            or ""
        ).strip(),
        dict(
            step.get("arguments")
            or step.get("args")
            or reference.get("args_template")
            or {}
        ),
    )
