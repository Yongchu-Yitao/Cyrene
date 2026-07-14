"""Behavior telemetry, repeat detection, and learned skill execution.

The primary learning path is intentionally small:

- persist successful project-local tool chains;
- observe the first occurrence, ask on the second, auto-learn on the third;
- store reusable workflows as declarative parameterized tool scripts;
- execute scripts through the central tool dispatcher with risk guards.

Legacy fingerprint, version, replay, and migration helpers remain for existing
databases and public compatibility APIs, but are not on the automatic path.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import sys

import aiosqlite
from collections import defaultdict
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from cyrene.config import DATA_DIR

logger = logging.getLogger(__name__)

_DATA_DIR: Path | None = None
_WORKSPACE_DIR: Path | None = None
_DB_FILE: Path = DATA_DIR / "behavior-learning.db"
# SQLite busy-wait (seconds). Block instead of failing instantly when another
# connection holds a write lock — e.g. background pattern processing running
# concurrently with the per-tool-call record_action writes.
_SQLITE_BUSY_TIMEOUT = 15.0
_INIT_DONE = False
_PROCESS_LOCK: asyncio.Lock | None = None
_PROCESS_LOCK_LOOP: asyncio.AbstractEventLoop | None = None

_current_session_id: ContextVar[str] = ContextVar("behavior_session_id", default="")
_current_turn_id: ContextVar[str] = ContextVar("behavior_turn_id", default="")
_current_round_id: ContextVar[str] = ContextVar("behavior_round_id", default="")

_VOCABULARY_VERSION = 1
_SHADOW_SUCCESS_THRESHOLD = 3
_SHADOW_CONSISTENCY_THRESHOLD = 0.85
_ROUTER_AUTO_THRESHOLD = 0.88
_ROUTER_JUDGE_THRESHOLD = 0.75
_PATTERN_STRONG_THRESHOLD = 0.85
_PATTERN_MEDIUM_THRESHOLD = 0.70
_MAX_PATTERN_EXAMPLES = 8
_CANDIDATE_USER_DECISION_COUNT = 2
_CANDIDATE_AUTO_LEARN_COUNT = 3
_SCRIPT_EXECUTION_TIMEOUT_SECONDS = 30.0
_INTERNAL_PROACTIVE_PROMPT_PREFIX = "This is a scheduler-initiated proactive check-in."
_SCHEDULED_CHECK_IN_LABEL = "Scheduled proactive check-in"
_IMAGE_ARTIFACT_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_MAX_IMAGE_ARTIFACT_BYTES = 20 * 1024 * 1024
_MAX_IMAGE_ARTIFACTS_PER_ACTION = 8
_IMAGE_ARTIFACT_PATH_RE = re.compile(
    r"(?P<path>(?:/[^\s\"'<>]+|[A-Za-z]:\\[^\s\"'<>]+)\.(?:png|jpg|jpeg|webp|gif))",
    re.IGNORECASE,
)

_CREATE_TABLES = """
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
    linked_skill_id TEXT NOT NULL DEFAULT '',
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

CREATE TABLE IF NOT EXISTS behavior_fingerprints (
    turn_id TEXT PRIMARY KEY,
    fingerprint_content TEXT NOT NULL,
    vocabulary_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (turn_id) REFERENCES behavior_turns(turn_id)
);

CREATE TABLE IF NOT EXISTS behavior_patterns (
    pattern_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT '',
    project_key TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    prototype_fingerprint TEXT NOT NULL DEFAULT '{}',
    statistics_json TEXT NOT NULL DEFAULT '{}',
    skillability_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'candidate',
    linked_skill_list TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_behavior_patterns_status ON behavior_patterns(status, updated_at);


CREATE TABLE IF NOT EXISTS behavior_pattern_turns (
    pattern_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    similarity REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (pattern_id, turn_id),
    FOREIGN KEY (pattern_id) REFERENCES behavior_patterns(pattern_id),
    FOREIGN KEY (turn_id) REFERENCES behavior_turns(turn_id)
);
CREATE INDEX IF NOT EXISTS idx_behavior_pattern_turns_turn_id ON behavior_pattern_turns(turn_id);

CREATE TABLE IF NOT EXISTS behavior_vocabulary_labels (
    label_id TEXT PRIMARY KEY,
    label_type TEXT NOT NULL,
    canonical_label TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT '',
    parent_label TEXT NOT NULL DEFAULT '',
    raw_description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_behavior_vocabulary_labels_unique
    ON behavior_vocabulary_labels(label_type, canonical_label);

CREATE TABLE IF NOT EXISTS behavior_vocabulary_aliases (
    alias_id TEXT PRIMARY KEY,
    label_type TEXT NOT NULL,
    canonical_label TEXT NOT NULL,
    alias_label TEXT NOT NULL,
    created_at TEXT NOT NULL,
    vocabulary_version INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_behavior_vocabulary_aliases_unique
    ON behavior_vocabulary_aliases(label_type, alias_label);

CREATE TABLE IF NOT EXISTS behavior_unknown_labels (
    unknown_id TEXT PRIMARY KEY,
    label_type TEXT NOT NULL,
    raw_description TEXT NOT NULL,
    proposed_domain TEXT NOT NULL DEFAULT '',
    proposed_type TEXT NOT NULL DEFAULT '',
    proposed_subtype TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    seen_count INTEGER NOT NULL DEFAULT 1,
    example_turns TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_behavior_unknown_labels_status
    ON behavior_unknown_labels(status, seen_count DESC, updated_at DESC);

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
    pattern_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_learned_skills_status ON learned_skills(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_learned_skills_pattern_id ON learned_skills(pattern_id);


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

CREATE TABLE IF NOT EXISTS behavior_replay_tests (
    test_id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL,
    turn_id TEXT NOT NULL DEFAULT '',
    test_type TEXT NOT NULL,
    input_payload TEXT NOT NULL DEFAULT '{}',
    expected_payload TEXT NOT NULL DEFAULT '{}',
    last_result TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (skill_id) REFERENCES learned_skills(skill_id)
);
CREATE INDEX IF NOT EXISTS idx_behavior_replay_tests_skill_id
    ON behavior_replay_tests(skill_id, created_at DESC);

CREATE TABLE IF NOT EXISTS behavior_turn_tool_chains (
    chain_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT '',
    project_key TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL,
    session_kind TEXT NOT NULL DEFAULT '',
    turn_id TEXT NOT NULL UNIQUE,
    round_id TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'agent',
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


CREATE TABLE IF NOT EXISTS behavior_learning_agent_reviews (
    review_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT '',
    project_key TEXT NOT NULL DEFAULT '',
    turn_id TEXT NOT NULL,
    chain_id TEXT NOT NULL DEFAULT '',
    decision TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    rationale TEXT NOT NULL DEFAULT '',
    proposed_skill_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_behavior_learning_agent_reviews_turn
    ON behavior_learning_agent_reviews(turn_id);

CREATE TABLE IF NOT EXISTS behavior_skill_candidates (
    candidate_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT '',
    project_key TEXT NOT NULL DEFAULT '',
    bucket_key TEXT NOT NULL,
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
CREATE INDEX IF NOT EXISTS idx_behavior_skill_candidates_bucket
    ON behavior_skill_candidates(project_id, bucket_key, updated_at DESC);

CREATE TABLE IF NOT EXISTS behavior_skill_candidate_turns (
    candidate_id TEXT NOT NULL,
    turn_id TEXT NOT NULL UNIQUE,
    occurrence_index INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (candidate_id, turn_id),
    FOREIGN KEY (candidate_id) REFERENCES behavior_skill_candidates(candidate_id),
    FOREIGN KEY (turn_id) REFERENCES behavior_turns(turn_id)
);
"""

_PROJECT_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_behavior_sessions_project ON behavior_sessions(project_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_behavior_turns_project ON behavior_turns(project_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_behavior_patterns_project ON behavior_patterns(project_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_learned_skills_project ON learned_skills(project_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_behavior_turn_tool_chains_project
    ON behavior_turn_tool_chains(project_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_behavior_browser_user_events_project
    ON behavior_browser_user_events(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_behavior_learning_agent_reviews_project
    ON behavior_learning_agent_reviews(project_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_behavior_skill_candidate_turns_candidate
    ON behavior_skill_candidate_turns(candidate_id, occurrence_index);
"""

_CORE_DOMAINS = {
    "internal_reasoning",
    "local_resource_operation",
    "external_information_query",
    "external_service_operation",
    "content_generation",
    "content_transformation",
    "software_development",
    "system_operation",
    "communication",
    "schedule_management",
    "state_management",
    "user_interaction",
    "unknown",
}

_CORE_TYPES = {
    "observe_context",
    "read_resource",
    "search_resource",
    "query_realtime_info",
    "retrieve_external_knowledge",
    "parse_content",
    "extract_information",
    "compare_items",
    "transform_data",
    "calculate_result",
    "diagnose_problem",
    "plan_steps",
    "generate_content",
    "edit_resource",
    "create_resource",
    "manage_state",
    "manage_schedule",
    "send_communication",
    "operate_external_service",
    "run_command",
    "call_tool",
    "ask_clarification",
    "request_confirmation",
    "return_result",
    "unknown",
}

_STATIC_ALIASES = {
    "domain:software": "software_development",
    "domain:code": "software_development",
    "domain:filesystem": "local_resource_operation",
    "domain:file_system": "local_resource_operation",
    "domain:web": "external_information_query",
    "type:edit_file": "edit_resource",
    "type:write_file": "edit_resource",
    "type:create_file": "create_resource",
    "type:read_file": "read_resource",
    "type:list_files": "search_resource",
    "type:search_files": "search_resource",
    "type:search_web": "query_realtime_info",
    "type:fetch_web_page": "retrieve_external_knowledge",
    "type:run_shell_command": "run_command",
    "type:ask_user": "ask_clarification",
    "type:tool_call": "call_tool",
    "intent_type:search_weather": "query_realtime_info",
    "intent_subtype:search_weather": "weather_lookup",
    "intent_subtype:compare_weather": "weather_lookup",
    "object_type:weather_forecast": "weather_data",
}

_TOOL_ACTION_MAP: dict[str, tuple[str, str, str, int]] = {
    "Read": ("local_resource_operation", "read_resource", "read_file", 0),
    "Write": ("local_resource_operation", "edit_resource", "write_file", 0),
    "Edit": ("local_resource_operation", "edit_resource", "edit_file", 0),
    "Glob": ("local_resource_operation", "search_resource", "list_files", 0),
    "Grep": ("local_resource_operation", "search_resource", "search_file_content", 0),
    "Bash": ("system_operation", "run_command", "shell_command", 0),
    "WebSearch": ("external_information_query", "query_realtime_info", "search_web", 0),
    "WebFetch": ("external_information_query", "retrieve_external_knowledge", "fetch_web_page", 0),
    "AnalyzeAttachment": ("content_transformation", "parse_content", "analyze_attachment", 1),
    "spawn_subagent": ("internal_reasoning", "manage_state", "spawn_subagent", 1),
    "send_agent_message": ("communication", "send_communication", "send_agent_message", 0),
    "broadcast_agent_message": ("communication", "send_communication", "broadcast_agent_message", 0),
    "query_round": ("state_management", "observe_context", "query_round", 0),
    "recall_memory": ("state_management", "observe_context", "recall_memory", 1),
    "ask_user": ("user_interaction", "ask_clarification", "ask_user", 1),
    "schedule_task": ("schedule_management", "manage_schedule", "schedule_task", 0),
    "list_tasks": ("schedule_management", "manage_schedule", "list_tasks", 0),
    "pause_task": ("schedule_management", "manage_schedule", "pause_task", 0),
    "resume_task": ("schedule_management", "manage_schedule", "resume_task", 0),
    "cancel_task": ("schedule_management", "manage_schedule", "cancel_task", 0),
    "StartShell": ("system_operation", "run_command", "start_shell", 0),
    "SendShell": ("system_operation", "run_command", "send_shell", 0),
    "CloseShell": ("system_operation", "manage_state", "close_shell", 0),
    "start_shell": ("system_operation", "run_command", "start_shell", 0),
    "send_shell": ("system_operation", "run_command", "send_shell", 0),
    "close_shell": ("system_operation", "manage_state", "close_shell", 0),
    "cc_launch": ("external_service_operation", "operate_external_service", "launch_claude_code", 0),
    "prompt_claude_code": ("external_service_operation", "operate_external_service", "prompt_claude_code", 1),
    "read_file": ("local_resource_operation", "read_resource", "read_file", 0),
    "write_file": ("local_resource_operation", "edit_resource", "write_file", 0),
    "edit_file": ("local_resource_operation", "edit_resource", "edit_file", 0),
    "list_files": ("local_resource_operation", "search_resource", "list_files", 0),
    "search_files": ("local_resource_operation", "search_resource", "search_file_content", 0),
    "run_shell": ("system_operation", "run_command", "shell_command", 0),
    "run_command": ("system_operation", "run_command", "shell_command", 0),
    "search_web": ("external_information_query", "query_realtime_info", "search_web", 0),
    "fetch_web_page": ("external_information_query", "retrieve_external_knowledge", "fetch_web_page", 0),
}

# Internal messaging tools — not useful for workflow pattern matching
_INTERNAL_TOOLS: frozenset[str] = frozenset({
    "spawn_subagent",
    "send_agent_message",
    "broadcast_agent_message",
})

# Tools that should not trigger skill creation when used alone — they're interactive
# or informational, not "production" tool calls that form a reusable workflow.
_TRIVIAL_SKILL_TOOLS: frozenset[str] = frozenset({
    "ask_user",
    "send_message",
    "send_message_to_user",
    "query_round",
    "browser.user.control_start",
    "browser.user.control_stop",
    "browser.user.key",
    "browser.user.mousemove",
    "browser.user.mouseMove",
    "GetLearnedSkill",
    "RunLearnedSkill",
})

_INTERNAL_LEARNING_MESSAGE_PREFIXES = (
    "[Internal permission decision received.",
    "This is a scheduler-initiated proactive check-in.",
    "你正在持续执行模式中完成一个有界工作片段。",
    "You are completing one bounded work packet",
)

_MIN_SKILL_CHAIN_STEPS = 2

# These steps change the conversation state or pause for the user. They are
# useful in the normal agent loop, but learned-skill replay should never run
# them ahead of the router's ordinary clarification/permission flow.
_AUTO_REPLAY_BLOCKED_TOOLS: frozenset[str] = frozenset({
    "ask_user",
    "send_message",
    "send_message_to_user",
    "browser.user.control_start",
    "browser.user.control_stop",
    "browser.user.click",
    "browser.user.scroll",
    "browser.user.key",
    "browser.user.text",
})

# Tools that carry meaningful side-effects and must never be replayed silently.
# A learned skill whose steps include any of these requires fresh user approval;
# the skill router falls back to the normal agent loop instead of auto-executing.
_HIGH_RISK_TOOLS: frozenset[str] = frozenset({
    # Arbitrary shell / command execution
    "Bash", "run_shell", "run_command", "StartShell", "SendShell", "start_shell", "send_shell",
    # File write operations (outside-workspace risk)
    "Write", "write_file", "Edit", "edit_file",
    # Persistent scheduled task creation
    "schedule_task",
    # Browser automation (navigates and interacts with external pages)
    "browser_navigate", "browser_click", "browser_click_ref", "browser_click_text", "browser_click_at",
    "browser_type", "browser_type_ref",
})

_CORRECTION_TERMS = (
    "不对", "不行", "错", "重来", "改一下", "重新", "fix", "wrong", "retry", "instead",
)

_SKILL_TYPE_ORDER = {
    "draft": 0,
    "workflow": 1,
    "parameterized": 2,
    "deterministic": 3,
}

_IO_FAMILIES = {
    "file": {
        "file", "file_path", "filepath", "path", "resource", "codebase", "module",
        "source_code", "modified_file", "workspace_file",
    },
    "text": {
        "text", "plain_text", "markdown", "report", "summary", "answer",
    },
    "code": {
        "code", "source_code", "patch", "diff", "modified_file",
    },
    "structured": {
        "json", "yaml", "csv", "table", "list",
    },
    "web": {
        "url", "web_page", "search_results", "external_data",
    },
    "weather": {
        "weather_report", "weather_info", "weather_forecast", "current_weather", "city_names",
    },
}

_CITY_ALIASES = {
    "beijing": "beijing",
    "北京": "beijing",
    "toronto": "toronto",
    "多伦多": "toronto",
}

_WEATHER_ENTITY_HINTS = tuple(_CITY_ALIASES.keys())

_NOISY_CONSTRAINTS = {
    "unknown",
    "search_returned_no_results",
    "tool_failure",
    "fetch_failed",
}

_GENERIC_ROUTER_ENTITIES = {
    "location",
    "locations",
    "city",
    "cities",
    "time",
    "date",
    "topic",
    "information",
}

_GENERIC_ROUTER_CONSTRAINTS = {
    "location_multi",
    "time_today",
    "time_now",
    "today",
    "now",
}

_SEMANTIC_FAMILIES = {
    "browser": {
        "browser", "web_browser", "webpage", "web_page", "navigate_to_site",
        "navigate_to_url", "launch_application", "open_browser", "browser_navigate",
    },
    "weather": {
        "weather", "forecast", "temperature", "humidity", "current_weather", "weather_data",
        "weather_report", "weather_forecast", "weather_lookup",
    },
    "realtime_info": {
        "query_realtime_info", "information_lookup", "search_weather", "compare_weather",
        "weather_lookup", "stock_lookup", "news_lookup", "price_lookup", "rate_lookup",
    },
    "information": {
        "information", "topic", "requested_output", "text_response", "general_request",
    },
    "code_change": {
        "edit_resource", "code_change", "source_code_file", "workspace_file", "codebase", "workspace",
    },
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _truncate_text(text: Any, limit: int = 500) -> str:
    compact = _normalize_whitespace(str(text or ""))
    return compact[:limit]


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(raw: Any, fallback: Any) -> Any:
    if raw is None:
        return fallback
    if isinstance(raw, (dict, list)):
        return raw
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return fallback
    if isinstance(fallback, dict):
        return parsed if isinstance(parsed, dict) else fallback
    if isinstance(fallback, list):
        return parsed if isinstance(parsed, list) else fallback
    return parsed


async def _persist_image_artifacts(turn_id: str, value: Any) -> str:
    """Copy tool-produced images out of the expiring temp directory.

    Behavior chains keep a textual tool result. Replacing temporary paths with
    durable, per-turn artifact paths lets the Workbench preview those images
    after a restart or temporary-cache cleanup.
    """
    text = str(value or "")
    if not text or not turn_id:
        return text
    matches = []
    for match in _IMAGE_ARTIFACT_PATH_RE.finditer(text):
        path = match.group("path").rstrip(".,);")
        if path and path not in matches:
            matches.append(path)
    if not matches:
        return text

    artifact_root = (_DATA_DIR or DATA_DIR) / "behavior-media" / str(turn_id)
    replacements: dict[str, str] = {}
    for index, raw_path in enumerate(matches[:_MAX_IMAGE_ARTIFACTS_PER_ACTION]):
        source = Path(raw_path).expanduser()
        try:
            if source.suffix.lower() not in _IMAGE_ARTIFACT_EXTS or not source.is_file():
                continue
            if source.stat().st_size > _MAX_IMAGE_ARTIFACT_BYTES:
                continue
            artifact_root.mkdir(parents=True, exist_ok=True)
            target = artifact_root / f"{index:02d}-{uuid4().hex[:8]}-{source.name}"
            await asyncio.to_thread(shutil.copy2, source, target)
            replacements[raw_path] = str(target.resolve())
        except OSError:
            logger.debug("Unable to preserve image artifact %s", raw_path, exc_info=True)
    for raw_path, stored_path in replacements.items():
        text = text.replace(raw_path, stored_path)
    return text


class _Conn:
    """Async context manager wrapping aiosqlite with sqlite3.Row row_factory."""
    def __init__(self):
        self._conn: aiosqlite.Connection | None = None

    async def __aenter__(self) -> aiosqlite.Connection:
        _DB_FILE.parent.mkdir(parents=True, exist_ok=True)
        # timeout maps to SQLite's busy_timeout: wait for a held write lock
        # instead of raising "database is locked" immediately.
        self._conn = aiosqlite.connect(str(_DB_FILE), timeout=_SQLITE_BUSY_TIMEOUT)
        await self._conn.__aenter__()
        self._conn.row_factory = sqlite3.Row
        return self._conn

    async def __aexit__(self, *args):
        if self._conn is None:
            return False
        return await self._conn.__aexit__(*args)


_conn = _Conn


def _get_process_lock() -> asyncio.Lock:
    global _PROCESS_LOCK, _PROCESS_LOCK_LOOP
    loop = asyncio.get_running_loop()
    if _PROCESS_LOCK is None or _PROCESS_LOCK_LOOP is not loop:
        _PROCESS_LOCK = asyncio.Lock()
        _PROCESS_LOCK_LOOP = loop
    return _PROCESS_LOCK


_STATS_LOCK: asyncio.Lock | None = None
_STATS_LOCK_LOOP: object | None = None


def _get_stats_lock() -> asyncio.Lock:
    global _STATS_LOCK, _STATS_LOCK_LOOP
    loop = asyncio.get_running_loop()
    if _STATS_LOCK is None or _STATS_LOCK_LOOP is not loop:
        _STATS_LOCK = asyncio.Lock()
        _STATS_LOCK_LOOP = loop
    return _STATS_LOCK


def _extract_json_object(text: str) -> dict[str, Any]:
    source = str(text or "").strip()
    if not source:
        return {}
    try:
        parsed = json.loads(source)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass
    match = re.search(r"\{.*\}", source, re.DOTALL)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_slug(value: str, default: str = "unknown") -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or default


def _canonical_city_name(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    for alias, canonical in _CITY_ALIASES.items():
        if alias.lower() in lowered:
            return canonical
    return ""


def _semantic_tokens(value: str) -> set[str]:
    normalized = _safe_slug(value, default="")
    if not normalized:
        return set()
    tokens = {token for token in normalized.split("_") if token and token not in {"current", "data", "requested"}}
    for family, members in _SEMANTIC_FAMILIES.items():
        if normalized in members or tokens & members:
            tokens.add(family)
    return tokens


def _extract_city_entities(*values: Any) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, dict):
            candidates = value.values()
        elif isinstance(value, (list, tuple, set)):
            candidates = value
        else:
            candidates = [value]
        for candidate in candidates:
            text = str(candidate or "")
            if not text:
                continue
            for hint in _WEATHER_ENTITY_HINTS:
                if hint.lower() in text.lower():
                    canonical = _canonical_city_name(hint)
                    if canonical and canonical not in seen:
                        seen.add(canonical)
                        found.append(canonical)
            canonical = _canonical_city_name(text)
            if canonical and canonical not in seen:
                seen.add(canonical)
                found.append(canonical)
    return found


def _normalize_entity_value(value: Any) -> list[str]:
    text = _normalize_whitespace(str(value or ""))
    if not text:
        return []
    city_entities = _extract_city_entities(text)
    if city_entities:
        return city_entities
    lowered = text.lower()
    normalized: list[str] = []
    if lowered.startswith("http://") or lowered.startswith("https://"):
        host_match = re.search(r"https?://([^/?#]+)", lowered)
        if host_match:
            host = host_match.group(1).replace("www.", "")
            normalized.append(_safe_slug(host))
        path_tokens = re.findall(r"[a-zA-Z]{3,}", lowered)
        for token in path_tokens[:6]:
            slug = _safe_slug(token, default="")
            if slug and slug not in normalized and slug not in {"https", "http", "www", "com", "cn"}:
                normalized.append(slug)
        return normalized[:6]
    if "/" in text or "." in text:
        slug = _safe_slug(text, default="")
        return [slug] if slug else []
    words = re.findall(r"[A-Za-z]{3,}|[\u4e00-\u9fff]{2,}", text)
    for word in words[:6]:
        slug = _safe_slug(word, default="")
        if slug and slug not in normalized:
            normalized.append(slug)
    return normalized[:6]


def _normalize_entities(values: list[Any]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        for item in _normalize_entity_value(value):
            if item not in seen:
                seen.add(item)
                normalized.append(item)
    return normalized


def _coerce_short_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = _normalize_whitespace(value)
        if not text:
            return []
        return [text]
    if isinstance(value, (list, tuple, set)):
        result: list[str] = []
        for item in value:
            text = _normalize_whitespace(str(item or ""))
            if text:
                result.append(text)
        return result
    text = _normalize_whitespace(str(value))
    return [text] if text else []


def _compress_action_sequence(action_sequence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compressed: list[dict[str, Any]] = []
    previous_key: tuple[str, str, str] | None = None
    for action in action_sequence:
        if not isinstance(action, dict):
            continue
        key = (
            str(action.get("domain") or ""),
            str(action.get("type") or ""),
            str(action.get("subtype") or ""),
        )
        if key == previous_key:
            continue
        compressed.append(action)
        previous_key = key
    return compressed


def _looks_like_file_path(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(
        text
        and not text.startswith("http://")
        and not text.startswith("https://")
        and ("/" in text or re.search(r"\.[A-Za-z0-9]{1,8}$", text))
    )


def _arg_value_family(value: Any) -> str:
    text = str(value or "").strip()
    lowered = text.lower()
    if not text:
        return "empty"
    if lowered.startswith("http://") or lowered.startswith("https://"):
        return "url"
    if _looks_like_file_path(text):
        return "file_path"
    if re.search(r"-?\d+(?:\.\d+)?", text):
        return "number"
    return "text"


def _arg_entities(value: Any) -> tuple[str, ...]:
    return tuple(_normalize_entities([value]))


def _should_parameterize_arg(key: str, observed_values: list[Any]) -> bool:
    values = [value for value in observed_values if value not in (None, "")]
    if len(values) <= 1:
        return False
    families = {_arg_value_family(value) for value in values}
    if "file_path" in families:
        return True
    if key in {"query", "url"}:
        entity_sets = {tuple(_arg_entities(value)) for value in values}
        if len(entity_sets) == 1 and next(iter(entity_sets), ()):
            return False
    return True


def _should_expose_stable_arg(key: str, value: Any) -> bool:
    """Expose reusable inputs even when the first observations used one value."""
    normalized = _safe_slug(key)
    return normalized in {
        "path", "file_path", "filepath", "directory", "cwd",
        "query", "url", "uri", "command", "pattern", "glob",
    } and value not in (None, "")


def _parameter_type_for_value(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "number"
    if isinstance(value, list):
        return "list"
    family = _arg_value_family(value)
    if family == "file_path":
        return "path"
    if family == "url":
        return "url"
    return "text"


def _looks_like_weather_request(user_message: str, action_sequence: list[dict[str, Any]] | None = None) -> bool:
    text = str(user_message or "")
    lowered = text.lower()
    if re.search(r"(weather|forecast|temperature|humidity|天气|气温|预报)", lowered):
        return True
    action_types = {str((action or {}).get("type") or "") for action in (action_sequence or [])}
    action_subtypes = {str((action or {}).get("subtype") or "") for action in (action_sequence or [])}
    return bool(
        {"query_realtime_info", "retrieve_external_knowledge"} & action_types
        and {"search_web", "fetch_web_page"} & action_subtypes
        and _extract_city_entities(text)
    )


def _looks_like_referential_request(user_message: str) -> bool:
    text = _normalize_whitespace(user_message)
    if not text:
        return False
    lowered = text.lower()
    return bool(
        len(text) <= 18
        or re.search(r"(再|继续|还是|这个|那个|这些|这两个|those|them|it|again|retry|再试|重新|换个)", lowered)
    )


def _infer_context_entities(context_summary: str) -> list[str]:
    if not context_summary:
        return []
    return _normalize_entities([context_summary])


def _infer_context_domain_hints(context_summary: str) -> dict[str, str]:
    summary = str(context_summary or "")
    lowered = summary.lower()
    if _looks_like_weather_request(summary):
        return {
            "intent_type": "query_realtime_info",
            "intent_subtype": "weather_lookup",
            "object_type": "weather_data",
            "object_subtype": "current_weather",
            "domain": "external_information_query",
            "input_type": "city_names",
            "output_type": "weather_report",
        }
    if re.search(r"(stock|price|股价|市值|行情|latest price)", lowered):
        return {
            "intent_type": "query_realtime_info",
            "intent_subtype": "information_lookup",
            "object_type": "topic",
            "object_subtype": "information",
            "domain": "external_information_query",
            "input_type": "text",
            "output_type": "text",
        }
    if re.search(r"(refactor|fix|patch|rewrite|重构|修复|修改|补测试|测试用例|登录)", lowered):
        return {
            "intent_type": "edit_resource",
            "intent_subtype": "code_change",
            "object_type": "codebase",
            "object_subtype": "workspace",
            "domain": "software_development",
            "input_type": "text",
            "output_type": "modified_source_code_file",
        }
    return {}


def _history_summary(history: list[dict[str, Any]], limit: int = 6) -> str:
    snippets: list[str] = []
    for message in history[-limit:]:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip()
        if role not in {"user", "assistant", "tool"}:
            continue
        content = _truncate_text(message.get("content") or "", 180)
        if not content:
            continue
        snippets.append(f"{role}: {content}")
    return "\n".join(snippets)


def _turn_feedback_from_message(message: str) -> str:
    lowered = str(message or "").lower()
    if any(term in lowered for term in _CORRECTION_TERMS):
        return "correction"
    return ""


def _default_pattern_stats() -> dict[str, Any]:
    return {
        "frequency": 0,
        "success_count": 0.0,
        "partial_success_count": 0.0,
        "failure_count": 0.0,
        "correction_count": 0.0,
        "success_rate": 0.0,
        "effective_count": 0.0,
        "action_stability": 0.0,
        "io_stability": 0.0,
        "last_seen_at": "",
    }


def _default_skill_stats() -> dict[str, Any]:
    return {
        "total_runs": 0,
        "actual_runs": 0,
        "shadow_runs": 0,
        "shadow_success": 0,
        "shadow_failure": 0,
        "active_success": 0,
        "active_failure": 0,
        "last_run_at": "",
        "consistency_avg": 0.0,
    }


async def _ensure_tables() -> None:
    async with _conn() as conn:
        # WAL lets the writer and readers proceed concurrently and removes the
        # rollback-journal SHARED→EXCLUSIVE deadlock that surfaced as
        # "database is locked" under concurrent record_action + pattern
        # processing. journal_mode is persisted in the DB header (set once here).
        await conn.execute("PRAGMA journal_mode = WAL")
        await conn.executescript(_CREATE_TABLES)
        await conn.commit()
        # Migration: add permission_snapshot to pre-existing learned_skill_runs tables
        try:
            await conn.execute(
                "ALTER TABLE learned_skill_runs ADD COLUMN permission_snapshot TEXT NOT NULL DEFAULT 'workspace_only'"
            )
            await conn.commit()
        except Exception:
            pass
        for table, columns in {
            "behavior_sessions": [
                ("project_id", "TEXT NOT NULL DEFAULT ''"),
                ("project_key", "TEXT NOT NULL DEFAULT ''"),
                ("session_kind", "TEXT NOT NULL DEFAULT ''"),
            ],
            "behavior_turns": [
                ("project_id", "TEXT NOT NULL DEFAULT ''"),
                ("project_key", "TEXT NOT NULL DEFAULT ''"),
                ("session_kind", "TEXT NOT NULL DEFAULT ''"),
                ("agent_response", "TEXT NOT NULL DEFAULT ''"),
            ],
            "behavior_patterns": [
                ("project_id", "TEXT NOT NULL DEFAULT ''"),
                ("project_key", "TEXT NOT NULL DEFAULT ''"),
            ],
            "learned_skills": [
                ("project_id", "TEXT NOT NULL DEFAULT ''"),
                ("project_key", "TEXT NOT NULL DEFAULT ''"),
                ("script_json", "TEXT NOT NULL DEFAULT '{}'"),
            ],
            "behavior_turn_tool_chains": [
                ("project_id", "TEXT NOT NULL DEFAULT ''"),
                ("project_key", "TEXT NOT NULL DEFAULT ''"),
            ],
            "behavior_browser_user_events": [
                ("project_id", "TEXT NOT NULL DEFAULT ''"),
                ("project_key", "TEXT NOT NULL DEFAULT ''"),
            ],
            "behavior_learning_agent_reviews": [
                ("project_id", "TEXT NOT NULL DEFAULT ''"),
                ("project_key", "TEXT NOT NULL DEFAULT ''"),
            ],
        }.items():
            for column, decl in columns:
                try:
                    await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
                    await conn.commit()
                except Exception:
                    pass
        # Create project_id-dependent indexes after the ALTER TABLE migration
        # ensures the column exists on old databases before referencing it in an index.
        await conn.executescript(_PROJECT_INDEXES)
        await conn.commit()


async def _seed_core_vocabulary() -> None:
    now = _now_iso()
    async with _conn() as conn:
        for label in sorted(_CORE_DOMAINS):
            await conn.execute(
                """
                INSERT OR IGNORE INTO behavior_vocabulary_labels
                (label_id, label_type, canonical_label, domain, parent_label, raw_description, status, created_at, updated_at)
                VALUES (?, 'domain', ?, '', '', '', 'active', ?, ?)
                """,
                (f"domain:{label}", label, now, now),
            )
        for label in sorted(_CORE_TYPES):
            await conn.execute(
                """
                INSERT OR IGNORE INTO behavior_vocabulary_labels
                (label_id, label_type, canonical_label, domain, parent_label, raw_description, status, created_at, updated_at)
                VALUES (?, 'type', ?, '', '', '', 'active', ?, ?)
                """,
                (f"type:{label}", label, now, now),
            )
        for alias_key, canonical in _STATIC_ALIASES.items():
            label_type, alias = alias_key.split(":", 1)
            await conn.execute(
                """
                INSERT OR IGNORE INTO behavior_vocabulary_aliases
                (alias_id, label_type, canonical_label, alias_label, created_at, vocabulary_version)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (f"alias:{label_type}:{alias}", label_type, canonical, alias, now, _VOCABULARY_VERSION),
            )
        await conn.commit()


async def init(data_dir: Path, workspace_dir: Path) -> None:
    global _DATA_DIR, _WORKSPACE_DIR, _DB_FILE, _INIT_DONE
    _DATA_DIR = data_dir
    _WORKSPACE_DIR = workspace_dir
    _DB_FILE = Path(data_dir) / "behavior-learning.db"
    _DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    await _ensure_tables()
    await _seed_core_vocabulary()
    await _migrate_generated_skill_scripts()
    _INIT_DONE = True


def _pattern_dir() -> Path:
    base = _WORKSPACE_DIR or Path.cwd()
    path = base / "patterns"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _project_scope_for_session(session_id: str | None) -> dict[str, str]:
    sid = str(session_id or "").strip()
    if not sid:
        return {"project_id": "global", "project_key": "global", "session_kind": "global"}
    try:
        from cyrene.workbench_context import (
            resolve_project_data_key_for_session,
            resolve_workbench_project_id_for_session,
            resolve_workbench_session_kind,
        )

        project_id = str(resolve_workbench_project_id_for_session(sid) or "").strip()
        project_key = str(resolve_project_data_key_for_session(sid) or "").strip()
        session_kind = str(resolve_workbench_session_kind(sid) or "").strip()
    except Exception:
        project_id = ""
        project_key = ""
        session_kind = ""
    project_id = project_id or project_key or "global"
    project_key = project_key or project_id
    # When falling back to dataKey, resolve to project UUID so stored
    # project_id is consistent with _learning_project_id in routes.py
    # (which resolves dataKey -> UUID). Without this, a project whose
    # dataKey == "default" but UUID != "default" would never see chains
    # from non-project-scoped sessions.
    if project_id == project_key and project_key:
        try:
            from cyrene.workbench_context import _read_projects as _resolve_projects
            for _p in _resolve_projects():
                if str(_p.get("dataKey") or "") == project_key:
                    _resolved = str(_p.get("id") or "").strip()
                    if _resolved:
                        project_id = _resolved
                        break
        except Exception:
            pass
    return {
        "project_id": project_id,
        "project_key": project_key,
        "session_kind": session_kind or "global",
    }


async def _project_scope_for_turn(turn_id: str) -> dict[str, str]:
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT session_id, project_id, project_key, session_kind FROM behavior_turns WHERE turn_id = ?",
            (str(turn_id or ""),),
        )
        row = await cursor.fetchone()
    if row is None:
        return {"project_id": "global", "project_key": "global", "session_kind": "global"}
    project_id = str(row["project_id"] or "").strip()
    project_key = str(row["project_key"] or "").strip()
    session_kind = str(row["session_kind"] or "").strip()
    if project_id and project_key:
        return {"project_id": project_id, "project_key": project_key, "session_kind": session_kind or "global"}
    return _project_scope_for_session(str(row["session_id"] or ""))


async def _latest_turn_for_session_round(session_id: str, round_id: str = "") -> str:
    sid = str(session_id or "").strip()
    rid = str(round_id or "").strip()
    if not sid:
        return ""
    async with _conn() as conn:
        if rid:
            cursor = await conn.execute(
                """
                SELECT turn_id FROM behavior_turns
                WHERE session_id = ? AND round_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (sid, rid),
            )
        else:
            cursor = await conn.execute(
                """
                SELECT turn_id FROM behavior_turns
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (sid,),
            )
        row = await cursor.fetchone()
    return str(row["turn_id"] or "") if row is not None else ""


async def begin_turn(
    *,
    session_id: str,
    round_id: str,
    user_message: str,
    history: list[dict[str, Any]],
    session_title: str = "",
    system_initiated: bool = False,
) -> dict[str, Any]:
    if not _INIT_DONE:
        await _ensure_tables()
    now = _now_iso()
    normalized_session_id = str(session_id or "").strip() or _new_id("session")
    normalized_round_id = str(round_id or "").strip() or _new_id("round")
    scope = _project_scope_for_session(normalized_session_id)
    turn_id = _new_id("turn")
    feedback = _turn_feedback_from_message(user_message)
    context_summary = _history_summary(history)
    metadata = {
        "round_id": normalized_round_id,
        "session_title": str(session_title or "").strip(),
        "correction_feedback": False,
        "round_title": "",
        "system_initiated": bool(system_initiated),
    }
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT session_id FROM behavior_sessions WHERE session_id = ?",
            (normalized_session_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            await conn.execute(
                """
                INSERT INTO behavior_sessions
                (session_id, project_id, project_key, session_kind, created_at, updated_at, session_summary, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_session_id,
                    scope["project_id"],
                    scope["project_key"],
                    scope["session_kind"],
                    now,
                    now,
                    _truncate_text(session_title or user_message, 240),
                    _json_dumps({"source": "live_session", **scope}),
                ),
            )
        else:
            await conn.execute(
                """
                UPDATE behavior_sessions
                SET project_id = ?, project_key = ?, session_kind = ?,
                    updated_at = ?, session_summary = COALESCE(NULLIF(?, ''), session_summary)
                WHERE session_id = ?
                """,
                (
                    scope["project_id"],
                    scope["project_key"],
                    scope["session_kind"],
                    now,
                    _truncate_text(session_title, 240),
                    normalized_session_id,
                ),
            )
        if feedback:
            cursor = await conn.execute(
                """
                SELECT turn_id, user_feedback, metadata_json
                FROM behavior_turns
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (normalized_session_id,),
            )
            latest_turn = await cursor.fetchone()
            if latest_turn is not None:
                latest_meta = _json_loads(latest_turn["metadata_json"], {})
                latest_meta["correction_feedback"] = True
                await conn.execute(
                    """
                    UPDATE behavior_turns
                    SET user_feedback = ?, metadata_json = ?, updated_at = ?
                    WHERE turn_id = ?
                    """,
                    ("correction", _json_dumps(latest_meta), now, latest_turn["turn_id"]),
                )
        await conn.execute(
            """
            INSERT INTO behavior_turns
            (turn_id, session_id, project_id, project_key, session_kind, round_id, created_at, updated_at, user_message, context_summary,
             agent_response, outcome_status, user_feedback, processed_status, linked_skill_id, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', 'success', '', 0, '', ?)
            """,
            (
                turn_id,
                normalized_session_id,
                scope["project_id"],
                scope["project_key"],
                scope["session_kind"],
                normalized_round_id,
                now,
                now,
                str(user_message or ""),
                context_summary,
                _json_dumps({**metadata, **scope}),
            ),
        )
        await conn.commit()
    session_token = _current_session_id.set(normalized_session_id)
    turn_token = _current_turn_id.set(turn_id)
    round_token = _current_round_id.set(normalized_round_id)
    return {
        "turn_id": turn_id,
        "session_id": normalized_session_id,
        "round_id": normalized_round_id,
        "session_token": session_token,
        "turn_token": turn_token,
        "round_token": round_token,
    }


def clear_turn_context(context: dict[str, Any]) -> None:
    try:
        _current_session_id.reset(context["session_token"])
        _current_turn_id.reset(context["turn_token"])
        _current_round_id.reset(context["round_token"])
    except Exception:
        logger.debug("Failed to reset behavior context", exc_info=True)


def current_turn_id() -> str:
    return _current_turn_id.get()


def _chain_item_from_action(row: dict[str, Any]) -> dict[str, Any]:
    meta = row.get("metadata_json") or {}
    return {
        "id": str(row.get("action_id") or ""),
        "source": "agent",
        "index": int(row.get("action_index") or 0),
        "tool": str(row.get("tool_name") or ""),
        "type": str(row.get("action_type") or ""),
        "subtype": str(row.get("action_subtype") or ""),
        "domain": str(meta.get("action_domain") or ""),
        "args": meta.get("raw_args") or {},
        "input_summary": str(row.get("input_summary") or ""),
        "output_summary": str(row.get("output_summary") or ""),
        "success": bool(row.get("success")),
        "duration_ms": float(meta.get("duration_ms") or 0),
        "created_at": str(row.get("created_at") or ""),
    }


def _browser_target_label(target: dict[str, Any]) -> str:
    if not isinstance(target, dict):
        return ""
    text = _truncate_text(str(target.get("text") or target.get("innerText") or "").strip(), 80)
    aria = _truncate_text(str(target.get("ariaLabel") or target.get("aria_label") or "").strip(), 80)
    name = _truncate_text(str(target.get("name") or "").strip(), 80)
    placeholder = _truncate_text(str(target.get("placeholder") or "").strip(), 80)
    element_id = _truncate_text(str(target.get("id") or "").strip(), 80)
    role = _truncate_text(str(target.get("role") or "").strip(), 40)
    tag = _truncate_text(str(target.get("tag") or target.get("tagName") or "").strip().lower(), 40)
    label = text or aria or placeholder or name or element_id
    prefix = role or tag
    if prefix and label:
        return f"{prefix} {label!r}"
    return label or prefix


def _browser_value_preview(kind: str, payload: dict[str, Any], target: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""
    target_type = str((target or {}).get("type") or "").lower()
    target_name = str((target or {}).get("name") or (target or {}).get("id") or "").lower()
    if "password" in target_type or "password" in target_name:
        return "[redacted]"
    for key in ("value", "text", "query", "url", "href"):
        if key in payload and payload.get(key) not in (None, ""):
            value = str(payload.get(key) or "")
            if len(value) > 120:
                value = value[:117] + "..."
            return value
    if kind in {"scroll", "wheel"}:
        x = payload.get("scrollX", payload.get("x", ""))
        y = payload.get("scrollY", payload.get("y", ""))
        return _truncate_text(f"x={x}, y={y}", 80)
    return ""


def _browser_event_learning_fields(
    kind: str,
    payload: dict[str, Any],
    target: dict[str, Any],
    url: str,
    title: str,
) -> dict[str, str]:
    event_kind = str(kind or "event").strip() or "event"
    target_label = _browser_target_label(target)
    value_preview = _browser_value_preview(event_kind, payload, target)
    page = title or url
    if event_kind in {"input", "text"}:
        destination = target_label or "focused field"
        action = f"entered {value_preview!r} into {destination}" if value_preview else f"entered text into {destination}"
        purpose = f"provide browser input for {destination}"
    elif event_kind == "click":
        destination = target_label or "page element"
        action = f"clicked {destination}"
        purpose = f"activate {destination}"
    elif event_kind == "submit":
        destination = target_label or "browser form"
        action = f"submitted {destination}"
        purpose = f"submit {destination}"
    elif event_kind in {"navigate", "navigation"}:
        destination = value_preview or url or "browser page"
        action = f"navigated to {destination}"
        purpose = "open or change browser page"
    elif event_kind in {"scroll", "wheel"}:
        action = f"scrolled {page or 'browser page'}"
        purpose = "inspect more page content"
    elif event_kind in {"back", "forward", "reload", "select_tab", "close_tab"}:
        action = f"{event_kind.replace('_', ' ')} on {page or 'browser tab'}"
        purpose = "manage browser navigation state"
    else:
        destination = target_label or page or "browser page"
        action = f"performed browser user event {event_kind} on {destination}"
        purpose = "continue browser task"
    return {
        "purpose": _truncate_text(purpose, 240),
        "action_summary": _truncate_text(action, 300),
        "object_summary": _truncate_text(target_label or page or url, 240),
        "value_preview": _truncate_text(value_preview, 160),
    }


def _chain_item_from_browser_event(row: dict[str, Any]) -> dict[str, Any]:
    payload = _json_loads(row.get("payload_json"), {})
    target = _json_loads(row.get("target_json"), {})
    event_kind = str(row.get("event_kind") or "event")
    url = str(row.get("browser_url") or "")
    title = str(row.get("browser_title") or "")
    learning_fields = _browser_event_learning_fields(event_kind, payload, target, url, title)
    return {
        "id": str(row.get("event_id") or ""),
        "source": "user_browser",
        "index": int(row.get("event_index") or 0),
        "tool": "browser.user." + event_kind,
        "type": "browser_user_operation",
        "subtype": event_kind,
        "domain": "browser_operation",
        "args": payload,
        "target": target,
        "url": url,
        "title": title,
        **learning_fields,
        "success": True,
        "created_at": str(row.get("created_at") or ""),
    }


async def _rebuild_tool_chain_for_turn(turn_id: str) -> dict[str, Any] | None:
    tid = str(turn_id or "").strip()
    if not tid:
        return None
    async with _conn() as conn:
        cursor = await conn.execute(
            """
            SELECT turn_id, session_id, project_id, project_key, session_kind, round_id, created_at
            FROM behavior_turns
            WHERE turn_id = ?
            """,
            (tid,),
        )
        turn_row = await cursor.fetchone()
        if turn_row is None:
            return None
        cursor = await conn.execute(
            """
            SELECT *
            FROM behavior_actions
            WHERE turn_id = ?
            ORDER BY action_index ASC
            """,
            (tid,),
        )
        action_rows = [dict(row) for row in await cursor.fetchall()]
        cursor = await conn.execute(
            """
            SELECT *
            FROM behavior_browser_user_events
            WHERE turn_id = ?
            ORDER BY event_index ASC, created_at ASC
            """,
            (tid,),
        )
        browser_rows = [dict(row) for row in await cursor.fetchall()]
        now = _now_iso()
        actions = []
        for row in action_rows:
            row["metadata_json"] = _json_loads(row.get("metadata_json"), {})
            actions.append(_chain_item_from_action(row))
        browser_events = [_chain_item_from_browser_event(row) for row in browser_rows]
        chain = sorted(
            [*actions, *browser_events],
            key=lambda item: (str(item.get("created_at") or ""), str(item.get("source") or ""), int(item.get("index") or 0)),
        )
        sources = sorted({str(item.get("source") or "") for item in chain if item.get("source")})
        summary = {
            "total_steps": len(chain),
            "agent_steps": len(actions),
            "browser_user_steps": len(browser_events),
            "sources": sources,
            "success_steps": sum(1 for item in chain if item.get("success")),
            "failed_steps": sum(1 for item in chain if not item.get("success")),
            "tool_names": [str(item.get("tool") or "") for item in chain],
        }
        source = "mixed" if len(sources) > 1 else (sources[0] if sources else "agent")
        await conn.execute(
            """
            INSERT INTO behavior_turn_tool_chains
            (chain_id, project_id, project_key, session_id, session_kind, turn_id, round_id, source,
             chain_json, summary_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(turn_id) DO UPDATE SET
                project_id = excluded.project_id,
                project_key = excluded.project_key,
                session_id = excluded.session_id,
                session_kind = excluded.session_kind,
                round_id = excluded.round_id,
                source = excluded.source,
                chain_json = excluded.chain_json,
                summary_json = excluded.summary_json,
                updated_at = excluded.updated_at
            """,
            (
                "chain:" + tid,
                str(turn_row["project_id"] or ""),
                str(turn_row["project_key"] or ""),
                str(turn_row["session_id"] or ""),
                str(turn_row["session_kind"] or ""),
                tid,
                str(turn_row["round_id"] or ""),
                source,
                _json_dumps(chain),
                _json_dumps(summary),
                str(turn_row["created_at"] or now),
                now,
            ),
        )
        await conn.commit()
    return {"chain": chain, "summary": summary}


def _map_tool_to_action(tool_name: str) -> tuple[str, str, str, int]:
    if tool_name in _TOOL_ACTION_MAP:
        return _TOOL_ACTION_MAP[tool_name]
    return ("state_management", "call_tool", _safe_slug(tool_name), 0)


async def record_action(
    tool_name: str,
    args: dict[str, Any],
    caller: str,
    round_id: str,
    duration_ms: float,
    *,
    result: Any = "",
    success: bool = True,
    error: str = "",
) -> None:
    session_id = _current_session_id.get()
    turn_id = _current_turn_id.get()
    if not session_id or not turn_id:
        return
    now = _now_iso()
    action_id = _new_id("action")
    domain, action_type, action_subtype, requires_llm = _map_tool_to_action(tool_name)
    persisted_result = await _persist_image_artifacts(turn_id, result)
    metadata = {
        "caller": str(caller or "unknown"),
        "round_id": str(round_id or _current_round_id.get()),
        "duration_ms": round(float(duration_ms or 0), 2),
        "raw_args": dict(args or {}),
        "action_domain": domain,
    }
    try:
        async with _conn() as conn:
            cursor = await conn.execute(
                "SELECT COALESCE(MAX(action_index), -1) AS max_idx FROM behavior_actions WHERE turn_id = ?",
                (turn_id,),
            )
            row = await cursor.fetchone()
            max_idx = row["max_idx"] if row is not None else None
            next_index = (int(max_idx) + 1) if max_idx is not None else 0
            await conn.execute(
                """
                INSERT INTO behavior_actions
                (action_id, turn_id, session_id, round_id, created_at, action_index, action_type, action_subtype,
                 tool_name, input_summary, output_summary, success, error_summary, requires_llm, risk_level, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'none', ?)
                """,
                (
                    action_id,
                    turn_id,
                    session_id,
                    str(round_id or _current_round_id.get()),
                    now,
                    next_index,
                    action_type,
                    action_subtype,
                    tool_name,
                    _truncate_text(_json_dumps(args or {}), 500),
                    _truncate_text(persisted_result, 500),
                    1 if success else 0,
                    _truncate_text(error, 400),
                    requires_llm,
                    _json_dumps(metadata),
                ),
            )
            await conn.execute(
                "UPDATE behavior_turns SET updated_at = ?, processed_status = 0 WHERE turn_id = ?",
                (now, turn_id),
            )
            await conn.execute(
                "UPDATE behavior_sessions SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
            await conn.commit()
    except Exception:
        # Behaviour-learning is fire-and-forget telemetry: a write failure here
        # (e.g. a transient "database is locked") must never propagate and turn
        # a successful tool call into a failure. Log and drop.
        logger.debug("record_action telemetry write failed (ignored)", exc_info=True)


async def record_browser_user_event(
    *,
    session_id: str = "",
    round_id: str = "",
    event_kind: str,
    payload: dict[str, Any] | None = None,
    browser_url: str = "",
    browser_title: str = "",
    target: dict[str, Any] | None = None,
) -> None:
    sid = str(session_id or _current_session_id.get() or "").strip()
    rid = str(round_id or _current_round_id.get() or "").strip()
    if not sid:
        sid = _new_id("browser_session")
    turn_id = await _latest_turn_for_session_round(sid, rid)
    if not turn_id:
        ctx = await begin_turn(
            session_id=sid,
            round_id=rid or _new_id("browser_round"),
            user_message="用户接管内置浏览器并执行操作。",
            history=[],
            session_title="Browser user operation",
        )
        turn_id = str(ctx["turn_id"])
        clear_turn_context(ctx)
    scope = await _project_scope_for_turn(turn_id)
    now = _now_iso()
    try:
        async with _conn() as conn:
            cursor = await conn.execute(
                "SELECT COALESCE(MAX(event_index), -1) AS max_idx FROM behavior_browser_user_events WHERE turn_id = ?",
                (turn_id,),
            )
            row = await cursor.fetchone()
            max_idx = row["max_idx"] if row is not None else None
            next_index = (int(max_idx) + 1) if max_idx is not None else 0
            await conn.execute(
                """
                INSERT INTO behavior_browser_user_events
                (event_id, project_id, project_key, session_id, session_kind, turn_id, round_id, created_at,
                 event_index, event_kind, browser_url, browser_title, target_json, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _new_id("browser_event"),
                    scope["project_id"],
                    scope["project_key"],
                    sid,
                    scope["session_kind"],
                    turn_id,
                    rid,
                    now,
                    next_index,
                    str(event_kind or "event"),
                    _truncate_text(browser_url, 500),
                    _truncate_text(browser_title, 240),
                    _json_dumps(target or {}),
                    _json_dumps(payload or {}),
                ),
            )
            await conn.execute(
                "UPDATE behavior_turns SET updated_at = ?, processed_status = 0 WHERE turn_id = ?",
                (now, turn_id),
            )
            await conn.commit()
    except Exception:
        logger.debug("browser user event learning write failed (ignored)", exc_info=True)
    if str(event_kind or "").strip().lower() == "control_stop":
        await _rebuild_tool_chain_for_turn(turn_id)


async def list_recent_browser_user_events(
    *,
    session_id: str = "",
    round_id: str = "",
    limit: int = 30,
) -> list[dict[str, Any]]:
    sid = str(session_id or _current_session_id.get() or "").strip()
    rid = str(round_id or "").strip()
    capped_limit = max(1, min(int(limit or 30), 100))
    if not sid:
        return []
    async with _conn() as conn:
        if rid:
            cursor = await conn.execute(
                """
                SELECT *
                FROM behavior_browser_user_events
                WHERE session_id = ? AND round_id = ?
                ORDER BY created_at DESC, event_index DESC
                LIMIT ?
                """,
                (sid, rid, capped_limit),
            )
        else:
            cursor = await conn.execute(
                """
                SELECT *
                FROM behavior_browser_user_events
                WHERE session_id = ?
                ORDER BY created_at DESC, event_index DESC
                LIMIT ?
                """,
                (sid, capped_limit),
            )
        rows = await cursor.fetchall()
    events: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        kind = str(item.get("event_kind") or "")
        url = str(item.get("browser_url") or "")
        title = str(item.get("browser_title") or "")
        target = _json_loads(item.get("target_json"), {})
        payload = _json_loads(item.get("payload_json"), {})
        learning_fields = _browser_event_learning_fields(kind, payload, target, url, title)
        events.append({
            "id": str(item.get("event_id") or ""),
            "session_id": str(item.get("session_id") or ""),
            "round_id": str(item.get("round_id") or ""),
            "turn_id": str(item.get("turn_id") or ""),
            "created_at": str(item.get("created_at") or ""),
            "index": int(item.get("event_index") or 0),
            "kind": kind,
            "tool": "browser.user." + (kind or "event"),
            "url": url,
            "title": title,
            "target": target,
            "payload": payload,
            **learning_fields,
        })
    events.reverse()
    return events


async def mark_turn_skill_routed(skill_id: str) -> None:
    turn_id = _current_turn_id.get()
    if not turn_id:
        return
    async with _conn() as conn:
        await conn.execute(
            "UPDATE behavior_turns SET linked_skill_id = ?, updated_at = ? WHERE turn_id = ?",
            (str(skill_id or ""), _now_iso(), turn_id),
        )
        await conn.commit()


async def _classify_turn_outcome(turn_id: str) -> str:
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT success FROM behavior_actions WHERE turn_id = ?",
            (turn_id,),
        )
        rows = await cursor.fetchall()
        if not rows:
            return "success"
        success_count = sum(1 for row in rows if int(row["success"] or 0) == 1)
        failure_count = len(rows) - success_count
        if failure_count == 0:
            return "success"
        if success_count == 0:
            return "failure"
        return "partial_success"


async def complete_turn(
    *,
    turn_id: str,
    assistant_response: str,
    session_title: str = "",
    round_title: str = "",
) -> None:
    # Tool telemetry is intentionally fire-and-forget during execution.  The
    # finalization barrier guarantees learning sees the complete turn.
    try:
        from cyrene.tool_executor import flush_behavior_action_tasks

        await flush_behavior_action_tasks()
    except Exception:
        logger.debug("failed to flush behavior action telemetry", exc_info=True)
    now = _now_iso()
    outcome = await _classify_turn_outcome(turn_id)
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT metadata_json, session_id FROM behavior_turns WHERE turn_id = ?",
            (turn_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return
        metadata = _json_loads(row["metadata_json"], {})
        metadata["assistant_preview"] = _truncate_text(assistant_response, 240)
        if session_title:
            metadata["session_title"] = session_title
        if round_title:
            metadata["round_title"] = round_title
        await conn.execute(
            """
            UPDATE behavior_turns
            SET updated_at = ?, outcome_status = ?, agent_response = ?, metadata_json = ?
            WHERE turn_id = ?
            """,
            (now, outcome, str(assistant_response or ""), _json_dumps(metadata), turn_id),
        )
        if session_title:
            await conn.execute(
                """
                UPDATE behavior_sessions
                SET updated_at = ?, session_summary = ?
                WHERE session_id = ?
                """,
                (now, _truncate_text(session_title, 240), row["session_id"]),
            )
        await conn.commit()
    await _rebuild_tool_chain_for_turn(turn_id)


async def _alias_lookup(label_type: str, label: str) -> str:
    normalized = _safe_slug(label)
    if not normalized:
        return ""
    static_key = f"{label_type}:{normalized}"
    if static_key in _STATIC_ALIASES:
        return _STATIC_ALIASES[static_key]
    async with _conn() as conn:
        cursor = await conn.execute(
            """
            SELECT canonical_label
            FROM behavior_vocabulary_aliases
            WHERE label_type = ? AND alias_label = ?
            """,
            (label_type, normalized),
        )
        row = await cursor.fetchone()
        if row is not None:
            return str(row["canonical_label"] or "")
    return normalized


async def _record_unknown_label(
    *,
    turn_id: str,
    label_type: str,
    raw_description: str,
    proposed_domain: str = "",
    proposed_type: str = "",
    proposed_subtype: str = "",
    reason: str = "",
) -> None:
    normalized_raw = _normalize_whitespace(raw_description)
    if not normalized_raw:
        return
    now = _now_iso()
    async with _conn() as conn:
        cursor = await conn.execute(
            """
            SELECT unknown_id, seen_count, example_turns
            FROM behavior_unknown_labels
            WHERE label_type = ? AND raw_description = ?
            """,
            (label_type, normalized_raw),
        )
        row = await cursor.fetchone()
        if row is None:
            await conn.execute(
                """
                INSERT INTO behavior_unknown_labels
                (unknown_id, label_type, raw_description, proposed_domain, proposed_type, proposed_subtype,
                 reason, seen_count, example_turns, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, 'open', ?, ?)
                """,
                (
                    _new_id("unknown"),
                    label_type,
                    normalized_raw,
                    proposed_domain,
                    proposed_type,
                    proposed_subtype,
                    reason,
                    _json_dumps([turn_id] if turn_id else []),
                    now,
                    now,
                ),
            )
        else:
            examples = _json_loads(row["example_turns"], [])
            if turn_id and turn_id not in examples:
                examples = [turn_id, *examples][:12]
            await conn.execute(
                """
                UPDATE behavior_unknown_labels
                SET proposed_domain = COALESCE(NULLIF(?, ''), proposed_domain),
                    proposed_type = COALESCE(NULLIF(?, ''), proposed_type),
                    proposed_subtype = COALESCE(NULLIF(?, ''), proposed_subtype),
                    reason = COALESCE(NULLIF(?, ''), reason),
                    seen_count = ?,
                    example_turns = ?,
                    updated_at = ?
                WHERE unknown_id = ?
                """,
                (
                    proposed_domain,
                    proposed_type,
                    proposed_subtype,
                    reason,
                    int(row["seen_count"] or 0) + 1,
                    _json_dumps(examples),
                    now,
                    row["unknown_id"],
                ),
            )
        await conn.commit()


async def _normalize_domain(value: str, turn_id: str = "") -> str:
    normalized = await _alias_lookup("domain", value)
    if normalized in _CORE_DOMAINS:
        return normalized
    if normalized and normalized in _CORE_TYPES:
        return "unknown"
    await _record_unknown_label(
        turn_id=turn_id,
        label_type="domain",
        raw_description=value,
        proposed_domain=normalized,
        reason="domain_not_in_core",
    )
    return "unknown"


async def _normalize_type(value: str, turn_id: str = "") -> str:
    normalized = await _alias_lookup("type", value)
    if normalized in _CORE_TYPES:
        return normalized
    await _record_unknown_label(
        turn_id=turn_id,
        label_type="type",
        raw_description=value,
        proposed_type=normalized,
        reason="type_not_in_core",
    )
    return "unknown"


async def _normalize_subtype(value: str, turn_id: str = "") -> str:
    normalized = await _alias_lookup("subtype", value)
    if normalized == "unknown":
        await _record_unknown_label(
            turn_id=turn_id,
            label_type="subtype",
            raw_description=value,
            proposed_subtype=normalized,
            reason="subtype_unknown",
        )
    return normalized


async def _normalize_semantic_label(value: str, *, label_type: str, turn_id: str = "") -> str:
    normalized = await _alias_lookup(label_type, value)
    if not normalized:
        normalized = "unknown"
    if normalized not in {"", "unknown"}:
        await _record_unknown_label(
            turn_id=turn_id,
            label_type=label_type,
            raw_description=value,
            proposed_type=normalized if label_type.endswith("_type") else "",
            proposed_subtype=normalized if label_type.endswith("_subtype") else "",
            reason="open_semantic_label",
        )
    elif value:
        await _record_unknown_label(
            turn_id=turn_id,
            label_type=label_type,
            raw_description=value,
            reason="semantic_label_unknown",
        )
    return normalized or "unknown"


def _normalize_slot(slot: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": _safe_slug(slot.get("name") or slot.get("parameter_name") or "param"),
        "type": _safe_slug(slot.get("type") or "text"),
        "required": bool(slot.get("required", False)),
        "examples": [str(item) for item in (slot.get("examples") or [])[:6]],
        "default_value": slot.get("default_value"),
        "aliases": [str(item) for item in (slot.get("aliases") or [])[:6]],
    }


async def normalize_fingerprint(fingerprint: dict[str, Any], *, turn_id: str = "") -> dict[str, Any]:
    fp = dict(fingerprint or {})
    intent = fp.get("intent") or {}
    obj = fp.get("object") or {}
    if not isinstance(intent, dict):
        intent = {}
    if not isinstance(obj, dict):
        obj = {}
    action_sequence = fp.get("action_sequence") or []

    normalized_intent_type = await _normalize_semantic_label(
        str(intent.get("type") or "unknown"),
        label_type="intent_type",
        turn_id=turn_id,
    )
    normalized_intent_subtype = await _normalize_semantic_label(
        str(intent.get("subtype") or "unknown"),
        label_type="intent_subtype",
        turn_id=turn_id,
    )
    normalized_object_type = await _normalize_semantic_label(
        str(obj.get("type") or "unknown"),
        label_type="object_type",
        turn_id=turn_id,
    )
    normalized_object_subtype = await _normalize_semantic_label(
        str(obj.get("subtype") or "unknown"),
        label_type="object_subtype",
        turn_id=turn_id,
    )
    normalized_domain = await _normalize_domain(str(fp.get("domain") or "unknown"), turn_id)
    raw_text = " ".join(
        str(part or "")
        for part in (
            (intent or {}).get("raw_description"),
            (obj or {}).get("raw_description"),
            fp.get("domain"),
            " ".join(str(item) for item in (fp.get("constraints") or [])),
            " ".join(str(item) for item in (fp.get("entities") or [])),
        )
    )

    normalized_actions: list[dict[str, Any]] = []
    has_unknown = False
    for action in action_sequence:
        if not isinstance(action, dict):
            continue
        action_domain = await _normalize_domain(str(action.get("domain") or normalized_domain), turn_id)
        action_type = await _normalize_type(str(action.get("type") or "unknown"), turn_id)
        action_subtype = await _normalize_subtype(str(action.get("subtype") or "unknown"), turn_id)
        if "unknown" in {action_domain, action_type, action_subtype}:
            has_unknown = True
        normalized_actions.append(
            {
                "domain": action_domain,
                "type": action_type,
                "subtype": action_subtype,
                "raw_description": _truncate_text(action.get("raw_description") or "", 180),
            }
        )
    normalized_actions = _compress_action_sequence(normalized_actions)

    if _looks_like_weather_request(raw_text, normalized_actions):
        normalized_intent_type = "query_realtime_info"
        normalized_intent_subtype = "weather_lookup"
        normalized_object_type = "weather_data"
        normalized_object_subtype = "current_weather"
        normalized_domain = "external_information_query"

    normalized = {
        "intent": {
            "type": normalized_intent_type,
            "subtype": normalized_intent_subtype,
            "raw_description": _truncate_text(intent.get("raw_description") or "", 180),
        },
        "object": {
            "type": normalized_object_type,
            "subtype": normalized_object_subtype,
            "raw_description": _truncate_text(obj.get("raw_description") or "", 180),
        },
        "input_type": _safe_slug(str(fp.get("input_type") or "unknown")),
        "output_type": _safe_slug(str(fp.get("output_type") or "unknown")),
        "domain": normalized_domain,
        "constraints": sorted(
            {
                _safe_slug(item)
                for item in _coerce_short_text_list(fp.get("constraints"))
                if str(item).strip() and _safe_slug(item) not in _NOISY_CONSTRAINTS
            }
        ),
        "entities": sorted(_normalize_entities(fp.get("entities") or [])),
        "action_sequence": normalized_actions,
        "parameter_slots": [_normalize_slot(slot) for slot in (fp.get("parameter_slots") or []) if isinstance(slot, dict)],
        "llm_dependency": _safe_slug(str(fp.get("llm_dependency") or "medium")),
        "risk_level": _safe_slug(str(fp.get("risk_level") or "none")),
        "vocabulary_status": {
            "uses_core_vocabulary": normalized_domain in _CORE_DOMAINS and all(
                action.get("type") in _CORE_TYPES for action in normalized_actions
            ),
            "uses_learned_vocabulary": any(
                label not in {"unknown", ""} and label not in _CORE_TYPES and label not in _CORE_DOMAINS
                for label in (
                    normalized_intent_type,
                    normalized_intent_subtype,
                    normalized_object_type,
                    normalized_object_subtype,
                )
            ),
            "has_unknown": has_unknown
            or normalized_intent_type == "unknown"
            or normalized_object_type == "unknown"
            or normalized_domain == "unknown",
            "proposed_new_labels": [],
        },
        "vocabulary_version": _VOCABULARY_VERSION,
    }
    return normalized


async def _heuristic_request_fingerprint(
    user_message: str,
    action_sequence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    text = str(user_message or "")
    lowered = text.lower()
    weather_request = _looks_like_weather_request(text, action_sequence)
    intent_type = "generate_content"
    intent_subtype = "general_request"
    domain = "content_generation"
    output_type = "text"
    object_type = "topic"
    object_subtype = "information"
    action_types = {str((action or {}).get("type") or "") for action in (action_sequence or [])}
    action_subtypes = {str((action or {}).get("subtype") or "") for action in (action_sequence or [])}
    code_action_detected = bool(
        {"read_resource", "search_resource", "edit_resource", "run_command"} & action_types
        or {
            "read_file",
            "write_file",
            "edit_file",
            "list_files",
            "search_file_content",
            "shell_command",
        }
        & action_subtypes
    )
    realtime_query_detected = bool(
        {"query_realtime_info", "retrieve_external_knowledge"} & action_types
        or {"search_web", "fetch_web_page"} & action_subtypes
    )
    code_request = bool(
        re.search(r"(refactor|implement|fix|patch|rewrite|重构|实现|修复|修改|优化|补充|新增|调整|测试|test)", lowered)
        or code_action_detected
    )
    code_analysis_request = bool(
        re.search(r"(review|inspect|analy[sz]e|explain|检查|分析|解释|看看|看一下|阅读)", lowered)
        and (
            code_action_detected
            or bool(re.search(r"(src/|\.py\b|\.js\b|\.ts\b|\.tsx\b|\.jsx\b|\.java\b|\.go\b|\.rs\b|\.swift\b|\.cpp\b|\.c\b)", lowered))
        )
    )
    realtime_request = bool(
        realtime_query_detected
        or re.search(r"(weather|stock|news|price|rate|score|latest|实时|最新|天气|股票|汇率|新闻|比分|价格)", lowered)
        or re.search(r"(搜索|查询)", text)
    )
    if code_request:
        intent_type = "edit_resource"
        intent_subtype = "code_change"
        domain = "software_development"
        output_type = "modified_source_code_file"
    elif code_analysis_request:
        intent_type = "extract_information"
        intent_subtype = "code_explanation"
        domain = "software_development"
    elif weather_request:
        intent_type = "query_realtime_info"
        intent_subtype = "weather_lookup"
        domain = "external_information_query"
        object_type = "weather_data"
        object_subtype = "current_weather"
        output_type = "weather_report"
    elif realtime_request:
        intent_type = "query_realtime_info"
        intent_subtype = "information_lookup"
        domain = "external_information_query"
    elif re.search(r"(总结|总结一下|summari[sz]e|summary)", lowered):
        intent_type = "generate_content"
        intent_subtype = "summary"
        domain = "content_generation"
    entities = _extract_city_entities(text)
    for match in re.findall(r"(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+|[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,8})", text):
        if match not in entities:
            entities.append(match)
    if domain == "software_development":
        object_type = "source_code_file" if entities else "codebase"
        object_subtype = "workspace_file" if entities else "workspace"
    elif domain == "content_generation":
        object_type = "requested_output"
        object_subtype = "text_response"
    if not action_sequence:
        action_sequence = []
        if domain == "software_development":
            action_sequence = [
                {"domain": "local_resource_operation", "type": "search_resource", "subtype": "list_files", "raw_description": "Inspect workspace files"},
                {"domain": "local_resource_operation", "type": "read_resource", "subtype": "read_file", "raw_description": "Read relevant source files"},
                {"domain": "software_development", "type": "edit_resource", "subtype": "edit_file", "raw_description": "Update implementation"},
            ]
        elif domain == "external_information_query":
            action_sequence = [
                {"domain": "external_information_query", "type": "query_realtime_info", "subtype": "search_web", "raw_description": "Search current information"},
            ]
    return await normalize_fingerprint(
        {
            "intent": {
                "type": intent_type,
                "subtype": intent_subtype,
                "raw_description": _truncate_text(text, 180),
            },
            "object": {
                "type": object_type,
                "subtype": object_subtype,
                "raw_description": _truncate_text(text, 180),
            },
            "input_type": "city_names" if weather_request else ("file_path" if entities and domain == "software_development" else "text"),
            "output_type": output_type,
            "domain": domain,
            "constraints": (
                ["multiple_cities"] if len(_extract_city_entities(text)) >= 2 else []
            ) + (["chinese"] if re.search(r"[\u4e00-\u9fff]", text) else []),
            "entities": entities,
            "action_sequence": action_sequence,
            "parameter_slots": [],
            "llm_dependency": "medium",
            "risk_level": "none",
        }
    )


async def _call_llm_json(prompt: str, *, caller: str = "behavior_learning") -> dict[str, Any]:
    from cyrene.agent.state import _call_llm, _caller_type
    from cyrene.llm import _assistant_text

    token = _caller_type.set(caller)
    try:
        response = await _call_llm([{"role": "user", "content": prompt}], tools=None, max_tokens=2000)
        return _extract_json_object(_assistant_text(response))
    except Exception:
        logger.debug("behavior learning LLM JSON call failed", exc_info=True)
        return {}
    finally:
        _caller_type.reset(token)


async def _action_rows_for_turn(turn_id: str) -> list[dict[str, Any]]:
    async with _conn() as conn:
        cursor = await conn.execute(
            """
            SELECT *
            FROM behavior_actions
            WHERE turn_id = ?
            ORDER BY action_index ASC
            """,
            (turn_id,),
        )
        rows = await cursor.fetchall()
    actions: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["metadata_json"] = _json_loads(item.get("metadata_json"), {})
        actions.append(item)
    return actions


async def build_turn_fingerprint(turn_id: str) -> dict[str, Any]:
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT fingerprint_content FROM behavior_fingerprints WHERE turn_id = ?",
            (turn_id,),
        )
        existing = await cursor.fetchone()
        if existing is not None:
            return _json_loads(existing["fingerprint_content"], {})
        cursor = await conn.execute(
            """
            SELECT turn_id, session_id, round_id, user_message, context_summary
            FROM behavior_turns
            WHERE turn_id = ?
            """,
            (turn_id,),
        )
        turn_row = await cursor.fetchone()
    if turn_row is None:
        return {}
    action_rows = await _action_rows_for_turn(turn_id)
    chain_snapshot = await _load_tool_chain_for_turn(turn_id)
    browser_chain_items = [
        item for item in (chain_snapshot.get("chain") or [])
        if str((item or {}).get("source") or "") == "user_browser"
    ]
    action_summary = []
    deterministic_actions = []
    deterministic_entities: list[str] = []
    for row in action_rows:
        if row["tool_name"] in _INTERNAL_TOOLS:
            continue
        raw_args = (row.get("metadata_json") or {}).get("raw_args") or {}
        deterministic_entities.extend(_normalize_entities(list(raw_args.values())))
        action_summary.append(
            {
                "tool_name": row["tool_name"],
                "action_type": row["action_type"],
                "action_subtype": row["action_subtype"],
                "input_summary": row["input_summary"],
                "output_summary": row["output_summary"],
                "success": bool(row["success"]),
            }
        )
        deterministic_actions.append(
            {
                "domain": str((row.get("metadata_json") or {}).get("action_domain") or "state_management"),
                "type": str(row["action_type"]),
                "subtype": str(row["action_subtype"]),
                "raw_description": str(row["tool_name"]),
            }
        )
    for item in browser_chain_items:
        payload = item.get("args") or {}
        deterministic_entities.extend(_normalize_entities(list(payload.values())))
        browser_summary = str(item.get("action_summary") or item.get("purpose") or "")
        action_summary.append(
            {
                "tool_name": item.get("tool"),
                "action_type": item.get("type"),
                "action_subtype": item.get("subtype"),
                "input_summary": _truncate_text(browser_summary or _json_dumps(payload), 500),
                "output_summary": item.get("url") or "",
                "purpose": item.get("purpose") or "",
                "object_summary": item.get("object_summary") or "",
                "success": True,
            }
        )
        deterministic_actions.append(
            {
                "domain": "browser_operation",
                "type": "browser_user_operation",
                "subtype": str(item.get("subtype") or "event"),
                "raw_description": browser_summary or str(item.get("tool") or "browser.user.event"),
            }
        )
    prompt = f"""You are building a structured behavior fingerprint for an autonomous coding agent turn.

Return exactly one JSON object with these keys:
intent, object, input_type, output_type, domain, constraints, entities, action_sequence, parameter_slots, llm_dependency, risk_level

Rules:
- intent.type and object.type must use short snake_case labels.
- domain should prefer one of:
  {sorted(_CORE_DOMAINS)}
- action_sequence items must have: domain, type, subtype, raw_description
- type labels should prefer one of:
  {sorted(_CORE_TYPES)}
- parameter_slots items should have: name, type, required, examples
- constraints/entities are arrays of short strings
- keep labels stable and reusable
- infer from the actual tool sequence, not just the user wording

User message:
{turn_row["user_message"]}

Context summary:
{turn_row["context_summary"]}

Observed agent actions:
{json.dumps(action_summary, ensure_ascii=False, indent=2)}

JSON only.
"""
    payload = await _call_llm_json(prompt)
    heuristic_fp = await _heuristic_request_fingerprint(
        str(turn_row["user_message"]),
        action_sequence=deterministic_actions,
    )
    if not payload:
        payload = heuristic_fp
        payload["action_sequence"] = deterministic_actions or payload.get("action_sequence") or []
        if deterministic_actions:
            payload["input_type"] = heuristic_fp.get("input_type")
            payload["output_type"] = heuristic_fp.get("output_type")
        payload["entities"] = _normalize_entities(list(payload.get("entities") or []) + deterministic_entities)
    else:
        if not isinstance(payload.get("intent"), dict):
            payload["intent"] = {}
        if not isinstance(payload.get("object"), dict):
            payload["object"] = {}
        payload.setdefault("intent", heuristic_fp.get("intent") or {})
        payload.setdefault("object", heuristic_fp.get("object") or {})
        if str((payload.get("intent") or {}).get("type") or "").strip().lower() in {"", "unknown"}:
            payload["intent"] = heuristic_fp.get("intent") or {}
        if str((payload.get("object") or {}).get("type") or "").strip().lower() in {"", "unknown"}:
            payload["object"] = heuristic_fp.get("object") or {}
        if deterministic_actions:
            payload["input_type"] = heuristic_fp.get("input_type")
            payload["output_type"] = heuristic_fp.get("output_type")
        else:
            if not str(payload.get("input_type") or "").strip():
                payload["input_type"] = heuristic_fp.get("input_type")
            if not str(payload.get("output_type") or "").strip():
                payload["output_type"] = heuristic_fp.get("output_type")
        if not str(payload.get("domain") or "").strip():
            payload["domain"] = heuristic_fp.get("domain")
        payload["action_sequence"] = deterministic_actions or payload.get("action_sequence") or []
        merged_entities = list(payload.get("entities") or [])
        merged_entities.extend(deterministic_entities)
        payload["entities"] = _normalize_entities(merged_entities)
    fingerprint = await normalize_fingerprint(payload, turn_id=turn_id)
    if deterministic_actions:
        fingerprint["action_sequence"] = _compress_action_sequence(deterministic_actions)
    now = _now_iso()
    async with _conn() as conn:
        await conn.execute(
            """
            INSERT OR REPLACE INTO behavior_fingerprints
            (turn_id, fingerprint_content, vocabulary_version, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (turn_id, _json_dumps(fingerprint), _VOCABULARY_VERSION, now, now),
        )
        await conn.commit()
    return fingerprint


async def build_request_fingerprint(user_message: str, history: list[dict[str, Any]]) -> dict[str, Any]:
    context_summary = _history_summary(history)
    heuristic = await _heuristic_request_fingerprint(user_message)
    prompt = f"""You are matching a new user request against learned automation skills.

Return exactly one JSON object with these keys:
intent, object, input_type, output_type, domain, constraints, entities, action_sequence, parameter_slots, llm_dependency, risk_level

Rules:
- Predict the likely abstract action sequence needed to satisfy the request.
- Use short snake_case labels.
- Prefer stable reusable categories.
- action_sequence items must contain domain, type, subtype, raw_description.

User message:
{user_message}

Recent context:
{context_summary}

JSON only.
"""
    payload = await _call_llm_json(prompt, caller="skill_router")
    if not payload:
        return heuristic
    normalized = await normalize_fingerprint(payload)
    heuristic_actions = heuristic.get("action_sequence") or []
    normalized_actions = normalized.get("action_sequence") or []
    if (
        not normalized_actions
        or all(str((item or {}).get("type") or "") in {"", "unknown"} for item in normalized_actions)
        or all(str((item or {}).get("domain") or "") in {"", "unknown"} for item in normalized_actions)
    ):
        normalized["action_sequence"] = heuristic_actions
    if not normalized.get("constraints") and heuristic.get("constraints"):
        normalized["constraints"] = list(heuristic.get("constraints") or [])
    elif heuristic.get("constraints"):
        normalized_constraints = {str(item) for item in (normalized.get("constraints") or [])}
        heuristic_constraints = {str(item) for item in (heuristic.get("constraints") or [])}
        if normalized_constraints and heuristic_constraints and (
            normalized_constraints <= _GENERIC_ROUTER_CONSTRAINTS
            or not (normalized_constraints & heuristic_constraints)
        ):
            normalized["constraints"] = list(heuristic.get("constraints") or [])
    if not normalized.get("entities") and heuristic.get("entities"):
        normalized["entities"] = list(heuristic.get("entities") or [])
    elif heuristic.get("entities"):
        normalized_entities = {str(item) for item in (normalized.get("entities") or [])}
        heuristic_entities = {str(item) for item in (heuristic.get("entities") or [])}
        if normalized_entities and heuristic_entities and (
            normalized_entities <= _GENERIC_ROUTER_ENTITIES
            or not (normalized_entities & heuristic_entities)
        ):
            normalized["entities"] = list(heuristic.get("entities") or [])
    if str(normalized.get("input_type") or "unknown") == "unknown" and heuristic.get("input_type"):
        normalized["input_type"] = heuristic.get("input_type")
    elif str(normalized.get("input_type") or "") in {"text", "text_query", "query"} and heuristic.get("input_type") not in {"", "text", "text_query", "query"}:
        normalized["input_type"] = heuristic.get("input_type")
    if str(normalized.get("output_type") or "unknown") == "unknown" and heuristic.get("output_type"):
        normalized["output_type"] = heuristic.get("output_type")
    elif str(normalized.get("output_type") or "") in {"text", "text_response", "answer"} and heuristic.get("output_type") not in {"", "text", "text_response", "answer"}:
        normalized["output_type"] = heuristic.get("output_type")
    if str((normalized.get("intent") or {}).get("type") or "unknown") == "unknown":
        normalized["intent"] = dict(heuristic.get("intent") or {})
    if str((normalized.get("object") or {}).get("type") or "unknown") == "unknown":
        normalized["object"] = dict(heuristic.get("object") or {})
    if str(normalized.get("domain") or "unknown") == "unknown" and heuristic.get("domain"):
        normalized["domain"] = heuristic.get("domain")
    if heuristic.get("parameter_slots") == [] and (normalized.get("parameter_slots") or []):
        slot_names = {_safe_slug(str((slot or {}).get("name") or ""), default="") for slot in (normalized.get("parameter_slots") or [])}
        if slot_names <= {"location", "locations", "time", "date", "city", "cities"}:
            normalized["parameter_slots"] = []
    return normalized


def _node_similarity(node_a: dict[str, Any], node_b: dict[str, Any]) -> float:
    type_a = str(node_a.get("type") or "")
    type_b = str(node_b.get("type") or "")
    subtype_a = str(node_a.get("subtype") or "")
    subtype_b = str(node_b.get("subtype") or "")
    if type_a and type_a == type_b and subtype_a and subtype_a == subtype_b:
        return 1.0
    if type_a and type_a == type_b:
        return 0.75
    type_tokens_a = _semantic_tokens(type_a) | _semantic_tokens(subtype_a)
    type_tokens_b = _semantic_tokens(type_b) | _semantic_tokens(subtype_b)
    if type_tokens_a and type_tokens_b:
        overlap = len(type_tokens_a & type_tokens_b) / max(len(type_tokens_a), len(type_tokens_b))
        if overlap >= 0.6:
            return 0.75
        if overlap >= 0.34:
            return 0.5
    if type_a and type_b and type_a == "unknown" and type_b == "unknown":
        return 0.25
    return 0.0


def _scalar_similarity(left: str, right: str) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    left_families = {name for name, values in _IO_FAMILIES.items() if left in values}
    right_families = {name for name, values in _IO_FAMILIES.items() if right in values}
    if not left_families:
        if "file" in left or "path" in left:
            left_families.add("file")
        if "code" in left or "source" in left or "patch" in left or "diff" in left:
            left_families.add("code")
        if "text" in left or "summary" in left or "report" in left or "answer" in left:
            left_families.add("text")
        if "json" in left or "yaml" in left or "csv" in left or "table" in left:
            left_families.add("structured")
        if "url" in left or "web" in left or "search" in left:
            left_families.add("web")
    if not right_families:
        if "file" in right or "path" in right:
            right_families.add("file")
        if "code" in right or "source" in right or "patch" in right or "diff" in right:
            right_families.add("code")
        if "text" in right or "summary" in right or "report" in right or "answer" in right:
            right_families.add("text")
        if "json" in right or "yaml" in right or "csv" in right or "table" in right:
            right_families.add("structured")
        if "url" in right or "web" in right or "search" in right:
            right_families.add("web")
    if left_families & right_families:
        return 0.75
    if left in right or right in left:
        return 0.50
    return 1.0 if left == right else 0.0


def _set_similarity(left: list[str], right: list[str]) -> float:
    a = {str(item) for item in left if str(item).strip()}
    b = {str(item) for item in right if str(item).strip()}
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _slot_similarity(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> float:
    left_keys = {f"{item.get('name')}:{item.get('type')}" for item in left}
    right_keys = {f"{item.get('name')}:{item.get('type')}" for item in right}
    return _set_similarity(sorted(left_keys), sorted(right_keys))


def _action_item_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    if left.get("type") == right.get("type") and left.get("subtype") == right.get("subtype"):
        return 1.0
    if left.get("type") == right.get("type"):
        return 0.75
    if left.get("domain") == right.get("domain"):
        return 0.50
    return 0.0


def _lcs_similarity(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    dp = [[0] * (len(right) + 1) for _ in range(len(left) + 1)]
    for i in range(1, len(left) + 1):
        for j in range(1, len(right) + 1):
            if _action_item_similarity(left[i - 1], right[j - 1]) >= 0.75:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    lcs = dp[len(left)][len(right)]
    return (2 * lcs) / (len(left) + len(right))


def compute_fingerprint_similarity(fp_a: dict[str, Any], fp_b: dict[str, Any]) -> dict[str, Any]:
    intent_sim = _node_similarity(fp_a.get("intent") or {}, fp_b.get("intent") or {})
    object_sim = _node_similarity(fp_a.get("object") or {}, fp_b.get("object") or {})
    io_sim = (_scalar_similarity(str(fp_a.get("input_type") or ""), str(fp_b.get("input_type") or ""))
              + _scalar_similarity(str(fp_a.get("output_type") or ""), str(fp_b.get("output_type") or ""))) / 2
    domain_sim = _scalar_similarity(str(fp_a.get("domain") or ""), str(fp_b.get("domain") or ""))
    constraint_sim = _set_similarity(fp_a.get("constraints") or [], fp_b.get("constraints") or [])
    entity_sim = _set_similarity(fp_a.get("entities") or [], fp_b.get("entities") or [])
    action_sim = _lcs_similarity(fp_a.get("action_sequence") or [], fp_b.get("action_sequence") or [])
    slot_sim = _slot_similarity(fp_a.get("parameter_slots") or [], fp_b.get("parameter_slots") or [])
    total = (
        0.18 * intent_sim
        + 0.10 * object_sim
        + 0.10 * io_sim
        + 0.10 * domain_sim
        + 0.10 * constraint_sim
        + 0.10 * entity_sim
        + 0.25 * action_sim
        + 0.07 * slot_sim
    )
    return {
        "total": round(total, 4),
        "breakdown": {
            "intent": round(intent_sim, 4),
            "object": round(object_sim, 4),
            "io": round(io_sim, 4),
            "domain": round(domain_sim, 4),
            "constraints": round(constraint_sim, 4),
            "entities": round(entity_sim, 4),
            "action_sequence": round(action_sim, 4),
            "parameter_slots": round(slot_sim, 4),
        },
        "hard_fail": action_sim < 0.50 or io_sim < 0.25,
    }


async def _llm_should_merge(
    turn_fp: dict[str, Any],
    pattern_fp: dict[str, Any],
    similarity: dict[str, Any],
) -> bool:
    prompt = f"""Decide whether a new turn fingerprint should merge into an existing learned behavior pattern.

Return JSON only:
{{"should_merge": true|false, "confidence": 0-1, "same_skill_possible": true|false, "reason": "..."}}

New turn fingerprint:
{json.dumps(turn_fp, ensure_ascii=False, indent=2)}

Existing pattern prototype:
{json.dumps(pattern_fp, ensure_ascii=False, indent=2)}

Similarity breakdown:
{json.dumps(similarity, ensure_ascii=False, indent=2)}
"""
    result = await _call_llm_json(prompt, caller="pattern_merger")
    return bool(result.get("should_merge"))


def _is_internal_tool_action(action: dict[str, Any]) -> bool:
    """Check if an action_sequence item is an internal messaging tool."""
    return str(action.get("raw_description") or "") in _INTERNAL_TOOLS


def _is_trivial_skill_action(action: dict[str, Any]) -> bool:
    """Return True for interaction-only actions that should not become skills."""
    raw = str(action.get("raw_description") or "")
    action_type = str(action.get("type") or "")
    action_subtype = str(action.get("subtype") or "")
    domain = str(action.get("domain") or "")
    if raw in _TRIVIAL_SKILL_TOOLS or action_subtype in _TRIVIAL_SKILL_TOOLS:
        return True
    if domain == "user_interaction" and action_type in {"ask_clarification", "request_confirmation"}:
        return True
    return action_type in {"ask_clarification", "request_confirmation"}


def _enabled_step_tool_names(steps: list[dict[str, Any]]) -> list[str]:
    tool_names: list[str] = []
    for step in steps:
        if not bool(step.get("enabled", True)):
            continue
        reference = step.get("implementation_reference") or {}
        if str(step.get("implementation_kind") or "") == "script":
            tool_names.extend(_enabled_step_tool_names(reference.get("original_steps") or []))
            continue
        tool_names.append(str(reference.get("tool_name") or ""))
    return tool_names


def _tool_call_steps_for_replay(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    replay_steps: list[dict[str, Any]] = []
    for step in steps:
        if not bool(step.get("enabled", True)):
            continue
        reference = step.get("implementation_reference") or {}
        if str(step.get("implementation_kind") or "") == "script":
            replay_steps.extend(_tool_call_steps_for_replay(reference.get("original_steps") or []))
            continue
        replay_steps.append(step)
    return replay_steps


def _has_auto_replay_blocked_step(steps: list[dict[str, Any]]) -> bool:
    return any(
        tool in _AUTO_REPLAY_BLOCKED_TOOLS or tool.startswith("browser.user.")
        for tool in _enabled_step_tool_names(steps)
    )


def _has_skillworthy_steps(steps: list[dict[str, Any]]) -> bool:
    tool_names = [
        tool for tool in _enabled_step_tool_names(steps)
        if tool and tool not in _TRIVIAL_SKILL_TOOLS and tool not in _INTERNAL_TOOLS
    ]
    if len(tool_names) < _MIN_SKILL_CHAIN_STEPS:
        return False
    return len(set(tool_names)) >= _MIN_SKILL_CHAIN_STEPS


def _is_reusable_skill_definition(definition: dict[str, Any] | None) -> bool:
    """Return whether a stored skill is eligible for learning or execution.

    This is intentionally checked at every read boundary as well as during
    creation. Older databases can contain skills created before the
    multi-operation guard was introduced, and a generated-script wrapper may
    make such a skill look like a one-step skill in the UI.
    """
    if not isinstance(definition, dict):
        return False
    return _has_skillworthy_steps(definition.get("steps") or [])


async def _llm_workflow_merge(
    turn_id: str,
    turn_fp: dict[str, Any],
    pattern_id: str,
    pattern_fp: dict[str, Any],
    total_sim: float,
) -> bool:
    """Ask LLM whether two turns with tool calls represent the same workflow."""
    turn_actions = await _action_rows_for_turn(turn_id)
    member_turn_ids = await _member_turn_ids(pattern_id)
    example_turns = await _fetch_turn_rows(member_turn_ids[:_MAX_PATTERN_EXAMPLES])

    def _fmt_action(a: dict[str, Any]) -> str:
        inp = a.get("input_summary", "")
        tn = a["tool_name"]
        return f"  {tn} → {inp[:200]}" if inp else f"  {tn}"

    def _fmt_action_seq(fp: dict[str, Any]) -> str:
        seq = fp.get("action_sequence") or []
        filtered = [a for a in seq if not _is_internal_tool_action(a)]
        if filtered:
            items = [
                f"  {a.get('raw_description', '?')} ({a.get('type', '')}/{a.get('subtype', '')})"
                for a in filtered
            ]
            return "\n".join(items[:12])
        return "  (no tool calls)"

    _turn_tool_lines = [_fmt_action(a) for a in turn_actions if a["tool_name"] not in _INTERNAL_TOOLS]
    _ex_lines = [
        f"  - \"{str(et.get('user_message') or '')[:100]}\""
        for et in example_turns
    ]
    _turn_tools_str = "\n".join(_turn_tool_lines[:15])
    _ex_str = "\n".join(_ex_lines[:8]) if _ex_lines else "  (no example requests available)"
    _action_seq_str = _fmt_action_seq(pattern_fp)

    prompt = f"""You are comparing two AI agent interactions to decide if they represent the same type of workflow.

Turn A (new, user said: "{str(turn_fp.get('intent', {}).get('raw_description', ''))[:200]}"):
Tool calls ({len(_turn_tool_lines)}):
{_turn_tools_str}

Pattern B (existing, example requests:):
{_ex_str}
Tool call type signature:
{_action_seq_str}

These interactions have a low fingerprint match (similarity={total_sim:.2f}) but may still be the same workflow.
Focus on whether they follow the same general pattern of tool usage — same sequence of tool types serving the same purpose.

Return JSON: {{"same_workflow": true|false, "reason": "..."}}
"""
    result = await _call_llm_json(prompt, caller="workflow_merger")
    return bool(result.get("same_workflow"))


async def _fingerprint_for_turn(turn_id: str) -> dict[str, Any]:
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT fingerprint_content FROM behavior_fingerprints WHERE turn_id = ?",
            (turn_id,),
        )
        row = await cursor.fetchone()
    return _json_loads(row["fingerprint_content"], {}) if row else {}


async def _member_turn_ids(pattern_id: str) -> list[str]:
    async with _conn() as conn:
        cursor = await conn.execute(
            """
            SELECT turn_id
            FROM behavior_pattern_turns
            WHERE pattern_id = ?
            ORDER BY created_at ASC
            """,
            (pattern_id,),
        )
        rows = await cursor.fetchall()
    return [str(row["turn_id"]) for row in rows]


def _choose_pattern_prototype(fingerprints: list[dict[str, Any]]) -> dict[str, Any]:
    if not fingerprints:
        return {}
    if len(fingerprints) == 1:
        return fingerprints[0]
    best_index = 0
    best_score = -1.0
    for idx, left in enumerate(fingerprints):
        total = 0.0
        for jdx, right in enumerate(fingerprints):
            if idx == jdx:
                continue
            total += compute_fingerprint_similarity(left, right)["total"]
        avg = total / max(1, len(fingerprints) - 1)
        if avg > best_score:
            best_index = idx
            best_score = avg
    return fingerprints[best_index]


def _pattern_description(prototype: dict[str, Any]) -> str:
    intent = prototype.get("intent") or {}
    obj = prototype.get("object") or {}
    return " / ".join(
        part for part in [
            str(intent.get("type") or ""),
            str(intent.get("subtype") or ""),
            str(obj.get("type") or ""),
            str(obj.get("subtype") or ""),
        ]
        if part
    ) or "behavior_pattern"


async def _fetch_turn_rows(turn_ids: list[str]) -> list[dict[str, Any]]:
    if not turn_ids:
        return []
    placeholders = ",".join("?" for _ in turn_ids)
    async with _conn() as conn:
        cursor = await conn.execute(
            f"""
            SELECT *
            FROM behavior_turns
            WHERE turn_id IN ({placeholders})
            ORDER BY created_at ASC
            """,
            tuple(turn_ids),
        )
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def _compute_pattern_stats(turn_ids: list[str], prototype: dict[str, Any]) -> dict[str, Any]:
    turns = await _fetch_turn_rows(turn_ids)
    fingerprints = [await _fingerprint_for_turn(turn_id) for turn_id in turn_ids]
    success_count = 0.0
    partial_count = 0.0
    failure_count = 0.0
    correction_count = 0.0
    action_stability_values: list[float] = []
    io_stability_values: list[float] = []
    last_seen = ""
    for turn, fp in zip(turns, fingerprints):
        outcome = str(turn.get("outcome_status") or "success")
        if outcome == "success":
            success_count += 1
        elif outcome == "partial_success":
            partial_count += 1
        elif outcome == "failure":
            failure_count += 1
        metadata = _json_loads(turn.get("metadata_json"), {})
        if bool(metadata.get("correction_feedback")) or str(turn.get("user_feedback") or "") == "correction":
            correction_count += 1
        sim = compute_fingerprint_similarity(fp, prototype)
        action_stability_values.append(float(sim["breakdown"]["action_sequence"]))
        io_stability_values.append(
            (float(sim["breakdown"]["io"]) + float(sim["breakdown"]["domain"])) / 2
        )
        last_seen = str(turn.get("updated_at") or turn.get("created_at") or last_seen)
    frequency = len(turn_ids)
    effective_count = success_count + (0.5 * partial_count) - (1.0 * failure_count) - (1.5 * correction_count)
    success_rate = success_count / frequency if frequency else 0.0
    return {
        "frequency": frequency,
        "success_count": success_count,
        "partial_success_count": partial_count,
        "failure_count": failure_count,
        "correction_count": correction_count,
        "success_rate": round(success_rate, 4),
        "effective_count": round(effective_count, 4),
        "action_stability": round(sum(action_stability_values) / len(action_stability_values), 4) if action_stability_values else 0.0,
        "io_stability": round(sum(io_stability_values) / len(io_stability_values), 4) if io_stability_values else 0.0,
        "total_actions": sum(len(fp.get("action_sequence") or []) for fp in fingerprints),
        "last_seen_at": last_seen,
    }


def _pattern_skillability(stats: dict[str, Any], prototype: dict[str, Any]) -> dict[str, Any]:
    effective = float(stats.get("effective_count") or 0)
    llm_dependency = str(prototype.get("llm_dependency") or "medium")
    has_actions = int(stats.get("total_actions") or 0) > 0
    # Must have at least one non-trivial tool call — interactive-only workflows
    # (ask_user, send_message, etc.) are not worth learning as skills.
    proto_actions = prototype.get("action_sequence") or []
    has_real_tools = any(
        not _is_internal_tool_action(a) and not _is_trivial_skill_action(a)
        for a in proto_actions
    )
    skillable = has_actions and has_real_tools
    return {
        "draft": skillable and effective >= 2,
        "workflow": skillable and effective >= 3,
        "parameterized": skillable and effective >= 5,
        "deterministic": skillable and effective >= 8 and llm_dependency in {"low", "none"},
    }


def _pattern_status(stats: dict[str, Any], linked_skill_ids: list[str]) -> str:
    effective = float(stats.get("effective_count") or 0)
    frequency = int(stats.get("frequency") or 0)
    if linked_skill_ids:
        return "linked_to_skill"
    if effective >= 2:
        return "skill_candidate"
    if frequency >= 2:
        return "stable"
    return "candidate"


async def _upsert_pattern(pattern_id: str) -> None:
    turn_ids = await _member_turn_ids(pattern_id)
    fingerprints = [await _fingerprint_for_turn(turn_id) for turn_id in turn_ids]
    prototype = _choose_pattern_prototype([fp for fp in fingerprints if fp])
    stats = await _compute_pattern_stats(turn_ids, prototype)
    skillability = _pattern_skillability(stats, prototype)
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT * FROM learned_skills WHERE pattern_id = ? AND status != 'deprecated' ORDER BY created_at ASC",
            (pattern_id,),
        )
        linked_skill_rows = await cursor.fetchall()
        linked_skill_ids = [
            str(row["skill_id"])
            for row in linked_skill_rows
            if _is_reusable_skill_definition(_skill_row_to_definition(row))
        ]
        status = _pattern_status(stats, linked_skill_ids)
        await conn.execute(
            """
            UPDATE behavior_patterns
            SET description = ?, prototype_fingerprint = ?, statistics_json = ?, skillability_json = ?,
                status = ?, linked_skill_list = ?, updated_at = ?
            WHERE pattern_id = ?
            """,
            (
                _pattern_description(prototype),
                _json_dumps(prototype),
                _json_dumps(stats),
                _json_dumps(skillability),
                status,
                _json_dumps(linked_skill_ids),
                _now_iso(),
                pattern_id,
            ),
        )
        await conn.commit()


async def _merge_turn_into_pattern(turn_id: str, fingerprint: dict[str, Any]) -> tuple[str, bool]:
    scope = await _project_scope_for_turn(turn_id)
    async with _conn() as conn:
        cursor = await conn.execute(
            """
            SELECT pattern_id, prototype_fingerprint
            FROM behavior_patterns
            WHERE status != 'deprecated' AND project_id = ?
            ORDER BY updated_at DESC
            """,
            (scope["project_id"],),
        )
        rows = await cursor.fetchall()
    best_pattern_id = ""
    best_similarity: dict[str, Any] = {"total": 0.0, "hard_fail": False, "breakdown": {}}
    best_prototype: dict[str, Any] = {}
    for row in rows:
        prototype = _json_loads(row["prototype_fingerprint"], {})
        sim = compute_fingerprint_similarity(fingerprint, prototype)
        if sim["total"] > float(best_similarity["total"]):
            best_pattern_id = str(row["pattern_id"])
            best_similarity = sim
            best_prototype = prototype
    should_merge = False
    if best_pattern_id and not best_similarity["hard_fail"] and float(best_similarity["total"]) >= _PATTERN_STRONG_THRESHOLD:
        should_merge = True
    elif best_pattern_id and not best_similarity["hard_fail"] and float(best_similarity["total"]) >= _PATTERN_MEDIUM_THRESHOLD:
        breakdown = best_similarity.get("breakdown") or {}
        if (
            float(breakdown.get("action_sequence") or 0.0) >= 0.85
            and float(breakdown.get("intent") or 0.0) >= 0.75
            and float(breakdown.get("object") or 0.0) >= 0.75
            and float(breakdown.get("domain") or 0.0) >= 0.75
        ):
            should_merge = True
        else:
            should_merge = await _llm_should_merge(fingerprint, best_prototype, best_similarity)
    if not should_merge and best_pattern_id and not best_similarity["hard_fail"]:
        _turn_actions = fingerprint.get("action_sequence") or []
        _pat_actions = best_prototype.get("action_sequence") or []
        _turn_has_real = any(not _is_internal_tool_action(a) for a in _turn_actions)
        _pat_has_real = any(not _is_internal_tool_action(a) for a in _pat_actions)
        _total = float(best_similarity["total"])
        if _turn_has_real and _pat_has_real and _total >= 0.40:
            should_merge = await _llm_workflow_merge(turn_id, fingerprint, best_pattern_id, best_prototype, _total)
    if not should_merge:
        pattern_id = _new_id("pattern")
        now = _now_iso()
        async with _conn() as conn:
            await conn.execute(
                """
                INSERT INTO behavior_patterns
                (pattern_id, project_id, project_key, description, prototype_fingerprint, statistics_json, skillability_json, status, linked_skill_list, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'candidate', '[]', ?, ?)
                """,
                (
                    pattern_id,
                    scope["project_id"],
                    scope["project_key"],
                    _pattern_description(fingerprint),
                    _json_dumps(fingerprint),
                    _json_dumps(_default_pattern_stats()),
                    _json_dumps({}),
                    now,
                    now,
                ),
            )
            await conn.execute(
                """
                INSERT INTO behavior_pattern_turns
                (pattern_id, turn_id, similarity, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (pattern_id, turn_id, 1.0, now),
            )
            await conn.commit()
        await _upsert_pattern(pattern_id)
        return pattern_id, False
    async with _conn() as conn:
        await conn.execute(
            """
            INSERT OR REPLACE INTO behavior_pattern_turns
            (pattern_id, turn_id, similarity, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (best_pattern_id, turn_id, float(best_similarity["total"]), _now_iso()),
        )
        await conn.commit()
    await _upsert_pattern(best_pattern_id)
    return best_pattern_id, True


async def _derive_parameter_templates(turn_ids: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    action_groups: list[list[dict[str, Any]]] = []
    for turn_id in turn_ids[:_MAX_PATTERN_EXAMPLES]:
        group: list[dict[str, Any]] = []
        for action in await _action_rows_for_turn(turn_id):
            if action["tool_name"] in _INTERNAL_TOOLS:
                continue
            metadata = action.get("metadata_json") or {}
            group.append(
                {
                    "tool_name": action["tool_name"],
                    "action_type": action["action_type"],
                    "action_subtype": action["action_subtype"],
                    "args": metadata.get("raw_args") or {},
                }
            )
        chain = await _load_tool_chain_for_turn(turn_id)
        for item in chain.get("chain") or []:
            if str((item or {}).get("source") or "") != "user_browser":
                continue
            tool_name = str(item.get("tool") or "browser.user.event")
            group.append(
                {
                    "tool_name": tool_name,
                    "action_type": "browser_user_operation",
                    "action_subtype": str(item.get("subtype") or "event"),
                    "args": item.get("args") or {},
                }
            )
        if group:
            compressed_group: list[dict[str, Any]] = []
            for item in group:
                signature = (
                    str(item.get("tool_name") or ""),
                    str(item.get("action_type") or ""),
                    str(item.get("action_subtype") or ""),
                )
                if compressed_group and compressed_group[-1].get("_agg_signature") == signature:
                    # Same tool repeated — aggregate args instead of discarding
                    existing = compressed_group[-1]
                    merged_items = existing.setdefault("_items", [dict(existing.get("args") or {})])
                    merged_items.append(dict(item.get("args") or {}))
                else:
                    item["_agg_signature"] = signature
                    compressed_group.append(item)
            action_groups.append(compressed_group)
    if not action_groups:
        return [], []
    grouped_by_signature: dict[tuple[tuple[str, str, str], ...], list[list[dict[str, Any]]]] = defaultdict(list)
    for group in action_groups:
        signature = tuple(
            (
                str(item.get("tool_name") or ""),
                str(item.get("action_type") or ""),
                str(item.get("action_subtype") or ""),
            )
            for item in group
        )
        grouped_by_signature[signature].append(group)
    matching_groups = max(
        grouped_by_signature.values(),
        key=lambda groups: (len(groups), -len(groups[0]), -sum(len(g) for g in groups)),
    )
    # Keep every observed repeated call when counts differ across occurrences;
    # the structural signature intentionally compresses those calls into one
    # step, so choose the richest representative for its `_items` template.
    template_group = max(
        matching_groups,
        key=lambda group: sum(len(item.get("_items") or [item.get("args") or {}]) for item in group),
    )
    steps: list[dict[str, Any]] = []
    schema: dict[str, dict[str, Any]] = {}
    schema_reuse: dict[tuple[str, str, tuple[str, ...]], str] = {}
    param_index = 1
    for step_index, template in enumerate(template_group):
        items = template.get("_items")
        if items:
            # Aggregated repeated calls remain one declarative step, but each
            # varying argument is parameterized across observed occurrences.
            item_templates = _clone_json_value(list(items))
            for item_index, item_args in enumerate(item_templates):
                if not isinstance(item_args, dict):
                    continue
                for key, value in list(item_args.items()):
                    observed_values: list[Any] = []
                    for group in action_groups:
                        if step_index >= len(group):
                            continue
                        observed_items = group[step_index].get("_items") or [group[step_index].get("args") or {}]
                        if item_index < len(observed_items) and isinstance(observed_items[item_index], dict):
                            observed_values.append(observed_items[item_index].get(key))
                    values = {json.dumps(item, ensure_ascii=False) for item in observed_values}
                    varies = len(values) > 1
                    if not ((varies and _should_parameterize_arg(key, observed_values)) or _should_expose_stable_arg(key, value)):
                        continue
                    examples = [str(_json_loads(item, item)) for item in sorted(values)][:6]
                    param_type = _parameter_type_for_value(value)
                    param_name = f"param_{_safe_slug(key)}_{item_index + 1}_{param_index}"
                    param_index += 1
                    schema[param_name] = {
                        "parameter_name": param_name,
                        "type": param_type,
                        "required": varies,
                        "default_value": _clone_json_value(value),
                        "default_strategy": "use_first_observed",
                        "validation_rule": "",
                        "examples": examples,
                        "aliases": [key],
                    }
                    item_args[key] = f"{{{{{param_name}}}}}"
            args_template = {"_items": item_templates}
        else:
            args_template = dict(template.get("args") or {})
            for key, value in list(args_template.items()):
                observed_values = [
                    group[step_index].get("args", {}).get(key)
                    for group in action_groups
                    if step_index < len(group)
                ]
                values = {json.dumps(item, ensure_ascii=False) for item in observed_values}
                varies = len(values) > 1
                if (varies and _should_parameterize_arg(key, observed_values)) or _should_expose_stable_arg(key, value):
                    examples = []
                    for item in sorted(values):
                        try:
                            examples.append(str(json.loads(item)))
                        except Exception:
                            examples.append(str(item))
                    param_type = _parameter_type_for_value(value)
                    reuse_key = (_safe_slug(key), param_type, tuple(examples[:6]))
                    param_name = schema_reuse.get(reuse_key, "")
                    if not param_name:
                        param_name = f"param_{_safe_slug(key)}_{param_index}"
                        param_index += 1
                        schema_reuse[reuse_key] = param_name
                        schema[param_name] = {
                            "parameter_name": param_name,
                            "type": param_type,
                            "required": varies,
                            "default_value": _clone_json_value(value),
                            "default_strategy": "use_first_observed",
                            "validation_rule": "",
                            "examples": examples[:6],
                            "aliases": [key],
                        }
                    args_template[key] = f"{{{{{param_name}}}}}"
        steps.append(
            {
                "step_id": f"step_{step_index + 1}",
                "type": template["action_type"],
                "subtype": template["action_subtype"],
                "description": f"{template['tool_name']} via learned pattern",
                "enabled": True,
                "requires_llm": False,
                "implementation_kind": "tool_call",
                "implementation_reference": {
                    "tool_name": template["tool_name"],
                    "args_template": args_template,
                },
                "failure_policy": "fail",
            }
        )
    return steps, list(schema.values())


def _sanitize_skill_name(name: str) -> str:
    text = _normalize_whitespace(name)
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" .,:;|-_")
    if not text:
        return "学习技能"
    if len(text) > 24:
        text = text[:24].rstrip(" .,:;|-_")
    return text or "学习技能"


def _sanitize_skill_description(description: str) -> str:
    text = _normalize_whitespace(description)
    text = re.sub(r"[\r\n\t]+", " ", text).strip()
    if len(text) > 120:
        text = text[:120].rstrip(" .,:;|-_")
    return text or "从重复行为中学到的自动技能。"


def _looks_like_generated_skill_name(name: str) -> bool:
    text = str(name or "").strip()
    if not text:
        return True
    if re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+){2,}(?:_[0-9]{4})?", text):
        return True
    return text.startswith("skill_")


async def _unique_skill_name(conn: aiosqlite.Connection, preferred_name: str, *, skill_id: str = "") -> str:
    base = str(preferred_name or "").strip() or "学习技能"
    candidate = base
    counter = 2
    while True:
        if skill_id:
            cursor = await conn.execute(
                "SELECT skill_id FROM learned_skills WHERE name = ? AND skill_id != ?",
                (candidate, skill_id),
            )
            row = await cursor.fetchone()
        else:
            cursor = await conn.execute(
                "SELECT skill_id FROM learned_skills WHERE name = ?",
                (candidate,),
            )
            row = await cursor.fetchone()
        if row is None:
            return candidate
        candidate = f"{base} {counter}"
        counter += 1


async def _generate_skill_identity_with_llm(
    *,
    pattern_id: str,
    prototype: dict[str, Any],
    turn_examples: list[dict[str, Any]],
    skill_type: str,
    current_name: str = "",
    current_description: str = "",
) -> tuple[str, str]:
    example_messages = [
        _truncate_text(str(turn.get("user_message") or ""), 120)
        for turn in turn_examples
        if str(turn.get("user_message") or "").strip()
    ][:5]
    payload = {
        "pattern_id": pattern_id,
        "skill_type": skill_type,
        "prototype": prototype,
        "example_requests": example_messages,
        "current_name": current_name,
        "current_description": current_description,
    }
    prompt = f"""You are naming a learned automation skill for end users.

Return JSON with:
- name: a short user-facing skill name in Chinese, ideally 4-12 characters, natural and concrete.
- description: one short Chinese sentence describing what this skill can do for the user.

Requirements:
- Do not output internal taxonomy labels, snake_case, tool names, URLs, IDs, random numbers, or implementation details.
- Do not use generic filler like "技能", "任务", "自动化流程" unless absolutely necessary.
- Prefer what the user would recognize as the task itself.
- If there are concrete entities in the examples, you may use them when they make the skill clearer.
- Keep the name concise and natural.

Context JSON:
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""
    result = await _call_llm_json(prompt, caller="skill_namer")
    proposed_name = _sanitize_skill_name(str(result.get("name") or ""))
    proposed_description = _sanitize_skill_description(str(result.get("description") or ""))
    return proposed_name, proposed_description


def _generated_skill_script_dir(skill_id: str) -> Path:
    base = _DATA_DIR or DATA_DIR
    path = Path(base) / "learned_skill_scripts" / _safe_slug(skill_id, default="skill")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _generated_python_script_source(original_steps: list[dict[str, Any]], skill_name: str) -> str:
    embedded_steps = json.dumps(original_steps, ensure_ascii=True, indent=2)
    embedded_name = json.dumps(str(skill_name or "learned skill"), ensure_ascii=True)
    return f'''#!/usr/bin/env python3
"""Generated Workbench learned-skill script."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import urllib.parse
import urllib.request
import webbrowser

SKILL_NAME = {embedded_name}
ORIGINAL_STEPS = {embedded_steps}


def resolve(value, params):
    if isinstance(value, str):
        result = value
        for key, param in params.items():
            result = result.replace("{{{{" + str(key) + "}}}}", str(param))
        return result
    if isinstance(value, list):
        return [resolve(item, params) for item in value]
    if isinstance(value, dict):
        return {{key: resolve(item, params) for key, item in value.items()}}
    return value


def run_tool(tool_name, args):
    tool = str(tool_name or "")
    if tool in {{"read_file", "Read"}}:
        path = pathlib.Path(str(args.get("path") or args.get("file_path") or ""))
        return {{"tool": tool, "ok": True, "output": path.read_text(encoding="utf-8", errors="replace")[:12000]}}
    if tool in {{"Glob", "list_files", "search_files"}}:
        pattern = str(args.get("pattern") or args.get("glob") or "*")
        root = pathlib.Path(str(args.get("path") or args.get("cwd") or "."))
        return {{"tool": tool, "ok": True, "output": sorted(str(path) for path in root.glob(pattern))[:500]}}
    if tool in {{"Grep", "search_file_content"}}:
        pattern = str(args.get("pattern") or args.get("query") or "")
        root = pathlib.Path(str(args.get("path") or args.get("cwd") or "."))
        regex = re.compile(pattern)
        hits = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if regex.search(line):
                        hits.append({{"path": str(path), "line": lineno, "text": line[:500]}})
                        if len(hits) >= 200:
                            return {{"tool": tool, "ok": True, "output": hits}}
            except Exception:
                continue
        return {{"tool": tool, "ok": True, "output": hits}}
    if tool in {{"search_web", "WebSearch"}}:
        query = str(args.get("query") or args.get("q") or "")
        url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)
        return {{"tool": tool, "ok": True, "output": {{"query": query, "search_url": url}}}}
    if tool in {{"fetch_web_page", "WebFetch"}}:
        url = str(args.get("url") or "")
        with urllib.request.urlopen(url, timeout=20) as response:
            body = response.read(200000).decode("utf-8", errors="replace")
        return {{"tool": tool, "ok": True, "output": body[:12000]}}
    if tool in {{"browser_navigate", "open_browser", "open_website"}}:
        url = str(args.get("url") or args.get("website") or "")
        if url:
            webbrowser.open(url)
        return {{"tool": tool, "ok": True, "output": url}}
    if tool in {{"write_file", "Write"}}:
        path = pathlib.Path(str(args.get("path") or ""))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(args.get("content") or ""), encoding="utf-8")
        return {{"tool": tool, "ok": True, "output": str(path)}}
    if tool in {{"edit_file", "Edit"}}:
        path = pathlib.Path(str(args.get("path") or ""))
        old = str(args.get("old_string") or "")
        new = str(args.get("new_string") or "")
        text = path.read_text(encoding="utf-8", errors="replace")
        if old not in text:
            return {{"tool": tool, "ok": False, "error": "old_string not found", "path": str(path)}}
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        return {{"tool": tool, "ok": True, "output": str(path)}}
    if tool in {{"run_shell", "run_command", "Bash"}}:
        command = str(args.get("command") or args.get("cmd") or "")
        completed = subprocess.run(command, shell=True, text=True, capture_output=True, timeout=120)
        return {{
            "tool": tool,
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-12000:],
            "stderr": completed.stderr[-12000:],
        }}
    return {{"tool": tool, "ok": False, "error": "unsupported generated-script tool", "args": args}}


def main():
    parser = argparse.ArgumentParser(description="Run generated Workbench learned skill")
    parser.add_argument("--params-json", default="{{}}")
    ns = parser.parse_args()
    params = json.loads(ns.params_json or "{{}}")
    outputs = []
    for step in ORIGINAL_STEPS:
        if not step.get("enabled", True):
            continue
        ref = step.get("implementation_reference") or {{}}
        tool_name = ref.get("tool_name") or ""
        args_template = ref.get("args_template") or {{}}
        items = args_template.get("_items")
        arg_sets = items if isinstance(items, list) and items else [args_template]
        for item in arg_sets:
            outputs.append(run_tool(tool_name, resolve(item, params)))
    print(json.dumps({{"skill": SKILL_NAME, "results": outputs}}, ensure_ascii=False, indent=2))
    return 1 if any(not item.get("ok") for item in outputs) else 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _should_generate_parameterized_script(steps: list[dict[str, Any]]) -> bool:
    tool_steps = [
        step for step in steps
        if bool(step.get("enabled", True)) and str(step.get("implementation_kind") or "") == "tool_call"
    ]
    if len(tool_steps) >= 2:
        return True
    return any(
        isinstance(((step.get("implementation_reference") or {}).get("args_template") or {}).get("_items"), list)
        for step in tool_steps
    )


def _attach_generated_script_to_definition(definition: dict[str, Any], skill_id: str) -> dict[str, Any]:
    steps = definition.get("steps") or []
    if not _should_generate_parameterized_script(steps):
        return definition
    original_steps = _clone_json_value(steps)
    script_path = _generated_skill_script_dir(skill_id) / "run.py"
    script_path.write_text(
        _generated_python_script_source(original_steps, str(definition.get("name") or "")),
        encoding="utf-8",
    )
    try:
        os.chmod(script_path, 0o755)
    except Exception:
        pass
    definition["steps"] = [
        {
            "step_id": "script_1",
            "title": f"Execute {definition.get('name') or 'learned skill'}",
            "type": "run_command",
            "subtype": "generated_python_script",
            "description": f"Run generated script for {definition.get('name') or 'learned skill'}",
            "raw_description": f"Run generated script ({script_path.name})",
            "enabled": True,
            "requires_llm": False,
            "implementation_kind": "script",
            "implementation_reference": {
                "language": "python",
                "script_path": str(script_path),
                "original_steps": original_steps,
            },
            "failure_policy": "fail",
        }
    ]
    inferred_risk = _infer_skill_risk_level(definition["steps"])
    definition["risk_level"] = inferred_risk
    if isinstance(definition.get("guards"), dict):
        definition["guards"]["risk_level"] = inferred_risk
    return definition


async def _migrate_generated_skill_scripts() -> int:
    """Unwrap legacy generated Python scripts into declarative tool steps."""
    async with _conn() as conn:
        cursor = await conn.execute("SELECT * FROM learned_skills")
        rows = await cursor.fetchall()
        migrated = 0
        for row in rows:
            definition = _skill_row_to_definition(row)
            steps = definition.get("steps") or []
            if len(steps) != 1 or str(steps[0].get("implementation_kind") or "") != "script":
                continue
            reference = steps[0].get("implementation_reference") or {}
            original_steps = reference.get("original_steps") or []
            if not _has_skillworthy_steps(original_steps):
                continue
            script = {
                "format": "cyrene.parameterized-tool-script",
                "version": int(definition.get("version") or 1),
                "name": definition.get("name") or "",
                "description": definition.get("description") or "",
                "parameters": definition.get("input_schema") or [],
                "steps": original_steps,
                "execution": {"stop_on_failure": True, "record_run": True, "suppress_relearning": True},
                "risk": {
                    "level": _infer_skill_risk_level(original_steps),
                    "requires_runtime_approval": _infer_skill_risk_level(original_steps) == "high",
                },
                "source_turn_ids": (definition.get("created_from") or {}).get("turn_list") or [],
            }
            await conn.execute(
                """
                UPDATE learned_skills
                SET steps_json = ?, script_json = ?, risk_level = ?, guards_json = ?, updated_at = ?
                WHERE skill_id = ?
                """,
                (
                    _json_dumps(original_steps),
                    _json_dumps(script),
                    str(script["risk"]["level"]),
                    _json_dumps({**(definition.get("guards") or {}), "risk_level": str(script["risk"]["level"])}),
                    _now_iso(),
                    definition["skill_id"],
                ),
            )
            migrated += 1
        if migrated:
            await conn.commit()
    return migrated


async def _execute_script_step(reference: dict[str, Any], params: dict[str, Any]) -> tuple[str, bool, str]:
    script_path = Path(str(reference.get("script_path") or ""))
    if not script_path.exists():
        return f"Script failed: missing script {script_path}", False, "missing_script"
    if str(reference.get("language") or "python") != "python":
        return "Script failed: unsupported script language", False, "unsupported_script_language"
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(script_path),
        "--params-json",
        _json_dumps(params or {}),
        cwd=str(_WORKSPACE_DIR or Path.cwd()),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_SCRIPT_EXECUTION_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return "Script failed: timed out", False, "script_timeout"
    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    if proc.returncode == 0:
        return out or "Script completed.", True, ""
    return (out + ("\n" if out and err else "") + err).strip() or f"Script failed with exit code {proc.returncode}", False, "script_failed"


async def _refresh_generated_skill_names_with_llm() -> None:
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT skill_id, name, description, pattern_id, skill_type, trigger_json FROM learned_skills ORDER BY created_at ASC"
        )
        rows = await cursor.fetchall()
    updates: list[tuple[str, str, str]] = []
    seen_names = {str(row["name"] or "").strip() for row in rows if str(row["name"] or "").strip()}
    for row in rows:
        current_name = str(row["name"] or "")
        if not _looks_like_generated_skill_name(current_name):
            continue
        trigger = _json_loads(row["trigger_json"], {})
        prototype = (trigger or {}).get("base_fingerprint") or {}
        if not prototype:
            continue
        turn_examples = await _fetch_turn_rows(await _member_turn_ids(str(row["pattern_id"] or "")))
        proposed_name, proposed_description = await _generate_skill_identity_with_llm(
            pattern_id=str(row["pattern_id"] or ""),
            prototype=prototype,
            turn_examples=turn_examples,
            skill_type=str(row["skill_type"] or "draft"),
            current_name=current_name,
            current_description=str(row["description"] or ""),
        )
        unique_name = proposed_name
        suffix = 2
        while unique_name in seen_names - {current_name}:
            unique_name = f"{proposed_name} {suffix}"
            suffix += 1
        seen_names.discard(current_name)
        seen_names.add(unique_name)
        if unique_name != current_name or proposed_description != str(row["description"] or ""):
            updates.append((unique_name, proposed_description, str(row["skill_id"] or "")))
    if not updates:
        return
    async with _conn() as conn:
        now = _now_iso()
        for name, description, skill_id in updates:
            await conn.execute(
                "UPDATE learned_skills SET name = ?, description = ?, updated_at = ? WHERE skill_id = ?",
                (name, description, now, skill_id),
            )
        await conn.commit()


def _infer_skill_risk_level(steps: list[dict[str, Any]]) -> str:
    """Return 'high' if any enabled step references a high-risk tool, else 'none'."""
    for tool in _enabled_step_tool_names(steps):
        if tool in _HIGH_RISK_TOOLS:
            return "high"
    return "none"


def _skill_stats_with_usage_counters(stats: dict[str, Any] | None) -> dict[str, Any]:
    raw = stats or {}
    merged = {**_default_skill_stats(), **raw}
    actual_runs = int(merged.get("actual_runs") or 0)
    if "actual_runs" not in raw:
        actual_runs = int(merged.get("active_success") or 0) + int(merged.get("active_failure") or 0)
    shadow_runs = int(merged.get("shadow_runs") or 0)
    if "shadow_runs" not in raw:
        shadow_runs = int(merged.get("shadow_success") or 0) + int(merged.get("shadow_failure") or 0)
    merged["actual_runs"] = actual_runs
    merged["shadow_runs"] = shadow_runs
    return merged


def _semantic_family(value: Any) -> str:
    normalized = _safe_slug(str(value or ""), default="")
    if not normalized:
        return ""
    for family, members in _SEMANTIC_FAMILIES.items():
        if normalized == family or normalized in members:
            return family
    return normalized


def _skill_duplicate_key(definition: dict[str, Any]) -> str:
    trigger = definition.get("trigger") or {}
    fp = trigger.get("base_fingerprint") or {}
    intent = fp.get("intent") or {}
    obj = fp.get("object") or {}
    actions = fp.get("action_sequence") or []
    action_signature = [
        (
            _semantic_family((action or {}).get("type")),
            _semantic_family((action or {}).get("subtype")),
        )
        for action in actions[:6]
        if isinstance(action, dict)
    ]
    if not action_signature:
        action_signature = [
            ("tool", _safe_slug(tool, default=""))
            for tool in _enabled_step_tool_names(definition.get("steps") or [])[:6]
            if tool
        ]
    parts = [
        _semantic_family((intent or {}).get("type")),
        _semantic_family((intent or {}).get("subtype")),
        _semantic_family((obj or {}).get("type")),
        _semantic_family((obj or {}).get("subtype")),
        _semantic_family(fp.get("domain")),
        "|".join(f"{kind}:{subtype}" for kind, subtype in action_signature),
    ]
    return "||".join(part for part in parts if part)


def _skill_duplicate_score(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_fp = ((left.get("trigger") or {}).get("base_fingerprint") or {})
    right_fp = ((right.get("trigger") or {}).get("base_fingerprint") or {})
    if not left_fp or not right_fp:
        return 0.0
    sim = compute_fingerprint_similarity(left_fp, right_fp)
    if bool(sim.get("hard_fail")):
        return 0.0
    total = float(sim.get("total") or 0.0)
    if total >= 0.88:
        return total
    if _skill_duplicate_key(left) and _skill_duplicate_key(left) == _skill_duplicate_key(right) and total >= 0.70:
        return total
    return 0.0


def _status_rank(status: str) -> int:
    return {"active": 4, "shadow": 3, "refined": 2, "draft": 1}.get(str(status or ""), 0)


def _dedupe_skill_definitions(definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[list[dict[str, Any]]] = []
    for definition in definitions:
        matched: list[dict[str, Any]] | None = None
        for group in groups:
            if any(_skill_duplicate_score(definition, existing) > 0 for existing in group):
                matched = group
                break
        if matched is None:
            groups.append([definition])
        else:
            matched.append(definition)
    result: list[dict[str, Any]] = []
    for group in groups:
        ranked = sorted(
            group,
            key=lambda item: (
                _status_rank(str(item.get("status") or "")),
                int((_skill_stats_with_usage_counters(item.get("run_statistics") or {})).get("actual_runs") or 0),
                str(item.get("updated_at") or ""),
            ),
            reverse=True,
        )
        primary = dict(ranked[0])
        if len(ranked) > 1:
            primary["duplicate_skill_ids"] = [
                str(item.get("skill_id") or item.get("id") or "")
                for item in ranked[1:]
                if str(item.get("skill_id") or item.get("id") or "")
            ]
        result.append(primary)
    result.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return result


async def _find_existing_duplicate_skill(
    conn: aiosqlite.Connection,
    *,
    project_id: str,
    definition: dict[str, Any],
    exclude_skill_id: str = "",
) -> str:
    cursor = await conn.execute(
        """
        SELECT *
        FROM learned_skills
        WHERE project_id = ? AND status != 'deprecated'
        ORDER BY updated_at DESC
        """,
        (str(project_id or ""),),
    )
    rows = await cursor.fetchall()
    best_id = ""
    best_score = 0.0
    for row in rows:
        existing = _skill_row_to_definition(row)
        if not _is_reusable_skill_definition(existing):
            continue
        if exclude_skill_id and str(existing.get("skill_id") or "") == exclude_skill_id:
            continue
        score = _skill_duplicate_score(definition, existing)
        if score > best_score:
            best_score = score
            best_id = str(existing.get("skill_id") or "")
    return best_id


def _skill_trigger_from_prototype(prototype: dict[str, Any], turn_examples: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "intent_types": [str((prototype.get("intent") or {}).get("type") or "")],
        "intent_subtypes": [str((prototype.get("intent") or {}).get("subtype") or "")],
        "object_types": [str((prototype.get("object") or {}).get("type") or "")],
        "object_subtypes": [str((prototype.get("object") or {}).get("subtype") or "")],
        "positive_examples": [_truncate_text(turn.get("user_message") or "", 200) for turn in turn_examples[:6]],
        "negative_examples": [],
        "min_match_score": _ROUTER_JUDGE_THRESHOLD,
        "base_fingerprint": prototype,
    }


async def _skill_definition_from_pattern(pattern_id: str, skill_type: str) -> dict[str, Any]:
    turn_ids = await _member_turn_ids(pattern_id)
    turn_examples = await _fetch_turn_rows(turn_ids)
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT prototype_fingerprint, description FROM behavior_patterns WHERE pattern_id = ?",
            (pattern_id,),
        )
        row = await cursor.fetchone()
    prototype = _json_loads(row["prototype_fingerprint"], {}) if row else {}
    steps, input_schema = await _derive_parameter_templates(turn_ids)
    if not input_schema:
        input_schema = [
            {
                "parameter_name": slot.get("name"),
                "type": slot.get("type"),
                "required": bool(slot.get("required")),
                "default_value": slot.get("default_value"),
                "default_strategy": "use_observed_examples",
                "validation_rule": "",
                "examples": slot.get("examples") or [],
                "aliases": slot.get("aliases") or [],
            }
            for slot in prototype.get("parameter_slots") or []
        ]
    trigger = _skill_trigger_from_prototype(prototype, turn_examples)
    fallback_description = str(row["description"] if row else "") or _pattern_description(prototype)
    name, description = await _generate_skill_identity_with_llm(
        pattern_id=pattern_id,
        prototype=prototype,
        turn_examples=turn_examples,
        skill_type=skill_type,
        current_description=fallback_description,
    )
    status = "draft" if skill_type == "draft" else "shadow"
    inferred_risk = _infer_skill_risk_level(steps)
    return {
        "name": name,
        "description": description or fallback_description,
        "status": status,
        "skill_type": skill_type,
        "risk_level": inferred_risk,
        "requires_llm": prototype.get("llm_dependency") not in {"low", "none"},
        "trigger": trigger,
        "input_schema": input_schema,
        "parameter_extractor": {
            "mode": "hybrid",
            "rule_list": [
                {"kind": "path"},
                {"kind": "quoted_string"},
                {"kind": "number"},
                {"kind": "date"},
                {"kind": "url"},
            ],
            "llm_fallback": True,
        },
        "steps": steps,
        "guards": {
            "risk_level": inferred_risk,
            "required_context": [],
            "forbidden_conditions": [],
            "confidence_threshold": _ROUTER_JUDGE_THRESHOLD,
        },
        "fallback_policy": {
            "on_missing_args": "fallback_to_agent",
            "on_low_confidence": "fallback_to_agent",
            "on_step_failure": "fallback_to_agent",
            "on_user_reject": "fallback_to_agent",
        },
        "tests": [],
        "editable_fields": [
            "trigger",
            "input_schema",
            "parameter_extractor",
            "steps",
            "guards",
            "fallback_policy",
        ],
        "created_from": {
            "pattern_list": [pattern_id],
            "turn_list": turn_ids[:_MAX_PATTERN_EXAMPLES],
            "failure_case_list": [],
        },
    }

def _skill_row_to_definition(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    return {
        "skill_id": data["skill_id"],
        "project_id": data.get("project_id", ""),
        "project_key": data.get("project_key", ""),
        "name": data["name"],
        "description": data["description"],
        "version": int(data["current_version"]),
        "status": data["status"],
        "skill_type": data["skill_type"],
        "risk_level": data["risk_level"],
        "requires_llm": bool(data["requires_llm"]),
        "trigger": _json_loads(data["trigger_json"], {}),
        "input_schema": _json_loads(data["input_schema_json"], []),
        "parameter_extractor": _json_loads(data["parameter_extractor_json"], {}),
        "steps": _json_loads(data["steps_json"], []),
        "script": _json_loads(data.get("script_json"), {}),
        "guards": _json_loads(data["guards_json"], {}),
        "fallback_policy": _json_loads(data["fallback_policy_json"], {}),
        "tests": _json_loads(data["tests_json"], []),
        "editable_fields": _json_loads(data["editable_fields_json"], []),
        "created_from": _json_loads(data["created_from_json"], {}),
        "run_statistics": _json_loads(data["run_statistics_json"], {}),
        "pattern_id": data["pattern_id"],
        "created_at": data["created_at"],
        "updated_at": data["updated_at"],
    }


async def _save_skill_version(
    *,
    conn: aiosqlite.Connection,
    skill_id: str,
    version: int,
    parent_version: int | None,
    definition: dict[str, Any],
    change_type: str,
    change_summary: str,
    patch_list: list[dict[str, Any]] | None = None,
    test_result: dict[str, Any] | None = None,
    rollback_target: int | None = None,
) -> None:
    await conn.execute(
        """
        INSERT OR REPLACE INTO learned_skill_versions
        (skill_id, version, parent_version, skill_definition, change_type, change_summary,
         patch_list, created_at, test_result, rollback_target)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            skill_id,
            version,
            parent_version,
            _json_dumps(definition),
            change_type,
            change_summary,
            _json_dumps(patch_list or []),
            _now_iso(),
            _json_dumps(test_result or {}),
            rollback_target,
        ),
    )


async def _insert_replay_tests(conn: aiosqlite.Connection, skill_id: str, turn_ids: list[str], trigger: dict[str, Any]) -> list[str]:
    created_ids: list[str] = []
    now = _now_iso()
    for turn_id in turn_ids[:_MAX_PATTERN_EXAMPLES]:
        test_id = _new_id("replay")
        expected = {
            "trigger": trigger,
            "turn_id": turn_id,
        }
        await conn.execute(
            """
            INSERT OR REPLACE INTO behavior_replay_tests
            (test_id, skill_id, turn_id, test_type, input_payload, expected_payload, last_result, created_at, updated_at)
            VALUES (?, ?, ?, 'regression', ?, ?, '{}', ?, ?)
            """,
            (test_id, skill_id, turn_id, "{}", _json_dumps(expected), now, now),
        )
        created_ids.append(test_id)
    return created_ids


async def _create_skill(pattern_id: str, *, force: bool = False) -> str | None:
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT statistics_json, skillability_json, project_id, project_key FROM behavior_patterns WHERE pattern_id = ?",
            (pattern_id,),
        )
        pattern_row = await cursor.fetchone()
        if pattern_row is None:
            return None
        if not force:
            stats = _json_loads(pattern_row["statistics_json"], {})
            if float(stats.get("effective_count") or 0) < 2:
                return None
            skillability = _json_loads(pattern_row["skillability_json"], {})
            if not bool(skillability.get("draft")):
                return None
        cursor = await conn.execute(
            "SELECT * FROM learned_skills WHERE pattern_id = ?",
            (pattern_id,),
        )
        existing = await cursor.fetchone()
        if existing is not None:
            existing_definition = _skill_row_to_definition(existing)
            if _is_reusable_skill_definition(existing_definition):
                return str(existing["skill_id"])
            # A legacy one-tool skill must not block a valid skill from being
            # learned later from the same pattern.
            await conn.execute(
                "UPDATE learned_skills SET status = 'deprecated', updated_at = ? WHERE skill_id = ?",
                (_now_iso(), str(existing["skill_id"])),
            )
        definition = await _skill_definition_from_pattern(pattern_id, "draft")
        if not _has_skillworthy_steps(definition.get("steps") or []):
            return None
        duplicate_skill_id = await _find_existing_duplicate_skill(
            conn,
            project_id=str(pattern_row["project_id"] or ""),
            definition=definition,
        )
        if duplicate_skill_id:
            return duplicate_skill_id
        definition["name"] = await _unique_skill_name(conn, str(definition.get("name") or "学习技能"))
        skill_id = _new_id("learned_skill")
        definition["script"] = {
            "format": "cyrene.parameterized-tool-script",
            "version": 1,
            "name": definition["name"],
            "description": definition["description"],
            "parameters": definition["input_schema"],
            "steps": definition["steps"],
            "execution": {"stop_on_failure": True, "record_run": True, "suppress_relearning": True},
            "risk": {
                "level": definition["risk_level"],
                "requires_runtime_approval": definition["risk_level"] == "high",
            },
            "source_turn_ids": definition["created_from"]["turn_list"],
        }
        now = _now_iso()
        replay_ids = await _insert_replay_tests(conn, skill_id, definition["created_from"]["turn_list"], definition["trigger"])
        definition["tests"] = replay_ids
        await conn.execute(
            """
            INSERT INTO learned_skills
            (skill_id, project_id, project_key, name, description, current_version, status, skill_type, risk_level, requires_llm,
             trigger_json, input_schema_json, parameter_extractor_json, steps_json, script_json, guards_json, fallback_policy_json,
             tests_json, editable_fields_json, created_from_json, run_statistics_json, pattern_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                skill_id,
                str(pattern_row["project_id"] or ""),
                str(pattern_row["project_key"] or ""),
                definition["name"],
                definition["description"],
                definition["status"],
                definition["skill_type"],
                definition["risk_level"],
                1 if definition["requires_llm"] else 0,
                _json_dumps(definition["trigger"]),
                _json_dumps(definition["input_schema"]),
                _json_dumps(definition["parameter_extractor"]),
                _json_dumps(definition["steps"]),
                _json_dumps(definition["script"]),
                _json_dumps(definition["guards"]),
                _json_dumps(definition["fallback_policy"]),
                _json_dumps(definition["tests"]),
                _json_dumps(definition["editable_fields"]),
                _json_dumps(definition["created_from"]),
                _json_dumps(_default_skill_stats()),
                pattern_id,
                now,
                now,
            ),
        )
        persisted = {
            "skill_id": skill_id,
            "project_id": str(pattern_row["project_id"] or ""),
            "project_key": str(pattern_row["project_key"] or ""),
            **definition,
            "version": 1,
            "run_statistics": _default_skill_stats(),
            "pattern_id": pattern_id,
            "created_at": now,
            "updated_at": now,
        }
        await _save_skill_version(
            conn=conn,
            skill_id=skill_id,
            version=1,
            parent_version=None,
            definition=persisted,
            change_type="create",
            change_summary="Initial draft learned skill generated from pattern evidence.",
        )
        await conn.commit()
    await _upsert_pattern(pattern_id)
    return skill_id


async def _create_manual_skill_review(
    pattern_id: str,
    skill_id: str,
    project_id: str = "",
) -> None:
    """Create a learning-agent review record for a manually promoted skill.

    This lets the behavior-analysis UI match the skill to its source chain
    via ``_decision.target_pattern_id`` when the user promotes a pattern
    directly (bypassing the full LLM review pipeline).
    """
    pid = str(pattern_id or "").strip()
    sid = str(skill_id or "").strip()
    if not pid or not sid:
        return
    now = _now_iso()
    try:
        async with _conn() as conn:
            turn_cursor = await conn.execute(
                "SELECT turn_id FROM behavior_pattern_turns WHERE pattern_id = ? ORDER BY created_at ASC LIMIT 1",
                (pid,),
            )
            turn_row = await turn_cursor.fetchone()
            if turn_row is None:
                return
            source_turn_id = str(turn_row["turn_id"] or "")
            chain_cursor = await conn.execute(
                "SELECT chain_id, project_id, project_key FROM behavior_turn_tool_chains WHERE turn_id = ?",
                (source_turn_id,),
            )
            chain_row = await chain_cursor.fetchone()
            chain_id = str(chain_row["chain_id"] or "") if chain_row is not None else ""
            review_proj_id = str(chain_row["project_id"] or project_id) if chain_row is not None else project_id
            review_proj_key = str(chain_row["project_key"] or "") if chain_row is not None else ""
            proposed = {
                "_decision": {
                    "raw_decision": "promote",
                    "target_pattern_id": pid,
                    "target_skill_id": sid,
                    "similar_patterns": [],
                    "similar_skills": [],
                }
            }
            await conn.execute(
                """
                INSERT INTO behavior_learning_agent_reviews
                (review_id, project_id, project_key, turn_id, chain_id, decision, confidence, rationale,
                 proposed_skill_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(turn_id) DO UPDATE SET
                    project_id = excluded.project_id,
                    project_key = excluded.project_key,
                    chain_id = excluded.chain_id,
                    decision = excluded.decision,
                    confidence = excluded.confidence,
                    rationale = excluded.rationale,
                    proposed_skill_json = excluded.proposed_skill_json,
                    updated_at = excluded.updated_at
                """,
                (
                    _new_id("learning_review"),
                    review_proj_id,
                    review_proj_key,
                    source_turn_id,
                    chain_id,
                    "promote",
                    1.0,
                    "User manually promoted this pattern to a skill.",
                    _json_dumps(proposed),
                    now,
                    now,
                ),
            )
            await conn.commit()
    except Exception:
        logger.warning("Failed to create review for manually created skill", exc_info=True)


async def learn_skill_from_pattern(pattern_id: str, project_id: str = "") -> dict[str, Any]:
    pid = str(pattern_id or "").strip()
    scoped_project_id = str(project_id or "").strip()
    if not pid:
        return {"ok": False, "code": "invalid_pattern", "error": "pattern_id is required"}
    async with _conn() as conn:
        if scoped_project_id:
            cursor = await conn.execute(
                "SELECT pattern_id FROM behavior_patterns WHERE pattern_id = ? AND project_id = ?",
                (pid, scoped_project_id),
            )
        else:
            cursor = await conn.execute(
                "SELECT pattern_id FROM behavior_patterns WHERE pattern_id = ?",
                (pid,),
            )
        pattern_row = await cursor.fetchone()
        if pattern_row is None:
            return {"ok": False, "code": "pattern_not_found", "error": "Pattern not found"}
        cursor = await conn.execute(
            "SELECT skill_id FROM learned_skills WHERE pattern_id = ?",
            (pid,),
        )
        existing_row = await cursor.fetchone()
        existing_skill_id = str(existing_row["skill_id"]) if existing_row is not None else ""
        cursor = await conn.execute(
            "SELECT project_id, prototype_fingerprint FROM behavior_patterns WHERE pattern_id = ?",
            (pid,),
        )
        current_pattern = await cursor.fetchone()
        current_project_id = str(current_pattern["project_id"] or "") if current_pattern is not None else scoped_project_id
        current_fp = _json_loads(current_pattern["prototype_fingerprint"], {}) if current_pattern is not None else {}
        if not existing_skill_id and current_fp:
            cursor = await conn.execute(
                """
                SELECT skill_id, trigger_json
                FROM learned_skills
                WHERE project_id = ? AND status != 'deprecated'
                ORDER BY updated_at DESC
                """,
                (current_project_id,),
            )
            for skill_row in await cursor.fetchall():
                trigger = _json_loads(skill_row["trigger_json"], {})
                base_fp = trigger.get("base_fingerprint") or {}
                if not base_fp:
                    continue
                similarity = compute_fingerprint_similarity(current_fp, base_fp)
                if not similarity.get("hard_fail") and float(similarity.get("total") or 0) >= 0.88:
                    duplicate_skill_id = str(skill_row["skill_id"] or "")
                    # Create review for the new pattern's turn so the existing
                    # skill's behavior analysis can also match via this chain.
                    try:
                        await _create_manual_skill_review(pid, duplicate_skill_id, scoped_project_id)
                    except Exception:
                        pass
                    return {
                        "ok": True,
                        "created": False,
                        "skill": await get_learned_skill(duplicate_skill_id),
                        "skill_id": duplicate_skill_id,
                        "pattern_id": pid,
                    }

    skill_id = await _create_skill(pid, force=True)
    if not skill_id:
        return {"ok": False, "code": "skill_generation_failed", "error": "Unable to generate a skill from this pattern"}
    skill = await get_learned_skill(skill_id)
    try:
        await _create_manual_skill_review(pid, skill_id, scoped_project_id)
    except Exception:
        pass
    return {
        "ok": True,
        "created": bool(not existing_skill_id and skill is not None and str(skill.get("pattern_id") or "") == pid),
        "skill": skill,
        "skill_id": skill_id,
        "pattern_id": pid,
    }


def _target_skill_type(stats: dict[str, Any], prototype: dict[str, Any]) -> str:
    effective = float(stats.get("effective_count") or 0)
    llm_dependency = str(prototype.get("llm_dependency") or "medium")
    if effective >= 8 and llm_dependency in {"low", "none"}:
        return "deterministic"
    if effective >= 5:
        return "parameterized"
    if effective >= 3:
        return "workflow"
    return "draft"


async def _update_skill_to_type(skill_id: str, target_type: str, reason: str) -> bool:
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT * FROM learned_skills WHERE skill_id = ?", (skill_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return False
        current = _skill_row_to_definition(row)
        current_type = current["skill_type"]
        if _SKILL_TYPE_ORDER.get(target_type, 0) <= _SKILL_TYPE_ORDER.get(current_type, 0):
            return False
        next_version = int(row["current_version"]) + 1
        pattern_id = current["pattern_id"]
        definition = await _skill_definition_from_pattern(pattern_id, target_type)
        definition["script"] = {
            "format": "cyrene.parameterized-tool-script",
            "version": next_version,
            "name": definition["name"],
            "description": definition["description"],
            "parameters": definition["input_schema"],
            "steps": definition["steps"],
            "execution": {"stop_on_failure": True, "record_run": True, "suppress_relearning": True},
            "risk": {"level": definition["risk_level"], "requires_runtime_approval": definition["risk_level"] == "high"},
            "source_turn_ids": definition["created_from"]["turn_list"],
        }
        definition["status"] = "shadow"
        definition["tests"] = current["tests"]
        # Validation counters apply to one executable definition.  Carrying
        # them across an upgrade could activate unvalidated steps using the
        # predecessor's successful shadow runs.
        definition["run_statistics"] = _default_skill_stats()
        persisted = {
            "skill_id": skill_id,
            **definition,
            "version": next_version,
            "run_statistics": definition["run_statistics"],
            "pattern_id": pattern_id,
            "created_at": current["created_at"],
            "updated_at": _now_iso(),
        }
        await conn.execute(
            """
            UPDATE learned_skills
            SET name = ?, description = ?, current_version = ?, status = 'shadow', skill_type = ?,
                risk_level = ?, requires_llm = ?, trigger_json = ?, input_schema_json = ?,
                parameter_extractor_json = ?, steps_json = ?, script_json = ?, guards_json = ?, fallback_policy_json = ?,
                tests_json = ?, editable_fields_json = ?, created_from_json = ?,
                run_statistics_json = ?, updated_at = ?
            WHERE skill_id = ?
            """,
            (
                definition["name"],
                definition["description"],
                next_version,
                target_type,
                definition["risk_level"],
                1 if definition["requires_llm"] else 0,
                _json_dumps(definition["trigger"]),
                _json_dumps(definition["input_schema"]),
                _json_dumps(definition["parameter_extractor"]),
                _json_dumps(definition["steps"]),
                _json_dumps(definition["script"]),
                _json_dumps(definition["guards"]),
                _json_dumps(definition["fallback_policy"]),
                _json_dumps(definition["tests"]),
                _json_dumps(definition["editable_fields"]),
                _json_dumps(definition["created_from"]),
                _json_dumps(definition["run_statistics"]),
                _now_iso(),
                skill_id,
            ),
        )
        await _save_skill_version(
            conn=conn,
            skill_id=skill_id,
            version=next_version,
            parent_version=int(row["current_version"]),
            definition=persisted,
            change_type="promote_type",
            change_summary=reason,
        )
        await conn.commit()
    return True


async def _activate_skill(skill_id: str, reason: str) -> None:
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT * FROM learned_skills WHERE skill_id = ?", (skill_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return
        current = _skill_row_to_definition(row)
        if current["status"] == "active":
            return
        current["status"] = "active"
        current["updated_at"] = _now_iso()
        await conn.execute(
            "UPDATE learned_skills SET status = 'active', updated_at = ? WHERE skill_id = ?",
            (current["updated_at"], skill_id),
        )
        # Activation is lifecycle state, not a new executable definition.
        # Keep the current snapshot coherent so a rollback does not turn a
        # validated definition back into a draft.
        await conn.execute(
            """
            UPDATE learned_skill_versions
            SET skill_definition = ?, change_summary = ?
            WHERE skill_id = ? AND version = ?
            """,
            (
                _json_dumps(current),
                reason,
                skill_id,
                int(row["current_version"]),
            ),
        )
        await conn.commit()


async def manual_activate_skill(skill_id: str) -> bool:
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT * FROM learned_skills WHERE skill_id = ?", (skill_id,)
        )
        row = await cursor.fetchone()
    if row is None:
        return False
    await _activate_skill(skill_id, "Manually activated from evolution UI.")
    return True


async def manual_deprecate_skill(skill_id: str) -> bool:
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT * FROM learned_skills WHERE skill_id = ?", (skill_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return False
        current = _skill_row_to_definition(row)
        next_version = int(row["current_version"]) + 1
        current["status"] = "deprecated"
        current["version"] = next_version
        current["updated_at"] = _now_iso()
        await conn.execute(
            "UPDATE learned_skills SET status = 'deprecated', current_version = ?, updated_at = ? WHERE skill_id = ?",
            (next_version, current["updated_at"], skill_id),
        )
        await _save_skill_version(
            conn=conn,
            skill_id=skill_id,
            version=next_version,
            parent_version=int(row["current_version"]),
            definition=current,
            change_type="deprecate",
            change_summary="Manually deprecated from evolution UI.",
        )
        await conn.commit()
    return True


async def delete_learned_skill(skill_id: str) -> bool:
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT skill_id FROM learned_skills WHERE skill_id = ?", (skill_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return False
        await conn.execute("DELETE FROM behavior_replay_tests WHERE skill_id = ?", (skill_id,))
        await conn.execute("DELETE FROM learned_skill_patches WHERE skill_id = ?", (skill_id,))
        await conn.execute("DELETE FROM learned_skill_runs WHERE skill_id = ?", (skill_id,))
        await conn.execute("DELETE FROM learned_skill_versions WHERE skill_id = ?", (skill_id,))
        await conn.execute(
            """
            UPDATE behavior_skill_candidates
            SET status = 'dismissed', linked_skill_id = '', user_decision = 'skill_deleted', updated_at = ?
            WHERE linked_skill_id = ?
            """,
            (_now_iso(), skill_id),
        )
        await conn.execute("DELETE FROM learned_skills WHERE skill_id = ?", (skill_id,))
        await conn.commit()
    return True


async def _update_shadow_promotion(skill_id: str) -> None:
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT * FROM learned_skills WHERE skill_id = ?", (skill_id,)
        )
        row = await cursor.fetchone()
        if row is None or str(row["status"]) != "shadow":
            return
        stats = _json_loads(row["run_statistics_json"], {})
        shadow_success = int(stats.get("shadow_success") or 0)
        shadow_failure = int(stats.get("shadow_failure") or 0)
        consistency_avg = float(stats.get("consistency_avg") or 0.0)
    if shadow_success >= _SHADOW_SUCCESS_THRESHOLD and shadow_failure <= 1 and consistency_avg >= _SHADOW_CONSISTENCY_THRESHOLD:
        await _activate_skill(skill_id, "Shadow validation passed and skill promoted to active.")


async def _update_skill_run_stats(
    skill_id: str,
    *,
    execution_status: str,
    consistency_score: float = 0.0,
    promote: bool = True,
) -> None:
    # Serialize concurrent stat updates so two callers don't read the same
    # baseline and silently lose one increment (read-modify-write race).
    async with _get_stats_lock():
        async with _conn() as conn:
            cursor = await conn.execute(
                "SELECT * FROM learned_skills WHERE skill_id = ?", (skill_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return
            stats = _skill_stats_with_usage_counters(_json_loads(row["run_statistics_json"], _default_skill_stats()))
            stats["total_runs"] = int(stats.get("total_runs") or 0) + 1
            stats["last_run_at"] = _now_iso()
            total_runs = stats["total_runs"]
            old_consistency = float(stats.get("consistency_avg") or 0.0)
            stats["consistency_avg"] = round(((old_consistency * (total_runs - 1)) + consistency_score) / total_runs, 4)
            if execution_status == "shadow_success":
                stats["shadow_success"] = int(stats.get("shadow_success") or 0) + 1
                stats["shadow_runs"] = int(stats.get("shadow_runs") or 0) + 1
            elif execution_status == "shadow_failure":
                stats["shadow_failure"] = int(stats.get("shadow_failure") or 0) + 1
                stats["shadow_runs"] = int(stats.get("shadow_runs") or 0) + 1
            elif execution_status == "success":
                stats["active_success"] = int(stats.get("active_success") or 0) + 1
                stats["actual_runs"] = int(stats.get("actual_runs") or 0) + 1
            elif execution_status == "failure":
                stats["active_failure"] = int(stats.get("active_failure") or 0) + 1
                stats["actual_runs"] = int(stats.get("actual_runs") or 0) + 1
            elif execution_status == "fallback":
                stats["active_failure"] = int(stats.get("active_failure") or 0) + 1
            await conn.execute(
                "UPDATE learned_skills SET run_statistics_json = ?, updated_at = ? WHERE skill_id = ?",
                (_json_dumps(stats), _now_iso(), skill_id),
            )
            await conn.commit()
    if promote:
        await _update_shadow_promotion(skill_id)


async def _create_patch_proposal(skill_id: str, base_version: int, patch_type: str, reason: str, patch_content: dict[str, Any]) -> None:
    async with _conn() as conn:
        await conn.execute(
            """
            INSERT INTO learned_skill_patches
            (patch_id, skill_id, base_version, patch_type, reason, patch_content, risk_assessment, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, '', 'proposed', ?)
            """,
            (
                _new_id("patch"),
                skill_id,
                base_version,
                patch_type,
                reason,
                _json_dumps(patch_content),
                _now_iso(),
            ),
        )
        await conn.commit()


async def _maybe_propose_patch(skill_id: str, version: int, failure_reason: str) -> None:
    reason = str(failure_reason or "")
    if not reason:
        return
    skill = await get_learned_skill(skill_id)
    if skill is None:
        return
    lowered = reason.lower()
    if "missing" in lowered or "parameter" in lowered or "参数" in reason:
        patch_type = "update_input_schema"
    elif "low_confidence" in lowered or "misfire" in lowered:
        patch_type = "update_trigger"
    else:
        patch_type = "replace_step"
    await _create_patch_proposal(
        skill_id,
        version,
        patch_type,
        reason,
        {
            "failure_reason": reason,
            "change_list": _build_patch_change_list(skill, patch_type, reason),
        },
    )


async def _read_patterns(project_id: str = "") -> list[dict[str, Any]]:
    async with _conn() as conn:
        pid = str(project_id or "").strip()
        if pid:
            cursor = await conn.execute(
                "SELECT * FROM behavior_patterns WHERE project_id = ? ORDER BY updated_at DESC",
                (pid,),
            )
        else:
            cursor = await conn.execute(
                "SELECT * FROM behavior_patterns ORDER BY updated_at DESC"
            )
        rows = await cursor.fetchall()
    patterns: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["prototype_fingerprint"] = _json_loads(item.get("prototype_fingerprint"), {})
        item["statistics"] = _json_loads(item.get("statistics_json"), _default_pattern_stats())
        item["skillability"] = _json_loads(item.get("skillability_json"), {})
        item["linked_skill_list"] = _json_loads(item.get("linked_skill_list"), [])
        patterns.append(item)
    return patterns


async def list_patterns(status: str = "all", project_id: str = "") -> list[dict[str, Any]]:
    patterns = await _read_patterns(project_id)
    if status != "all":
        patterns = [item for item in patterns if item.get("status") == status]
    result: list[dict[str, Any]] = []
    for item in patterns:
        prototype = item.get("prototype_fingerprint") or {}
        stats = item.get("statistics") or {}
        result.append(
            {
                "id": item["pattern_id"],
                "project_id": item.get("project_id", ""),
                "project_key": item.get("project_key", ""),
                "description": item.get("description", ""),
                "status": item.get("status", ""),
                "frequency": int(stats.get("frequency") or 0),
                "effective_count": float(stats.get("effective_count") or 0.0),
                "success_rate": float(stats.get("success_rate") or 0.0),
                "action_stability": float(stats.get("action_stability") or 0.0),
                "io_stability": float(stats.get("io_stability") or 0.0),
                "last_seen_at": stats.get("last_seen_at", ""),
                "linked_skill_list": item.get("linked_skill_list") or [],
                "prototype_fingerprint": prototype,
                "action_sequence": prototype.get("action_sequence") or [],
                "skillability": item.get("skillability") or {},
            }
        )
    return result


async def list_learned_skills(project_id: str = "") -> list[dict[str, Any]]:
    async with _conn() as conn:
        pid = str(project_id or "").strip()
        if pid:
            cursor = await conn.execute(
                "SELECT * FROM learned_skills WHERE project_id = ? ORDER BY updated_at DESC",
                (pid,),
            )
        else:
            cursor = await conn.execute(
                "SELECT * FROM learned_skills ORDER BY updated_at DESC"
            )
        rows = await cursor.fetchall()
    # Do not expose legacy or malformed one-tool records.  This also keeps
    # them out of the Workbench's "auto-learned skills" section, which only
    # receives this API response and cannot reliably reconstruct the source
    # chain itself.
    definitions = _dedupe_skill_definitions(
        [
            definition
            for definition in (_skill_row_to_definition(row) for row in rows)
            if _is_reusable_skill_definition(definition)
        ]
    )
    skills: list[dict[str, Any]] = []
    for definition in definitions:
        trigger = definition["trigger"]
        stats = _skill_stats_with_usage_counters(definition["run_statistics"])
        shadow_validation_count = int(stats.get("shadow_runs") or 0)
        actual_usage_count = int(stats.get("actual_runs") or 0)
        skills.append(
            {
                "id": definition["skill_id"],
                "project_id": definition.get("project_id", ""),
                "project_key": definition.get("project_key", ""),
                "name": definition["name"],
                "description": definition["description"],
                "status": definition["status"],
                "skill_type": definition["skill_type"],
                "version": definition["version"],
                "pattern_id": definition["pattern_id"],
                "requires_llm": definition["requires_llm"],
                "trigger": trigger,
                "input_schema": definition["input_schema"],
                "steps": definition["steps"],
                "script": definition.get("script") or {},
                "run_statistics": stats,
                "shadow_validation_count": shadow_validation_count,
                "actual_usage_count": actual_usage_count,
                "duplicate_skill_ids": definition.get("duplicate_skill_ids") or [],
                "updated_at": definition["updated_at"],
                "created_at": definition["created_at"],
                "positive_examples": trigger.get("positive_examples") or [],
                "min_match_score": trigger.get("min_match_score", _ROUTER_JUDGE_THRESHOLD),
            }
        )
    return skills


async def build_learned_skill_block(session_id: str = "", max_skills: int = 20) -> str:
    """Build a compact system-prompt block listing active learned skill names.

    Returns empty string when there are no active skills for the session's
    project.  Within a session the result is stable, so callers can safely
    cache it in the system prompt without degrading prefix-cache hit rates.
    """
    current_sid = str(session_id or _current_session_id.get() or "").strip()
    scope = _project_scope_for_session(current_sid or None)
    async with _conn() as conn:
        cursor = await conn.execute(
            """
            SELECT *
            FROM learned_skills
            WHERE status = 'active' AND project_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (scope["project_id"], max(int(max_skills or 20), 1)),
        )
        rows = await cursor.fetchall()
    if not rows:
        return ""
    lines: list[str] = ["## Learned Skills"]
    for row in rows:
        definition = _skill_row_to_definition(row)
        if not _is_reusable_skill_definition(definition):
            continue
        name = str(definition["name"] or "").strip()
        desc = str(definition["description"] or "").strip()
        if name:
            entry = f"- {name}"
            if desc:
                entry += f": {desc[:120]}"
            lines.append(entry)
    return "\n".join(lines) if len(lines) > 1 else ""


async def list_tool_chains(project_id: str | list[str] = "", limit: int = 80) -> list[dict[str, Any]]:
    if isinstance(project_id, list):
        pids = [str(p).strip() for p in project_id if str(p).strip()]
    else:
        pids = [str(project_id).strip()] if str(project_id or "").strip() else []
    capped_limit = max(1, min(int(limit or 80), 200))
    async with _conn() as conn:
        if pids:
            placeholders = ",".join("?" for _ in pids)
            cursor = await conn.execute(
                f"""
                SELECT
                    c.*,
                    t.user_message,
                    t.context_summary,
                    t.agent_response,
                    t.metadata_json AS turn_metadata_json,
                    r.review_id,
                    r.decision,
                    r.confidence,
                    r.rationale,
                    r.proposed_skill_json,
                    r.updated_at AS review_updated_at
                FROM behavior_turn_tool_chains c
                LEFT JOIN behavior_turns t ON t.turn_id = c.turn_id
                LEFT JOIN behavior_learning_agent_reviews r ON r.turn_id = c.turn_id
                WHERE c.project_id IN ({placeholders})
                ORDER BY c.updated_at DESC
                LIMIT ?
                """,
                (*pids, capped_limit),
            )
        else:
            cursor = await conn.execute(
                """
                SELECT
                    c.*,
                    t.user_message,
                    t.context_summary,
                    t.agent_response,
                    t.metadata_json AS turn_metadata_json,
                    r.review_id,
                    r.decision,
                    r.confidence,
                    r.rationale,
                    r.proposed_skill_json,
                    r.updated_at AS review_updated_at
                FROM behavior_turn_tool_chains c
                LEFT JOIN behavior_turns t ON t.turn_id = c.turn_id
                LEFT JOIN behavior_learning_agent_reviews r ON r.turn_id = c.turn_id
                ORDER BY c.updated_at DESC
                LIMIT ?
                """,
                (capped_limit,),
            )
        rows = await cursor.fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        metadata = _json_loads(item.get("turn_metadata_json"), {})
        review = {
            "id": str(item.get("review_id") or ""),
            "decision": str(item.get("decision") or ""),
            "confidence": float(item.get("confidence") or 0),
            "rationale": str(item.get("rationale") or ""),
            "proposed_skill": _json_loads(item.get("proposed_skill_json"), {}),
            "updated_at": str(item.get("review_updated_at") or ""),
        }
        result.append(
            {
                "id": str(item.get("chain_id") or ""),
                "chain_id": str(item.get("chain_id") or ""),
                "project_id": str(item.get("project_id") or ""),
                "project_key": str(item.get("project_key") or ""),
                "session_id": str(item.get("session_id") or ""),
                "session_kind": str(item.get("session_kind") or ""),
                "turn_id": str(item.get("turn_id") or ""),
                "round_id": str(item.get("round_id") or ""),
                "source": str(item.get("source") or ""),
                "user_message": str(item.get("user_message") or ""),
                "context_summary": str(item.get("context_summary") or ""),
                "agent_response": str(item.get("agent_response") or metadata.get("assistant_preview") or ""),
                "session_title": str(metadata.get("session_title") or ""),
                "round_title": str(metadata.get("round_title") or ""),
                "system_initiated": bool(metadata.get("system_initiated")),
                "chain": _json_loads(item.get("chain_json"), []),
                "summary": _json_loads(item.get("summary_json"), {}),
                "review": review,
                "created_at": str(item.get("created_at") or ""),
                "updated_at": str(item.get("updated_at") or ""),
            }
        )
    return result


async def get_learned_skill(skill_id: str) -> dict[str, Any] | None:
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT * FROM learned_skills WHERE skill_id = ?", (skill_id,)
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    definition = _skill_row_to_definition(row)
    return definition if _is_reusable_skill_definition(definition) else None


async def get_learned_skill_by_name(name: str, session_id: str = "") -> dict[str, Any] | None:
    """Look up an active learned skill by name for the current session's project."""
    current_sid = str(session_id or _current_session_id.get() or "").strip()
    scope = _project_scope_for_session(current_sid or None)
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT * FROM learned_skills WHERE status = 'active' AND project_id = ? AND name = ?",
            (scope["project_id"], str(name or "").strip()),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    definition = _skill_row_to_definition(row)
    return definition if _is_reusable_skill_definition(definition) else None


async def record_manual_skill_run(
    skill_id: str,
    version: int,
    *,
    execution_status: str = "success",
    consistency_score: float = 0.0,
) -> None:
    """Record a skill run initiated by the agent (not by the auto-router)."""
    from cyrene.settings_store import get_write_permission_mode as _get_perm_mode

    run_id = _new_id("skill_run")
    turn_id = _current_turn_id.get()
    async with _conn() as conn:
        await conn.execute(
            """
            INSERT INTO learned_skill_runs
            (run_id, skill_id, version, turn_id, match_score, parameter_status, execution_status, failure_reason,
             fallback_used, user_feedback, dry_run, consistency_score, permission_snapshot, created_at)
            VALUES (?, ?, ?, ?, 1.0, 'manual', ?, '', 0, '', 0, ?, ?, ?)
            """,
            (
                run_id,
                skill_id,
                version,
                turn_id or "",
                execution_status,
                round(consistency_score, 4),
                _get_perm_mode(),
                _now_iso(),
            ),
        )
        await conn.commit()
    await _update_skill_run_stats(skill_id, execution_status=execution_status, consistency_score=consistency_score)


async def list_learned_skill_versions(skill_id: str) -> list[dict[str, Any]]:
    async with _conn() as conn:
        cursor = await conn.execute(
            """
            SELECT skill_id, version, parent_version, change_type, change_summary, patch_list, created_at,
                   test_result, rollback_target
            FROM learned_skill_versions
            WHERE skill_id = ?
            ORDER BY version DESC
            """,
            (skill_id,),
        )
        rows = await cursor.fetchall()
    return [
        {
            "skill_id": str(row["skill_id"]),
            "version": int(row["version"]),
            "parent_version": int(row["parent_version"]) if row["parent_version"] is not None else None,
            "change_type": str(row["change_type"] or ""),
            "change_summary": str(row["change_summary"] or ""),
            "patch_list": _json_loads(row["patch_list"], []),
            "created_at": str(row["created_at"] or ""),
            "test_result": _json_loads(row["test_result"], {}),
            "rollback_target": int(row["rollback_target"]) if row["rollback_target"] is not None else None,
        }
        for row in rows
    ]


async def list_learned_skill_patches(skill_id: str, status: str = "all") -> list[dict[str, Any]]:
    async with _conn() as conn:
        if status == "all":
            cursor = await conn.execute(
                """
                SELECT *
                FROM learned_skill_patches
                WHERE skill_id = ?
                ORDER BY created_at DESC
                """,
                (skill_id,),
            )
            rows = await cursor.fetchall()
        else:
            cursor = await conn.execute(
                """
                SELECT *
                FROM learned_skill_patches
                WHERE skill_id = ? AND status = ?
                ORDER BY created_at DESC
                """,
                (skill_id, status),
            )
            rows = await cursor.fetchall()
    return [
        {
            "patch_id": str(row["patch_id"]),
            "skill_id": str(row["skill_id"]),
            "base_version": int(row["base_version"]),
            "patch_type": str(row["patch_type"] or ""),
            "reason": str(row["reason"] or ""),
            "patch_content": _json_loads(row["patch_content"], {}),
            "risk_assessment": str(row["risk_assessment"] or ""),
            "status": str(row["status"] or ""),
            "created_at": str(row["created_at"] or ""),
        }
        for row in rows
    ]


async def list_learned_skill_runs(skill_id: str, limit: int = 50) -> list[dict[str, Any]]:
    async with _conn() as conn:
        cursor = await conn.execute(
            """
            SELECT *
            FROM learned_skill_runs
            WHERE skill_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (skill_id, max(1, int(limit))),
        )
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def list_skill_replay_tests(skill_id: str) -> list[dict[str, Any]]:
    return await _replay_tests_for_skill(skill_id)


async def vocabulary_snapshot() -> dict[str, Any]:
    async with _conn() as conn:
        cursor = await conn.execute(
            """
            SELECT label_type, canonical_label, domain, parent_label, raw_description, status, updated_at
            FROM behavior_vocabulary_labels
            ORDER BY label_type ASC, canonical_label ASC
            """
        )
        label_rows = await cursor.fetchall()
        cursor = await conn.execute(
            """
            SELECT label_type, canonical_label, alias_label, vocabulary_version, created_at
            FROM behavior_vocabulary_aliases
            ORDER BY label_type ASC, canonical_label ASC, alias_label ASC
            """
        )
        alias_rows = await cursor.fetchall()
        cursor = await conn.execute(
            """
            SELECT *
            FROM behavior_unknown_labels
            ORDER BY seen_count DESC, updated_at DESC
            """
        )
        unknown_rows = await cursor.fetchall()
    return {
        "labels": [dict(row) for row in label_rows],
        "aliases": [dict(row) for row in alias_rows],
        "unknown_labels": [
            {
                **dict(row),
                "example_turns": _json_loads(row["example_turns"], []),
            }
            for row in unknown_rows
        ],
        "vocabulary_version": _VOCABULARY_VERSION,
    }


async def create_vocabulary_label(
    *,
    label_type: str,
    canonical_label: str,
    domain: str = "",
    parent_label: str = "",
    raw_description: str = "",
    status: str = "active",
) -> dict[str, Any]:
    normalized_type = _safe_slug(label_type)
    normalized_label = _safe_slug(canonical_label)
    if not normalized_type or not normalized_label:
        raise ValueError("label_type and canonical_label are required")
    now = _now_iso()
    label_id = f"{normalized_type}:{normalized_label}"
    async with _conn() as conn:
        await conn.execute(
            """
            INSERT OR REPLACE INTO behavior_vocabulary_labels
            (label_id, label_type, canonical_label, domain, parent_label, raw_description, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM behavior_vocabulary_labels WHERE label_id = ?), ?), ?)
            """,
            (
                label_id,
                normalized_type,
                normalized_label,
                _safe_slug(domain, default=""),
                _safe_slug(parent_label, default=""),
                _normalize_whitespace(raw_description),
                _safe_slug(status),
                label_id,
                now,
                now,
            ),
        )
        await conn.commit()
    return {
        "label_id": label_id,
        "label_type": normalized_type,
        "canonical_label": normalized_label,
    }


async def create_vocabulary_alias(*, label_type: str, canonical_label: str, alias_label: str) -> dict[str, Any]:
    normalized_type = _safe_slug(label_type)
    normalized_canonical = _safe_slug(canonical_label)
    normalized_alias = _safe_slug(alias_label)
    if not normalized_type or not normalized_canonical or not normalized_alias:
        raise ValueError("label_type, canonical_label, and alias_label are required")
    now = _now_iso()
    alias_id = f"alias:{normalized_type}:{normalized_alias}"
    async with _conn() as conn:
        await conn.execute(
            """
            INSERT OR REPLACE INTO behavior_vocabulary_aliases
            (alias_id, label_type, canonical_label, alias_label, created_at, vocabulary_version)
            VALUES (?, ?, ?, ?, COALESCE((SELECT created_at FROM behavior_vocabulary_aliases WHERE alias_id = ?), ?), ?)
            """,
            (
                alias_id,
                normalized_type,
                normalized_canonical,
                normalized_alias,
                alias_id,
                now,
                _VOCABULARY_VERSION,
            ),
        )
        await conn.commit()
    return {
        "alias_id": alias_id,
        "label_type": normalized_type,
        "canonical_label": normalized_canonical,
        "alias_label": normalized_alias,
    }


async def promote_unknown_label(unknown_id: str, *, canonical_label: str = "", alias_label: str = "") -> dict[str, Any]:
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT * FROM behavior_unknown_labels WHERE unknown_id = ?",
            (unknown_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError("unknown label not found")
        label_type = _safe_slug(str(row["label_type"] or "unknown"))
        proposed = _safe_slug(
            canonical_label
            or row["proposed_subtype"]
            or row["proposed_type"]
            or row["proposed_domain"]
            or row["raw_description"]
        )
        if not proposed:
            raise ValueError("canonical label is required")
        await conn.execute(
            """
            INSERT OR IGNORE INTO behavior_vocabulary_labels
            (label_id, label_type, canonical_label, domain, parent_label, raw_description, status, created_at, updated_at)
            VALUES (?, ?, ?, '', '', ?, 'active', ?, ?)
            """,
            (f"{label_type}:{proposed}", label_type, proposed, str(row["raw_description"] or ""), _now_iso(), _now_iso()),
        )
        alias_source = _safe_slug(alias_label or str(row["raw_description"] or ""))
        if alias_source:
            await conn.execute(
                """
                INSERT OR REPLACE INTO behavior_vocabulary_aliases
                (alias_id, label_type, canonical_label, alias_label, created_at, vocabulary_version)
                VALUES (?, ?, ?, ?, COALESCE((SELECT created_at FROM behavior_vocabulary_aliases WHERE alias_id = ?), ?), ?)
                """,
                (
                    f"alias:{label_type}:{alias_source}",
                    label_type,
                    proposed,
                    alias_source,
                    f"alias:{label_type}:{alias_source}",
                    _now_iso(),
                    _VOCABULARY_VERSION,
                ),
            )
        await conn.execute(
            "UPDATE behavior_unknown_labels SET status = 'promoted', updated_at = ? WHERE unknown_id = ?",
            (_now_iso(), unknown_id),
        )
        await conn.commit()
    return {
        "unknown_id": unknown_id,
        "label_type": label_type,
        "canonical_label": proposed,
        "alias_label": alias_source,
    }


async def dismiss_unknown_label(unknown_id: str) -> bool:
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT unknown_id FROM behavior_unknown_labels WHERE unknown_id = ?",
            (unknown_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return False
        await conn.execute(
            "UPDATE behavior_unknown_labels SET status = 'dismissed', updated_at = ? WHERE unknown_id = ?",
            (_now_iso(), unknown_id),
        )
        await conn.commit()
    return True


def _clone_json_value(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _path_parts(target_path: str) -> list[str | int]:
    parts: list[str | int] = []
    for raw in str(target_path or "").split("."):
        raw = raw.strip()
        if not raw:
            continue
        parts.append(int(raw) if raw.isdigit() else raw)
    return parts


def _walk_to_parent(root: Any, parts: list[str | int], *, create: bool = False) -> tuple[Any, str | int | None]:
    if not parts:
        return root, None
    current = root
    for index, part in enumerate(parts[:-1]):
        next_part = parts[index + 1]
        if isinstance(part, int):
            if not isinstance(current, list):
                raise KeyError(f"Path segment {part} requires list container")
            while create and part >= len(current):
                current.append({} if not isinstance(next_part, int) else [])
            current = current[part]
            continue
        if not isinstance(current, dict):
            raise KeyError(f"Path segment {part} requires dict container")
        if part not in current or current[part] is None:
            if not create:
                raise KeyError(part)
            current[part] = [] if isinstance(next_part, int) else {}
        current = current[part]
    return current, parts[-1]


def _set_path_value(root: Any, target_path: str, value: Any, *, create: bool = True) -> None:
    parent, leaf = _walk_to_parent(root, _path_parts(target_path), create=create)
    if leaf is None:
        raise KeyError("empty target path")
    if isinstance(leaf, int):
        if not isinstance(parent, list):
            raise KeyError(f"Leaf {leaf} requires list container")
        while create and leaf >= len(parent):
            parent.append(None)
        parent[leaf] = value
        return
    if not isinstance(parent, dict):
        raise KeyError(f"Leaf {leaf} requires dict container")
    parent[leaf] = value


def _remove_path_value(root: Any, target_path: str) -> None:
    parent, leaf = _walk_to_parent(root, _path_parts(target_path), create=False)
    if leaf is None:
        raise KeyError("empty target path")
    if isinstance(leaf, int):
        if not isinstance(parent, list):
            raise KeyError(f"Leaf {leaf} requires list container")
        parent.pop(leaf)
        return
    if not isinstance(parent, dict):
        raise KeyError(f"Leaf {leaf} requires dict container")
    parent.pop(leaf, None)


def _build_patch_change_list(skill: dict[str, Any], patch_type: str, reason: str, extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    extra = extra or {}
    if patch_type == "update_trigger":
        current_score = float((skill.get("trigger") or {}).get("min_match_score") or _ROUTER_JUDGE_THRESHOLD)
        next_score = round(min(0.95, current_score + 0.05), 2)
        if next_score != current_score:
            return [
                {
                    "operation": "replace",
                    "target_path": "trigger.min_match_score",
                    "old_value": current_score,
                    "new_value": next_score,
                }
            ]
    if patch_type == "update_input_schema":
        current_policy = str((skill.get("fallback_policy") or {}).get("on_missing_args") or "fallback_to_agent")
        if current_policy != "ask_user":
            return [
                {
                    "operation": "replace",
                    "target_path": "fallback_policy.on_missing_args",
                    "old_value": current_policy,
                    "new_value": "ask_user",
                }
            ]
    if patch_type == "replace_step":
        failing_tool = ""
        match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)", reason or "")
        if match:
            failing_tool = match.group(1)
        for index, step in enumerate(skill.get("steps") or []):
            reference = step.get("implementation_reference") or {}
            if failing_tool and str(reference.get("tool_name") or "") != failing_tool:
                continue
            current_policy = str(step.get("failure_policy") or "fail")
            if current_policy != "fallback_to_agent":
                return [
                    {
                        "operation": "replace",
                        "target_path": f"steps.{index}.failure_policy",
                        "old_value": current_policy,
                        "new_value": "fallback_to_agent",
                    }
                ]
            break
    return extra.get("change_list") or []


def _apply_change_list(definition: dict[str, Any], change_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    applied: list[dict[str, Any]] = []
    for change in change_list:
        operation = str(change.get("operation") or "replace")
        target_path = str(change.get("target_path") or "").strip()
        if not target_path:
            continue
        if operation in {"add", "replace", "enable", "disable"}:
            new_value = change.get("new_value")
            if operation == "enable":
                new_value = True
            elif operation == "disable":
                new_value = False
            _set_path_value(definition, target_path, _clone_json_value(new_value), create=True)
        elif operation == "remove":
            _remove_path_value(definition, target_path)
        else:
            continue
        applied.append(change)
    return applied


async def _sanitize_skill_definition(definition: dict[str, Any]) -> dict[str, Any]:
    sanitized = _clone_json_value(definition)
    if isinstance(sanitized.get("trigger"), dict):
        base_fp = (sanitized["trigger"] or {}).get("base_fingerprint")
        if isinstance(base_fp, dict):
            sanitized["trigger"]["base_fingerprint"] = await normalize_fingerprint(base_fp)
    if isinstance(sanitized.get("input_schema"), list):
        sanitized["input_schema"] = [
            _normalize_slot(item)
            for item in sanitized["input_schema"]
            if isinstance(item, dict)
        ]
    for key in ("parameter_extractor", "guards", "fallback_policy", "created_from", "run_statistics"):
        if not isinstance(sanitized.get(key), dict):
            sanitized[key] = {}
    for key in ("steps", "tests", "editable_fields"):
        if not isinstance(sanitized.get(key), list):
            sanitized[key] = []
    # Re-infer risk level from actual steps — high-risk tools cannot be silently downgraded.
    # This ensures the stored risk_level stays accurate even after manual step edits.
    if _infer_skill_risk_level(sanitized.get("steps") or []) == "high":
        sanitized["risk_level"] = "high"
        if isinstance(sanitized.get("guards"), dict):
            sanitized["guards"]["risk_level"] = "high"
    return sanitized


async def _persist_skill_version(
    conn: aiosqlite.Connection,
    *,
    skill_id: str,
    current_row: sqlite3.Row,
    definition: dict[str, Any],
    change_type: str,
    change_summary: str,
    patch_list: list[dict[str, Any]] | None = None,
    test_result: dict[str, Any] | None = None,
    rollback_target: int | None = None,
) -> dict[str, Any]:
    now = _now_iso()
    next_version = int(current_row["current_version"]) + 1
    persisted = {
        "skill_id": skill_id,
        **definition,
        "version": next_version,
        "pattern_id": definition.get("pattern_id") or str(current_row["pattern_id"] or ""),
        "created_at": definition.get("created_at") or str(current_row["created_at"] or now),
        "updated_at": now,
        "run_statistics": definition.get("run_statistics") or _json_loads(
            current_row["run_statistics_json"], _default_skill_stats()
        ),
    }
    script = _clone_json_value(persisted.get("script") or {})
    if str(script.get("format") or "") != "cyrene.parameterized-tool-script":
        script = {
            "format": "cyrene.parameterized-tool-script",
            "execution": {"stop_on_failure": True, "record_run": True, "suppress_relearning": True},
            "source_turn_ids": (persisted.get("created_from") or {}).get("turn_list") or [],
        }
    script.update({
        "version": next_version,
        "name": str(persisted.get("name") or ""),
        "description": str(persisted.get("description") or ""),
        "parameters": persisted.get("input_schema") or [],
        "steps": persisted.get("steps") or [],
        "risk": {
            "level": str(persisted.get("risk_level") or "none"),
            "requires_runtime_approval": str(persisted.get("risk_level") or "none") == "high",
        },
    })
    persisted["script"] = script
    await conn.execute(
        """
        UPDATE learned_skills
        SET name = ?, description = ?, current_version = ?, status = ?, skill_type = ?, risk_level = ?,
            requires_llm = ?, trigger_json = ?, input_schema_json = ?, parameter_extractor_json = ?,
            steps_json = ?, script_json = ?, guards_json = ?, fallback_policy_json = ?, tests_json = ?, editable_fields_json = ?,
            created_from_json = ?, run_statistics_json = ?, updated_at = ?
        WHERE skill_id = ?
        """,
        (
            str(persisted.get("name") or ""),
            str(persisted.get("description") or ""),
            next_version,
            str(persisted.get("status") or "draft"),
            str(persisted.get("skill_type") or "draft"),
            str(persisted.get("risk_level") or "none"),
            1 if bool(persisted.get("requires_llm")) else 0,
            _json_dumps(persisted.get("trigger") or {}),
            _json_dumps(persisted.get("input_schema") or []),
            _json_dumps(persisted.get("parameter_extractor") or {}),
            _json_dumps(persisted.get("steps") or []),
            _json_dumps(script),
            _json_dumps(persisted.get("guards") or {}),
            _json_dumps(persisted.get("fallback_policy") or {}),
            _json_dumps(persisted.get("tests") or []),
            _json_dumps(persisted.get("editable_fields") or []),
            _json_dumps(persisted.get("created_from") or {}),
            _json_dumps(persisted.get("run_statistics") or _default_skill_stats()),
            now,
            skill_id,
        ),
    )
    await _save_skill_version(
        conn=conn,
        skill_id=skill_id,
        version=next_version,
        parent_version=int(current_row["current_version"]),
        definition=persisted,
        change_type=change_type,
        change_summary=change_summary,
        patch_list=patch_list,
        test_result=test_result,
        rollback_target=rollback_target,
    )
    return persisted


def _extract_with_rules(user_message: str, schema_item: dict[str, Any]) -> tuple[Any, float]:
    text = str(user_message or "")
    aliases = [str(item).lower() for item in (schema_item.get("aliases") or []) if str(item).strip()]
    schema_type = str(schema_item.get("type") or "text")
    examples = [str(item) for item in (schema_item.get("examples") or [])]
    if examples:
        for example in examples:
            if example and example in text:
                return example, 0.95
    if schema_type in {"path", "file", "filepath"} or any(alias in {"path", "file", "file_path"} for alias in aliases):
        match = re.search(r"(~?/?[A-Za-z0-9_.-][A-Za-z0-9_./-]*\.[A-Za-z0-9]{1,8}|~?/?[A-Za-z0-9_.-][A-Za-z0-9_./-]*/[A-Za-z0-9_./-]+)", text)
        if match:
            return match.group(1), 0.85
    if schema_type in {"number", "int", "float"}:
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if match:
            raw = match.group(0)
            return (float(raw) if "." in raw else int(raw)), 0.80
    if schema_type == "date":
        match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", text)
        if match:
            return match.group(0), 0.90
    if schema_type == "url":
        match = re.search(r"https?://\S+", text)
        if match:
            return match.group(0), 0.90
    quoted = re.findall(r'"([^"]+)"|\'([^\']+)\'', text)
    if quoted:
        first = next((item[0] or item[1] for item in quoted if item[0] or item[1]), "")
        if first:
            return first, 0.65
    return None, 0.0


async def _extract_with_llm(
    *,
    user_message: str,
    context_summary: str,
    input_schema: list[dict[str, Any]],
    partial_params: dict[str, Any],
) -> dict[str, Any]:
    prompt = f"""Extract parameters for a learned automation skill.

Return JSON only:
{{"params": {{"name": "value"}}}}

User message:
{user_message}

Context summary:
{context_summary}

Input schema:
{json.dumps(input_schema, ensure_ascii=False, indent=2)}

Already extracted params:
{json.dumps(partial_params, ensure_ascii=False, indent=2)}
"""
    result = await _call_llm_json(prompt, caller="skill_param_extractor")
    params = result.get("params")
    return params if isinstance(params, dict) else {}


async def extract_skill_parameters(
    *,
    user_message: str,
    context_summary: str,
    input_schema: list[dict[str, Any]],
    llm_fallback: bool = True,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    confidence_scores: list[float] = []
    overrides = overrides or {}
    for item in input_schema:
        name = str(item.get("parameter_name") or item.get("name") or "").strip()
        if not name:
            continue
        if name in overrides:
            params[name] = overrides[name]
            confidence_scores.append(1.0)
            continue
        value, score = _extract_with_rules(user_message, item)
        if value is not None:
            params[name] = value
            confidence_scores.append(score)
            continue
        default_value = item.get("default_value")
        if default_value not in (None, "") and not item.get("required", False):
            params[name] = default_value
            confidence_scores.append(0.55)
    missing_required = [
        str(item.get("parameter_name") or item.get("name") or "")
        for item in input_schema
        if bool(item.get("required", False))
        and str(item.get("parameter_name") or item.get("name") or "")
        and str(item.get("parameter_name") or item.get("name") or "") not in params
    ]
    if missing_required and llm_fallback:
        llm_params = await _extract_with_llm(
            user_message=user_message,
            context_summary=context_summary,
            input_schema=input_schema,
            partial_params=params,
        )
        for key, value in llm_params.items():
            if key not in params and value not in (None, ""):
                params[key] = value
                confidence_scores.append(0.70)
        missing_required = [item for item in missing_required if item not in params]
    confidence = round(sum(confidence_scores) / len(confidence_scores), 4) if confidence_scores else 0.0
    return {
        "params": params,
        "missing_required": missing_required,
        "complete": not missing_required,
        "confidence": confidence,
    }


def _resolve_value_template(value: Any, params: dict[str, Any]) -> Any:
    if isinstance(value, str):
        resolved = value
        for key, param in params.items():
            resolved = resolved.replace(f"{{{{{key}}}}}", str(param))
        return resolved
    if isinstance(value, list):
        return [_resolve_value_template(item, params) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_value_template(item, params) for key, item in value.items()}
    return value


async def _llm_confirm_skill_match(request_fp: dict[str, Any], skill: dict[str, Any], similarity: dict[str, Any]) -> bool:
    prompt = f"""Decide whether a learned automation skill should handle a new request.

Return JSON only:
{{"should_use": true|false, "confidence": 0-1, "reason": "..."}}

Request fingerprint:
{json.dumps(request_fp, ensure_ascii=False, indent=2)}

Skill definition:
{json.dumps({"name": skill["name"], "trigger": skill["trigger"], "steps": skill["steps"]}, ensure_ascii=False, indent=2)}

Similarity:
{json.dumps(similarity, ensure_ascii=False, indent=2)}
"""
    result = await _call_llm_json(prompt, caller="skill_match_judge")
    return bool(result.get("should_use"))


async def match_active_skill(user_message: str, history: list[dict[str, Any]]) -> dict[str, Any] | None:
    scope = _project_scope_for_session(_current_session_id.get())
    async with _conn() as conn:
        cursor = await conn.execute(
            """
            SELECT *
            FROM learned_skills
            WHERE status = 'active' AND project_id = ?
            ORDER BY updated_at DESC
            """,
            (scope["project_id"],),
        )
        rows = await cursor.fetchall()
    if not rows:
        return None
    request_fp = await build_request_fingerprint(user_message, history)
    best: dict[str, Any] | None = None
    for skill in _dedupe_skill_definitions(
        [
            definition
            for definition in (_skill_row_to_definition(row) for row in rows)
            if _is_reusable_skill_definition(definition)
        ]
    ):
        skill_steps = skill.get("steps", [])
        if (
            str(skill.get("risk_level") or "none") == "high"
            or any(tool in _HIGH_RISK_TOOLS for tool in _enabled_step_tool_names(skill_steps))
            or _has_auto_replay_blocked_step(skill_steps)
        ):
            continue
        trigger = skill["trigger"]
        base_fp = trigger.get("base_fingerprint") or {}
        similarity = compute_fingerprint_similarity(request_fp, base_fp)
        min_score = float(trigger.get("min_match_score") or _ROUTER_JUDGE_THRESHOLD)
        if similarity["hard_fail"]:
            continue
        if best is None or float(similarity["total"]) > float(best["similarity"]["total"]):
            best = {
                "skill": skill,
                "request_fingerprint": request_fp,
                "similarity": similarity,
                "min_score": min_score,
            }
    if best is None:
        return None
    total = float(best["similarity"]["total"])
    if total >= max(_ROUTER_AUTO_THRESHOLD, best["min_score"]):
        return best
    if total >= max(_ROUTER_JUDGE_THRESHOLD, best["min_score"]):
        if await _llm_confirm_skill_match(best["request_fingerprint"], best["skill"], best["similarity"]):
            return best
    return None


def _skill_assistant_content(skill_name: str, lang: str) -> str:
    """返回已本地化的技能执行提示文案。"""
    is_zh = lang.lower().startswith("zh") or bool(lang) and any("一" <= c <= "鿿" for c in lang)
    if is_zh:
        return f"正在使用已学习的技能 `{skill_name}`。"
    return f"Using learned skill `{skill_name}`."


async def try_route_and_execute_skill(
    *,
    user_message: str,
    visible_user_entry: dict[str, Any],
    llm_user_entry: dict[str, Any],
    history: list[dict[str, Any]],
    bot: Any,
    chat_id: int,
    db_path: str,
    effective_system: str,
    client_request_id: str,
    round_id: str,
    lang: str = "",
) -> dict[str, Any] | None:
    match = await match_active_skill(user_message, history)
    if match is None:
        return None
    skill = match["skill"]
    similarity = match["similarity"]
    input_schema = skill["input_schema"]
    current_turn = _current_turn_id.get()
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT context_summary FROM behavior_turns WHERE turn_id = ?",
            (current_turn,),
        )
        turn_row = await cursor.fetchone()
    context_summary = str(turn_row["context_summary"] or "") if turn_row else ""
    extraction = await extract_skill_parameters(
        user_message=user_message,
        context_summary=context_summary,
        input_schema=input_schema,
        llm_fallback=bool((skill.get("parameter_extractor") or {}).get("llm_fallback", True)),
    )
    if not extraction["complete"]:
        from cyrene.settings_store import get_write_permission_mode as _get_perm_mode
        run_id = _new_id("skill_run")
        async with _conn() as conn:
            await conn.execute(
                """
                INSERT INTO learned_skill_runs
                (run_id, skill_id, version, turn_id, match_score, parameter_status, execution_status, failure_reason,
                 fallback_used, user_feedback, dry_run, consistency_score, permission_snapshot, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'fallback', ?, 1, '', 0, 0, ?, ?)
                """,
                (
                    run_id,
                    skill["skill_id"],
                    skill["version"],
                    current_turn,
                    float(similarity["total"]),
                    "missing_required",
                    f"missing parameters: {', '.join(extraction['missing_required'])}",
                    _get_perm_mode(),
                    _now_iso(),
                ),
            )
            await conn.commit()
        await _update_skill_run_stats(skill["skill_id"], execution_status="fallback")
        await _maybe_propose_patch(skill["skill_id"], int(skill["version"]), "missing_required_parameters")
        return None
    params = extraction["params"]

    # Require fresh user approval for any skill that contains high-risk steps.
    # Returning None lets the normal agent loop handle execution with its own
    # workspace-scope guard and permission prompts.
    skill_risk = str(skill.get("risk_level") or "none")
    skill_steps = skill.get("steps", [])
    has_risky_step = any(tool in _HIGH_RISK_TOOLS for tool in _enabled_step_tool_names(skill_steps))
    has_blocked_step = _has_auto_replay_blocked_step(skill_steps)
    if skill_risk == "high" or has_risky_step or has_blocked_step:
        logger.info(
            "Skill %s contains non-replayable steps; falling back to agent.",
            skill["skill_id"],
        )
        await _update_skill_run_stats(skill["skill_id"], execution_status="fallback")
        return None

    await mark_turn_skill_routed(skill["skill_id"])
    from cyrene.settings_store import get_write_permission_mode as _get_perm_mode
    from cyrene.agent.guidance import _final_user_reply_from_history
    from cyrene.agent.message import _apply_assistant_meta
    from cyrene.tools import _execute_tool

    permission_snapshot = _get_perm_mode()

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": effective_system},
        *history,
        dict(llm_user_entry),
    ]
    tool_calls: list[dict[str, Any]] = []
    planned_calls: list[dict[str, Any]] = []
    for step in skill["steps"]:
        if not bool(step.get("enabled", True)):
            continue
        reference = step.get("implementation_reference") or {}
        implementation_kind = str(step.get("implementation_kind") or "")
        if implementation_kind == "script":
            call_id = _new_id("tc")
            call_args = {
                "script_path": str(reference.get("script_path") or ""),
                "params": params,
            }
            tool_calls.append({
                "id": call_id,
                "type": "function",
                "function": {"name": "run_generated_skill_script", "arguments": _json_dumps(call_args)},
            })
            planned_calls.append({
                "id": call_id,
                "kind": "script",
                "reference": reference,
                "tool_name": "run_generated_skill_script",
                "args": call_args,
            })
            continue
        if implementation_kind != "tool_call":
            continue
        tool_name = str(reference.get("tool_name") or "")
        args_template = reference.get("args_template") or {}
        items = args_template.get("_items")
        if isinstance(items, list) and items:
            # Aggregated multi-arg step — expand into one tool_call per item
            for item_args in items:
                resolved = _resolve_value_template(item_args, params)
                call_id = _new_id("tc")
                tool_calls.append({
                    "id": call_id,
                    "type": "function",
                    "function": {"name": tool_name, "arguments": _json_dumps(resolved)},
                })
                planned_calls.append({
                    "id": call_id,
                    "kind": "tool_call",
                    "tool_name": tool_name,
                    "args": resolved,
                })
        else:
            call_id = _new_id("tc")
            resolved_args = _resolve_value_template(args_template, params)
            tool_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": _json_dumps(resolved_args),
                    },
                }
            )
            planned_calls.append({
                "id": call_id,
                "kind": "tool_call",
                "tool_name": tool_name,
                "args": resolved_args,
            })
    assistant_entry = {
        "role": "assistant",
        "content": _skill_assistant_content(skill["name"], lang),
        "tool_calls": tool_calls,
    }
    if round_id:
        assistant_entry["round_id"] = round_id
    messages.append(_apply_assistant_meta(assistant_entry))
    for planned in planned_calls:
        call = next((item for item in tool_calls if item["id"] == planned["id"]), None)
        if call is None:
            continue
        tool_name = str(planned.get("tool_name") or "run_generated_skill_script")
        try:
            if planned.get("kind") == "script":
                result, tool_success, failure_reason = await _execute_script_step(planned.get("reference") or {}, params)
            else:
                resolved_args = planned.get("args") or {}
                result = await _execute_tool(tool_name, resolved_args, bot, chat_id, db_path, None)
                tool_success = not str(result).lower().startswith("tool failed:")
                failure_reason = "" if tool_success else str(result)
        except Exception as exc:
            result = f"Tool failed: {exc}"
            tool_success = False
            failure_reason = str(exc)
        tool_entry = {"role": "tool", "tool_call_id": call["id"], "content": _truncate_text(result, 6000)}
        if round_id:
            tool_entry["round_id"] = round_id
        messages.append(tool_entry)
        if not tool_success:
            run_id = _new_id("skill_run")
            async with _conn() as conn:
                await conn.execute(
                    """
                    INSERT INTO learned_skill_runs
                    (run_id, skill_id, version, turn_id, match_score, parameter_status, execution_status, failure_reason,
                     fallback_used, user_feedback, dry_run, consistency_score, permission_snapshot, created_at)
                    VALUES (?, ?, ?, ?, ?, 'complete', 'failure', ?, 1, '', 0, 0, ?, ?)
                    """,
                    (
                        run_id,
                        skill["skill_id"],
                        skill["version"],
                        current_turn,
                        float(similarity["total"]),
                        failure_reason or f"{tool_name} failed",
                        permission_snapshot,
                        _now_iso(),
                    ),
                )
                await conn.commit()
            await _update_skill_run_stats(skill["skill_id"], execution_status="failure")
            await _maybe_propose_patch(skill["skill_id"], int(skill["version"]), failure_reason or f"{tool_name}_failed")
            return None
    final_text = await _final_user_reply_from_history(messages, max_tokens=None)
    final_entry = {"role": "assistant", "content": final_text}
    if client_request_id:
        final_entry["client_request_id"] = client_request_id
    if round_id:
        final_entry["round_id"] = round_id
    messages.append(_apply_assistant_meta(final_entry))
    run_id = _new_id("skill_run")
    async with _conn() as conn:
        await conn.execute(
            """
            INSERT INTO learned_skill_runs
            (run_id, skill_id, version, turn_id, match_score, parameter_status, execution_status, failure_reason,
             fallback_used, user_feedback, dry_run, consistency_score, permission_snapshot, created_at)
            VALUES (?, ?, ?, ?, ?, 'complete', 'success', '', 0, '', 0, ?, ?, ?)
            """,
            (
                run_id,
                skill["skill_id"],
                skill["version"],
                current_turn,
                float(similarity["total"]),
                round(extraction["confidence"], 4),
                permission_snapshot,
                _now_iso(),
            ),
        )
        await conn.commit()
    await _update_skill_run_stats(skill["skill_id"], execution_status="success", consistency_score=round(extraction["confidence"], 4))
    return {
        "skill": skill,
        "messages": messages,
        "final_text": final_text,
        "match_score": similarity["total"],
    }


async def _validate_shadow_skill_for_turn(
    skill: dict[str, Any],
    turn_row: dict[str, Any],
    fingerprint: dict[str, Any],
    *,
    promote: bool = True,
) -> None:
    trigger = skill["trigger"]
    similarity = compute_fingerprint_similarity(fingerprint, trigger.get("base_fingerprint") or {})
    if similarity["hard_fail"] or float(similarity["total"]) < max(_ROUTER_JUDGE_THRESHOLD, float(trigger.get("min_match_score") or 0.0)):
        return
    step_actions = []
    for step in _tool_call_steps_for_replay(skill["steps"]):
        if not bool(step.get("enabled", True)):
            continue
        step_actions.append(
            {
                "domain": str((trigger.get("base_fingerprint") or {}).get("domain") or "state_management"),
                "type": str(step.get("type") or "call_tool"),
                "subtype": str(step.get("subtype") or "unknown"),
                "raw_description": str(step.get("description") or ""),
            }
        )
    consistency = _lcs_similarity(step_actions, fingerprint.get("action_sequence") or [])
    extraction = await extract_skill_parameters(
        user_message=str(turn_row.get("user_message") or ""),
        context_summary=str(turn_row.get("context_summary") or ""),
        input_schema=skill["input_schema"],
        llm_fallback=bool((skill.get("parameter_extractor") or {}).get("llm_fallback", True)),
    )
    success = extraction["complete"] and (
        consistency >= _SHADOW_CONSISTENCY_THRESHOLD
        or (
            float(similarity["total"]) >= _PATTERN_STRONG_THRESHOLD
            and consistency >= 0.50
        )
    )
    run_id = _new_id("skill_run")
    async with _conn() as conn:
        await conn.execute(
            """
            INSERT INTO learned_skill_runs
            (run_id, skill_id, version, turn_id, match_score, parameter_status, execution_status, failure_reason,
             fallback_used, user_feedback, dry_run, consistency_score, permission_snapshot, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, '', 1, ?, 'workspace_only', ?)
            """,
            (
                run_id,
                skill["skill_id"],
                skill["version"],
                str(turn_row["turn_id"]),
                float(similarity["total"]),
                "complete" if extraction["complete"] else "missing_required",
                "shadow_success" if success else "shadow_failure",
                "" if success else "shadow_validation_failed",
                round(consistency, 4),
                _now_iso(),
            ),
        )
        await conn.commit()
    await _update_skill_run_stats(
        skill["skill_id"],
        execution_status="shadow_success" if success else "shadow_failure",
        consistency_score=round(consistency, 4),
        promote=promote,
    )


async def _validate_shadow_skills_for_turn(turn_id: str, fingerprint: dict[str, Any]) -> None:
    scope = await _project_scope_for_turn(turn_id)
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT * FROM learned_skills WHERE status = 'shadow' AND project_id = ? ORDER BY updated_at DESC",
            (scope["project_id"],),
        )
        rows = await cursor.fetchall()
        cursor = await conn.execute(
            "SELECT * FROM behavior_turns WHERE turn_id = ?",
            (turn_id,),
        )
        turn_row = await cursor.fetchone()
    if turn_row is None:
        return
    for row in rows:
        skill = _skill_row_to_definition(row)
        await _validate_shadow_skill_for_turn(skill, dict(turn_row), fingerprint)


async def _backfill_shadow_validation(skill_id: str, *, exclude_turn_id: str = "") -> None:
    skill = await get_learned_skill(skill_id)
    if skill is None or str(skill.get("status") or "") != "shadow":
        return
    turn_ids = list((skill.get("created_from") or {}).get("turn_list") or [])
    if not turn_ids and skill.get("pattern_id"):
        turn_ids = await _member_turn_ids(str(skill["pattern_id"]))
    for turn_id in turn_ids:
        if str(turn_id) == str(exclude_turn_id or ""):
            continue
        async with _conn() as conn:
            cursor = await conn.execute(
                """
                SELECT 1
                FROM learned_skill_runs
                WHERE skill_id = ? AND version = ? AND turn_id = ? AND dry_run = 1
                LIMIT 1
                """,
                (skill_id, int(skill["version"]), str(turn_id)),
            )
            existing = await cursor.fetchone()
            cursor = await conn.execute(
                "SELECT * FROM behavior_turns WHERE turn_id = ?",
                (str(turn_id),),
            )
            turn_row = await cursor.fetchone()
        if existing is not None or turn_row is None:
            continue
        fingerprint = await _fingerprint_for_turn(str(turn_id))
        if not fingerprint:
            continue
        # Replay all available history before promotion.  Promoting inside the
        # loop made the result depend on row order and skipped later evidence.
        await _validate_shadow_skill_for_turn(
            skill,
            dict(turn_row),
            fingerprint,
            promote=False,
        )
        skill = await get_learned_skill(skill_id)
        if skill is None:
            return
    await _update_shadow_promotion(skill_id)


async def _replay_tests_for_skill(skill_id: str) -> list[dict[str, Any]]:
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT * FROM behavior_replay_tests WHERE skill_id = ? ORDER BY created_at ASC",
            (skill_id,),
        )
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def _run_replay_tests(skill_id: str) -> dict[str, Any]:
    skill = await get_learned_skill(skill_id)
    if skill is None:
        return {"passed": 0, "total": 0, "pass_rate": 0.0}
    tests = await _replay_tests_for_skill(skill_id)
    passed = 0
    total = 0
    now = _now_iso()
    for test in tests:
        total += 1
        turn_id = str(test.get("turn_id") or "")
        async with _conn() as conn:
            cursor = await conn.execute(
                "SELECT user_message, context_summary FROM behavior_turns WHERE turn_id = ?", (turn_id,)
            )
            turn_row = await cursor.fetchone()
        if turn_row is None:
            continue
        request_fp = await build_request_fingerprint(str(turn_row["user_message"]), [{"role": "system", "content": str(turn_row["context_summary"])}])
        similarity = compute_fingerprint_similarity(request_fp, (skill["trigger"] or {}).get("base_fingerprint") or {})
        ok = not similarity["hard_fail"] and float(similarity["total"]) >= _ROUTER_JUDGE_THRESHOLD
        if ok:
            passed += 1
        async with _conn() as conn:
            await conn.execute(
                "UPDATE behavior_replay_tests SET last_result = ?, updated_at = ? WHERE test_id = ?",
                (_json_dumps({"ok": ok, "similarity": similarity}), now, test["test_id"]),
            )
            await conn.commit()
    return {
        "passed": passed,
        "total": total,
        "pass_rate": round((passed / total) if total else 0.0, 4),
    }


async def run_skill_replay_tests(skill_id: str) -> dict[str, Any]:
    return await _run_replay_tests(skill_id)


async def update_learned_skill(
    skill_id: str,
    updates: dict[str, Any],
    *,
    reason: str = "Manual skill edit.",
) -> dict[str, Any] | None:
    if not isinstance(updates, dict):
        return None
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT * FROM learned_skills WHERE skill_id = ?", (skill_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        current = _skill_row_to_definition(row)
        definition = _clone_json_value(current)
        allowed_fields = {
            "name",
            "description",
            "status",
            "skill_type",
            "risk_level",
            "requires_llm",
            "trigger",
            "input_schema",
            "parameter_extractor",
            "steps",
            "guards",
            "fallback_policy",
            "editable_fields",
            "created_from",
        }
        changed_fields = {key for key in updates.keys() if key in allowed_fields}
        for field in changed_fields:
            definition[field] = _clone_json_value(updates[field])
        structural_fields = {
            "trigger",
            "input_schema",
            "parameter_extractor",
            "steps",
            "guards",
            "fallback_policy",
            "skill_type",
        }
        if structural_fields & changed_fields and "status" not in changed_fields:
            definition["status"] = "shadow"
        definition["pattern_id"] = current["pattern_id"]
        definition["created_at"] = current["created_at"]
        definition["run_statistics"] = current["run_statistics"]
        sanitized = await _sanitize_skill_definition(definition)
        valid_statuses = {"draft", "shadow", "active", "refined", "deprecated"}
        if str(sanitized.get("status") or "") not in valid_statuses:
            sanitized["status"] = current["status"]
        if str(sanitized.get("skill_type") or "") not in _SKILL_TYPE_ORDER:
            sanitized["skill_type"] = current["skill_type"]
        sanitized["requires_llm"] = bool(sanitized.get("requires_llm"))
        persisted = await _persist_skill_version(
            conn,
            skill_id=skill_id,
            current_row=row,
            definition=sanitized,
            change_type="manual_edit",
            change_summary=reason,
        )
        await conn.commit()
    replay_result = await _run_replay_tests(skill_id)
    return {
        **persisted,
        "test_result": replay_result,
    }


async def apply_skill_patch(skill_id: str, patch_id: str) -> dict[str, Any]:
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT * FROM learned_skills WHERE skill_id = ?", (skill_id,)
        )
        skill_row = await cursor.fetchone()
        cursor = await conn.execute(
            "SELECT * FROM learned_skill_patches WHERE skill_id = ? AND patch_id = ?",
            (skill_id, patch_id),
        )
        patch_row = await cursor.fetchone()
        if skill_row is None or patch_row is None:
            return {"ok": False, "error": "Skill or patch not found."}
        if str(patch_row["status"] or "") != "proposed":
            return {"ok": False, "error": "Patch is not in proposed state."}
        current = _skill_row_to_definition(skill_row)
        patch_content = _json_loads(patch_row["patch_content"], {})
        change_list = patch_content.get("change_list") or []
        if not change_list:
            change_list = _build_patch_change_list(
                current,
                str(patch_row["patch_type"] or ""),
                str(patch_row["reason"] or ""),
                patch_content,
            )
        if not change_list:
            return {"ok": False, "error": "Patch is advisory only and needs manual editing."}
        definition = _clone_json_value(current)
        applied_changes = _apply_change_list(definition, change_list)
        definition["status"] = "shadow"
        definition["pattern_id"] = current["pattern_id"]
        definition["created_at"] = current["created_at"]
        definition["run_statistics"] = current["run_statistics"]
        sanitized = await _sanitize_skill_definition(definition)
        persisted = await _persist_skill_version(
            conn,
            skill_id=skill_id,
            current_row=skill_row,
            definition=sanitized,
            change_type="apply_patch",
            change_summary=str(patch_row["reason"] or "Applied skill patch."),
            patch_list=applied_changes,
        )
        await conn.execute(
            "UPDATE learned_skill_patches SET status = 'applied' WHERE patch_id = ?",
            (patch_id,),
        )
        await conn.commit()
    replay_result = await _run_replay_tests(skill_id)
    return {
        "ok": True,
        "skill": persisted,
        "patch_id": patch_id,
        "applied_changes": applied_changes,
        "test_result": replay_result,
    }


async def reject_skill_patch(skill_id: str, patch_id: str) -> bool:
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT patch_id FROM learned_skill_patches WHERE skill_id = ? AND patch_id = ?",
            (skill_id, patch_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return False
        await conn.execute(
            "UPDATE learned_skill_patches SET status = 'rejected' WHERE patch_id = ?",
            (patch_id,),
        )
        await conn.commit()
    return True


async def rollback_learned_skill(skill_id: str, rollback_version: int) -> dict[str, Any]:
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT * FROM learned_skills WHERE skill_id = ?", (skill_id,)
        )
        current_row = await cursor.fetchone()
        cursor = await conn.execute(
            """
            SELECT skill_definition
            FROM learned_skill_versions
            WHERE skill_id = ? AND version = ?
            """,
            (skill_id, int(rollback_version)),
        )
        version_row = await cursor.fetchone()
        if current_row is None or version_row is None:
            return {"ok": False, "error": "Skill or target version not found."}
        definition = _json_loads(version_row["skill_definition"], {})
        if not isinstance(definition, dict):
            return {"ok": False, "error": "Stored version is invalid."}
        definition["status"] = str(definition.get("status") or "shadow")
        definition["pattern_id"] = str(current_row["pattern_id"] or definition.get("pattern_id") or "")
        definition["created_at"] = str(current_row["created_at"] or definition.get("created_at") or _now_iso())
        definition["run_statistics"] = _json_loads(current_row["run_statistics_json"], _default_skill_stats())
        sanitized = await _sanitize_skill_definition(definition)
        persisted = await _persist_skill_version(
            conn,
            skill_id=skill_id,
            current_row=current_row,
            definition=sanitized,
            change_type="rollback",
            change_summary=f"Rolled back skill to version {rollback_version}.",
            rollback_target=int(rollback_version),
        )
        await conn.commit()
    replay_result = await _run_replay_tests(skill_id)
    return {
        "ok": True,
        "skill": persisted,
        "rollback_target": int(rollback_version),
        "test_result": replay_result,
    }


async def _promote_unknown_pool() -> None:
    async with _conn() as conn:
        cursor = await conn.execute(
            """
            SELECT *
            FROM behavior_unknown_labels
            WHERE status = 'open' AND seen_count >= 3
            ORDER BY seen_count DESC, updated_at DESC
            """
        )
        rows = await cursor.fetchall()
        now = _now_iso()
        for row in rows:
            label_type = str(row["label_type"] or "")
            raw = str(row["raw_description"] or "")
            proposed = _safe_slug(
                row["proposed_subtype"] or row["proposed_type"] or row["proposed_domain"] or raw
            )
            if not proposed:
                continue
            await conn.execute(
                """
                INSERT OR IGNORE INTO behavior_vocabulary_aliases
                (alias_id, label_type, canonical_label, alias_label, created_at, vocabulary_version)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    _new_id("alias"),
                    label_type,
                    proposed,
                    _safe_slug(raw),
                    now,
                    _VOCABULARY_VERSION,
                ),
            )
            await conn.execute(
                "UPDATE behavior_unknown_labels SET status = 'promoted', updated_at = ? WHERE unknown_id = ?",
                (now, row["unknown_id"]),
            )
        await conn.commit()


async def _load_tool_chain_for_turn(turn_id: str) -> dict[str, Any]:
    rebuilt = await _rebuild_tool_chain_for_turn(turn_id)
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT * FROM behavior_turn_tool_chains WHERE turn_id = ?",
            (str(turn_id or ""),),
        )
        row = await cursor.fetchone()
    if row is None:
        return {
            "chain_id": "",
            "turn_id": str(turn_id or ""),
            "chain": (rebuilt or {}).get("chain") or [],
            "summary": (rebuilt or {}).get("summary") or {},
        }
    item = dict(row)
    return {
        "chain_id": str(item.get("chain_id") or ""),
        "project_id": str(item.get("project_id") or ""),
        "project_key": str(item.get("project_key") or ""),
        "session_id": str(item.get("session_id") or ""),
        "session_kind": str(item.get("session_kind") or ""),
        "turn_id": str(item.get("turn_id") or ""),
        "round_id": str(item.get("round_id") or ""),
        "source": str(item.get("source") or ""),
        "chain": _json_loads(item.get("chain_json"), []),
        "summary": _json_loads(item.get("summary_json"), {}),
        "updated_at": str(item.get("updated_at") or ""),
    }


async def _pattern_summary_for_learning(pattern_id: str) -> dict[str, Any]:
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT * FROM behavior_patterns WHERE pattern_id = ?",
            (str(pattern_id or ""),),
        )
        row = await cursor.fetchone()
    if row is None:
        return {}
    item = dict(row)
    stats = _json_loads(item.get("statistics_json"), _default_pattern_stats())
    skillability = _json_loads(item.get("skillability_json"), {})
    linked = _json_loads(item.get("linked_skill_list"), [])
    return {
        "pattern_id": str(item.get("pattern_id") or ""),
        "project_id": str(item.get("project_id") or ""),
        "description": str(item.get("description") or ""),
        "status": str(item.get("status") or ""),
        "frequency": int(stats.get("frequency") or 0),
        "effective_count": float(stats.get("effective_count") or 0),
        "success_rate": float(stats.get("success_rate") or 0),
        "skillability": skillability,
        "linked_skill_list": linked,
        "prototype_fingerprint": _json_loads(item.get("prototype_fingerprint"), {}),
    }


async def _learning_similar_candidates(
    *,
    project_id: str,
    current_pattern_id: str,
    fingerprint: dict[str, Any],
    limit: int = 6,
) -> dict[str, list[dict[str, Any]]]:
    pattern_hits: list[dict[str, Any]] = []
    for pattern in await _read_patterns(project_id):
        pid = str(pattern.get("pattern_id") or "")
        if not pid or pid == current_pattern_id or str(pattern.get("status") or "") == "deprecated":
            continue
        prototype = pattern.get("prototype_fingerprint") or {}
        sim = compute_fingerprint_similarity(fingerprint, prototype)
        score = float(sim.get("total") or 0.0)
        if score < 0.40 or bool(sim.get("hard_fail")):
            continue
        stats = pattern.get("statistics") or {}
        pattern_hits.append({
            "pattern_id": pid,
            "description": str(pattern.get("description") or ""),
            "status": str(pattern.get("status") or ""),
            "similarity": round(score, 4),
            "frequency": int(stats.get("frequency") or 0),
            "effective_count": float(stats.get("effective_count") or 0),
            "linked_skill_list": pattern.get("linked_skill_list") or [],
            "breakdown": sim.get("breakdown") or {},
        })
    skill_hits: list[dict[str, Any]] = []
    for skill in await list_learned_skills(project_id):
        if str(skill.get("status") or "") == "deprecated":
            continue
        trigger = skill.get("trigger") or {}
        prototype = trigger.get("base_fingerprint") or {}
        if not prototype:
            continue
        sim = compute_fingerprint_similarity(fingerprint, prototype)
        score = float(sim.get("total") or 0.0)
        if score < 0.40 or bool(sim.get("hard_fail")):
            continue
        skill_hits.append({
            "skill_id": str(skill.get("id") or ""),
            "pattern_id": str(skill.get("pattern_id") or ""),
            "name": str(skill.get("name") or ""),
            "description": str(skill.get("description") or ""),
            "status": str(skill.get("status") or ""),
            "skill_type": str(skill.get("skill_type") or ""),
            "similarity": round(score, 4),
            "breakdown": sim.get("breakdown") or {},
        })
    pattern_hits.sort(key=lambda item: float(item.get("similarity") or 0), reverse=True)
    skill_hits.sort(key=lambda item: float(item.get("similarity") or 0), reverse=True)
    return {
        "patterns": pattern_hits[: max(1, limit)],
        "skills": skill_hits[: max(1, limit)],
    }


def _normalize_learning_decision(raw_decision: Any) -> str:
    decision = str(raw_decision or "").strip().lower()
    if decision in {"promote", "learn", "parameterize", "create_skill", "promote_candidate"}:
        return "promote"
    if decision in {"duplicate", "already_exists", "covered", "reuse_existing"}:
        return "duplicate"
    if decision in {"merge", "merge_candidate", "merge_pattern"}:
        return "merge"
    return "skip"


def _candidate_bucket_key(chain: list[dict[str, Any]]) -> str:
    """Return a cheap structural bucket for potentially repeated workflows."""
    runs: list[dict[str, Any]] = []
    for item in chain:
        tool = str(item.get("tool") or "")
        if not tool or tool in _INTERNAL_TOOLS or tool in _TRIVIAL_SKILL_TOOLS:
            continue
        args = item.get("args") if isinstance(item.get("args"), dict) else {}
        shape = {
            "tool": tool,
            "keys": sorted(str(key) for key in args.keys()),
            "families": {str(key): _arg_value_family(value) for key, value in sorted(args.items())},
        }
        if runs and runs[-1]["tool"] == tool and runs[-1]["keys"] == shape["keys"]:
            runs[-1]["repeated"] = True
        else:
            shape["repeated"] = False
            runs.append(shape)
    payload = _json_dumps(runs)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


async def _candidate_evidence_for_turn(turn_id: str) -> dict[str, Any] | None:
    async with _conn() as conn:
        cursor = await conn.execute("SELECT * FROM behavior_turns WHERE turn_id = ?", (turn_id,))
        row = await cursor.fetchone()
    if row is None or str(row["outcome_status"] or "") != "success":
        return None
    turn = dict(row)
    metadata = _json_loads(turn.get("metadata_json"), {})
    message = str(turn.get("user_message") or "").strip()
    if bool(metadata.get("system_initiated")) or any(message.startswith(prefix) for prefix in _INTERNAL_LEARNING_MESSAGE_PREFIXES):
        return None
    chain_record = await _load_tool_chain_for_turn(turn_id)
    meaningful = [
        item for item in (chain_record.get("chain") or [])
        if str(item.get("source") or "") == "agent"
        and str(item.get("tool") or "")
        and str(item.get("tool") or "") not in _INTERNAL_TOOLS
        and str(item.get("tool") or "") not in _TRIVIAL_SKILL_TOOLS
        and bool(item.get("success", True))
    ]
    tools = [str(item.get("tool") or "") for item in meaningful]
    if len(meaningful) < _MIN_SKILL_CHAIN_STEPS or len(set(tools)) < _MIN_SKILL_CHAIN_STEPS:
        return None
    return {
        "turn_id": turn_id,
        "project_id": str(turn.get("project_id") or ""),
        "project_key": str(turn.get("project_key") or ""),
        "user_message": message,
        "context_summary": str(turn.get("context_summary") or ""),
        "chain": meaningful,
        "bucket_key": _candidate_bucket_key(meaningful),
    }


async def _candidate_turn_examples(candidate_id: str) -> list[dict[str, Any]]:
    async with _conn() as conn:
        cursor = await conn.execute(
            """
            SELECT t.turn_id, t.user_message, t.context_summary
            FROM behavior_skill_candidate_turns ct
            JOIN behavior_turns t ON t.turn_id = ct.turn_id
            WHERE ct.candidate_id = ?
            ORDER BY ct.occurrence_index ASC
            """,
            (candidate_id,),
        )
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def _candidate_matches(candidate_id: str, evidence: dict[str, Any]) -> bool:
    examples = await _candidate_turn_examples(candidate_id)
    incoming = _normalize_whitespace(str(evidence.get("user_message") or "")).lower()
    if incoming and any(_normalize_whitespace(str(item.get("user_message") or "")).lower() == incoming for item in examples):
        return True
    prompt = f"""Decide whether a completed agent workflow belongs to the same reusable user workflow as the examples.

Return JSON only:
{{"same_workflow": true|false, "confidence": 0-1, "reason": "short reason"}}

Existing examples:
{json.dumps([item.get('user_message') for item in examples[:4]], ensure_ascii=False, indent=2)}

New user request:
{evidence.get('user_message', '')}

New tool chain:
{json.dumps([{"tool": item.get("tool"), "args": item.get("args") or {}} for item in evidence.get("chain") or []], ensure_ascii=False, indent=2)[:8000]}

Require the same user goal, not merely the same tools.
"""
    result = await _call_llm_json(prompt, caller="skill_candidate_matcher")
    return bool(result.get("same_workflow")) and float(result.get("confidence") or 0) >= 0.65


def _candidate_fallback_name(message: str) -> str:
    text = _normalize_whitespace(message)
    text = re.sub(r"\[[^\]]+\]", "", text).strip()
    return _sanitize_skill_name(text[:24] or "重复工具流程")


async def _build_candidate_script(candidate_id: str) -> dict[str, Any]:
    examples = await _candidate_turn_examples(candidate_id)
    turn_ids = [str(item.get("turn_id") or "") for item in examples]
    steps, input_schema = await _derive_parameter_templates(turn_ids)
    messages = [str(item.get("user_message") or "") for item in examples[:5]]
    prompt = f"""Name one reusable parameterized tool workflow.
Return JSON only: {{"name": "short Chinese name", "description": "one Chinese sentence"}}.
Do not mention internal tool names.

User requests:
{json.dumps(messages, ensure_ascii=False, indent=2)}
"""
    identity = await _call_llm_json(prompt, caller="skill_candidate_synthesizer")
    name = _sanitize_skill_name(str(identity.get("name") or _candidate_fallback_name(messages[0] if messages else "")))
    description = _sanitize_skill_description(str(identity.get("description") or (messages[0] if messages else "重复工具调用生成的参数化流程。")))
    risk_level = _infer_skill_risk_level(steps)
    return {
        "format": "cyrene.parameterized-tool-script",
        "version": 1,
        "name": name,
        "description": description,
        "parameters": input_schema,
        "steps": steps,
        "execution": {
            "stop_on_failure": True,
            "record_run": True,
            "suppress_relearning": True,
        },
        "risk": {
            "level": risk_level,
            "requires_runtime_approval": risk_level == "high",
        },
        "source_turn_ids": turn_ids[:_MAX_PATTERN_EXAMPLES],
    }


async def _refresh_candidate_script(candidate_id: str) -> dict[str, Any]:
    script = await _build_candidate_script(candidate_id)
    async with _conn() as conn:
        await conn.execute(
            """
            UPDATE behavior_skill_candidates
            SET name = ?, description = ?, script_json = ?, risk_level = ?,
                last_evaluated_count = occurrence_count, updated_at = ?
            WHERE candidate_id = ?
            """,
            (
                script["name"],
                script["description"],
                _json_dumps(script),
                str((script.get("risk") or {}).get("level") or "none"),
                _now_iso(),
                candidate_id,
            ),
        )
        await conn.commit()
    return script


async def _create_skill_from_candidate(candidate_id: str, *, auto: bool) -> str | None:
    async with _conn() as conn:
        cursor = await conn.execute("SELECT * FROM behavior_skill_candidates WHERE candidate_id = ?", (candidate_id,))
        row = await cursor.fetchone()
    if row is None:
        return None
    candidate = dict(row)
    if str(candidate.get("linked_skill_id") or ""):
        return str(candidate["linked_skill_id"])
    script = _json_loads(candidate.get("script_json"), {})
    if not script:
        script = await _refresh_candidate_script(candidate_id)
    steps = script.get("steps") or []
    if not _has_skillworthy_steps(steps):
        return None
    now = _now_iso()
    skill_id = _new_id("learned_skill")
    examples = await _candidate_turn_examples(candidate_id)
    async with _conn() as conn:
        name = await _unique_skill_name(conn, str(script.get("name") or candidate.get("name") or "重复工具流程"))
        definition = {
            "skill_id": skill_id,
            "project_id": str(candidate.get("project_id") or ""),
            "project_key": str(candidate.get("project_key") or ""),
            "name": name,
            "description": str(script.get("description") or candidate.get("description") or ""),
            "version": 1,
            "status": "active",
            "skill_type": "parameterized" if script.get("parameters") else "workflow",
            "risk_level": str((script.get("risk") or {}).get("level") or "none"),
            "requires_llm": False,
            "trigger": {"positive_examples": [item.get("user_message") for item in examples]},
            "input_schema": script.get("parameters") or [],
            "parameter_extractor": {"mode": "agent_provided", "llm_fallback": False},
            "steps": steps,
            "script": script,
            "guards": {"risk_level": str((script.get("risk") or {}).get("level") or "none")},
            "fallback_policy": {"on_step_failure": "fallback_to_agent", "on_missing_args": "fallback_to_agent"},
            "tests": [],
            "editable_fields": ["name", "description", "input_schema", "steps", "guards"],
            "created_from": {"candidate_id": candidate_id, "turn_list": script.get("source_turn_ids") or []},
            "run_statistics": _default_skill_stats(),
            "pattern_id": "",
            "created_at": now,
            "updated_at": now,
        }
        await conn.execute(
            """
            INSERT INTO learned_skills
            (skill_id, project_id, project_key, name, description, current_version, status, skill_type, risk_level, requires_llm,
             trigger_json, input_schema_json, parameter_extractor_json, steps_json, script_json, guards_json, fallback_policy_json,
             tests_json, editable_fields_json, created_from_json, run_statistics_json, pattern_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, 'active', ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?, '', ?, ?)
            """,
            (
                skill_id, definition["project_id"], definition["project_key"], name, definition["description"],
                definition["skill_type"], definition["risk_level"], _json_dumps(definition["trigger"]),
                _json_dumps(definition["input_schema"]), _json_dumps(definition["parameter_extractor"]),
                _json_dumps(steps), _json_dumps(script), _json_dumps(definition["guards"]),
                _json_dumps(definition["fallback_policy"]), _json_dumps(definition["editable_fields"]),
                _json_dumps(definition["created_from"]), _json_dumps(definition["run_statistics"]), now, now,
            ),
        )
        await _save_skill_version(
            conn=conn, skill_id=skill_id, version=1, parent_version=None, definition=definition,
            change_type="auto_candidate" if auto else "user_candidate",
            change_summary="Automatically learned on the third occurrence." if auto else "User accepted on the second occurrence.",
        )
        await conn.execute(
            """
            UPDATE behavior_skill_candidates
            SET status = ?, linked_skill_id = ?, user_decision = ?, updated_at = ?
            WHERE candidate_id = ?
            """,
            ("auto_learned" if auto else "accepted", skill_id, "auto" if auto else "learn_now", now, candidate_id),
        )
        await conn.commit()
    return skill_id


async def _record_candidate_occurrence(evidence: dict[str, Any]) -> dict[str, Any]:
    async with _conn() as conn:
        cursor = await conn.execute(
            """
            SELECT c.*
            FROM behavior_skill_candidates c
            WHERE c.project_id = ? AND c.bucket_key = ?
            ORDER BY c.updated_at DESC
            """,
            (evidence["project_id"], evidence["bucket_key"]),
        )
        possible = [dict(row) for row in await cursor.fetchall()]
    matched: dict[str, Any] | None = None
    for candidate in possible:
        if await _candidate_matches(str(candidate["candidate_id"]), evidence):
            matched = candidate
            break
    now = _now_iso()
    if matched is None:
        candidate_id = _new_id("candidate")
        async with _conn() as conn:
            await conn.execute(
                """
                INSERT INTO behavior_skill_candidates
                (candidate_id, project_id, project_key, bucket_key, status, occurrence_count,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, 'observing', 1, ?, ?)
                """,
                (candidate_id, evidence["project_id"], evidence["project_key"], evidence["bucket_key"], now, now),
            )
            await conn.execute(
                """
                INSERT INTO behavior_skill_candidate_turns
                (candidate_id, turn_id, occurrence_index, created_at)
                VALUES (?, ?, 1, ?)
                """,
                (candidate_id, evidence["turn_id"], now),
            )
            await conn.commit()
        return {"candidate_id": candidate_id, "occurrence_count": 1, "status": "observing", "created": True}
    candidate_id = str(matched["candidate_id"])
    async with _conn() as conn:
        cursor = await conn.execute("SELECT 1 FROM behavior_skill_candidate_turns WHERE turn_id = ?", (evidence["turn_id"],))
        if await cursor.fetchone() is not None:
            return {"candidate_id": candidate_id, "occurrence_count": int(matched["occurrence_count"]), "status": str(matched["status"]), "created": False}
        count = int(matched["occurrence_count"] or 0) + 1
        await conn.execute(
            "INSERT INTO behavior_skill_candidate_turns (candidate_id, turn_id, occurrence_index, created_at) VALUES (?, ?, ?, ?)",
            (candidate_id, evidence["turn_id"], count, now),
        )
        next_status = str(matched["status"] or "observing")
        if count == _CANDIDATE_USER_DECISION_COUNT and next_status == "observing":
            next_status = "awaiting_user"
        await conn.execute(
            "UPDATE behavior_skill_candidates SET occurrence_count = ?, status = ?, updated_at = ? WHERE candidate_id = ?",
            (count, next_status, now, candidate_id),
        )
        await conn.commit()
    if count == _CANDIDATE_USER_DECISION_COUNT:
        await _refresh_candidate_script(candidate_id)
    if count >= _CANDIDATE_AUTO_LEARN_COUNT and str(matched.get("status") or "") not in {"dismissed", "accepted", "auto_learned"}:
        await _refresh_candidate_script(candidate_id)
        skill_id = await _create_skill_from_candidate(candidate_id, auto=True)
        return {
            "candidate_id": candidate_id,
            "occurrence_count": count,
            "status": "auto_learned",
            "skill_id": skill_id,
            "created": False,
            "auto_created": bool(skill_id),
        }
    return {"candidate_id": candidate_id, "occurrence_count": count, "status": next_status, "created": False}


async def list_skill_candidates(project_id: str = "", status: str = "all") -> list[dict[str, Any]]:
    async with _conn() as conn:
        pid = str(project_id or "").strip()
        clauses: list[str] = []
        params: list[Any] = []
        if pid:
            clauses.append("project_id = ?")
            params.append(pid)
        if status != "all":
            clauses.append("status = ?")
            params.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        cursor = await conn.execute(f"SELECT * FROM behavior_skill_candidates{where} ORDER BY updated_at DESC", tuple(params))
        rows = await cursor.fetchall()
        candidate_ids = [str(row["candidate_id"]) for row in rows]
        turn_ids_by_candidate: dict[str, list[str]] = defaultdict(list)
        if candidate_ids:
            placeholders = ",".join("?" for _ in candidate_ids)
            cursor = await conn.execute(
                f"""
                SELECT candidate_id, turn_id
                FROM behavior_skill_candidate_turns
                WHERE candidate_id IN ({placeholders})
                ORDER BY occurrence_index ASC
                """,
                tuple(candidate_ids),
            )
            for turn_row in await cursor.fetchall():
                turn_ids_by_candidate[str(turn_row["candidate_id"])].append(str(turn_row["turn_id"]))
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        result.append({
            "id": str(item.get("candidate_id") or ""),
            "candidate_id": str(item.get("candidate_id") or ""),
            "project_id": str(item.get("project_id") or ""),
            "status": str(item.get("status") or ""),
            "occurrence_count": int(item.get("occurrence_count") or 0),
            "name": str(item.get("name") or ""),
            "description": str(item.get("description") or ""),
            "script": _json_loads(item.get("script_json"), {}),
            "risk_level": str(item.get("risk_level") or "none"),
            "linked_skill_id": str(item.get("linked_skill_id") or ""),
            "user_decision": str(item.get("user_decision") or ""),
            "turn_ids": turn_ids_by_candidate.get(str(item.get("candidate_id") or ""), []),
            "created_at": str(item.get("created_at") or ""),
            "updated_at": str(item.get("updated_at") or ""),
        })
    return result


async def decide_skill_candidate(candidate_id: str, decision: str) -> dict[str, Any]:
    normalized = str(decision or "").strip().lower()
    if normalized not in {"learn_now", "defer", "dismiss"}:
        return {"ok": False, "error": "decision must be learn_now, defer, or dismiss"}
    async with _conn() as conn:
        cursor = await conn.execute("SELECT * FROM behavior_skill_candidates WHERE candidate_id = ?", (candidate_id,))
        row = await cursor.fetchone()
    if row is None:
        return {"ok": False, "error": "candidate not found"}
    if normalized == "learn_now":
        if not _json_loads(dict(row).get("script_json"), {}):
            await _refresh_candidate_script(candidate_id)
        skill_id = await _create_skill_from_candidate(candidate_id, auto=False)
        return {"ok": bool(skill_id), "candidate_id": candidate_id, "skill_id": skill_id or "", "status": "accepted"}
    next_status = "waiting_third" if normalized == "defer" else "dismissed"
    async with _conn() as conn:
        await conn.execute(
            "UPDATE behavior_skill_candidates SET status = ?, user_decision = ?, updated_at = ? WHERE candidate_id = ?",
            (next_status, normalized, _now_iso(), candidate_id),
        )
        await conn.commit()
    return {"ok": True, "candidate_id": candidate_id, "status": next_status}


def _target_type_from_review(review: dict[str, Any], stats: dict[str, Any], prototype: dict[str, Any]) -> str:
    proposed = review.get("proposed_skill") if isinstance(review.get("proposed_skill"), dict) else {}
    target_type = str(proposed.get("skill_type") or "").strip()
    if target_type in _SKILL_TYPE_ORDER:
        return target_type
    raw_decision = str(review.get("raw_decision") or review.get("decision") or "")
    if raw_decision == "parameterize":
        return "parameterized"
    return _target_skill_type(stats, prototype)


async def _merge_pattern_into(target_pattern_id: str, source_pattern_id: str) -> bool:
    target = str(target_pattern_id or "").strip()
    source = str(source_pattern_id or "").strip()
    if not target or not source or target == source:
        return False
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT project_id FROM behavior_patterns WHERE pattern_id = ?",
            (target,),
        )
        target_row = await cursor.fetchone()
        cursor = await conn.execute(
            "SELECT project_id FROM behavior_patterns WHERE pattern_id = ?",
            (source,),
        )
        source_row = await cursor.fetchone()
        if target_row is None or source_row is None:
            return False
        if str(target_row["project_id"] or "") != str(source_row["project_id"] or ""):
            return False
        cursor = await conn.execute(
            "SELECT skill_id FROM learned_skills WHERE pattern_id = ?",
            (source,),
        )
        if await cursor.fetchone() is not None:
            return False
        cursor = await conn.execute(
            "SELECT turn_id, similarity, created_at FROM behavior_pattern_turns WHERE pattern_id = ?",
            (source,),
        )
        rows = await cursor.fetchall()
        for row in rows:
            await conn.execute(
                """
                INSERT OR REPLACE INTO behavior_pattern_turns
                (pattern_id, turn_id, similarity, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    target,
                    str(row["turn_id"] or ""),
                    float(row["similarity"] or 0),
                    str(row["created_at"] or _now_iso()),
                ),
            )
        await conn.execute("DELETE FROM behavior_pattern_turns WHERE pattern_id = ?", (source,))
        await conn.execute(
            "UPDATE behavior_patterns SET status = 'deprecated', updated_at = ? WHERE pattern_id = ?",
            (_now_iso(), source),
        )
        await conn.commit()
    await _upsert_pattern(target)
    return True


async def _learning_agent_review_turn(turn_id: str, fingerprint: dict[str, Any], pattern_id: str) -> dict[str, Any]:
    scope = await _project_scope_for_turn(turn_id)
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT * FROM behavior_learning_agent_reviews WHERE turn_id = ?",
            (turn_id,),
        )
        existing = await cursor.fetchone()
        if existing is not None:
            row = dict(existing)
            return {
                "decision": str(row.get("decision") or ""),
                "confidence": float(row.get("confidence") or 0),
                "rationale": str(row.get("rationale") or ""),
                "proposed_skill": _json_loads(row.get("proposed_skill_json"), {}),
            }
        cursor = await conn.execute(
            "SELECT user_message, context_summary FROM behavior_turns WHERE turn_id = ?",
            (turn_id,),
        )
        turn_row = await cursor.fetchone()
    chain = await _load_tool_chain_for_turn(turn_id)
    summary = chain.get("summary") or {}
    current_candidate = await _pattern_summary_for_learning(pattern_id)
    similar = await _learning_similar_candidates(
        project_id=scope["project_id"],
        current_pattern_id=pattern_id,
        fingerprint=fingerprint,
    )
    chain_items = chain.get("chain") or []
    chain_steps = [
        {"enabled": True, "implementation_reference": {"tool_name": str(item.get("tool") or "")}}
        for item in chain_items
    ]
    has_skillworthy_chain = _has_skillworthy_steps(chain_steps)
    prompt = f"""You are the project-local skill learning agent for project {scope["project_id"]}.

Review one completed conversation round and its exact tool/user-browser operation chain.
First inspect whether this project already has a similar skill candidate or learned skill.
Then decide whether to promote a candidate into a skill, merge with an existing candidate, or avoid duplicate learning.

Return JSON:
{{
  "decision": "promote" | "merge" | "duplicate" | "skip",
  "confidence": 0-1,
  "rationale": "short reason",
  "target_pattern_id": "pattern id to promote or merge into, if any",
  "target_skill_id": "existing skill id if duplicate",
  "proposed_skill": {{
    "name": "short Chinese user-facing name",
    "description": "one sentence",
    "skill_type": "draft" | "workflow" | "parameterized" | "deterministic"
  }}
}}

Decision rules:
- duplicate: choose this if an existing learned skill already covers the same purpose. Do not create a new skill.
- If the existing learned skill is linked to the current candidate, choose promote when the candidate needs an upgrade; that is not a duplicate.
- merge: choose this if a similar candidate exists and the new chain should be folded into that candidate before any future promotion.
- promote: choose this only when the current or target candidate is worth becoming a reusable project-local skill now.
- Prefer long task-chain skills: promote only chains with at least two meaningful and distinct operations.
- Do not promote a single tool call, repeated single-tool usage, or a lone browser event as a skill.
- skip: choose this for one-off answers, pure chat, unsafe side effects, weak/noisy chains, or insufficient evidence.

User message:
{turn_row["user_message"] if turn_row else ""}

Context:
{turn_row["context_summary"] if turn_row else ""}

Fingerprint:
{json.dumps(fingerprint, ensure_ascii=False, indent=2)}

Tool chain summary:
{json.dumps(summary, ensure_ascii=False, indent=2)}

Current candidate:
{json.dumps(current_candidate, ensure_ascii=False, indent=2)[:8000]}

Similar candidates and learned skills:
{json.dumps(similar, ensure_ascii=False, indent=2)[:12000]}

Tool chain:
{json.dumps(chain.get("chain") or [], ensure_ascii=False, indent=2)[:12000]}
"""
    result = await _call_llm_json(prompt, caller="project_skill_learning_agent")
    raw_decision = str(result.get("decision") or "").strip().lower()
    decision = _normalize_learning_decision(raw_decision)
    if not result or raw_decision not in {"promote", "merge", "duplicate", "skip", "learn", "parameterize", "create_skill", "promote_candidate", "already_exists", "covered", "reuse_existing", "merge_candidate", "merge_pattern"}:
        linked_skill_ids = current_candidate.get("linked_skill_list") or []
        current_stats = {
            "effective_count": current_candidate.get("effective_count") or 0,
            "frequency": current_candidate.get("frequency") or 0,
        }
        duplicate_skill = next(
            (
                item for item in similar.get("skills") or []
                if str(item.get("pattern_id") or "") != pattern_id and float(item.get("similarity") or 0) >= 0.88
            ),
            None,
        )
        if duplicate_skill:
            decision = "duplicate"
            result = {
                "decision": decision,
                "confidence": 0.72,
                "rationale": "Heuristic fallback found an existing project-local skill with the same purpose.",
                "target_skill_id": duplicate_skill.get("skill_id", ""),
                "proposed_skill": {},
            }
        elif linked_skill_ids or (has_skillworthy_chain and float(current_stats.get("effective_count") or 0) >= 2):
            decision = "promote"
            result = {
                "decision": decision,
                "confidence": 0.62,
                "rationale": "Heuristic fallback: reusable tool chain has enough project-local evidence and no duplicate skill was found.",
                "target_pattern_id": pattern_id,
                "proposed_skill": {},
            }
        else:
            decision = "skip"
            result = {
                "decision": decision,
                "confidence": 0.45,
                "rationale": "Heuristic fallback: not enough project-local evidence to promote this tool chain yet.",
                "proposed_skill": {},
            }
    if decision == "promote" and not has_skillworthy_chain:
        decision = "skip"
        result = {
            **result,
            "decision": decision,
            "confidence": min(float(result.get("confidence") or 0), 0.50),
            "rationale": "Promotion suppressed: skill learning requires a multi-step chain with at least two meaningful distinct operations.",
            "proposed_skill": {},
        }
    if decision == "promote":
        duplicate_skill = next(
            (
                item for item in similar.get("skills") or []
                if str(item.get("pattern_id") or "") != pattern_id and float(item.get("similarity") or 0) >= 0.88
            ),
            None,
        )
        if duplicate_skill:
            decision = "duplicate"
            result = {
                **result,
                "decision": decision,
                "target_skill_id": duplicate_skill.get("skill_id", ""),
                "confidence": max(float(result.get("confidence") or 0), 0.80),
                "rationale": "Existing project-local skill already covers this workflow; duplicate promotion suppressed.",
            }
    proposed = result.get("proposed_skill") if isinstance(result.get("proposed_skill"), dict) else {}
    proposed["_decision"] = {
        "raw_decision": raw_decision,
        "target_pattern_id": str(result.get("target_pattern_id") or ""),
        "target_skill_id": str(result.get("target_skill_id") or ""),
        "similar_patterns": similar.get("patterns") or [],
        "similar_skills": similar.get("skills") or [],
    }
    now = _now_iso()
    async with _conn() as conn:
        await conn.execute(
            """
            INSERT INTO behavior_learning_agent_reviews
            (review_id, project_id, project_key, turn_id, chain_id, decision, confidence, rationale,
             proposed_skill_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(turn_id) DO UPDATE SET
                project_id = excluded.project_id,
                project_key = excluded.project_key,
                chain_id = excluded.chain_id,
                decision = excluded.decision,
                confidence = excluded.confidence,
                rationale = excluded.rationale,
                proposed_skill_json = excluded.proposed_skill_json,
                updated_at = excluded.updated_at
            """,
            (
                _new_id("learning_review"),
                scope["project_id"],
                scope["project_key"],
                turn_id,
                str(chain.get("chain_id") or ""),
                decision,
                float(result.get("confidence") or 0),
                _truncate_text(result.get("rationale") or "", 500),
                _json_dumps(proposed),
                now,
                now,
            ),
        )
        await conn.commit()
    return {
        "decision": decision,
        "raw_decision": raw_decision,
        "confidence": float(result.get("confidence") or 0),
        "rationale": str(result.get("rationale") or ""),
        "proposed_skill": proposed,
        "target_pattern_id": str(result.get("target_pattern_id") or ""),
        "target_skill_id": str(result.get("target_skill_id") or ""),
    }


def _fresh_learning_stats() -> dict[str, int]:
    return {
        "processed_turns": 0,
        "candidates_created": 0,
        "candidates_awaiting_user": 0,
        "candidates_auto_learned": 0,
        "merged_patterns": 0,
        "new_patterns": 0,
        "skills_created": 0,
        "skills_updated": 0,
        "shadow_checks": 0,
        "learning_reviews": 0,
        "learning_skipped": 0,
        "learning_duplicates": 0,
        "candidate_merges": 0,
        "candidate_promotions": 0,
        "agent_created_skills": 0,
    }


async def process_unprocessed_turns(force: bool = False, project_id: str = "") -> dict[str, Any]:
    async with _get_process_lock():
        async with _conn() as conn:
            pid = str(project_id or "").strip()
            if pid:
                cursor = await conn.execute(
                    """
                    SELECT turn_id
                    FROM behavior_turns
                    WHERE processed_status = 0 AND project_id = ?
                    ORDER BY created_at ASC
                    """,
                    (pid,),
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT turn_id
                    FROM behavior_turns
                    WHERE processed_status = 0
                    ORDER BY created_at ASC
                    """
                )
            turn_rows = await cursor.fetchall()
        stats = _fresh_learning_stats()
        for row in turn_rows:
            turn_id = str(row["turn_id"])
            if await _process_single_turn(turn_id, stats):
                stats["processed_turns"] += 1
        await _promote_unknown_pool()
        return stats


async def tick(_bot: Any, _db_path: str) -> None:
    try:
        await process_unprocessed_turns()
    except Exception:
        logger.debug("behavior learning tick failed", exc_info=True)


async def scan_for_session_start() -> dict[str, Any]:
    return await process_unprocessed_turns()


async def _process_single_turn(
    turn_id: str,
    stats: dict[str, int],
    *,
    update_reason: str = "",
    allow_single_evidence: bool = False,
) -> bool:
    """Record one completed turn in the three-occurrence candidate state machine.

    Returns True once the turn has been scanned, including turns that are not
    reusable multi-tool evidence. Always marks the turn as processed.

    NOTE: Caller MUST hold ``_get_process_lock()`` to prevent concurrent
    processing of the same turn by the background tick or other callers.
    """
    # Scheduler prompts are internal execution guidance, not user requests.
    # Older versions recorded the entire prompt. Replace it with the same safe
    # event label used for new turns, while preserving its action history for
    # behavior learning.
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT user_message, metadata_json FROM behavior_turns WHERE turn_id = ?", (turn_id,)
        )
        turn_row = await cursor.fetchone()
        if turn_row is not None and str(turn_row["user_message"] or "").lstrip().startswith(_INTERNAL_PROACTIVE_PROMPT_PREFIX):
            metadata = _json_loads(turn_row["metadata_json"], {})
            metadata["system_initiated"] = True
            await conn.execute(
                "UPDATE behavior_turns SET user_message = ?, metadata_json = ?, updated_at = ? WHERE turn_id = ?",
                (_SCHEDULED_CHECK_IN_LABEL, _json_dumps(metadata), _now_iso(), turn_id),
            )
            await conn.commit()

    # The simplified learner records only real, successful multi-tool chains.
    # Structural bucketing is cheap; semantic comparison happens only when a
    # structurally similar chain is seen again.
    await _rebuild_tool_chain_for_turn(turn_id)
    evidence = await _candidate_evidence_for_turn(turn_id)
    if not evidence:
        async with _conn() as conn:
            await conn.execute(
                "UPDATE behavior_turns SET processed_status = 1, updated_at = ? WHERE turn_id = ?",
                (_now_iso(), turn_id),
            )
            await conn.commit()
        return True

    candidate_result = await _record_candidate_occurrence(evidence)
    if candidate_result.get("created"):
        stats["candidates_created"] += 1
    if str(candidate_result.get("status") or "") == "awaiting_user":
        stats["candidates_awaiting_user"] += 1
    if bool(candidate_result.get("auto_created")):
        stats["candidates_auto_learned"] += 1
        if candidate_result.get("skill_id"):
            stats["skills_created"] += 1
    if allow_single_evidence:
        candidate_id = str(candidate_result.get("candidate_id") or "")
        if candidate_id:
            decision_result = await decide_skill_candidate(candidate_id, "learn_now")
            if decision_result.get("skill_id"):
                stats["skills_created"] += 1
    async with _conn() as conn:
        await conn.execute(
            "UPDATE behavior_turns SET processed_status = 1, updated_at = ? WHERE turn_id = ?",
            (_now_iso(), turn_id),
        )
        await conn.commit()
    return True


async def learn_from_turn(turn_id: str) -> dict[str, Any]:
    tid = str(turn_id or "").strip()
    if not tid:
        return {"processed_turns": 0, "skills_created": 0, "error": "turn_id is required"}

    async with _get_process_lock():
        # Reset so the turn is picked up even if already processed
        async with _conn() as conn:
            await conn.execute(
                "UPDATE behavior_turns SET processed_status = 0, updated_at = ? WHERE turn_id = ?",
                (_now_iso(), tid),
            )
            await conn.commit()

        stats = _fresh_learning_stats()

        await _process_single_turn(
            tid,
            stats,
            update_reason="User-initiated single-turn skill learning.",
            allow_single_evidence=True,
        )
        stats["processed_turns"] = 1
        return stats


async def scan_for_manual_learn(project_id: str = "") -> dict[str, Any]:
    return await process_unprocessed_turns(force=True, project_id=project_id)


async def rebuild_learning_state(*, reprocess_all_turns: bool = True, project_id: str = "") -> dict[str, Any]:
    pid = str(project_id or "").strip()
    async with _conn() as conn:
        if pid:
            cursor = await conn.execute("SELECT candidate_id FROM behavior_skill_candidates WHERE project_id = ?", (pid,))
            candidate_ids = [str(row["candidate_id"]) for row in await cursor.fetchall()]
            cursor = await conn.execute("SELECT skill_id FROM learned_skills WHERE project_id = ?", (pid,))
            skill_ids = [str(row["skill_id"]) for row in await cursor.fetchall()]
            cursor = await conn.execute("SELECT pattern_id FROM behavior_patterns WHERE project_id = ?", (pid,))
            pattern_ids = [str(row["pattern_id"]) for row in await cursor.fetchall()]
            for skill_id in skill_ids:
                await conn.execute("DELETE FROM behavior_replay_tests WHERE skill_id = ?", (skill_id,))
                await conn.execute("DELETE FROM learned_skill_patches WHERE skill_id = ?", (skill_id,))
                await conn.execute("DELETE FROM learned_skill_runs WHERE skill_id = ?", (skill_id,))
                await conn.execute("DELETE FROM learned_skill_versions WHERE skill_id = ?", (skill_id,))
            for pattern_id in pattern_ids:
                await conn.execute("DELETE FROM behavior_pattern_turns WHERE pattern_id = ?", (pattern_id,))
            for candidate_id in candidate_ids:
                await conn.execute("DELETE FROM behavior_skill_candidate_turns WHERE candidate_id = ?", (candidate_id,))
            await conn.execute("DELETE FROM behavior_skill_candidates WHERE project_id = ?", (pid,))
            await conn.execute("DELETE FROM learned_skills WHERE project_id = ?", (pid,))
            await conn.execute("DELETE FROM behavior_patterns WHERE project_id = ?", (pid,))
            await conn.execute("DELETE FROM behavior_learning_agent_reviews WHERE project_id = ?", (pid,))
            if reprocess_all_turns:
                await conn.execute("UPDATE behavior_turns SET processed_status = 0, linked_skill_id = '' WHERE project_id = ?", (pid,))
        else:
            await conn.execute("DELETE FROM behavior_skill_candidate_turns")
            await conn.execute("DELETE FROM behavior_skill_candidates")
            await conn.execute("DELETE FROM behavior_pattern_turns")
            await conn.execute("DELETE FROM behavior_patterns")
            await conn.execute("DELETE FROM behavior_fingerprints")
            await conn.execute("DELETE FROM behavior_replay_tests")
            await conn.execute("DELETE FROM learned_skill_patches")
            await conn.execute("DELETE FROM learned_skill_runs")
            await conn.execute("DELETE FROM learned_skill_versions")
            await conn.execute("DELETE FROM learned_skills")
            await conn.execute("DELETE FROM behavior_learning_agent_reviews")
            if reprocess_all_turns:
                await conn.execute("UPDATE behavior_turns SET processed_status = 0, linked_skill_id = '', updated_at = updated_at")
        if reprocess_all_turns:
            pass
        await conn.commit()
    stats = await process_unprocessed_turns(force=True, project_id=pid)
    learned = await list_learned_skills(pid)
    return {
        **stats,
        "patterns": await list_patterns("all", pid),
        "learned_skills": learned,
        "skill_candidates": await list_skill_candidates(pid),
    }


async def run_learned_skill(skill_id: str, param_overrides: dict[str, Any] | None = None) -> str:
    skill = await get_learned_skill(skill_id)
    if skill is None:
        return f"Learned skill '{skill_id}' not found."
    if str(skill.get("risk_level") or "none") == "high" or _has_auto_replay_blocked_step(skill.get("steps") or []):
        return f"Learned skill '{skill_id}' requires normal agent execution and fresh runtime approval."
    from cyrene.tools import _execute_tool

    context_summary = ""
    extraction = {
        "params": param_overrides or {},
        "complete": True,
        "missing_required": [],
        "confidence": 1.0,
    }
    if skill["input_schema"]:
        extraction = await extract_skill_parameters(
            user_message=" ".join(str(value) for value in (param_overrides or {}).values()),
            context_summary=context_summary,
            input_schema=skill["input_schema"],
            llm_fallback=False,
            overrides=param_overrides,
        )
    if not extraction["complete"]:
        return f"Skill '{skill_id}' is missing required params: {', '.join(extraction['missing_required'])}"
    results: list[str] = []
    for step in skill["steps"]:
        if not bool(step.get("enabled", True)):
            continue
        reference = step.get("implementation_reference") or {}
        implementation_kind = str(step.get("implementation_kind") or "")
        if implementation_kind == "script":
            result, ok, reason = await _execute_script_step(reference, extraction["params"])
            prefix = "run_generated_skill_script" if ok else f"run_generated_skill_script failed ({reason})"
            results.append(f"{prefix}: {_truncate_text(result, 500)}")
            continue
        if implementation_kind != "tool_call":
            continue
        tool_name = str(reference.get("tool_name") or "")
        args_template = reference.get("args_template") or {}
        items = args_template.get("_items")
        if isinstance(items, list) and items:
            for item_args in items:
                resolved = _resolve_value_template(item_args, extraction["params"])
                try:
                    result = await _execute_tool(tool_name, resolved, None, 0, "", None)
                except Exception as exc:
                    result = f"Tool failed: {exc}"
                results.append(f"{tool_name}: {_truncate_text(result, 500)}")
        else:
            resolved_args = _resolve_value_template(args_template, extraction["params"])
            try:
                result = await _execute_tool(tool_name, resolved_args, None, 0, "", None)
            except Exception as exc:
                result = f"Tool failed: {exc}"
            results.append(f"{tool_name}: {_truncate_text(result, 500)}")
    return "\n".join(results) if results else f"Skill '{skill_id}' has no executable steps."
