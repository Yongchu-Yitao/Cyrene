"""Persistence facade for analytics and shared runtime database setup."""

from __future__ import annotations

import aiosqlite

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
from cyrene.runtime.persistence.migrations import initialize_runtime_database
from cyrene.runtime.persistence.schema import RUNTIME_SCHEMA

__all__ = [
    "activity_column",
    "backfill_analytics",
    "bump_activity_sync",
    "count_stat_days",
    "estimate_cost",
    "extract_topic_terms",
    "get_daily_stats_range",
    "get_llm_cache_stats_by_phase",
    "get_model_stats_range",
    "get_runtime_trace",
    "get_task_time_totals",
    "get_token_usage_stats",
    "get_tool_counts_range",
    "get_topic_counts_range",
    "init_db",
    "record_archive_exchange",
    "record_llm_latency",
    "record_llm_telemetry_batch",
    "record_memory_touch_sync",
    "record_permission_decision",
    "record_runtime_trace_span",
    "record_runtime_trace_spans",
    "record_token_usage",
    "record_tool_call",
    "record_usage_stats_batch",
]

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


async def get_task_time_totals(db_path: str) -> dict:
    async with aiosqlite.connect(str(db_path)) as connection:
        cursor = await connection.execute(
            "SELECT COALESCE(SUM(active_seconds), 0), "
            "COALESCE(MAX(active_seconds), 0), COUNT(*) FROM goal_runs"
        )
        goal_total_s, goal_longest_s, goal_runs = await cursor.fetchone()

    goal_total_ms = int(round(float(goal_total_s or 0) * 1000))
    goal_longest_ms = int(round(float(goal_longest_s or 0) * 1000))
    task_total_ms = 0
    task_longest_ms = 0
    task_runs = 0
    from cyrene.core.plugin import application_plugin_service

    schedules = application_plugin_service("schedules")
    time_totals = getattr(schedules, "time_totals", None)
    if callable(time_totals):
        schedule_totals = await time_totals()
        task_total_ms = int(getattr(schedule_totals, "total_ms", 0) or 0)
        task_longest_ms = int(getattr(schedule_totals, "longest_ms", 0) or 0)
        task_runs = int(getattr(schedule_totals, "runs", 0) or 0)
    return {
        "total_ms": goal_total_ms + task_total_ms,
        "longest_ms": max(goal_longest_ms, task_longest_ms),
        "runs": int(goal_runs or 0) + task_runs,
    }
