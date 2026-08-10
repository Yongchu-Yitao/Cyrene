"""Compatibility wrapper for behavior learning and learned skills."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cyrene.learning import engine as _behavior


async def record_action(
    tool: str,
    args: dict[str, Any],
    caller: str,
    round_id: str,
    duration_ms: float,
    *,
    result: Any = "",
    success: bool = True,
    error: str = "",
) -> None:
    await _behavior.record_action(
        tool,
        args,
        caller,
        round_id,
        duration_ms,
        result=result,
        success=success,
        error=error,
    )


async def list_learned_skills(project_id: str = "") -> list[dict[str, Any]]:
    return await _behavior.list_learned_skills(project_id)


async def list_tool_chains(project_id: str | list[str] = "", limit: int = 80) -> list[dict[str, Any]]:
    return await _behavior.list_tool_chains(project_id, limit)


async def list_skill_candidates(project_id: str = "", status: str = "all") -> list[dict[str, Any]]:
    return await _behavior.list_skill_candidates(project_id, status)


async def decide_skill_candidate(candidate_id: str, decision: str) -> dict[str, Any]:
    return await _behavior.decide_skill_candidate(candidate_id, decision)


async def get_learned_skill(skill_id: str) -> dict[str, Any] | None:
    return await _behavior.get_learned_skill(skill_id)


async def list_learned_skill_versions(skill_id: str) -> list[dict[str, Any]]:
    return await _behavior.list_learned_skill_versions(skill_id)


async def list_learned_skill_patches(skill_id: str, status: str = "all") -> list[dict[str, Any]]:
    return await _behavior.list_learned_skill_patches(skill_id, status)


async def list_learned_skill_runs(skill_id: str, limit: int = 50) -> list[dict[str, Any]]:
    return await _behavior.list_learned_skill_runs(skill_id, limit)


async def activate_learned_skill(skill_id: str) -> bool:
    return await _behavior.manual_activate_skill(skill_id)


async def deprecate_learned_skill(skill_id: str) -> bool:
    return await _behavior.manual_deprecate_skill(skill_id)


async def run_learned_skill(skill_id: str, param_overrides: dict[str, Any] | None = None) -> str:
    return await _behavior.run_learned_skill(skill_id, param_overrides)


async def update_learned_skill(skill_id: str, updates: dict[str, Any], *, reason: str = "Manual skill edit.") -> dict[str, Any] | None:
    return await _behavior.update_learned_skill(skill_id, updates, reason=reason)


async def apply_skill_patch(skill_id: str, patch_id: str) -> dict[str, Any]:
    return await _behavior.apply_skill_patch(skill_id, patch_id)


async def reject_skill_patch(skill_id: str, patch_id: str) -> bool:
    return await _behavior.reject_skill_patch(skill_id, patch_id)


async def rollback_learned_skill(skill_id: str, version: int) -> dict[str, Any]:
    return await _behavior.rollback_learned_skill(skill_id, version)


async def delete_learned_skill(skill_id: str) -> bool:
    return await _behavior.delete_learned_skill(skill_id)


async def scan_for_session_start() -> dict[str, Any]:
    return await _behavior.scan_for_session_start()


async def scan_for_manual_learn(project_id: str = "") -> dict[str, Any]:
    return await _behavior.scan_for_manual_learn(project_id)


async def rebuild_learning_state(*, reprocess_all_turns: bool = True, project_id: str = "") -> dict[str, Any]:
    return await _behavior.rebuild_learning_state(reprocess_all_turns=reprocess_all_turns, project_id=project_id)


async def learn_from_turn(turn_id: str) -> dict[str, Any]:
    return await _behavior.learn_from_turn(turn_id)


async def tick(bot: Any, db_path: str) -> None:
    await _behavior.tick(bot, db_path)


async def init(data_dir: Path, workspace_dir: Path) -> None:
    await _behavior.init(data_dir, workspace_dir)
