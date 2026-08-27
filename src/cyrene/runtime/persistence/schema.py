"""Canonical SQLite schemas for runtime persistence."""

from __future__ import annotations

RUNTIME_SCHEMA = """
CREATE TABLE IF NOT EXISTS goal_loop_drafts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    base_plan_revision INTEGER NOT NULL,
    goal TEXT NOT NULL,
    goal_changed INTEGER NOT NULL DEFAULT 0,
    plan_json TEXT NOT NULL,
    acceptance_json TEXT NOT NULL,
    limits_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_goal_loop_drafts_session ON goal_loop_drafts(session_id);
CREATE INDEX IF NOT EXISTS idx_goal_loop_drafts_expires ON goal_loop_drafts(expires_at);

CREATE TABLE IF NOT EXISTS goal_runs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    objective TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    phase TEXT NOT NULL DEFAULT 'executing',
    plan_definition_revision INTEGER NOT NULL,
    current_step_id TEXT,
    permission_mode TEXT NOT NULL DEFAULT 'auto',
    reflection_mode TEXT NOT NULL DEFAULT 'proactive',
    max_active_seconds INTEGER NOT NULL,
    max_repair_rounds INTEGER NOT NULL,
    active_seconds REAL NOT NULL DEFAULT 0,
    active_started_at TEXT,
    repair_round INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_until TEXT,
    stop_reason TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_goal_runs_status ON goal_runs(status);
CREATE INDEX IF NOT EXISTS idx_goal_runs_lease ON goal_runs(lease_until);

CREATE TABLE IF NOT EXISTS goal_run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    step_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES goal_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_goal_run_events_run ON goal_run_events(run_id);

CREATE TABLE IF NOT EXISTS daily_stats (
    day TEXT PRIMARY KEY,
    llm_requests INTEGER NOT NULL DEFAULT 0,
    tool_calls INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
    cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
    archive_entries INTEGER NOT NULL DEFAULT 0,
    memory_new INTEGER NOT NULL DEFAULT 0,
    memory_mentions INTEGER NOT NULL DEFAULT 0,
    emotion_sum REAL NOT NULL DEFAULT 0,
    emotion_count INTEGER NOT NULL DEFAULT 0,
    activity_00_04 INTEGER NOT NULL DEFAULT 0,
    activity_04_08 INTEGER NOT NULL DEFAULT 0,
    activity_08_12 INTEGER NOT NULL DEFAULT 0,
    activity_12_16 INTEGER NOT NULL DEFAULT 0,
    activity_16_20 INTEGER NOT NULL DEFAULT 0,
    activity_20_24 INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS daily_model_stats (
    day TEXT NOT NULL,
    model TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, model)
);

CREATE TABLE IF NOT EXISTS daily_topic_terms (
    day TEXT NOT NULL,
    term TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, term)
);
CREATE INDEX IF NOT EXISTS idx_daily_topic_terms_day ON daily_topic_terms(day);

CREATE TABLE IF NOT EXISTS daily_tool_stats (
    day TEXT NOT NULL,
    tool TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, tool)
);
CREATE INDEX IF NOT EXISTS idx_daily_tool_stats_day ON daily_tool_stats(day);

CREATE TABLE IF NOT EXISTS analytics_backfills (
    source TEXT PRIMARY KEY,
    completed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    model TEXT NOT NULL,
    round_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    caller TEXT NOT NULL DEFAULT 'main',
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
    cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    estimated_cost REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_token_usage_created_at ON token_usage(created_at);
CREATE INDEX IF NOT EXISTS idx_token_usage_model ON token_usage(model);
CREATE INDEX IF NOT EXISTS idx_token_usage_round_id ON token_usage(round_id);

CREATE TABLE IF NOT EXISTS permission_decisions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    round_id TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    tool_name TEXT NOT NULL DEFAULT '',
    operation TEXT NOT NULL DEFAULT '',
    permission_kind TEXT NOT NULL DEFAULT '',
    path_hint TEXT NOT NULL DEFAULT '',
    approved INTEGER NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_permission_decisions_session
ON permission_decisions(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_permission_decisions_round
ON permission_decisions(round_id, created_at);

CREATE TABLE IF NOT EXISTS llm_latency_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    round_id TEXT NOT NULL DEFAULT '',
    caller TEXT NOT NULL DEFAULT '',
    phase TEXT NOT NULL DEFAULT '',
    model_type TEXT NOT NULL DEFAULT 'primary',
    candidate_id TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    endpoint TEXT NOT NULL DEFAULT '',
    candidate_rank INTEGER NOT NULL DEFAULT 0,
    endpoint_rank INTEGER NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 1,
    outcome TEXT NOT NULL,
    status_code INTEGER NOT NULL DEFAULT 0,
    error_type TEXT NOT NULL DEFAULT '',
    error_body TEXT NOT NULL DEFAULT '',
    error_body_truncated INTEGER NOT NULL DEFAULT 0,
    queue_wait_ms REAL NOT NULL DEFAULT 0,
    pre_attempt_wait_ms REAL NOT NULL DEFAULT 0,
    request_ms REAL NOT NULL DEFAULT 0,
    response_headers_ms REAL,
    ttft_ms REAL,
    first_token_after_headers_ms REAL,
    generation_ms REAL,
    retry_backoff_ms REAL NOT NULL DEFAULT 0,
    total_call_ms REAL NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    prompt_cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
    prompt_cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit_ratio REAL NOT NULL DEFAULT 0,
    output_tokens_per_second REAL,
    fallback_used INTEGER NOT NULL DEFAULT 0,
    client_pool_reused INTEGER NOT NULL DEFAULT 0,
    connection_pool_key TEXT NOT NULL DEFAULT '',
    model_lease_id TEXT NOT NULL DEFAULT '',
    request_messages_fingerprint TEXT NOT NULL DEFAULT '',
    request_tools_fingerprint TEXT NOT NULL DEFAULT '',
    request_payload_fingerprint TEXT NOT NULL DEFAULT '',
    previous_payload_fingerprint TEXT NOT NULL DEFAULT '',
    cache_prefix_status TEXT NOT NULL DEFAULT '',
    cache_invalidation_reason TEXT NOT NULL DEFAULT '',
    cache_prefix_message_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_llm_latency_created_at ON llm_latency_events(created_at);
CREATE INDEX IF NOT EXISTS idx_llm_latency_call_id ON llm_latency_events(call_id);
CREATE INDEX IF NOT EXISTS idx_llm_latency_endpoint ON llm_latency_events(endpoint);

CREATE TABLE IF NOT EXISTS runtime_trace_spans (
    span_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    parent_span_id TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    round_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ok',
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    duration_ms REAL NOT NULL DEFAULT 0,
    attributes_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(trace_id, span_id)
);
CREATE INDEX IF NOT EXISTS idx_runtime_trace_spans_trace
ON runtime_trace_spans(trace_id, started_at);
CREATE INDEX IF NOT EXISTS idx_runtime_trace_spans_run
ON runtime_trace_spans(run_id, started_at);

CREATE TABLE IF NOT EXISTS workbench_state (
    key TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""
