"""Legacy Workbench orchestration implementation behind typed services.

New HTTP and local adapters must depend on the domain application services,
not this module. ``cyrene.workbench.runtime`` remains the stable compatibility
module identity for older extensions and test seams.
"""

# This module is the compatibility facade consumed by route adapters while
# application services continue to be extracted by domain.
# ruff: noqa: E402, F401

import asyncio
import base64
import difflib
import getpass
import hashlib
import importlib
import json
import logging
import math
import mimetypes
import os
import random
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import cyrene.agent.state as _agent_state
import cyrene.workbench.memory as _workbench_memory
import cyrene.workbench.session_view as _session_view
from PIL import Image
from fastapi import APIRouter, BackgroundTasks, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from cyrene.tooling.result_store import (
    ToolResultReferenceError,
    project_tool_result_for_model,
    read_tool_result,
)
from cyrene.runtime.attachments import (
    EXPORTS_DIR as _EXPORTS_DIR,
    attachment_kind_from_meta,
    build_public_attachment_payload,
)
from cyrene.config import strip_wrapping_quotes
from cyrene.agent.context import bind_run_context, is_permission_mode
from cyrene.agent import (
    _AWAITING_USER_SENTINEL,
    _append_session_message,
    _remove_messages_by_request_id,
    answer_pending_question,
    append_system_message,
    clear_session_id,
    get_pending_question,
    get_live_rounds,
    get_session_labels,
    interrupt_active_run,
    is_session_running,
    queue_round_guidance,
    run_agent,
    SessionRunConflictError,
)
from cyrene.workbench import generation_gateway as _generation_gateway
from cyrene.config import (
    ASSISTANT_NAME,
    BASE_DIR,
    DATA_DIR,
    DEFAULT_OPENAI_BASE_URL,
    DEFAULT_OPENAI_MODEL,
    DB_PATH,
    PATTERNS_DIR,
    SEARXNG_HOST,
    SEARXNG_PORT,
    SOUL_PATH,
    STATE_FILE,
    TEMP_DIR,
    WORKSPACE_DIR,
    cyrene_dir,
)
from cyrene.runtime.memory.conversations import (
    CONVERSATIONS_DIR,
    archive_exchange,
    parse_archive_meta as _parse_archive_meta,
    search_conversations_structured,
    split_archive_entry_blocks as _split_archive_entry_blocks,
    upsert_archive_session_title as _upsert_archive_session_title,
)
from cyrene.runtime.onboarding import (
    get_onboarding_status,
    reset_onboarding_state,
    save_and_test_llm_setup,
    save_codex_oauth_setup,
    save_personality_setup,
)
from cyrene.runtime.settings_store import get_all as get_web_settings
from cyrene.runtime.memory.short_term import load_entries
from cyrene.runtime.memory.soul import get_default_soul_content, read_soul, get_soul_path
from cyrene.runtime.version import get_version_label
from cyrene.workbench.store import (
    patch_project_bundle_fields,
    read_project_bundle,
    summarize_task_session,
    write_project_bundle,
)
from cyrene.workbench.workspace_changes import is_cyrene_managed_workspace_path
from cyrene.workbench.session_view import (
    build_pending_question as _ui_pending_question,
    collapse_duplicate_user_messages as _collapse_duplicate_user_messages,
    count_tool_calls as _count_tool_calls,
    dedupe_repeated_messages as _dedupe_repeated_messages,
    has_recent_main_agent_activity as _has_recent_main_agent_activity,
    merge_adjacent_trace_only_messages as _merge_adjacent_trace_only_messages,
    prune_flow_rounds as _prune_flow_rounds,
    session_started_at as _session_view_started_at,
    split_raw_rounds as _split_raw_rounds,
)
from cyrene.workbench.session_metrics import (
    elapsed_since as _elapsed_since,
    format_duration as _format_duration,
    format_token_count as _fmt_tok,
    format_tokens as _format_tokens,
    last_request_context_tokens as _last_request_context_tokens,
    merge_usage_totals as _merge_usage_totals,
    safe_json_loads as _safe_json_loads,
    short_time as _short_time,
    status_progress as _status_progress,
    summarize_text as _summarize_text,
    tool_args_signature as _tool_args_signature,
    tool_output_ids as _tool_output_ids,
    tool_output_map as _tool_output_map,
    usage_totals as _usage_totals,
)
from cyrene.workbench.task_context import (
    build_main_context as _workbench_task_build_main_context,
    build_volatile_context as _workbench_task_build_volatile_context,
    _clean_text as _workbench_clean_text,
    ensure_shared_context as _workbench_task_ensure_shared_context,
)
from cyrene.runtime.io import atomic_write_json, read_json_safe

logger = logging.getLogger(__name__)

# Historical monkeypatch seam; the compatibility module forwards assignments
# to the explicit generation gateway used by extracted services.
_call_llm = _generation_gateway.call_llm

# Historical private presentation helpers remain available from the old
# Workbench composition module.
_is_trace_only_agent_message = _session_view._is_trace_only_agent_message
_round_has_activity = _session_view._round_has_activity
_ui_tool_message_key = _session_view._ui_tool_message_key
add_agent_memory = _workbench_memory.add_agent_memory
memory_injection_ids = _workbench_memory.memory_injection_ids
render_memory_for_injection = _workbench_memory.render_memory_for_injection
render_task_reports_for_planning = (
    _workbench_memory.render_task_reports_for_planning
)
_strip_wrapping_quotes = strip_wrapping_quotes
_conversation_source = _agent_state._conversation_source
_attachment_paths_by_name = _agent_state._attachment_paths_by_name
_reply_stream_writer = _agent_state._reply_stream_writer


def reset_lottery() -> None:
    """Compatibility seam for the scheduler's historical reset helper."""
    _scheduler_service().reset_lottery()


def _memory_service():
    return importlib.import_module("cyrene.workbench.memory")


def _scheduler_service():
    return importlib.import_module("cyrene.runtime.scheduler")


def schedule_capture(
    workspace_id: str | None,
    user_text: str,
    agent_text: str,
    **kwargs: Any,
) -> None:
    """Compatibility facade for asynchronous Workbench memory capture."""
    _memory_service().schedule_capture(
        workspace_id,
        user_text,
        agent_text,
        **kwargs,
    )


def _notification_service():
    return importlib.import_module("cyrene.workbench.notifications")


def append_notification(**kwargs: Any) -> dict[str, Any]:
    """Compatibility facade for creating a Workbench notification."""
    return _notification_service().append_notification(**kwargs)


def list_notifications(**kwargs: Any) -> dict[str, Any]:
    """Compatibility facade for reading Workbench notifications."""
    return _notification_service().list_notifications(**kwargs)


def mark_notifications_read(
    ids: list[str] | None = None,
    *,
    mark_all: bool = False,
) -> dict[str, Any]:
    """Compatibility facade for notification read-state updates."""
    return _notification_service().mark_notifications_read(ids, mark_all=mark_all)


_bot: Any = None
_db_path: str = ""
_CHAT_ID = -1

# Static assets remain owned by ``webui``.  Route adapters live in a separate
# top-level package, so deriving this path from ``route.__file__`` would point
# at a non-existent ``src/route/static`` directory in editable and packaged
# installs.
_STATIC_DIR = Path(__file__).resolve().parents[2] / "webui" / "static"
_APP_DIR = _STATIC_DIR / "app"
_UPLOADS_DIR = DATA_DIR / "webui_uploads"
_WORKBENCH_STORE = DATA_DIR / "workbench_projects.json"
_WORKBENCH_STORE_LOCK = threading.RLock()
_CONFIGURED_WORKBENCH_STORE: Path | None = None
_SERVER_STARTED_AT = time.time()
_WORKBENCH_LEGACY_DATA_KEY = "default"

_WORKBENCH_PLANNER_CONTRACT_VERSION = "planner-contract-v1"
_WORKBENCH_PLANNER_NO_TOOLS_VERSION = "planner-no-tools-v1"
_WORKBENCH_PLANNER_EXPLORE_VERSION = "planner-explore-v1"
_WORKBENCH_PLANNING_THREAD_MAX_CHARS = 120_000
# Constrained JSON mode for the tool-less "just emit JSON" rounds (final answer
# + repair) of the explore agent. Providers require the word "json" to appear in
# the messages, which those rounds' instructions guarantee.
_WORKBENCH_JSON_RESPONSE_FORMAT = {"type": "json_object"}
# Independent acceptance is autonomous: a single flaky model reply must not pause
# the whole goal loop, so retry transient failures a few times with backoff.
# Auth/config failures won't fix themselves on retry, so they bail immediately.
_WORKBENCH_VERIFY_MAX_ATTEMPTS = 3
_WORKBENCH_VERIFY_RETRY_BASE_DELAY = 2.0
_WORKBENCH_VERIFY_NON_RETRYABLE = frozenset({"authentication", "configuration"})
_WORKBENCH_PLANNER_SYSTEM_PROMPT = """你是任务执行规划 Agent。你的职责是根据任务目标、约束、已有计划、用户反馈和已经确认的工作区事实，生成完整、可执行、可核验的计划。

工作区探索规则：
- 只有当计划质量依赖尚未确认的项目事实、用户引入新文件/新模块，或工作区自上次规划后发生变化时才探索。
- 已经观察且指纹未变化的资源不得重复读取。
- 局部修改步骤、描述、顺序、依赖或验收标准时，优先基于已有上下文直接修订。
- “重新生成”只表示重新拆解计划，不自动等于重新探索工作区。
- 与本地项目无关的任务不得探索工作区。

修订规则：
- revise：保留未被反馈影响的步骤，返回完整修订计划；保留或修改的步骤用 sourceStepId 对应原步骤。
- replace：从最终目标重新拆解，不保留旧步骤，sourceStepId 使用 null。
- goal 表示应用本次反馈后的最终目标；反馈未改变目标时原样保留。

只输出一个合法 JSON 对象，不要输出 Markdown 或解释。结构：
{
  "goal": "最终任务目标",
  "title": "仅直接开始任务需要，可省略",
  "revisionMode": "revise|replace",
  "steps": [
    {
      "sourceStepId": "原步骤 id 或 null",
      "title": "简洁的动宾短语",
      "description": "具体工作、涉及的真实文件或模块",
      "dependsOnStepIndexes": [1]
    }
  ],
  "acceptanceCriteria": ["可独立核验的结果标准"]
}

生成 3-7 个步骤和 3-8 条验收标准。dependsOnStepIndexes 使用当前返回列表中的 1-based 序号，只能引用前面的步骤；无依赖时返回空数组。全部使用简体中文。"""


from cyrene.workbench import project_runtime as _project_runtime
from cyrene.workbench import task_goal_service as _task_goal_service
_WORKBENCH_PLACEHOLDER_GOAL = _project_runtime._WORKBENCH_PLACEHOLDER_GOAL
_get_base_url = _project_runtime._get_base_url
_get_current_model_ctx_limit = _project_runtime._get_current_model_ctx_limit
_get_model = _project_runtime._get_model
_live_llm_config = _project_runtime._live_llm_config
_ndjson_line = _project_runtime._ndjson_line
_parse_ctx_limit = _project_runtime._parse_ctx_limit
_safe_workbench_data_key = _project_runtime._safe_workbench_data_key
_short_id = _project_runtime._short_id
_utc_now_iso = _project_runtime._utc_now_iso
_workbench_acceptance_fully_passed = _project_runtime._workbench_acceptance_fully_passed
_workbench_default_init_form = _project_runtime._workbench_default_init_form
_workbench_default_project = _project_runtime._workbench_default_project
_workbench_default_project_name = _project_runtime._workbench_default_project_name
_workbench_derive_title = _project_runtime._workbench_derive_title
_workbench_is_blank_goal = _project_runtime._workbench_is_blank_goal
_workbench_is_default_title = _project_runtime._workbench_is_default_title
_workbench_mark_completed_if_acceptance_passed = _project_runtime._workbench_mark_completed_if_acceptance_passed
_workbench_new_init_session = _project_runtime._workbench_new_init_session
_workbench_new_session = _project_runtime._workbench_new_session
_workbench_project_data_key = _project_runtime._workbench_project_data_key
_workbench_project_memory_key = _project_runtime._workbench_project_memory_key
set_task_goal_for_session = _task_goal_service.set_task_goal_for_session


_WORKBENCH_TEMPLATE_LABELS = {
    "blank": "空白项目",
    "product": "产品开发",
    "pm": "项目管理",
    "knowledge": "科学研究",
    "ai": "AI 应用开发",
    "import": "导入项目",
}

_INIT_QUESTION_TYPES = {"text", "textarea", "single", "multi"}


from cyrene.workbench import task_initialization_runtime as _task_initialization_runtime
_WORKBENCH_EMPTY_WORKSPACE_SKIP_DIRS = _task_initialization_runtime._WORKBENCH_EMPTY_WORKSPACE_SKIP_DIRS
_WORKBENCH_EXPLORE_TOOLS = _task_initialization_runtime._WORKBENCH_EXPLORE_TOOLS
_WorkbenchAgentRunError = _task_initialization_runtime._WorkbenchAgentRunError
_WorkbenchGenerationError = _task_initialization_runtime._WorkbenchGenerationError
_is_workspace_empty = _task_initialization_runtime._is_workspace_empty
_workbench_answer_text = _task_initialization_runtime._workbench_answer_text
_workbench_classify_plan_routing = _task_initialization_runtime._workbench_classify_plan_routing
_workbench_coerce_init_form = _task_initialization_runtime._workbench_coerce_init_form
_workbench_coerce_init_task_plan = _task_initialization_runtime._workbench_coerce_init_task_plan
_workbench_create_sessions_from_init_plan = _task_initialization_runtime._workbench_create_sessions_from_init_plan
_workbench_exec_explore_tool = _task_initialization_runtime._workbench_exec_explore_tool
_workbench_explore_parse_failure = _task_initialization_runtime._workbench_explore_parse_failure
_workbench_fallback_init_task_plan = _task_initialization_runtime._workbench_fallback_init_task_plan
_workbench_generate_init_form = _task_initialization_runtime._workbench_generate_init_form
_workbench_generate_init_task_plan = _task_initialization_runtime._workbench_generate_init_task_plan
_workbench_generation_error = _task_initialization_runtime._workbench_generation_error
_workbench_hash_json = _task_initialization_runtime._workbench_hash_json
_workbench_init_brief = _task_initialization_runtime._workbench_init_brief
_workbench_init_workspace_relationship_guidance = _task_initialization_runtime._workbench_init_workspace_relationship_guidance
_workbench_maybe_compact_planning_thread = _task_initialization_runtime._workbench_maybe_compact_planning_thread
_workbench_parse_json_object = _task_initialization_runtime._workbench_parse_json_object
_workbench_plan_tool_bundle = _task_initialization_runtime._workbench_plan_tool_bundle
_workbench_planning_checkpoint = _task_initialization_runtime._workbench_planning_checkpoint
_workbench_planning_context_chars = _task_initialization_runtime._workbench_planning_context_chars
_workbench_planning_thread = _task_initialization_runtime._workbench_planning_thread
_workbench_redact_error_text = _task_initialization_runtime._workbench_redact_error_text
_workbench_repair_json_response = _task_initialization_runtime._workbench_repair_json_response
_workbench_run_explore_agent = _task_initialization_runtime._workbench_run_explore_agent
_workbench_run_json_generation = _task_initialization_runtime._workbench_run_json_generation
_workbench_stable_json = _task_initialization_runtime._workbench_stable_json
_workbench_workspace_revision = _task_initialization_runtime._workbench_workspace_revision
_workbench_workspace_state = _task_initialization_runtime._workbench_workspace_state


from cyrene.workbench import project_repository as _project_repository
_configure_workbench_store = _project_repository._configure_workbench_store
_persist_workbench_selection = _project_repository._persist_workbench_selection
_read_workbench_store = _project_repository._read_workbench_store
_read_workbench_store_lightweight = _project_repository._read_workbench_store_lightweight
_task_plan_event_body = _project_repository._task_plan_event_body
_workbench_ensure_invariants = _project_repository._workbench_ensure_invariants
_workbench_find_project = _project_repository._workbench_find_project
_workbench_find_project_lightweight = _project_repository._workbench_find_project_lightweight
_workbench_find_session = _project_repository._workbench_find_session
_workbench_lightweight_store = _project_repository._workbench_lightweight_store
_workbench_project_shell = _project_repository._workbench_project_shell
_workbench_session_summary = _project_repository._workbench_session_summary
_workbench_store_uses_sqlite = _project_repository._workbench_store_uses_sqlite
_write_workbench_store = _project_repository._write_workbench_store
update_task_plan_for_session = _project_repository.update_task_plan_for_session




from cyrene.workbench import planning_runtime as _planning_runtime
_workbench_acceptance_from_session = _planning_runtime._workbench_acceptance_from_session
_workbench_coerce_acceptance_criteria = _planning_runtime._workbench_coerce_acceptance_criteria
_workbench_coerce_plan_steps = _planning_runtime._workbench_coerce_plan_steps
_workbench_dependency_ids = _planning_runtime._workbench_dependency_ids
_workbench_existing_plan_block = _planning_runtime._workbench_existing_plan_block
_workbench_extract_constraints = _planning_runtime._workbench_extract_constraints
_workbench_fallback_acceptance = _planning_runtime._workbench_fallback_acceptance
_workbench_follow_up_seed = _planning_runtime._workbench_follow_up_seed
_workbench_generate_acceptance_criteria = _planning_runtime._workbench_generate_acceptance_criteria
_workbench_generate_plan_steps = _planning_runtime._workbench_generate_plan_steps
_workbench_keep_ordered_dependencies = _planning_runtime._workbench_keep_ordered_dependencies
_workbench_new_plan_step = _planning_runtime._workbench_new_plan_step
_workbench_normalize_plan = _planning_runtime._workbench_normalize_plan
_workbench_plan_definition_signature = _planning_runtime._workbench_plan_definition_signature
_workbench_plan_from_input = _planning_runtime._workbench_plan_from_input
_workbench_plan_has_started = _planning_runtime._workbench_plan_has_started
_workbench_plan_title_key = _planning_runtime._workbench_plan_title_key
_workbench_reconcile_revised_plan = _planning_runtime._workbench_reconcile_revised_plan
_workbench_render_task_brief_block = _planning_runtime._workbench_render_task_brief_block
_workbench_session_summary_text = _planning_runtime._workbench_session_summary_text
_workbench_step_dependencies_satisfied = _planning_runtime._workbench_step_dependencies_satisfied
_workbench_validate_plan_graph = _planning_runtime._workbench_validate_plan_graph


def _workbench_normalize_attachments(attachments: Any) -> list[dict[str, Any]]:
    """Mirror the /api/chat attachment normalization for workbench runs."""
    items = attachments if isinstance(attachments, list) else []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if not str(item.get("path") or "").strip():
            continue
        norm: dict[str, Any] = {
            "id": str(item.get("id") or "").strip(),
            "name": str(item.get("name") or "file"),
            "path": str(item.get("path") or ""),
            "content_type": str(item.get("content_type") or "application/octet-stream"),
            "size": int(item.get("size") or 0),
            "kind": str(item.get("kind") or "file"),
        }
        if str(item.get("width", "")).strip().isdigit():
            norm["width"] = int(item.get("width"))
        if str(item.get("height", "")).strip().isdigit():
            norm["height"] = int(item.get("height"))
        out.append(norm)
    return out


async def _workbench_register_attachments_kb(
    session_id: str, items: list[dict[str, Any]]
) -> None:
    """Register attachments sent in a Workbench session into its project KB.

    Idempotent by content hash; mirrors the send_file tool's registration so
    user uploads land in the same workspace-scoped database as agent output.
    """
    try:
        from cyrene.knowledge import ingest, store
        from cyrene.workbench.context import ensure_knowledge_db_for_session

        if not items:
            return
        kb_db_path = await ensure_knowledge_db_for_session(session_id)
        for item in items:
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            doc_file = Path(path)
            if not doc_file.is_file():
                continue
            content_hash = await asyncio.to_thread(store.content_hash_file, doc_file)
            doc = await store.upsert_document_by_path(
                kb_db_path,
                path=str(doc_file.resolve()),
                source="chat_upload",
                name=str(item.get("name") or doc_file.name),
                content_type=str(item.get("content_type") or "application/octet-stream"),
                kind=str(item.get("kind") or "file"),
                size=int(item.get("size") or 0) or doc_file.stat().st_size,
                metadata={"session_id": session_id},
                content_hash=content_hash,
            )
            if doc.get("status") in {"pending", "error"}:
                asyncio.create_task(ingest.index_document(kb_db_path, doc["id"]))
    except Exception:
        logger.exception(
            "Failed to register chat attachments in knowledge base for session %s",
            session_id,
        )


def _tool_args_preview(args: Any) -> str:
    """One-line compact preview of tool arguments (≤80 chars)."""
    if not isinstance(args, dict) or not args:
        return ""
    parts: list[str] = []
    for v in args.values():
        if v is None or v == "":
            continue
        sv = str(v).strip()
        if not sv:
            continue
        sv = sv.replace("\n", " ").replace("\r", "")
        if len(sv) > 50:
            sv = sv[:47] + "…"
        parts.append(sv)
        if len(parts) >= 2:
            break
    result = "  ".join(parts)
    return result[:80]


from cyrene.workbench import artifact_runtime as _artifact_runtime
_WORKBENCH_DIFF_SNAPSHOT_MAX_BYTES = _artifact_runtime._WORKBENCH_DIFF_SNAPSHOT_MAX_BYTES
_WORKBENCH_FINAL_KNOWLEDGE_STATUSES = _artifact_runtime._WORKBENCH_FINAL_KNOWLEDGE_STATUSES
_WORKBENCH_SNAPSHOT_IGNORED_DIRS = _artifact_runtime._WORKBENCH_SNAPSHOT_IGNORED_DIRS
_WORKBENCH_TEXT_SNAPSHOT_MAX_BYTES = _artifact_runtime._WORKBENCH_TEXT_SNAPSHOT_MAX_BYTES
_WORKBENCH_TEXT_SNAPSHOT_MAX_FILES = _artifact_runtime._WORKBENCH_TEXT_SNAPSHOT_MAX_FILES
_WORKBENCH_TEXT_SNAPSHOT_MAX_TOTAL_BYTES = _artifact_runtime._WORKBENCH_TEXT_SNAPSHOT_MAX_TOTAL_BYTES
_workbench_apply_step_file_changes = _artifact_runtime._workbench_apply_step_file_changes
_workbench_artifact_download_target = _artifact_runtime._workbench_artifact_download_target
_workbench_backfill_file_artifacts = _artifact_runtime._workbench_backfill_file_artifacts
_workbench_backfill_referenced_file_artifacts = _artifact_runtime._workbench_backfill_referenced_file_artifacts
_workbench_collect_run_file_changes = _artifact_runtime._workbench_collect_run_file_changes
_workbench_current_file_snapshot_diff = _artifact_runtime._workbench_current_file_snapshot_diff
_workbench_display_path = _artifact_runtime._workbench_display_path
_workbench_file_change = _artifact_runtime._workbench_file_change
_workbench_file_changes_from_tool_event = _artifact_runtime._workbench_file_changes_from_tool_event
_workbench_final_artifact_file_changes = _artifact_runtime._workbench_final_artifact_file_changes
_workbench_find_exported_copy = _artifact_runtime._workbench_find_exported_copy
_workbench_git_context = _artifact_runtime._workbench_git_context
_workbench_git_diff_for_path = _artifact_runtime._workbench_git_diff_for_path
_workbench_git_status_change_type = _artifact_runtime._workbench_git_status_change_type
_workbench_git_status_delta = _artifact_runtime._workbench_git_status_delta
_workbench_git_status_snapshot = _artifact_runtime._workbench_git_status_snapshot
_workbench_is_artifact_change = _artifact_runtime._workbench_is_artifact_change
_workbench_merge_file_changes = _artifact_runtime._workbench_merge_file_changes
_workbench_promote_file_artifacts = _artifact_runtime._workbench_promote_file_artifacts
_workbench_prune_invalid_file_records = _artifact_runtime._workbench_prune_invalid_file_records
_workbench_prune_non_file_artifacts = _artifact_runtime._workbench_prune_non_file_artifacts
_workbench_recorded_diff_for_path = _artifact_runtime._workbench_recorded_diff_for_path
_workbench_resolve_workspace_file = _artifact_runtime._workbench_resolve_workspace_file
_workbench_unified_diff = _artifact_runtime._workbench_unified_diff
_workbench_workspace_file_snapshot = _artifact_runtime._workbench_workspace_file_snapshot
_workbench_workspace_root = _artifact_runtime._workbench_workspace_root
_workbench_workspace_snapshot_delta = _artifact_runtime._workbench_workspace_snapshot_delta
_workbench_workspace_text_snapshot = _artifact_runtime._workbench_workspace_text_snapshot


async def _workbench_archive_run_knowledge(
    project: dict[str, Any],
    session: dict[str, Any],
    run: dict[str, Any],
    workspace_root: Path | None,
    now: str,
) -> list[dict[str, Any]]:
    """Best-effort durable archive of a task's final deliverables."""
    if str(session.get("status") or "").strip() not in _WORKBENCH_FINAL_KNOWLEDGE_STATUSES:
        return []
    final_artifacts = _workbench_final_artifact_file_changes(session)
    if not final_artifacts:
        return []
    try:
        from cyrene.knowledge.workbench import archive_workbench_run

        archive_root = workspace_root or WORKSPACE_DIR.resolve()
        documents = await archive_workbench_run(
            # Knowledge is keyed on the project id (like memory), not dataKey, so
            # the legacy default project does not archive into the shared global
            # kb_default.db catalog. See
            # cyrene.knowledge.workspace.resolve_workspace_id.
            data_key=_workbench_project_memory_key(project),
            session_id=str(session.get("id") or ""),
            run_id=str(run.get("id") or ""),
            title=str(session.get("title") or "Workbench task"),
            goal=str(session.get("goal") or ""),
            user_input=str(run.get("userInput") or ""),
            agent_response=str(run.get("agentResponse") or ""),
            file_changes=final_artifacts,
            workspace_root=archive_root,
            include_summary=False,
        )
    except Exception:
        logger.exception(
            "Failed to archive Workbench run %s into project knowledge",
            run.get("id"),
        )
        return []

    document_ids = [
        str(document.get("id") or "")
        for document in documents
        if isinstance(document, dict) and str(document.get("id") or "")
    ]
    if not document_ids:
        return documents

    run["knowledgeDocumentIds"] = document_ids
    context = project.get("context") if isinstance(project.get("context"), dict) else {}
    known_ids = context.get("knowledgeDocumentIds")
    known_ids = list(known_ids) if isinstance(known_ids, list) else []
    for document_id in document_ids:
        if document_id not in known_ids:
            known_ids.append(document_id)
    context["knowledgeDocumentIds"] = known_ids
    project["context"] = context

    return documents


def _collect_run_tool_events(session_id: str, run_start_ts: str, run_id: str, workspace_root: Path | None = None) -> list[dict[str, Any]]:
    """Return ToolCallEvent dicts for tool calls published during an agent run."""
    return [
        event for event in _collect_run_activity_events(session_id, run_start_ts, run_id, workspace_root)
        if event.get("type") == "ToolCallEvent"
    ]


def _workbench_actor_label(caller: Any, agent_id: Any = "") -> str:
    raw_agent = str(agent_id or "").strip()
    raw = str(caller or "").strip()
    if raw_agent:
        return raw_agent
    if raw.startswith("subagent_"):
        return raw.replace("subagent_", "", 1) or raw
    if raw == "main_agent":
        return "main agent"
    return raw or "agent"


def _workbench_subagent_status_text(status: Any) -> str:
    mapping = {
        "running": "正在执行",
        "resumed": "恢复执行",
        "waiting": "等待其他 subagent",
        "done": "已完成",
        "timeout": "已超时",
        "incomplete": "部分完成",
    }
    return mapping.get(str(status or "").strip(), str(status or "").strip() or "状态更新")


def _collect_run_activity_events(session_id: str, run_start_ts: str, run_id: str, workspace_root: Path | None = None) -> list[dict[str, Any]]:
    """Return workbench log events for runtime activity published during a run."""
    from cyrene.observability.debug import get_recent_events

    raw = get_recent_events(500)
    out: list[dict[str, Any]] = []
    for e in raw:
        if str(e.get("session_id") or "") != session_id:
            continue
        ts = str(e.get("timestamp") or "")
        if ts and ts < run_start_ts:
            continue
        event_type = str(e.get("type") or "")
        created_at = ts or run_start_ts

        if event_type == "tool_call":
            tool_name = str(e.get("tool") or "").strip()
            if not tool_name:
                continue
            actor = _workbench_actor_label(e.get("caller"))
            file_changes = _workbench_file_changes_from_tool_event(e, workspace_root)
            out.append({
                "id": _short_id("event"),
                "type": "ToolCallEvent",
                "runId": run_id,
                "createdAt": created_at,
                "tool": tool_name,
                "actor": actor,
                "argsPreview": _tool_args_preview(e.get("args")),
                "fileChanges": file_changes,
                "body": f"{actor} 调用工具 {tool_name}",
            })
        elif event_type == "llm_call":
            actor = _workbench_actor_label(e.get("caller"))
            phase = str(e.get("phase") or "").strip()
            llm_status = str(e.get("status") or "completed").strip()
            duration = e.get("duration_ms")
            duration_text = ""
            try:
                if duration is not None:
                    duration_text = f"，耗时 {float(duration) / 1000:.1f}s"
            except (TypeError, ValueError):
                duration_text = ""
            tools = e.get("tools") if isinstance(e.get("tools"), list) else []
            tool_text = f"，可用工具 {len(tools)} 个" if tools else ""
            phase_text = f"（{phase}）" if phase else ""
            out.append({
                "id": _short_id("event"),
                "type": "LlmCallEvent",
                "runId": run_id,
                "createdAt": created_at,
                "actor": actor,
                "phase": phase,
                "model": str(e.get("model") or ""),
                "status": llm_status,
                "body": (
                    f"{actor} 正在思考{phase_text}"
                    if llm_status == "started"
                    else f"{actor} 完成一轮思考{phase_text}{tool_text}{duration_text}"
                ),
            })
        elif event_type == "subagent_update":
            actor = _workbench_actor_label("", e.get("agent_id"))
            status_text = _workbench_subagent_status_text(e.get("status"))
            task = str(e.get("task") or "").strip()
            message = str(e.get("message") or "").strip()
            detail = message or task
            body = f"{actor} {status_text}" + (f"：{detail[:180]}" if detail else "")
            out.append({
                "id": _short_id("event"),
                "type": "SubagentStatusEvent",
                "runId": run_id,
                "createdAt": created_at,
                "actor": actor,
                "status": str(e.get("status") or ""),
                "message": message,
                "body": body,
            })
    out.sort(key=lambda item: str(item.get("createdAt") or ""))
    return out


# ---- Deep reflection for task sessions -------------------------------------
# Reflection runs over a task session's accumulated agent history (the same
# per-session store each step run writes to). It is cross-session safe: the
# history is loaded by id, so we can reflect on a session that is not the
# active one (e.g. a failed task being forked into a fresh retry).

def _workbench_session_history(session_id: str) -> list[dict[str, Any]]:
    """Load a task session's user-visible agent message history."""
    sid = str(session_id or "").strip()
    if not sid:
        return []
    try:
        from cyrene.agent.session import load_session_state
        state = load_session_state(sid)
    except Exception:
        logger.exception("Failed to load session history for reflection: %s", sid)
        return []
    messages = state.get("messages", []) if isinstance(state, dict) else []
    return [
        m for m in messages
        if isinstance(m, dict)
        and str(m.get("role") or "") != "system"
        and not bool(m.get("hidden_from_ui"))
        and not bool(m.get("deep_reflection_record"))
    ]


async def _workbench_run_reflection(
    session_id: str,
    *,
    focus: str = "",
    goal_gap: str = "",
) -> dict[str, Any] | None:
    """Run deep reflection over a session's history; return the packet dict."""
    history = _workbench_session_history(session_id)
    if not history:
        return None
    from cyrene.agent.deep_reflection import create_deep_reflection_record
    # Tag reflection runtime events to the session being reflected on.
    binding = bind_run_context(session_id=str(session_id or ""))
    try:
        record = await create_deep_reflection_record(
            list(history),
            scope="session_tail",
            goal_gap=goal_gap or "任务执行未达成目标/验收，需要复盘并重整方向。",
            focus=str(focus or ""),
            lang_text=str(focus or goal_gap or "深度反思"),
        )
    except Exception:
        logger.exception("Workbench deep reflection failed for session %s", session_id)
        return None
    finally:
        binding.reset()
    packet = record.get("packet") if isinstance(record, dict) else None
    return packet if isinstance(packet, dict) else None


def _workbench_sink_reflection_insights(
    project: dict[str, Any] | None,
    packet: dict[str, Any],
) -> None:
    """Distill a reflection packet's durable insights (excluded_paths /
    promising_directions) into the project memory store under the internal
    ``reflection`` category, so they still propagate to (and are injected into)
    every session in the project — but stay HIDDEN from the user memory page
    rather than inflating the user-facing "fact" bucket. Best-effort."""
    if not project or not isinstance(packet, dict):
        return
    try:
        data_key = _workbench_project_memory_key(project)
        excluded = packet.get("excluded_paths") if isinstance(packet.get("excluded_paths"), list) else []
        promising = packet.get("promising_directions") if isinstance(packet.get("promising_directions"), list) else []
        for path in excluded[:5]:
            text = str(path or "").strip()
            if text:
                _memory_service().add_agent_memory(data_key, "避免：" + text, category="reflection", source="agent", tags=["反思", "死路"])
        for direction in promising[:5]:
            text = str(direction or "").strip()
            if text:
                _memory_service().add_agent_memory(data_key, "有效方向：" + text, category="reflection", source="agent", tags=["反思", "有效方向"])
    except Exception:
        logger.exception("Failed to sink reflection insights into project memory")


def _workbench_store_reflection(
    session: dict[str, Any],
    packet: dict[str, Any],
    *,
    trigger: str = "manual",
    source_session_id: str = "",
    project: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach a reflection packet to a task session (+ timeline event).

    When ``project`` is given, the packet's durable insights are also distilled
    into the project memory store (cross-session propagation + memory page)."""
    entry = {
        "packet": packet,
        "createdAt": _utc_now_iso(),
        "trigger": str(trigger or "manual"),
        "sourceSessionId": str(source_session_id or session.get("id") or ""),
    }
    session["reflection"] = entry
    next_step = str(packet.get("next_step") or "").strip()
    body = "已完成深度反思。" + (f"建议下一步：{next_step}" if next_step else "已生成方向重整建议。")
    session["events"] = list(session.get("events") or []) + [{
        "id": _short_id("event"),
        "type": "DeepReflection",
        "createdAt": entry["createdAt"],
        "body": body,
    }]
    _workbench_sink_reflection_insights(project, packet)
    return entry


def _workbench_render_reflection_block(session: dict[str, Any]) -> str:
    """Render the session's current reflection packet as a prompt text block."""
    reflection = session.get("reflection") if isinstance(session.get("reflection"), dict) else None
    packet = reflection.get("packet") if isinstance(reflection, dict) else None
    if not isinstance(packet, dict) or not packet:
        return ""
    try:
        from cyrene.agent.deep_reflection_prompts import render_deep_reflection_packet
        return render_deep_reflection_packet(packet)
    except Exception:
        logger.exception("Failed to render reflection packet")
        return ""


# Run-invariant task-mode framing for Workbench task runs. These instructions
# never change between runs, so they belong in the cache-stable SYSTEM prefix
# (injected via ``run_agent(static_system_extra=...)``) rather than the per-run
# ``ephemeral_system`` tail, which is re-processed on every tool round.
_WORKBENCH_TASK_MODE_SYSTEM = (
    "## 任务执行模式\n"
    "你正在一个带有可编辑「执行计划」的任务里工作，本工作台鼓励先规划再执行。\n"
    "- 需要动手完成多步工作时，优先制定或更新执行计划，再按计划逐步推进，不要脱离计划临时发挥。\n"
    "- 已有计划时以它为准，按步骤推进；发现计划需要调整就调用 update_task_plan 更新当前任务的执行计划，而不是只在回复里描述新计划。\n"
    "- 仅当用户只是提问、或一句话就能完成的小事时，才直接回答或执行、无需计划。\n"
    "- 如果这个任务还没有明确目标，或现有目标/标题与你实际要做的事不符（例如用户开场只是提了个"
    "问题），就调用 set_task_goal 设定一个简洁的目标和短标题。"
    "\n\n"
    "## 把产物交付给用户\n"
    "任务的交付物（报告、数据、导出文件、生成的代码包等）要让用户能在「产物」面板下载：\n"
    "- 你需要判断哪些是真正面向用户的最终交付物，并用 send_file 声明；只写文件路径不算交付。\n"
    "- 用户指定保存地址时，先保存文件，再对真实路径调用 send_file 登记产物；已授权的工作区外路径也适用。\n"
    "- 不要声明源代码、脚本、.tex、缓存、依赖、构建目录或中间数据，除非用户明确要求这些也是交付物。\n"
    "- 例：代码生成数据分析报告时，默认只交付最终报告（如 PDF/HTML/Markdown），不交付分析脚本；"
    "LaTeX 生成文档时，默认只交付编译后的 PDF，不交付 .tex/.aux/.log。\n"
    "- 如果最终交付文件是通过 Bash/shell/命令行生成的，也必须用 send_file 声明，否则不会出现在「产物」面板。\n"
    "- send_file 的 path 参数写文件当前的实际位置即可（工作区相对路径或已授权的外部路径），不需要挪到专门目录。\n"
    "- 不要只在回复里写出文件路径就当作已经交付。"
)

_WORKBENCH_TASK_REPLY_DIRECTIVE = (
    "## 本轮任务对话回复模式\n"
    "用户这次是在任务里继续对话或提问。优先根据当前任务上下文直接回复，把完整回复写在普通 "
    "assistant 内容中，quit 只作为无回答文本参数的终止信号。不要因为处于任务页就自动查看文件、执行命令或更新计划。只有用户明确要求"
    "检查/执行/修改，或缺少必要事实无法准确回答时，才进入工具执行。若用户明确要求增删、"
    "重排或实质修改步骤，再调用 update_task_plan。"
)

def _workbench_compose_static_system(
    project: dict[str, Any] | None = None,
    session: dict[str, Any] | None = None,
) -> str:
    """Cache-stable system block for Workbench (system prefix).

    Only includes invariant task-mode framing. Project memory and reflection
    packets are session/run scoped, so they are injected via ephemeral context
    instead of changing the byte-stable system prefix.
    """
    return _WORKBENCH_TASK_MODE_SYSTEM


def _workbench_compose_memory_ephemeral(
    project: dict[str, Any] | None,
    session: dict[str, Any],
    *,
    entries: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """Return (run_fixed_memory, volatile_tail_memory) for this Workbench session.

    A session snapshots the project-memory ids it first saw. Those memories stay
    in the run-fixed block for cache stability. Memories created later in the
    same session are rendered in the volatile tail so they remain visible without
    invalidating the already-established fixed prefix. A new session snapshots
    again and promotes them back into the fixed block.

    ``entries`` is the already-loaded memory document: the caller composes the
    fixed and volatile blocks once per run, so the three internal reads collapse
    into a single load.
    """
    if not project:
        return "", ""
    memory_key = _workbench_project_memory_key(project)
    if entries is None:
        # Load the memory document once so id selection and both renders share
        # a single read instead of each loading it independently.
        try:
            entries = _memory_service()._load(memory_key)
        except Exception:
            logger.exception("Failed to load project memory for prompt injection")
            return "", ""
    try:
        current_ids = _memory_service().memory_injection_ids(
            memory_key, entries=entries
        )
    except Exception:
        logger.exception("Failed to list project memory ids for prompt injection")
        return "", ""

    stored_key = str(session.get("_promptMemoryKey") or "")
    raw_base_ids = session.get("_promptMemoryBaseIds")
    if stored_key != memory_key or not isinstance(raw_base_ids, list):
        base_ids = list(current_ids)
        session["_promptMemoryKey"] = memory_key
        session["_promptMemoryBaseIds"] = list(base_ids)
    else:
        base_ids = [str(item) for item in raw_base_ids if str(item).strip()]

    # Build injection list: top 20 by mention_count + 5 random from the rest.
    # Snapshot at session start — once stored in session, same set is reused
    # for every request in this session, so prompt cache stays stable.
    stored_inject = session.get("_promptMemoryInjectIds")
    if stored_key != memory_key or not stored_inject:
        inject_ids = base_ids[:20]
        if len(base_ids) > 20:
            n_extra = min(5, len(base_ids) - 20)
            inject_ids += random.sample(base_ids[20:], n_extra)
        session["_promptMemoryInjectIds"] = inject_ids
    else:
        inject_ids = stored_inject

    base_set = set(base_ids)
    new_ids = [mem_id for mem_id in current_ids if mem_id not in base_set]
    try:
        fixed = _memory_service().render_memory_for_injection(
            memory_key,
            include_ids=inject_ids,
            preserve_id_order=True,
            limit=25,
            entries=entries,
        )
        volatile = _memory_service().render_memory_for_injection(
            memory_key,
            include_ids=new_ids,
            preserve_id_order=True,
            header="## 本 session 新增项目记忆（刚写入，放在最后供本轮参考；与当前任务无关则忽略）",
            entries=entries,
        ) if new_ids else ""
    except Exception:
        logger.exception("Failed to render project memory for prompt injection")
        return "", ""
    return fixed, volatile


# ── Per-run context enrichment (ephemeral_system tail, cache-safe) ──────

def _workbench_render_workspace_state_block(
    session: dict[str, Any],
    workspace_root: Path | None,
    *,
    recent_run_count: int = 2,
) -> str:
    """Progressive context enrichment: recent file changes + git status + last N
    run summaries. Deterministic (no LLM), cheap to compute, cache-safe in tail."""
    lines: list[str] = []
    # ── Recently changed files (from session run records, zero I/O) ─────
    runs = session.get("runs") if isinstance(session.get("runs"), list) else []
    if runs:
        recent_paths: list[str] = []
        seen: set[str] = set()
        for run in runs[-recent_run_count:]:
            if not isinstance(run, dict):
                continue
            for fc in (run.get("fileChanges") or []):
                if not isinstance(fc, dict):
                    continue
                path = str(fc.get("path") or fc.get("newPath") or "").strip()
                if path and path not in seen:
                    seen.add(path)
                    recent_paths.append(path)
        if recent_paths:
            lines.append("- 近期变更文件：" + "、".join(recent_paths[:12]))
    # ── Git status summary ──────────────────────────────────────────────
    git_snap = _workbench_git_status_snapshot(workspace_root)
    if git_snap:
        total = len(git_snap)
        # porcelain=v1 2-char code: index 0 = staging status, index 1 = worktree status.
        # Staged: index 0 is NOT space (modified in index) AND not '?' (untracked).
        staged = sum(1 for v in git_snap.values() if v[0] not in (' ', '?'))
        lines.append(f"- Git 状态：{total} files changed ({staged} staged)")
    # ── Recent run summaries (same session) ─────────────────────────────
    if runs and recent_run_count > 0:
        for run in runs[-recent_run_count:]:
            if not isinstance(run, dict):
                continue
            ui = str(run.get("userInput") or "")[:80]
            ar = str(run.get("agentResponse") or "")[:80]
            if ui or ar:
                lines.append(f"- 上次执行: 「{ui}」→「{ar}」")
    if not lines:
        return ""
    return "## 当前工作区状态\n" + "\n".join(lines)


def _workbench_render_step_context_block(
    session: dict[str, Any],
    current_step_id: str = "",
) -> str:
    """Step context cascade: inject completed-steps outcomes so the agent
    knows what preceding steps produced without re-exploring the workspace."""
    plan = session.get("plan") if isinstance(session.get("plan"), list) else []
    if not plan:
        return ""
    ordered = sorted(
        (s for s in plan if isinstance(s, dict)),
        key=lambda s: int(s.get("order") or 0),
    )
    blocks: list[str] = []
    for step in ordered:
        sid = str(step.get("id") or "")
        if sid == current_step_id:
            break  # don't include current step or steps after it
        outcome = step.get("outcome") if isinstance(step.get("outcome"), dict) else None
        status = str(step.get("status") or "pending")
        if outcome and status in ("completed", "done"):
            title = str(step.get("title") or sid)[:60]
            summary = str(outcome.get("summary") or "")[:120]
            files_changed = outcome.get("filesChanged") if isinstance(outcome.get("filesChanged"), list) else []
            issues = outcome.get("issues") if isinstance(outcome.get("issues"), list) else []
            block = f"Step [{status}] {title}\n  摘要：{summary}"
            if files_changed:
                block += f"\n  产出文件：{'、'.join(str(f) for f in files_changed[:8])}"
            if issues:
                block += f"\n  注意事项：{'；'.join(str(i) for i in issues[:3])}"
            blocks.append(block)
        elif status in ("completed", "done"):
            title = str(step.get("title") or sid)[:60]
            blocks.append(f"Step [{status}] {title}（无详细摘要）")
    if not blocks:
        return ""
    return "## 已完成步骤摘要\n" + "\n".join(blocks)


async def _workbench_generate_step_outcome(
    step: dict[str, Any],
    agent_reply: str,
    user_input: str = "",
) -> dict[str, Any] | None:
    """Generate a structured step-outcome summary from the agent reply + step
    definition. One lightweight secondary LLM call; failures are silent (best-effort).

    The outcome is stored on the step dict in-place and also returned.
    """
    title = str(step.get("title") or "").strip()
    description = str(step.get("description") or "").strip()
    reply_snippet = str(agent_reply or "")[:2000]
    user_snippet = str(user_input or "")[:500]
    prompt = (
        "用一句话（中文，<=60字）概括 agent 在这个步骤中做了什么，并列出关键信息。"
        "只返回 JSON：{\"summary\":\"一句话概括\",\"filesChanged\":[\"文件路径\"],"
        "\"keyDecisions\":[\"关键决策（<=30字）\"],\"issues\":[\"遇到的问题（<=30字）\"]}。\n\n"
        f"步骤标题：{title}\n步骤说明：{description}\n用户输入：{user_snippet}\n"
        f"Agent 回复/执行结果：{reply_snippet}"
    )
    try:
        resp = await asyncio.wait_for(
            _call_llm([{"role": "user", "content": prompt}], tools=None, max_tokens=900, secondary=True, thinking="disabled"),
            timeout=20,
        )
    except Exception:
        return None
    parsed = _workbench_parse_json_object(resp.get("content") or "")
    if not isinstance(parsed, dict):
        return None
    outcome: dict[str, Any] = {
        "summary": str(parsed.get("summary") or "").strip()[:120],
        "filesChanged": [
            str(f).strip()[:200]
            for f in (parsed.get("filesChanged") or [])
            if isinstance(f, str) and str(f).strip()
        ][:10],
        "keyDecisions": [
            str(d).strip()[:60]
            for d in (parsed.get("keyDecisions") or [])
            if isinstance(d, str) and str(d).strip()
        ][:5],
        "issues": [
            str(i).strip()[:60]
            for i in (parsed.get("issues") or [])
            if isinstance(i, str) and str(i).strip()
        ][:3],
        "generatedAt": _utc_now_iso(),
    }
    step["outcome"] = outcome
    return outcome


# ── Task completion report (cross-session linkage) ──────────────────────

def _workbench_render_past_task_reports(project: dict[str, Any] | None) -> str:
    """Render past task completion reports for injection into the plan-generation
    prompt. Separate from regular memory injection — these are only useful at
    planning time, not during every agent run."""
    if not project:
        return ""
    data_key = _workbench_project_memory_key(project)
    try:
        return _memory_service().render_task_reports_for_planning(data_key, limit=3, max_chars=2500)
    except Exception:
        logger.exception("Failed to render past task reports for planning")
        return ""


def _workbench_compose_task_report_text(session: dict[str, Any]) -> str:
    """Compose a compact task report directly from session structured data.
    No LLM call — goal, step outcomes, issues, files, verification results are
    already on the session. Returns "" when there is nothing worth reporting."""
    goal = str(session.get("goal") or session.get("title") or "").strip()
    if not goal:
        return ""
    lines: list[str] = [f"任务：{goal}"]
    plan = session.get("plan") if isinstance(session.get("plan"), list) else []
    completed = [
        s for s in plan
        if isinstance(s, dict)
        and str(s.get("status") or "") in ("completed", "done")
        and isinstance(s.get("outcome"), dict)
    ]
    if completed:
        for s in completed[:6]:
            summary = str(s["outcome"].get("summary") or "").strip()
            if summary:
                lines.append(f"  - {summary}")
    # Issues across completed steps
    issues: list[str] = []
    for s in completed:
        raw = s["outcome"].get("issues")
        if not isinstance(raw, (list, tuple)):
            continue
        for issue in raw[:2]:
            text = str(issue).strip()
            if text and text not in issues:
                issues.append(text)
    if issues:
        lines.append(f"踩坑：{'；'.join(issues[:5])}")
    # Verification
    verify = str(session.get("verifyReason") or "").strip()
    criteria = session.get("acceptanceCriteria") if isinstance(session.get("acceptanceCriteria"), list) else []
    if criteria:
        passed = sum(1 for c in criteria if isinstance(c, dict) and c.get("status") == "passed")
        lines.append(f"验收：{passed}/{len(criteria)} 通过" + (f"——{verify}" if verify else ""))
    elif verify:
        lines.append(f"验收：{verify}")
    # Key files (deduplicated, from step outcomes)
    files: list[str] = []
    for s in completed:
        raw = s["outcome"].get("filesChanged")
        if not isinstance(raw, (list, tuple)):
            continue
        for f in raw[:3]:
            text = str(f).strip()
            if text and text not in files:
                files.append(text)
    if files:
        lines.append(f"关键文件：{'、'.join(files[:8])}")
    return "\n".join(lines)


async def _workbench_generate_task_report(
    project: dict[str, Any],
    session: dict[str, Any],
) -> str | None:
    """Compose a task completion report from session data and store as project
    memory (category=task_report). Best-effort; failures are silent."""
    report = _workbench_compose_task_report_text(session)
    if len(report) < 20:
        return None
    data_key = _workbench_project_memory_key(project)
    try:
        _memory_service().add_agent_memory(
            data_key,
            report,
            category="task_report",
            tags=["任务报告", "自动生成"],
            source="agent",
        )
    except Exception:
        logger.exception("Failed to store task report for %s", data_key)
        return None
    return report


def _schedule_task_report(
    project: dict[str, Any],
    session: dict[str, Any],
) -> None:
    """Fire-and-forget task completion report generation."""
    async def _runner() -> None:
        try:
            report = await _workbench_generate_task_report(project, session)
            if report:
                logger.info("Task report generated for session %s", session.get("id"))
        except Exception:
            logger.debug("Task report generation failed", exc_info=True)
    try:
        asyncio.create_task(_runner())
    except RuntimeError:
        pass


def _workbench_compose_ephemeral_system(
    project: dict[str, Any] | None,
    session: dict[str, Any],
    *,
    step_id: str = "",
    workspace_root: Path | None = None,
    memory_pair: tuple[str, str] | None = None,
) -> str:
    """Assemble run-fixed context for a Workbench agent run.

    The coordinator inserts this block before the current user turn, not into the
    base system prefix and not at the absolute prompt tail. That keeps volatile
    Workbench context out of cross-run system caching while allowing tool rounds
    in this run to reuse the full previous prompt as a prefix.

    Blocks (in order): Workbench task shared context → project memory snapshot →
    reflection seed → step context cascade → workspace state.

    ``memory_pair`` lets a caller that already composed the memory blocks once
    (see :func:`_workbench_compose_memory_ephemeral`) reuse them for both the
    fixed and volatile blocks instead of composing twice per run.
    """
    parts: list[str] = []
    # 1. Workbench task shared context: project blocks first, then session task /
    # plan / acceptance.  This applies only to Workbench task sessions.
    shared_task_context = _workbench_task_build_main_context(project, session)
    if shared_task_context:
        parts.append(shared_task_context)
    # 2. Project durable memories: snapshot at session start for cache stability.
    if memory_pair is not None:
        memory_block, _new_memory_tail = memory_pair
    else:
        memory_block, _new_memory_tail = _workbench_compose_memory_ephemeral(project, session)
    if memory_block:
        parts.append(memory_block)
    # 3. Reflection seed: session scoped; keep out of the base system prefix.
    reflection_seed = _workbench_render_reflection_block(session)
    if reflection_seed:
        parts.append(
            "## 深度反思结论（执行时请避开 excluded_paths，优先 promising_directions）\n"
            + reflection_seed
        )
    # 4. Step context cascade: what preceding steps produced (same session).
    step_block = _workbench_render_step_context_block(session, current_step_id=step_id)
    if step_block:
        parts.append(step_block)
    # 5. Workspace state: recent file changes, git status, recent run summaries.
    state_block = _workbench_render_workspace_state_block(session, workspace_root)
    if state_block:
        parts.append(state_block)
    return "\n\n".join(parts).strip()


def _workbench_compose_volatile_ephemeral_system(
    project: dict[str, Any] | None,
    session: dict[str, Any],
    *,
    memory_pair: tuple[str, str] | None = None,
) -> str:
    """Context that intentionally stays at the absolute prompt tail."""
    if memory_pair is not None:
        _fixed, new_memory_tail = memory_pair
    else:
        _fixed, new_memory_tail = _workbench_compose_memory_ephemeral(project, session)
    shared_tail = _workbench_task_build_volatile_context(project, session)
    return "\n\n".join(part for part in (shared_tail, new_memory_tail) if part).strip()


def _workbench_finalize_directive(session: dict[str, Any]) -> str:
    """Completion/handoff directive for the finalize dispatch path: the user
    considers the task done and wants the deliverables surfaced, not more work.

    Instructs the agent to summarize and hand off what already exists — never
    re-plan or re-run steps — and lists the artifacts already on record so the
    summary is grounded on real outputs instead of re-derived from scratch."""
    lines = [
        "## 收尾交付（用户认为任务已完成）",
        "用户判断这个任务已经做完，想直接看到并验收成果。请严格按下面来：",
        "- 不要重新规划、不要新增或重排步骤、也不要重复执行已完成的工作。",
        "- 核对工作区里已经产出的成果，把这次任务的【最终成果】清晰地汇总在这条回复里交付给用户："
        "先用一两句话说明任务结论与完成情况，再分点给出关键产出——结论性的内容（数据、要点、说明）"
        "直接写进回复，交付文件则点出文件名/路径，方便用户查看。",
        "- 如果某个交付文件是用 Bash/脚本/命令行生成、还没登记为可下载产物，请用 send_file 声明它；"
        "只声明最终交付物，不要把中间代码、脚本、.tex、缓存、构建日志或临时数据声明为交付物。",
        "- 如果任务产出是数据分析报告，最终报告进产物；分析代码默认不进。"
        "如果任务产出是 LaTeX 文档，最终 PDF 进产物；.tex/.aux/.log 默认不进。",
        "- 只在为了汇总成果而确有必要时才读取文件；不要借收尾之机开展新工作。",
        "- 如果你核对后发现成果其实并不完整、或与目标不符，就如实说明还差什么、建议下一步，"
        "而不是假装已经完成。",
    ]
    artifacts = session.get("artifacts") if isinstance(session.get("artifacts"), list) else []
    names: list[str] = []
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("path") or "").strip()
        if name and name not in names:
            names.append(name)
    if names:
        lines.append("- 已登记的产物（交付汇总里应当体现）：" + "；".join(names[:12]) + "。")
    return "\n".join(lines)


def _workbench_acceptance_repair_directive(session: dict[str, Any]) -> str:
    """Tell a same-session repair run how to use the latest verification.

    Verification is deliberately independent from the task agent, so the repair
    turn must receive an explicit hand-off: preserve passed criteria, inspect the
    failed evidence, and make the concrete workspace changes needed to satisfy
    the current task.  This directive keeps the action in the existing session
    instead of silently creating a new plan/task.
    """
    criteria = session.get("acceptanceCriteria") if isinstance(session.get("acceptanceCriteria"), list) else []
    failed = [
        item for item in criteria
        if isinstance(item, dict) and str(item.get("status") or "") == "failed"
    ]
    lines = [
        "## 验收未完全通过：继续修改当前 session",
        "这是一次基于最近验收结果的修复回合。必须继续当前 session，不要新建任务、不要只给建议，也不要跳过未通过标准。",
        "先检查工作区和已有产物，逐条处理未通过的验收标准；已通过的标准视为约束，不要为了修复其他问题回退它们。",
        "完成必要的代码、配置、测试或文档修改后，在回复中说明实际改动和仍未解决的风险。",
    ]
    if failed:
        lines.append("最近一次未通过的验收标准：")
        for item in failed[:8]:
            text = _workbench_clean_text(item.get("text"), 300)
            evidence = _workbench_clean_text(item.get("evidence"), 700)
            if text:
                lines.append("- " + text + (f"；验收依据：{evidence}" if evidence else ""))
    reason = _workbench_clean_text(session.get("verifyReason"), 1000)
    if reason:
        lines.append("验收器结论：" + reason)
    return "\n".join(lines)


async def _workbench_should_reflect(goal: str, acceptance: list[Any], feedback: str) -> bool:
    """Decide if feedback signals a goal-level miss (→ reflect) vs a minor tweak."""
    fb = str(feedback or "").strip()
    if not fb:
        return False
    accept_text = "；".join(
        str(a.get("text") or "") for a in (acceptance or []) if isinstance(a, dict) and str(a.get("text") or "").strip()
    )
    prompt = (
        "你在判断用户对一个任务结果的反馈，是意味着【整体方向/目标没达成、需要复盘重整】，"
        "还是只是【局部小修小补】。只返回 JSON：{\"reflect\": true/false}。\n\n"
        f"任务目标：{goal}\n验收标准：{accept_text}\n用户反馈：{fb}\n\n"
        "若反馈表达不满意、结果不对、偏离目标、要重做或换思路 → reflect=true；"
        "若只是微调措辞/参数/补充细节 → reflect=false。"
    )
    try:
        resp = await asyncio.wait_for(
            _call_llm([{"role": "user", "content": prompt}], tools=None, max_tokens=180, secondary=True, thinking="disabled"),
            timeout=30,
        )
    except Exception:
        logger.exception("Workbench reflect-classifier failed")
        return False
    parsed = _workbench_parse_json_object(resp.get("content") or "")
    return bool(parsed.get("reflect")) if isinstance(parsed, dict) else False


async def _workbench_classify_intent(text: str, session: dict[str, Any]) -> str:
    """Classify a task-composer input → ``answer`` | ``direct`` | ``plan``.

    Not every line typed into a task is a multi-step goal. A question wants a
    reply; a one-shot instruction wants to just be done; only a genuinely complex
    goal is worth planning first. One lightweight secondary call; any failure
    falls back to ``plan`` — the existing, safe behaviour."""
    src = str(text or "").strip()
    if not src:
        return "plan"
    goal = str(session.get("goal") or session.get("title") or "").strip()
    plan = session.get("plan") if isinstance(session.get("plan"), list) else []
    plan_note = (
        "\n注意：本任务已经有一份执行计划。只是单纯的增删步骤、调整顺序、修改步骤标题/说明 → "
        "command（agent 可直接使用 update_task_plan 工具修改）；需要结合项目内容重新规划执行路径、"
        "改变做法或追加目标 → task（会按计划修订处理）；只是就计划或项目提问 → question；"
        "一条立刻能做完的小改动 → command；表示整件事已经做完、要收尾/"
        "交付/验收、或让你把成果汇总出来给他看 → done（不要再据此规划或重排步骤）。"
        if plan else ""
    )
    prompt = (
        "你在判断用户在一个工作台「任务」里输入的一句话应该如何处理。"
        "只返回 JSON：{\"kind\":\"question\"|\"command\"|\"task\"|\"done\"}。\n\n"
        "- question：在提问或想了解信息，期待一个直接回答，本身不要求改动文件或执行操作。"
        "例：「这个项目用什么框架？」「登录逻辑在哪个文件？」\n"
        "- command：一条明确、范围清晰、基本一步就能做完的直接指令。"
        "例：「把 README 标题改成 X」「跑一下测试」「格式化这个文件」。\n"
        "- task：较复杂、需要拆成多步、值得先规划再执行的目标。"
        "例：「实现用户登录系统」「重构整个支付模块」。\n"
        "- done：用户认为整件任务已经做完，要收尾、交付成果或进入验收，或让你把这次任务"
        "已有的产出汇总给他看，而不是再开展新工作。"
        "例：「任务完成了」「就到这吧，把成果给我」「可以验收了」「整理一下最终交付物」。\n\n"
        f"当前任务背景：{goal or '（暂无）'}{plan_note}\n用户输入：{src}\n\n"
        "判定倾向：本工作台鼓励「先规划再执行」。除非是纯提问(question)、一句话立刻能做完的"
        "小事(command)、或明确表示任务已完成/要交付验收(done)，凡是要动手推进、涉及多步、"
        "或会改动多个文件/模块的工作，一律优先 task（制定或修改执行计划）。"
        "特别注意：用户说「完成了/搞定了/可以了/到这吧/去验收/给我成果」这类收尾话语时优先 "
        "done，不要再当成新任务去重新规划或重复执行。"
    )
    try:
        resp = await asyncio.wait_for(
            _call_llm([{"role": "user", "content": prompt}], tools=None, max_tokens=180, secondary=True, thinking="disabled"),
            timeout=20,
        )
    except Exception:
        logger.exception("Workbench intent-classifier failed")
        return "plan"
    parsed = _workbench_parse_json_object(resp.get("content") or "")
    kind = str(parsed.get("kind") or "").strip().lower() if isinstance(parsed, dict) else ""
    return {"question": "answer", "command": "direct", "task": "plan", "done": "finalize"}.get(kind, "plan")


# Session statuses still "open" enough that a sibling's reflection is worth a nudge.
_WORKBENCH_OPEN_STATUSES = {"idle", "pending", "planning", "paused", "review", "failed"}


async def _workbench_match_relevant_sessions(
    packet: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, str]:
    """Ask the LLM which candidate sessions a reflection packet is relevant to.

    One lightweight secondary call. Returns ``{sessionId: hint}`` for candidates
    judged genuinely relevant (threshold = the model's own relevant=true + a
    non-empty hint). Best-effort: any failure returns ``{}`` and never raises."""
    if not packet or not candidates:
        return {}
    objective = str(packet.get("objective") or packet.get("goal") or "").strip()
    excluded = packet.get("excluded_paths") if isinstance(packet.get("excluded_paths"), list) else []
    promising = packet.get("promising_directions") if isinstance(packet.get("promising_directions"), list) else []
    findings = "\n".join(filter(None, [
        ("目标：" + objective) if objective else "",
        ("应避免：" + "；".join(str(x) for x in excluded[:5])) if excluded else "",
        ("有效方向：" + "；".join(str(x) for x in promising[:5])) if promising else "",
    ])).strip()
    if not findings:
        return {}
    cand_lines = []
    for c in candidates:
        cid = str(c.get("id") or "")
        goal = str(c.get("goal") or c.get("title") or "").strip()
        cons = "；".join(str(x) for x in (c.get("constraints") or []) if str(x).strip())
        cand_lines.append(f"- id={cid}: 目标={goal}" + (f"；约束={cons}" if cons else ""))
    prompt = (
        "一个任务完成了深度反思，得到下面的结论。请判断这些结论对【其它正在进行的任务】是否有借鉴价值"
        "（例如同样会踩的死路、可复用的有效方向）。\n\n"
        f"【反思结论】\n{findings}\n\n"
        "【候选任务】\n" + "\n".join(cand_lines) + "\n\n"
        '只返回 JSON：{"matches":[{"sessionId":"...","relevant":true/false,'
        '"hint":"给该任务的一句具体建议（中文，<=40字）"}]}。'
        "只有确有借鉴价值才 relevant=true；无关一律 false，hint 留空。"
    )
    try:
        resp = await asyncio.wait_for(
            _call_llm([{"role": "user", "content": prompt}], tools=None, max_tokens=1200, secondary=True, thinking="disabled"),
            timeout=30,
        )
    except Exception:
        logger.exception("Workbench hint-matcher failed")
        return {}
    parsed = _workbench_parse_json_object(resp.get("content") or "")
    out: dict[str, str] = {}
    if isinstance(parsed, dict):
        for m in (parsed.get("matches") or []):
            if not isinstance(m, dict) or not bool(m.get("relevant")):
                continue
            sid = str(m.get("sessionId") or "").strip()
            hint = str(m.get("hint") or "").strip()
            if sid and hint:
                out[sid] = hint
    return out


async def _workbench_dispatch_reflection_hints(
    project: dict[str, Any] | None,
    source_session: dict[str, Any],
    packet: dict[str, Any],
) -> None:
    """After a reflection, nudge OTHER open sessions in the same project that the
    packet is relevant to — by appending a *pending* hint (+ a ReflectionHint
    event) for the user to accept or dismiss. Suggestion only: it never
    auto-modifies another session, stays within the project, and dedups one live
    hint per source session. Mutates sessions in-place under ``project`` so the
    caller's single store-write persists them atomically. Best-effort."""
    try:
        if not isinstance(project, dict) or not isinstance(packet, dict) or not packet:
            return
        source_id = str(source_session.get("id") or "")
        source_title = str(source_session.get("title") or "任务")
        candidates = [
            s for s in (project.get("sessions") or [])
            if isinstance(s, dict)
            and str(s.get("id") or "") != source_id
            and str(s.get("status") or "idle") in _WORKBENCH_OPEN_STATUSES
        ]
        if not candidates:
            return
        matches = await _workbench_match_relevant_sessions(packet, candidates)
        if not matches:
            return
        now = _utc_now_iso()
        for sess in candidates:
            hint_text = matches.get(str(sess.get("id") or ""))
            if not hint_text:
                continue
            hints = sess.get("pendingHints")
            if not isinstance(hints, list):
                hints = []
                sess["pendingHints"] = hints
            # Dedup: at most one live (pending) hint per source session.
            if any(
                isinstance(h, dict)
                and str(h.get("fromSessionId")) == source_id
                and str(h.get("status")) == "pending"
                for h in hints
            ):
                continue
            hints.append({
                "id": _short_id("hint"),
                "fromSessionId": source_id,
                "fromTitle": source_title,
                "hint": hint_text,
                "packet": packet,
                "status": "pending",
                "createdAt": now,
            })
            sess["events"] = list(sess.get("events") or []) + [{
                "id": _short_id("event"),
                "type": "ReflectionHint",
                "createdAt": now,
                "body": f"相关任务《{source_title}》反思发现：{hint_text}",
            }]
            sess["updatedAt"] = now
    except Exception:
        logger.exception("Failed to dispatch reflection hints")


def _workbench_merge_hint_mutations(
    orig_project: dict[str, Any],
    latest_project: dict[str, Any],
) -> None:
    """Merge ``pendingHints`` / ``events`` added by
    ``_workbench_dispatch_reflection_hints`` on *original* project sessions into
    the *latest* project sessions.  Only appends — never overwrites existing
    data.  Best-effort."""
    orig_sessions = orig_project.get("sessions") if isinstance(orig_project.get("sessions"), list) else []
    latest_sessions = latest_project.get("sessions") if isinstance(latest_project.get("sessions"), list) else []
    if not orig_sessions or not latest_sessions:
        return
    latest_by_id: dict[str, dict[str, Any]] = {}
    for s in latest_sessions:
        if isinstance(s, dict):
            sid = str(s.get("id") or "")
            if sid:
                latest_by_id[sid] = s
    for orig_sess in orig_sessions:
        if not isinstance(orig_sess, dict):
            continue
        orig_id = str(orig_sess.get("id") or "")
        if not orig_id:
            continue
        latest_sess = latest_by_id.get(orig_id)
        if latest_sess is None:
            continue
        orig_hints = orig_sess.get("pendingHints")
        if isinstance(orig_hints, list) and orig_hints:
            latest_hints = latest_sess.get("pendingHints")
            if not isinstance(latest_hints, list):
                latest_hints = []
                latest_sess["pendingHints"] = latest_hints
            existing_ids = {str(h.get("id") or "") for h in latest_hints if isinstance(h, dict)}
            for h in orig_hints:
                if isinstance(h, dict) and str(h.get("id") or "") not in existing_ids:
                    latest_hints.append(h)
        orig_events = orig_sess.get("events")
        if isinstance(orig_events, list) and orig_events:
            latest_events = latest_sess.get("events")
            if not isinstance(latest_events, list):
                latest_events = []
                latest_sess["events"] = latest_events
            existing_event_ids = {str(e.get("id") or "") for e in latest_events if isinstance(e, dict)}
            for e in orig_events:
                if isinstance(e, dict) and str(e.get("id") or "") not in existing_event_ids:
                    latest_events.append(e)
        latest_sess["updatedAt"] = orig_sess.get("updatedAt") or latest_sess.get("updatedAt")


async def _workbench_verify_acceptance(
    session: dict[str, Any],
    project: dict[str, Any],
) -> dict[str, Any] | None:
    """Independent acceptance agent: inspect the workspace + task results and
    judge each acceptance criterion, and whether reflection is advised."""
    goal = str(session.get("goal") or session.get("title") or "").strip()
    criteria = [a for a in (session.get("acceptanceCriteria") or []) if isinstance(a, dict)]
    if not criteria:
        return None
    workspace_path = str(project.get("workspacePath") or "").strip()
    workspace_root = Path(workspace_path).expanduser().resolve() if workspace_path else None
    crit_lines = "\n".join(f'- id={a.get("id")}: {a.get("text") or ""}' for a in criteria)
    prompt = (
        "你是上下文完全独立的验收 Agent。你看不到也不得依赖任务执行 Agent 的对话、"
        "推理、结论或自我报告。请只基于本提示提供的任务定义和工作区里的真实产物，"
        "逐条核验下面的验收标准是否达成。"
        "可以用 list_directory、read_file、glob 工具检查文件/结果，不要臆测。\n\n"
        f"任务目标：{goal}\n验收标准：\n{crit_lines}\n\n"
        "核验后只返回一个 JSON 对象，不要 Markdown：\n"
        "{\n"
        '  "results": [{"id": "标准id", "passed": true/false, "evidence": "依据（简短）"}],\n'
        '  "recommend_reflection": true/false,\n'
        '  "reason": "为什么建议/不建议深度反思（简短）"\n'
        "}\n"
        "只要有任一标准未达成，通常 recommend_reflection 应为 true。"
    )
    expected_ids = {str(item.get("id") or "") for item in criteria}
    # No max_tokens cap: a reasoning-heavy or verbose verdict must never be
    # truncated before it emits the JSON. Retry transient failures with backoff
    # so a single flaky reply does not pause the whole goal loop.
    last_error: _WorkbenchGenerationError | None = None
    for attempt in range(_WORKBENCH_VERIFY_MAX_ATTEMPTS):
        try:
            parsed = await _workbench_run_explore_agent(
                workspace_root, prompt, max_tokens=None, timeout=120,
                session_id=str(session.get("id") or ""),
                clean_context=True,
                raise_on_failure=True,
            )
            if not isinstance(parsed, dict):
                raise _WorkbenchGenerationError("response_format", "验收模型没有返回 JSON 对象。")

            raw_results = parsed.get("results")
            if not isinstance(raw_results, list):
                raise _WorkbenchGenerationError("response_format", "验收结果缺少 results 数组。")
            normalized_results: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            for result in raw_results:
                if not isinstance(result, dict):
                    continue
                result_id = str(result.get("id") or "").strip()
                if result_id not in expected_ids or result_id in seen_ids:
                    continue
                passed = result.get("passed")
                if not isinstance(passed, bool):
                    continue
                seen_ids.add(result_id)
                normalized_results.append({
                    "id": result_id,
                    "passed": passed,
                    "evidence": str(result.get("evidence") or "").strip(),
                })
            missing_ids = expected_ids - seen_ids
            if missing_ids:
                raise _WorkbenchGenerationError(
                    "response_format",
                    f"验收模型遗漏了 {len(missing_ids)} 条验收标准。",
                )
            return {
                **parsed,
                "results": normalized_results,
                "recommend_reflection": bool(parsed.get("recommend_reflection")),
                "reason": str(parsed.get("reason") or "").strip(),
            }
        except _WorkbenchGenerationError as exc:
            last_error = exc
            if exc.category in _WORKBENCH_VERIFY_NON_RETRYABLE or attempt >= _WORKBENCH_VERIFY_MAX_ATTEMPTS - 1:
                raise
            delay = _WORKBENCH_VERIFY_RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning(
                "Workbench acceptance verify failed (attempt %d/%d, category=%s); retrying in %.1fs: %s",
                attempt + 1, _WORKBENCH_VERIFY_MAX_ATTEMPTS, exc.category, delay, exc.message,
            )
            await asyncio.sleep(delay)
    # The loop body always returns or raises; this is only for type-checkers.
    if last_error is not None:
        raise last_error
    return None


def _option_label(option: Any) -> str:
    """Flatten an option to its display label. Options should be plain strings,
    but models sometimes emit objects despite the schema (and normalized
    pending-question options are stored as ``{"id", "label"}`` dicts). ``str()`` on
    such a dict would leak ``{'id': ..., 'label': ...}`` into the UI, so pull the
    first label-bearing field instead."""
    if isinstance(option, dict):
        for key in ("label", "text", "value", "title", "name"):
            val = str(option.get(key, "") or "").strip()
            if val:
                return val
        return ""
    return str(option or "").strip()


def _public_pending_question(q: dict[str, Any] | None) -> dict[str, Any] | None:
    """Public (camelCase) shape of a paused-round pending question for the UI.

    Returns ``None`` when there is no real question (empty dict / missing id).
    """
    if not isinstance(q, dict) or not str(q.get("id") or "").strip():
        return None
    meta = q.get("meta") if isinstance(q.get("meta"), dict) else {}
    public: dict[str, Any] = {
        "id": str(q.get("id") or ""),
        "text": str(q.get("text") or ""),
        "options": [lbl for o in (q.get("options") or []) if (lbl := _option_label(o))],
        "roundId": str(q.get("round_id") or ""),
        "clientRequestId": str(q.get("client_request_id") or ""),
        "ownerLane": (
            str(q.get("owner_lane") or "decision").strip().lower()
            if str(q.get("owner_lane") or "decision").strip().lower()
            in {"decision", "execution"}
            else "decision"
        ),
        "allowCustom": bool(q.get("allow_custom", True)),
        "kind": str(meta.get("kind") or ""),
    }
    # Permission cards render from structured fields so tool/capability names
    # can be localized instead of displaying the backend's preformatted text.
    # Keep this allowlisted: arbitrary pending-question metadata may contain
    # internal plans or implementation details.
    if str(meta.get("kind") or "") in {
        "scope_elevation",
        "write_permission_request",
        "read_elevation",
        "subshell_elevation",
        "external_delivery_request",
        "external_upload_confirmation",
        "delete_confirmation",
        "destructive_confirmation",
        "self_configuration_confirmation",
        "host_lifecycle_confirmation",
        "task_permission_request",
        "git_commit",
    }:
        public["meta"] = {
            key: str(meta.get(key) or "")
            for key in ("kind", "tool_name", "operation", "path_hint", "reason")
        }
    # Plan-mode confirmations carry the proposed plan in meta — surface it so the
    # chat UI can render it in the right-side 计划 tab (the prompt text refers to
    # "右侧「计划」标签"). Only the structured {title, summary, steps} dict.
    plan = meta.get("plan")
    if isinstance(plan, dict) and plan:
        public["plan"] = plan
    return public


def _workbench_pending_question_for(session_id: str) -> dict[str, Any] | None:
    """Read a session's pending question (set when a run paused for permission /
    clarification). Session-scoped: temporarily binds ``_current_session_id`` so
    the read targets the right Workbench task/chat session, not the default one.
    """
    binding = bind_run_context(session_id=str(session_id or ""))
    try:
        return _public_pending_question(get_pending_question())
    finally:
        binding.reset()


def _workbench_apply_pending(session: dict[str, Any], session_id: str, agent_reply: str) -> tuple[str, bool]:
    """If ``agent_reply`` is the awaiting-user sentinel, attach the session's
    pending question to ``session`` (so the reply card renders it) and return a
    human-readable stand-in for the raw sentinel. Otherwise clear any stale
    pending question. Returns ``(display_reply, is_awaiting)``.
    """
    if agent_reply == _AWAITING_USER_SENTINEL:
        pending = _workbench_pending_question_for(session_id)
        if pending:
            session["pendingQuestion"] = pending
            session["status"] = "waiting_for_user"
            return (pending.get("text") or "需要你确认后才能继续。"), True
        # Sentinel but no question recoverable — degrade gracefully.
        session.pop("pendingQuestion", None)
        return "我需要你的确认才能继续，但没能取到具体问题，请重试。", False
    session.pop("pendingQuestion", None)
    return agent_reply, False


# Task-meta fields the agent may edit mid-run via the set_task_goal tool.
_WORKBENCH_AGENT_EDITABLE_META = ("goal", "title", "summary", "titleLocked", "constraints")


def _workbench_capture_task_meta(session: dict[str, Any]) -> dict[str, Any]:
    """Snapshot the task-meta the agent may change mid-run, so afterwards the
    handler can tell which fields the agent actually touched."""
    return {field: session.get(field) for field in _WORKBENCH_AGENT_EDITABLE_META}


def _workbench_sync_agent_task_meta(
    session: dict[str, Any], session_id: str, before: dict[str, Any]
) -> None:
    """Merge task-meta the agent changed mid-run (via ``set_task_goal``, which
    writes the store on its own separate read) back into this handler's in-memory
    session, so the handler's end-of-run write doesn't clobber it.

    Only fields that actually changed on disk during the run are pulled — values
    the handler seeded pre-run (e.g. goal copied from the user's input) are left
    intact when the agent didn't touch them.
    """
    try:
        fresh_payload = _read_workbench_store()
    except Exception:
        logger.exception("Failed to re-read workbench store for agent meta sync")
        return
    _, fresh = _workbench_find_session(fresh_payload, session_id)
    if not isinstance(fresh, dict):
        return
    for field in _WORKBENCH_AGENT_EDITABLE_META:
        if fresh.get(field) != before.get(field):
            session[field] = fresh.get(field)


async def _workbench_answer_pending(
    session_id: str,
    question_id: str,
    answer_text: str,
    workspace_dir: str,
    permission_mode: str = "default",
    ui_instance_id: str = "",
    conversation_source: str = "",
) -> str:
    """Resume a paused Workbench round with the user's answer. Binds the session
    + workspace ContextVars so ``answer_pending_question`` (which calls
    ``_run_chat_agent`` directly, bypassing ``run_agent``'s context setup) grants
    permission / retries the blocked action inside the right project scope.

    ``permission_mode`` carries the run's mode into the resumed slice — a goal
    loop configured for "auto"/"full_access" must keep that mode when continuing
    from a clarification, otherwise the continuation silently reverts to
    "default" and starts asking for permissions the user opted out of.
    """
    binding = bind_run_context(
        session_id=str(session_id or ""),
        user_request_text=str(answer_text or ""),
        conversation_source=str(conversation_source or ""),
        ui_instance_id=str(ui_instance_id or ""),
        workspace_dir=workspace_dir or "",
        response_capabilities=frozenset({"interactive_blocks"}),
    )
    try:
        run_session_operation = importlib.import_module(
            "cyrene.agent.coordinator"
        ).run_session_operation

        async def resume_pending_round() -> str:
            return await answer_pending_question(
                question_id, answer_text, _bot, _CHAT_ID, _db_path,
                permission_mode=permission_mode,
            )

        return await run_session_operation(session_id, resume_pending_round)
    finally:
        binding.reset()


def _migrate_project_workspace(workspace_path: Path) -> None:
    """Fold Cyrene-owned folders inside a project workspace into its .cyrene.

    Project workspaces are resolved lazily (per request / per agent run), so
    the migration hooks here instead of the startup sweep which only covers the
    global workspace. Best-effort and idempotent per process.
    """
    try:
        from cyrene.runtime.cyrene_migration import migrate_workspace_to_cyrene

        migrate_workspace_to_cyrene(workspace_path)
    except Exception:
        logger.warning("Failed to migrate project workspace %s", workspace_path, exc_info=True)


def _workbench_resolve_workspace_dir(project: dict[str, Any] | None) -> str:
    """Resolve a project's confined workspace dir (created if missing). Empty →
    the global WORKSPACE_DIR. ``_workbench_workspace_root`` is the single source
    of truth for the mapping (generated projects, legacy path rebasing)."""
    root = _workbench_workspace_root(project)
    if root is None:
        return ""
    try:
        root.mkdir(parents=True, exist_ok=True)
        # root is already resolved by _workbench_workspace_root; resolving
        # again here (and inside the migration) costs extra stats per request.
        _migrate_project_workspace(root)
        return str(root)
    except OSError:
        logger.warning(
            "Workbench workspace unavailable, using global: %s",
            str((project or {}).get("workspacePath") or ""),
        )
        return ""


async def _workbench_resolve_workspace_dir_async(
    project: dict[str, Any] | None,
) -> str:
    """Async variant for request paths: the one-time legacy migration inside
    ``_workbench_resolve_workspace_dir`` must not block the event loop."""
    return await asyncio.to_thread(_workbench_resolve_workspace_dir, project)




async def _workbench_agent_reply(
    user_input: str,
    session: dict[str, Any],
    constraints: list[str],
    attachments: Any = None,
    permission_mode: str = "auto",
    command: str = "",
    project_workspace: str = "",
    ephemeral_system: str = "",
    volatile_ephemeral_system: str = "",
    static_system_extra: str = "",
    conversation_source: str = "",
    ui_instance_id: str = "",
    client_request_id: str = "",
) -> str:
    """Execute a real agent run for a workbench session.

    Mirrors the /api/chat pipeline for attachments + permission mode + slash
    command so the new workbench composer matches the legacy chat composer.

    ``project_workspace`` confines the agent's file tools + Bash cwd to the
    project's workspacePath; empty → the global WORKSPACE_DIR (legacy behaviour).
    """
    session_id = str(session.get("id") or "").strip()
    if not session_id:
        return str(user_input or "").strip()
    # Confine this run's file operations to the project's workspace.
    workspace_dir = ""
    ws_raw = str(project_workspace or "").strip()
    if ws_raw:
        try:
            ws_path = Path(ws_raw).expanduser()
            ws_path.mkdir(parents=True, exist_ok=True)
            # The one-time legacy migration can move whole folders; keep it
            # off the event loop (steady-state calls short-circuit on the
            # migration marker and cost a few stats).
            await asyncio.to_thread(_migrate_project_workspace, ws_path)
            workspace_dir = str(ws_path.resolve())
        except OSError:
            logger.warning("Workbench workspace unavailable, using global: %s", ws_raw)
            workspace_dir = ""
    mode = str(permission_mode or "auto").strip().lower()
    if not is_permission_mode(mode):
        mode = "auto"

    # ── Budget gate (checked early, before attachment I/O) ──
    _bgt = await _check_budget_gate(session_id)
    if _bgt:
        raise _WorkbenchAgentRunError(
            str(_bgt.get("code") or "budget_blocked"),
            str(_bgt.get("error") or "预算限制阻止了本次执行。"),
            status_code=403,
        )

    normalized = _workbench_normalize_attachments(attachments)
    public_attachments = [build_public_attachment_payload(item) for item in normalized] or None
    message = str(user_input or "")
    llm_user_content = None
    attachment_binding = None
    if normalized:
        from cyrene.model_runtime.client import primary_candidate_supports_vision

        image_items = [
            item for item in normalized
            if str(item.get("kind") or "") == "image"
        ]
        primary_handles_images = bool(image_items) and primary_candidate_supports_vision(
            session_id
        )
        tool_items = [
            item for item in normalized
            if not primary_handles_images or str(item.get("kind") or "") != "image"
        ]
        if tool_items:
            message = (message or "[Attachment upload]") + _attachment_prompt_block(tool_items)
        if primary_handles_images:
            content: list[dict[str, Any]] = [
                {
                    "type": "text",
                    "text": message or "Describe the uploaded image in detail and extract any visible text.",
                }
            ]
            for item in image_items:
                path = Path(str(item.get("path") or "")).resolve()
                mime = str(
                    item.get("content_type")
                    or mimetypes.guess_type(str(path))[0]
                    or "image/png"
                )
                image_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{image_b64}"},
                    }
                )
            llm_user_content = content
        # Auto-allow uploaded files for tool read guards (same as /api/chat).
        att_map: dict[str, str] = {}
        for item in normalized:
            full_path = str(item.get("path") or "").strip()
            if not full_path:
                continue
            uuid_name = Path(full_path).name
            att_map[uuid_name] = full_path
            parts = uuid_name.split("_", 1)
            if len(parts) == 2:
                att_map[parts[1]] = full_path
        attachment_binding = bind_run_context(attachment_paths=att_map)

    resolved_conversation_source = str(conversation_source or "")
    if not resolved_conversation_source and ui_instance_id:
        from cyrene.runtime.host_bridge import resolve_conversation_source

        resolved_conversation_source = await resolve_conversation_source(ui_instance_id)

    try:
        reply = await run_agent(
            user_message=message,
            bot=_bot,
            chat_id=_CHAT_ID,
            db_path=_db_path,
            client_request_id=str(client_request_id or ""),
            session_id=session_id,
            permission_mode=mode,
            command=str(command or "").strip(),
            # Preserve an intentionally empty public message for attachment-only
            # turns. ``None`` means "show the model-facing message", which also
            # contains the private attachment instruction block.
            public_user_message=str(user_input or ""),
            public_attachments=public_attachments,
            llm_user_content=llm_user_content,
            workspace_dir=workspace_dir,
            ephemeral_system=str(ephemeral_system or ""),
            volatile_ephemeral_system=str(volatile_ephemeral_system or ""),
            static_system_extra=str(static_system_extra or ""),
            response_capabilities=("interactive_blocks",),
            ui_instance_id=str(ui_instance_id or ""),
            conversation_source=resolved_conversation_source,
        )
        if not str(reply or "").strip():
            from cyrene.model_runtime.client import resolve_llm_candidates

            if not resolve_llm_candidates():
                raise _WorkbenchAgentRunError(
                    "model_not_configured",
                    "No model is configured. Configure one in Settings → Models, then try again.",
                    status_code=400,
                )
        return reply
    except asyncio.CancelledError:
        raise
    except _WorkbenchAgentRunError:
        raise
    except Exception as exc:
        if isinstance(exc, SessionRunConflictError):
            raise _WorkbenchAgentRunError(
                "task_run_in_progress",
                "该任务已有正在执行的请求，请等待完成或先明确停止它。",
                status_code=409,
            ) from exc
        logger.exception("Workbench agent run failed for session %s", session_id)
        safe_error = _workbench_generation_error(exc)
        raise _WorkbenchAgentRunError(
            "workbench_agent_run_failed",
            f"Agent 执行失败：{safe_error.message}",
        ) from exc
    finally:
        if attachment_binding is not None:
            attachment_binding.reset()

def _reply_stream_chunks(text: str, target_chars: int = 36) -> list[str]:
    source = str(text or "")
    if not source:
        return []

    chunks: list[str] = []
    for block in re.split(r"(\n\n+)", source):
        if not block:
            continue
        if block.startswith("\n"):
            chunks.append(block)
            continue
        remaining = block
        while remaining:
            if len(remaining) <= target_chars:
                chunks.append(remaining)
                break
            split_at = target_chars
            lower_bound = max(0, target_chars - 14)
            for index in range(target_chars - 1, lower_bound - 1, -1):
                if remaining[index] in "，。！？；：,.!?;: ":
                    split_at = index + 1
                    break
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:]
    return [chunk for chunk in chunks if chunk]


async def _stream_reply_payload(response_text: str) -> StreamingResponse:
    async def event_stream():
        yield _ndjson_line({"type": "reply_start"})
        for chunk in _reply_stream_chunks(response_text):
            yield _ndjson_line({"type": "reply_delta", "delta": chunk})
            await asyncio.sleep(0)
        yield _ndjson_line({"type": "reply_done", "response": response_text})

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache"},
    )








def _image_dimensions(path: Path) -> tuple[int | None, int | None]:
    try:
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception:
        return None, None








# ---- Workbench global search helpers -------------------------------------


from cyrene.workbench import presentation_runtime as _presentation_runtime
_ACTIVITY_COLUMNS = _presentation_runtime._ACTIVITY_COLUMNS
_agent_lane_height = _presentation_runtime._agent_lane_height
_build_archive_sessions = _presentation_runtime._build_archive_sessions
_build_comm_edges = _presentation_runtime._build_comm_edges
_build_config = _presentation_runtime._build_config
_build_context_chips = _presentation_runtime._build_context_chips
_build_current_session = _presentation_runtime._build_current_session
_build_dashboard = _presentation_runtime._build_dashboard
_build_entities_summary = _presentation_runtime._build_entities_summary
_build_live_flow = _presentation_runtime._build_live_flow
_build_live_flow_round = _presentation_runtime._build_live_flow_round
_build_memory = _presentation_runtime._build_memory
_build_search_config = _presentation_runtime._build_search_config
_build_sessions = _presentation_runtime._build_sessions
_build_settings_meta = _presentation_runtime._build_settings_meta
_build_shells_from_messages = _presentation_runtime._build_shells_from_messages
_build_simple_flow = _presentation_runtime._build_simple_flow
_build_status = _presentation_runtime._build_status
_build_summary = _presentation_runtime._build_summary
_build_tool_nodes_for_owner = _presentation_runtime._build_tool_nodes_for_owner
_build_ui_data = _presentation_runtime._build_ui_data
_build_user = _presentation_runtime._build_user
_calc_current_streak = _presentation_runtime._calc_current_streak
_calc_longest_streak = _presentation_runtime._calc_longest_streak
_calc_messages_spend = _presentation_runtime._calc_messages_spend
_calc_peak_hour = _presentation_runtime._calc_peak_hour
_calc_spend = _presentation_runtime._calc_spend
_convert_messages = _presentation_runtime._convert_messages
_count_tool_nodes_for_owner = _presentation_runtime._count_tool_nodes_for_owner
_delete_chat_session = _presentation_runtime._delete_chat_session
_empty_session = _presentation_runtime._empty_session
_events_for_round = _presentation_runtime._events_for_round
_extract_topic_terms = _presentation_runtime._extract_topic_terms
_infer_subagent_entries = _presentation_runtime._infer_subagent_entries
_is_hidden_internal_message = _presentation_runtime._is_hidden_internal_message
_is_summary_agent_id = _presentation_runtime._is_summary_agent_id
_iter_flow_snapshots = _presentation_runtime._iter_flow_snapshots
_latest_round_id_from_messages = _presentation_runtime._latest_round_id_from_messages
_load_messages = _presentation_runtime._load_messages
_load_state_messages = _presentation_runtime._load_state_messages
_merge_subagent_record = _presentation_runtime._merge_subagent_record
_messages_from_archive_sections = _presentation_runtime._messages_from_archive_sections
_model_pricing = _presentation_runtime._model_pricing
_normalize_search_text = _presentation_runtime._normalize_search_text
_parse_archive_file = _presentation_runtime._parse_archive_file
_parse_archive_sections = _presentation_runtime._parse_archive_sections
_parse_archive_session_title = _presentation_runtime._parse_archive_session_title
_parse_conversation_archive = _presentation_runtime._parse_conversation_archive
_placeholder_logs = _presentation_runtime._placeholder_logs
_read_recent_logs = _presentation_runtime._read_recent_logs
_read_soul = _presentation_runtime._read_soul
_registry_status_from_ui = _presentation_runtime._registry_status_from_ui
_related_round_agent_names = _presentation_runtime._related_round_agent_names
_resolve_local_username = _presentation_runtime._resolve_local_username
_resolve_ui_tz = _presentation_runtime._resolve_ui_tz
_round_id_from_messages = _presentation_runtime._round_id_from_messages
_round_registry_for_flow = _presentation_runtime._round_registry_for_flow
_scan_inbox_agents = _presentation_runtime._scan_inbox_agents
_search_matches = _presentation_runtime._search_matches
_search_snippet = _presentation_runtime._search_snippet
_search_workbench_items = _presentation_runtime._search_workbench_items
_session_started_at = _presentation_runtime._session_started_at
_snapshot_comm_messages_from_messages = _presentation_runtime._snapshot_comm_messages_from_messages
_snapshot_entries_from_messages = _presentation_runtime._snapshot_entries_from_messages
_subagent_cards_from_registry = _presentation_runtime._subagent_cards_from_registry
_subagent_matches_round = _presentation_runtime._subagent_matches_round
_synthetic_live_round = _presentation_runtime._synthetic_live_round
_write_archive_sections = _presentation_runtime._write_archive_sections


# Historical API surface: implementations now live in explicit application
# services while this compatibility module keeps the original call signatures.
from cyrene.runtime.data_reset import (
    DataResetApplicationService as _DataResetApplicationService,
    clear_knowledge_data as _clear_knowledge_data,
    remove_directory_children as _remove_directory_children,
    remove_path as _remove_path,
    remove_path_checked as _remove_path_checked,
    reset_legacy_workspace_root_leftovers as _reset_legacy_workspace_root_leftovers,
    reset_process_runtime_state as _reset_process_runtime_state,
)
from cyrene.runtime.update_install import launch_update_restart as _launch_update_restart
from cyrene.workbench.global_chat_service import (
    GlobalChatApplicationService as _GlobalChatApplicationService,
)
from cyrene.workbench.subagent_messaging_service import (
    SubagentMessagingService as _SubagentMessagingService,
)


def _global_chat_service() -> _GlobalChatApplicationService:
    return _GlobalChatApplicationService(
        _db_path,
        bot=_bot,
        subagents=_SubagentMessagingService(_bot, _db_path, chat_id=_CHAT_ID),
        reset_agent_lottery=reset_lottery,
        chat_id=_CHAT_ID,
    )


async def _check_budget_gate(session_id: str) -> dict | None:
    return await _global_chat_service().check_budget_gate(session_id)


def _attachment_prompt_block(items: list[dict[str, Any]]) -> str:
    return _GlobalChatApplicationService.attachment_prompt_block(items)


async def _reset_app_data() -> dict[str, Any]:
    return await _DataResetApplicationService(_db_path).reset_app_data()


__all__ = tuple(name for name in globals() if not name.startswith('__'))
