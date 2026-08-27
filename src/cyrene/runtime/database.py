"""Persistence facade for analytics and shared runtime database setup.

Scheduled-task lifecycle operations belong exclusively to the schedule Plugin
and its :class:`SchedulerRepository` service.
"""

from __future__ import annotations

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


async def get_task_time_totals(db_path: str) -> dict:
    return (await SchedulerRepository(db_path).time_totals()).to_dict()
