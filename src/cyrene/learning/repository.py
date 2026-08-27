"""SQLite connection boundary for behavior learning."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import aiosqlite


class LearningConnection:
    """Async connection context with the row contract used by repositories."""

    def __init__(self, db_file: Path, busy_timeout: float):
        self._db_file = db_file
        self._busy_timeout = busy_timeout
        self._connection: aiosqlite.Connection | None = None

    async def __aenter__(self) -> aiosqlite.Connection:
        self._db_file.parent.mkdir(parents=True, exist_ok=True)
        self._connection = aiosqlite.connect(
            str(self._db_file),
            timeout=self._busy_timeout,
        )
        await self._connection.__aenter__()
        self._connection.row_factory = sqlite3.Row
        return self._connection

    async def __aexit__(self, *args):
        if self._connection is None:
            return False
        return await self._connection.__aexit__(*args)


class LearningRepository:
    """Owns the behavior-learning SQLite schema."""

    def __init__(self, db_file: Path, busy_timeout: float):
        self._db_file = db_file
        self._busy_timeout = busy_timeout

    def connect(self) -> LearningConnection:
        return LearningConnection(self._db_file, self._busy_timeout)

    async def initialize(self) -> None:
        async with self.connect() as conn:
            cursor = await conn.execute("PRAGMA journal_mode = WAL")
            await cursor.fetchone()
            await cursor.close()
            await conn.executescript(LEARNING_SCHEMA)
            await conn.commit()

LEARNING_SCHEMA = """
CREATE TABLE IF NOT EXISTS behavior_sessions (
    session_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT '',
    project_key TEXT NOT NULL DEFAULT '',
    session_kind TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    session_summary TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_behavior_sessions_updated_at ON behavior_sessions(updated_at);


CREATE TABLE IF NOT EXISTS behavior_turns (
    turn_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    project_id TEXT NOT NULL DEFAULT '',
    project_key TEXT NOT NULL DEFAULT '',
    session_kind TEXT NOT NULL DEFAULT '',
    round_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    user_message TEXT NOT NULL,
    context_summary TEXT NOT NULL DEFAULT '',
    agent_response TEXT NOT NULL DEFAULT '',
    outcome_status TEXT NOT NULL DEFAULT 'success',
    user_feedback TEXT NOT NULL DEFAULT '',
    processed_status INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (session_id) REFERENCES behavior_sessions(session_id)
);
CREATE INDEX IF NOT EXISTS idx_behavior_turns_session_id ON behavior_turns(session_id);

CREATE INDEX IF NOT EXISTS idx_behavior_turns_processed_status ON behavior_turns(processed_status, created_at);
CREATE INDEX IF NOT EXISTS idx_behavior_turns_round_id ON behavior_turns(round_id);

CREATE TABLE IF NOT EXISTS behavior_actions (
    action_id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    round_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    action_index INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    action_subtype TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    input_summary TEXT NOT NULL DEFAULT '',
    output_summary TEXT NOT NULL DEFAULT '',
    success INTEGER NOT NULL DEFAULT 1,
    error_summary TEXT NOT NULL DEFAULT '',
    requires_llm INTEGER NOT NULL DEFAULT 0,
    risk_level TEXT NOT NULL DEFAULT 'none',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (turn_id) REFERENCES behavior_turns(turn_id),
    FOREIGN KEY (session_id) REFERENCES behavior_sessions(session_id)
);
CREATE INDEX IF NOT EXISTS idx_behavior_actions_turn_id ON behavior_actions(turn_id, action_index);

CREATE TABLE IF NOT EXISTS learned_skills (
    skill_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT '',
    project_key TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    current_version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'draft',
    skill_type TEXT NOT NULL DEFAULT 'draft',
    risk_level TEXT NOT NULL DEFAULT 'none',
    requires_llm INTEGER NOT NULL DEFAULT 1,
    trigger_json TEXT NOT NULL DEFAULT '{}',
    input_schema_json TEXT NOT NULL DEFAULT '[]',
    parameter_extractor_json TEXT NOT NULL DEFAULT '{}',
    steps_json TEXT NOT NULL DEFAULT '[]',
    script_json TEXT NOT NULL DEFAULT '{}',
    guards_json TEXT NOT NULL DEFAULT '{}',
    fallback_policy_json TEXT NOT NULL DEFAULT '{}',
    tests_json TEXT NOT NULL DEFAULT '[]',
    editable_fields_json TEXT NOT NULL DEFAULT '[]',
    created_from_json TEXT NOT NULL DEFAULT '{}',
    run_statistics_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_learned_skills_status ON learned_skills(status, updated_at);
CREATE TABLE IF NOT EXISTS learned_skill_versions (
    skill_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    parent_version INTEGER,
    skill_definition TEXT NOT NULL,
    change_type TEXT NOT NULL DEFAULT '',
    change_summary TEXT NOT NULL DEFAULT '',
    patch_list TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    test_result TEXT NOT NULL DEFAULT '{}',
    rollback_target INTEGER,
    PRIMARY KEY (skill_id, version),
    FOREIGN KEY (skill_id) REFERENCES learned_skills(skill_id)
);

CREATE TABLE IF NOT EXISTS learned_skill_runs (
    run_id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    turn_id TEXT NOT NULL DEFAULT '',
    match_score REAL NOT NULL DEFAULT 0,
    parameter_status TEXT NOT NULL DEFAULT '',
    execution_status TEXT NOT NULL DEFAULT '',
    failure_reason TEXT NOT NULL DEFAULT '',
    fallback_used INTEGER NOT NULL DEFAULT 0,
    user_feedback TEXT NOT NULL DEFAULT '',
    dry_run INTEGER NOT NULL DEFAULT 0,
    consistency_score REAL NOT NULL DEFAULT 0,
    permission_snapshot TEXT NOT NULL DEFAULT 'workspace_only',
    created_at TEXT NOT NULL,
    FOREIGN KEY (skill_id) REFERENCES learned_skills(skill_id)
);
CREATE INDEX IF NOT EXISTS idx_learned_skill_runs_skill_id
    ON learned_skill_runs(skill_id, created_at DESC);

CREATE TABLE IF NOT EXISTS learned_skill_patches (
    patch_id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL,
    base_version INTEGER NOT NULL,
    patch_type TEXT NOT NULL,
    reason TEXT NOT NULL,
    patch_content TEXT NOT NULL DEFAULT '{}',
    risk_assessment TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'proposed',
    created_at TEXT NOT NULL,
    FOREIGN KEY (skill_id) REFERENCES learned_skills(skill_id)
);
CREATE INDEX IF NOT EXISTS idx_learned_skill_patches_skill_id
    ON learned_skill_patches(skill_id, created_at DESC);

CREATE TABLE IF NOT EXISTS behavior_turn_tool_chains (
    chain_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT '',
    project_key TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL,
    session_kind TEXT NOT NULL DEFAULT '',
    turn_id TEXT NOT NULL UNIQUE,
    round_id TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'agent',
    purpose TEXT NOT NULL DEFAULT '',
    chain_json TEXT NOT NULL DEFAULT '[]',
    summary_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (turn_id) REFERENCES behavior_turns(turn_id)
);


CREATE TABLE IF NOT EXISTS behavior_browser_user_events (
    event_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT '',
    project_key TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    session_kind TEXT NOT NULL DEFAULT '',
    turn_id TEXT NOT NULL DEFAULT '',
    round_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    event_index INTEGER NOT NULL DEFAULT 0,
    event_kind TEXT NOT NULL DEFAULT '',
    browser_url TEXT NOT NULL DEFAULT '',
    browser_title TEXT NOT NULL DEFAULT '',
    target_json TEXT NOT NULL DEFAULT '{}',
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_behavior_browser_user_events_turn
    ON behavior_browser_user_events(turn_id, event_index);


CREATE TABLE IF NOT EXISTS behavior_skill_candidates (
    candidate_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT '',
    project_key TEXT NOT NULL DEFAULT '',
    purpose TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'observing',
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    script_json TEXT NOT NULL DEFAULT '{}',
    risk_level TEXT NOT NULL DEFAULT 'none',
    linked_skill_id TEXT NOT NULL DEFAULT '',
    user_decision TEXT NOT NULL DEFAULT '',
    last_evaluated_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_behavior_skill_candidates_project
    ON behavior_skill_candidates(project_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS behavior_skill_candidate_turns (
    candidate_id TEXT NOT NULL,
    turn_id TEXT NOT NULL UNIQUE,
    occurrence_index INTEGER NOT NULL,
    assignment_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (candidate_id, turn_id),
    FOREIGN KEY (candidate_id) REFERENCES behavior_skill_candidates(candidate_id),
    FOREIGN KEY (turn_id) REFERENCES behavior_turns(turn_id)
);
CREATE INDEX IF NOT EXISTS idx_behavior_sessions_project ON behavior_sessions(project_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_behavior_turns_project ON behavior_turns(project_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_learned_skills_project ON learned_skills(project_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_behavior_turn_tool_chains_project
    ON behavior_turn_tool_chains(project_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_behavior_browser_user_events_project
    ON behavior_browser_user_events(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_behavior_skill_candidate_turns_candidate
    ON behavior_skill_candidate_turns(candidate_id, occurrence_index);
"""
