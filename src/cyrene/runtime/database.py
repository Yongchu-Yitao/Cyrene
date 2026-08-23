"""Stable persistence facade for runtime callers.

SQLite schemas and query implementations live in :mod:`cyrene.runtime.persistence`.
This module deliberately retains the historical async function signatures and
legacy mapping return values used by the scheduler, routes, and extensions.
"""

from __future__ import annotations

from typing import Any

from cyrene.runtime.persistence.analytics import (
    activity_column,
    backfill_analytics,
    bump_activity_sync,
    count_stat_days,
    estimate_cost,
    extract_topic_terms,
    get_daily_stats_range,
    get_llm_cache_stats_by_phase,
    get_model_stats_range,
    get_runtime_trace,
    get_token_usage_stats,
    get_tool_counts_range,
    get_topic_counts_range,
    record_archive_exchange,
    record_llm_latency,
    record_llm_telemetry_batch,
    record_memory_touch_sync,
    record_permission_decision,
    record_runtime_trace_span,
    record_runtime_trace_spans,
    record_token_usage,
    record_tool_call,
    record_usage_stats_batch,
)
from cyrene.runtime.persistence.knowledge import (
    KB_FTS_SQL,
    KB_TABLES_SQL,
    LIBRARY_FTS_SQL,
    init_knowledge_db,
)
from cyrene.runtime.persistence.migrations import initialize_runtime_database
from cyrene.runtime.persistence.scheduler import SchedulerRepository
from cyrene.runtime.persistence.schema import RUNTIME_SCHEMA

# Historical diagnostic helper retained without importing a private repository
# member across the package boundary.
_estimate_cost = estimate_cost
# Historical diagnostic export.  New repository consumers should use the
# public ``activity_column`` name imported above.
_activity_column = activity_column


async def init_db(db_path: str) -> None:
    """Initialize the runtime schema and run one-time analytics backfills."""
    await initialize_runtime_database(db_path, RUNTIME_SCHEMA)
    await backfill_analytics(db_path)


async def create_task(
    db_path: str,
    chat_id: int,
    prompt: str,
    schedule_type: str,
    schedule_value: str,
    next_run: str,
    permission_mode: str = "workspace_only",
    project_id: str = "default",
    schedule_timezone: str = "UTC",
    origin_session_id: str = "",
    action_type: str = "agent_task",
) -> str:
    return await SchedulerRepository(db_path).create(
        chat_id=chat_id,
        prompt=prompt,
        schedule_type=schedule_type,
        schedule_value=schedule_value,
        next_run=next_run,
        permission_mode=permission_mode,
        project_id=project_id,
        schedule_timezone=schedule_timezone,
        origin_session_id=origin_session_id,
        action_type=action_type,
    )


async def get_all_tasks(db_path: str, project_id: str | None = None) -> list[dict]:
    return [task.to_legacy_dict() for task in await SchedulerRepository(db_path).list(project_id)]


async def get_task(db_path: str, task_id: str) -> dict | None:
    task = await SchedulerRepository(db_path).get(task_id)
    return task.to_legacy_dict() if task is not None else None


async def edit_task(db_path: str, task_id: str, updates: dict[str, Any]) -> bool:
    return await SchedulerRepository(db_path).edit(task_id, updates)


async def get_due_tasks(db_path: str) -> list[dict]:
    return [task.to_legacy_dict() for task in await SchedulerRepository(db_path).list_due()]


async def update_task_status(db_path: str, task_id: str, status: str) -> bool:
    return await SchedulerRepository(db_path).update_status(task_id, status)


async def delete_task(db_path: str, task_id: str) -> bool:
    return await SchedulerRepository(db_path).delete(task_id)


async def update_task_after_run(
    db_path: str,
    task_id: str,
    last_result: str,
    next_run: str | None,
    status: str = "active",
) -> None:
    await SchedulerRepository(db_path).update_after_run(task_id, last_result, next_run, status)


async def log_task_run(
    db_path: str,
    task_id: str,
    duration_ms: int,
    status: str,
    result: str | None = None,
    error: str | None = None,
) -> None:
    await SchedulerRepository(db_path).log_run(task_id, duration_ms, status, result, error)


async def get_task_time_totals(db_path: str) -> dict:
    return (await SchedulerRepository(db_path).time_totals()).to_legacy_dict()
