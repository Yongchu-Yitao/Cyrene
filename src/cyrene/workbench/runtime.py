"""Workbench application and presentation services used by HTTP adapters."""

# This module is the compatibility facade consumed by route adapters while
# application services continue to be extracted by domain.
# ruff: noqa: F401

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
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
import cyrene.agent.state as _agent_state
import cyrene.workbench.memory as _workbench_memory
import cyrene.workbench.session_view as _session_view
from PIL import Image
from fastapi import APIRouter, BackgroundTasks, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from cyrene.tooling.backends.claude_code_bridge import get_cc_status
from cyrene.learning.claude_code import analyze_session, learn_from_session
from cyrene.tooling.backends.claude_code_terminal import CCTerminalSession
from cyrene.observability import debug
from cyrene.model_runtime.errors import format_httpx_error
from cyrene.runtime.attachments import (
    EXPORTS_DIR as _EXPORTS_DIR,
    attachment_kind_from_meta,
    build_public_attachment_payload,
    run_vision_chat,
    safe_attachment_filename,
)
from cyrene.config import strip_wrapping_quotes
from cyrene.agent.context import bind_run_context, is_permission_mode
from cyrene.agent import (
    _AWAITING_USER_SENTINEL,
    _append_session_message,
    _call_llm,
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
)
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
    save_personality_setup,
)
from cyrene.runtime.settings_store import get_all as get_web_settings
from cyrene.learning.skills import (
    build_skills as _build_skills,
    install_skill_from_path,
    register_existing_skills as _register_existing_skills,
    toggle_skill as _toggle_skill,
    uninstall_skill as _uninstall_skill,
)
from cyrene.tooling.backends.shells import list_shells as list_live_shells
from cyrene.tooling.backends.shells import set_cc_since
from cyrene.runtime.memory.short_term import load_entries
from cyrene.runtime.memory.soul import get_default_soul_content, read_soul, get_soul_path
from cyrene.runtime.version import get_version_label
from cyrene.workbench.store import patch_document_fields, read_document, write_document
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
_CC_PROJECT_DIR = WORKSPACE_DIR.parent

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
) -> None:
    """Compatibility facade for asynchronous Workbench memory capture."""
    _memory_service().schedule_capture(workspace_id, user_text, agent_text)


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


def _safe_workbench_data_key(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return _WORKBENCH_LEGACY_DATA_KEY
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")
    return cleaned or _WORKBENCH_LEGACY_DATA_KEY


def _workbench_default_project_name() -> str:
    if WORKSPACE_DIR.name == "workspace" and WORKSPACE_DIR.parent.name:
        return WORKSPACE_DIR.parent.name
    return WORKSPACE_DIR.name or "Cyrene"


def _workbench_project_data_key(project: dict[str, Any] | None) -> str:
    if not project:
        return _WORKBENCH_LEGACY_DATA_KEY
    return _safe_workbench_data_key(project.get("dataKey") or project.get("id"))


def _workbench_project_memory_key(project: dict[str, Any] | None) -> str:
    """Return the project identity used for durable Workbench memory."""
    if not project:
        return "default"
    return _safe_workbench_data_key(project.get("id"))


def _ndjson_line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _live_llm_config() -> tuple[str, str]:
    from cyrene import config as cy_config

    return cy_config.OPENAI_MODEL, cy_config.OPENAI_BASE_URL


def _get_model() -> str:
    from cyrene import config as cy_config
    return cy_config.OPENAI_MODEL


def _get_base_url() -> str:
    from cyrene import config as cy_config
    return cy_config.OPENAI_BASE_URL


def _parse_ctx_limit(ctx_str: str) -> int:
    """Parse human-readable context limit like '128K', '1M', '200K' to int."""
    ctx_str = (ctx_str or "").strip().upper()
    if not ctx_str:
        return 0
    try:
        if ctx_str.endswith("M"):
            return int(float(ctx_str[:-1]) * 1_000_000)
        if ctx_str.endswith("K"):
            return int(float(ctx_str[:-1]) * 1_000)
        return int(ctx_str)
    except (ValueError, TypeError):
        return 0


def _get_current_model_ctx_limit() -> int:
    """Look up the current model's context window limit from settings."""
    from cyrene.runtime.config_store import get_models, get_vision_models
    model_name = _get_model()
    ctx_limit = 0

    for model in get_models() or []:
        if model.get("model") == model_name or model.get("name") == model_name:
            ctx_limit = _parse_ctx_limit(model.get("ctx", ""))
            break

    if not ctx_limit:
        for model in get_vision_models() or []:
            if model.get("model") == model_name or model.get("name") == model_name:
                ctx_limit = _parse_ctx_limit(model.get("ctx", ""))
                break

    # Fallback: known model context windows when not explicitly configured
    if not ctx_limit:
        model_lower = model_name.lower()
        if any(x in model_lower for x in ("claude-opus-4", "opus-4")):
            ctx_limit = 200_000
        elif any(x in model_lower for x in ("claude-sonnet-4", "sonnet-4")):
            ctx_limit = 200_000
        elif any(x in model_lower for x in ("claude-haiku-4", "haiku-4")):
            ctx_limit = 200_000
        elif "gpt-4" in model_lower or "gpt-4o" in model_lower:
            ctx_limit = 128_000
        elif "gpt-3.5" in model_lower:
            ctx_limit = 16_000
        elif "deepseek" in model_lower:
            ctx_limit = 128_000
        elif "qwen" in model_lower:
            ctx_limit = 128_000
        elif "gemini" in model_lower:
            ctx_limit = 1_000_000

    return ctx_limit


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _workbench_default_project() -> dict[str, Any]:
    now = _utc_now_iso()
    project_id = _short_id("project")
    workspace_name = _workbench_default_project_name()
    initial_session = _workbench_new_session(project_id, "新任务", "", now)
    return {
        "projects": [
            {
                "id": project_id,
                "name": workspace_name,
                "dataKey": _WORKBENCH_LEGACY_DATA_KEY,
                "workspacePath": str(WORKSPACE_DIR),
                "status": "active",
                "model": _get_model(),
                "accountTier": "Pro",
                "context": {
                    "summary": f"Workspace at {WORKSPACE_DIR}",
                    "stack": [],
                    "decisions": [],
                    "knowledgeDocumentIds": [],
                },
                "createdAt": now,
                "updatedAt": now,
                "sessions": [initial_session],
                "sharedArtifacts": [],
            }
        ],
        "activeProjectId": project_id,
        "activeSessionId": initial_session["id"],
    }


# Legacy placeholder goal once stamped on blank 新任务 sessions. New tasks now
# start with an empty goal; this constant still lets us recognize the old filler
# (in already-stored sessions) as "no real goal yet" — so it is never treated as
# a goal, and the first real message can become the goal.
_WORKBENCH_PLACEHOLDER_GOAL = "通过对话明确当前任务目标。"


def _workbench_is_blank_goal(goal: Any) -> bool:
    g = str(goal or "").strip()
    return not g or g == _WORKBENCH_PLACEHOLDER_GOAL


def _workbench_is_default_title(title: Any) -> bool:
    return not str(title or "").strip() or str(title or "").strip() == "新任务"


def _workbench_derive_title(text: str) -> str:
    """A short task title from free text — its first line/sentence, trimmed."""
    raw = re.sub(r"\s+", " ", str(text or "").strip())
    if not raw:
        return "新任务"
    head = re.split(r"[。\n？?！!；;]", raw, maxsplit=1)[0].strip() or raw
    return head[:24] or "新任务"


def set_task_goal_for_session(
    session_id: str, goal: str, title: str = "", summary: str = ""
) -> dict[str, Any]:
    """Set/correct a Workbench task session's goal, short title, and/or one-line
    summary (简介).

    Backs the ``set_task_goal`` agent tool: the agent may call it once it actually
    understands what the task is (e.g. after exploring the project, or when the
    user's opener was a question rather than a goal). At least one of goal/title/
    summary must be provided. The title is LOCKED once the user has manually edited
    it (``titleLocked``) — the agent can no longer change the title, though goal and
    summary still update. Returns a small status dict.
    """
    sid = str(session_id or "").strip()
    new_goal = str(goal or "").strip()
    new_title = str(title or "").strip()
    new_summary = str(summary or "").strip()
    if not sid:
        return {"ok": False, "error": "no active task session"}
    if not new_goal and not new_title and not new_summary:
        return {"ok": False, "error": "nothing to update (provide goal, title or summary)"}
    if new_goal and len(new_goal) < 3:
        return {"ok": False, "error": "goal is too short"}
    payload = _read_workbench_store()
    project, session = _workbench_find_session(payload, sid)
    if not session or not project:
        return {"ok": False, "error": "session not found"}
    if str(session.get("kind") or "") == "init":
        return {"ok": False, "error": "cannot set goal on an init session"}
    now = _utc_now_iso()
    if new_goal:
        session["goal"] = new_goal
        merged = list(session.get("constraints") or [])
        for item in _workbench_extract_constraints(new_goal):
            if item not in merged:
                merged.append(item)
        session["constraints"] = merged
    # Title: the user owns it once they've edited it (titleLocked) — never override.
    title_locked = bool(session.get("titleLocked"))
    title_blocked = False
    if new_title:
        if title_locked:
            title_blocked = True
        else:
            session["title"] = new_title[:80]
    elif new_goal and not title_locked and _workbench_is_default_title(session.get("title")):
        derived = _workbench_derive_title(new_goal)
        if derived:
            session["title"] = derived[:80]
    if new_summary:
        session["summary"] = new_summary
    session["updatedAt"] = now
    project["updatedAt"] = now
    _write_workbench_store(payload)
    return {
        "ok": True,
        "goal": session.get("goal") or "",
        "title": session.get("title") or "",
        "summary": _workbench_session_summary_text(session),
        "titleLocked": title_locked,
        "titleBlocked": title_blocked,
    }


def _workbench_new_session(
    project_id: str,
    title: str,
    goal: str = "",
    now: str | None = None,
    *,
    kind: str = "task",
    status: str = "idle",
) -> dict[str, Any]:
    now = now or _utc_now_iso()
    session_id = _short_id("session")
    return {
        "id": session_id,
        "projectId": project_id,
        "kind": kind,
        "title": str(title or "新任务").strip()[:80] or "新任务",
        "goal": str(goal or "").strip(),
        "constraints": [],
        "status": status,
        "priority": "medium",
        "createdAt": now,
        "updatedAt": now,
        "agentReply": "",
        "plan": [],
        "planRevision": 0,
        "planDefinitionRevision": 0,
        "approvedPlanDefinitionRevision": None,
        "events": [],
        "runs": [],
        "artifacts": [],
        "acceptanceCriteria": [],
        "summary": None,
        "titleLocked": False,
    }


def _workbench_acceptance_fully_passed(criteria: Any) -> bool:
    """Return whether a task has a non-empty, completely passed acceptance set."""
    if not isinstance(criteria, list) or not criteria:
        return False
    return all(
        isinstance(item, dict)
        and str(item.get("status") or "").strip().lower() in {"passed", "done", "completed"}
        for item in criteria
    )


def _workbench_mark_completed_if_acceptance_passed(
    session: dict[str, Any],
    *,
    now: str | None = None,
    event_body: str = "所有验收标准均已通过，任务自动标记为已完成。",
) -> bool:
    """Promote a task to completed once every acceptance criterion is passed.

    This is deliberately server-side so manual criterion updates, independent
    verification, and background goal-loop verification share the same rule.
    The event is emitted only on the transition, keeping retries idempotent.
    """
    if not _workbench_acceptance_fully_passed(session.get("acceptanceCriteria")):
        return False
    if str(session.get("status") or "").strip().lower() in {"completed", "done"}:
        return True
    session["status"] = "completed"
    timestamp = now or _utc_now_iso()
    session["events"] = list(session.get("events") or []) + [{
        "id": _short_id("event"),
        "type": "TaskCompleted",
        "createdAt": timestamp,
        "body": event_body,
    }]
    return True


# ---- Project initialization (the "初始化项目" onboarding session) ------------

# The default onboarding form. Also doubles as the schema the LLM is asked to
# mirror, and as the fallback whenever agent generation is unavailable.
def _workbench_default_init_form(project: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a deterministic onboarding form for empty-workspace projects.

    The section structure and questions are chosen by template so users get
    scoping questions that fit their project type. ``project`` is optional so
    callers that don't have it yet get the generic blank form.
    """
    template = str(project.get("template") or "blank").strip() if project else "blank"
    greeting = (
        "你好！我是你的项目初始化助理。"
        "我先从几个关键问题开始，以便更好地理解你的需求。"
    )

    FORMS: dict[str, dict] = {
        "blank": {
            "sections": [
                {
                    "id": "basics", "title": "项目概览",
                    "questions": [
                        {"id": "goal", "type": "textarea", "label": "你想做什么？期望达成什么目标？",
                         "placeholder": "例如：写一份市场分析报告、开发一个博客网站、完成期末作业"},
                        {"id": "description", "type": "textarea", "label": "具体描述一下要做的事情，包括背景和期望的结果",
                         "placeholder": "例如：分析 Q3 的销售数据，输出一份包含图表的 PDF 报告"},
                    ],
                },
                {
                    "id": "scope", "title": "范围与要求",
                    "questions": [
                        {"id": "requirements", "type": "textarea", "label": "有哪些具体要求或内容需要包含？",
                         "placeholder": "例如：数据分析图表、Python 后端、中英文双语输出"},
                        {"id": "out_of_scope", "type": "textarea", "label": "有哪些明确不需要的或排除在外的？",
                         "placeholder": "例如：不需要用户界面、不需要实时更新"},
                    ],
                },
                {
                    "id": "resources", "title": "资源与约束",
                    "questions": [
                        {"id": "resource", "type": "text", "label": "有哪些可用的资源或输入材料？",
                         "placeholder": "例如：项目代码仓库、数据集、参考文档、设计稿"},
                        {"id": "tech", "type": "text", "label": "是否有偏好的工具、技术栈或平台？",
                         "placeholder": "例如：Python、LaTeX、Figma、GitHub Pages"},
                    ],
                },
                {
                    "id": "timeline", "title": "时间计划",
                    "questions": [
                        {"id": "deadline", "type": "text", "label": "期望什么时候完成？有没有关键时间点？",
                         "placeholder": "例如：下周五之前"},
                        {"id": "milestones", "type": "textarea", "label": "有哪些阶段性的交付节点？",
                         "placeholder": "例如：周三前出初稿、周五前完成终版"},
                    ],
                },
            ],
        },
        "product": {
            "sections": [
                {
                    "id": "basics", "title": "产品概览",
                    "questions": [
                        {"id": "goal", "type": "textarea", "label": "这个产品的核心目标是什么？",
                         "placeholder": "例如：打造一个团队协作工具，提高跨部门任务管理效率"},
                        {"id": "problem", "type": "textarea", "label": "要解决用户的什么痛点？",
                         "placeholder": "例如：任务分散、进度不透明、沟通成本高"},
                        {"id": "users", "type": "text", "label": "目标用户是谁？",
                         "placeholder": "例如：中小团队的 PM 和开发者"},
                    ],
                },
                {
                    "id": "scope", "title": "功能规划",
                    "questions": [
                        {"id": "features", "type": "textarea", "label": "核心功能有哪些？优先级如何？",
                         "placeholder": "例如：任务看板（P0）、进度报表（P1）、消息通知（P2）"},
                        {"id": "mvp", "type": "textarea", "label": "MVP 需要包含哪些功能？",
                         "placeholder": "例如：用户登录、任务创建与指派、看板视图"},
                    ],
                },
                {
                    "id": "resources", "title": "资源与时间",
                    "questions": [
                        {"id": "team", "type": "text", "label": "团队规模和角色是怎样的？",
                         "placeholder": "例如：2 前端 + 2 后端 + 1 设计"},
                        {"id": "tech", "type": "text", "label": "确定的技术栈是什么？",
                         "placeholder": "例如：React、Node.js、PostgreSQL"},
                        {"id": "deadline", "type": "text", "label": "计划什么时候上线？",
                         "placeholder": "例如：8 周内交付 MVP"},
                    ],
                },
                {
                    "id": "quality", "title": "质量与验收",
                    "questions": [
                        {"id": "standard", "type": "textarea", "label": "有哪些质量要求或验收标准？",
                         "placeholder": "例如：页面加载 < 2s、核心流程覆盖测试、WCAG 无障碍"},
                    ],
                },
            ],
        },
        "pm": {
            "sections": [
                {
                    "id": "basics", "title": "项目概览",
                    "questions": [
                        {"id": "goal", "type": "textarea", "label": "这个项目的目标是什么？",
                         "placeholder": "例如：完成公司官网改版，提升品牌形象和转化率"},
                        {"id": "stakeholders", "type": "text", "label": "关键干系人或合作方有哪些？",
                         "placeholder": "例如：市场部、设计团队、外包开发"},
                    ],
                },
                {
                    "id": "scope", "title": "范围与任务",
                    "questions": [
                        {"id": "deliverables", "type": "textarea", "label": "主要交付物或产出有哪些？",
                         "placeholder": "例如：新版官网页面、CMS 后台、部署文档"},
                        {"id": "deps", "type": "textarea", "label": "有哪些外部依赖或前置条件？",
                         "placeholder": "例如：需要设计团队先输出视觉稿、第三方 API 密钥"},
                    ],
                },
                {
                    "id": "team", "title": "团队与协作",
                    "questions": [
                        {"id": "team", "type": "text", "label": "团队如何组成？协作方式是什么？",
                         "placeholder": "例如：5 人内部团队 + 外部顾问，每日站会 + 周报"},
                        {"id": "tools", "type": "text", "label": "使用的协作工具和平台有哪些？",
                         "placeholder": "例如：Jira、Confluence、Slack、GitHub"},
                    ],
                },
                {
                    "id": "timeline", "title": "时间与风险",
                    "questions": [
                        {"id": "deadline", "type": "text", "label": "关键里程碑和截止日期是什么？",
                         "placeholder": "例如：第 4 周设计定稿、第 8 周上线"},
                        {"id": "risks", "type": "textarea", "label": "已知的风险或阻塞项有哪些？",
                         "placeholder": "例如：设计资源紧张、第三方 API 稳定性未知"},
                    ],
                },
            ],
        },
        "knowledge": {
            "sections": [
                {
                    "id": "direction", "title": "研究方向",
                    "questions": [
                        {"id": "goal", "type": "textarea", "label": "你当前想研究的具体方向是什么？",
                         "placeholder": "例如：基于大语言模型的分子动力学模拟方法优化"},
                        {"id": "scenario", "type": "textarea", "label": "这个方向主要面向什么任务、场景或应用？",
                         "placeholder": "例如：药物分子筛选中的构象采样效率提升"},
                    ],
                },
                {
                    "id": "problem", "title": "问题定位",
                    "questions": [
                        {"id": "problem", "type": "textarea", "label": "你希望优先解决什么问题？",
                         "placeholder": "例如：现有 MD 模拟方法在长时程构象变化上的采样效率不足"},
                        {"id": "gap", "type": "textarea", "label": "你认为现有方法最明显的不足是什么？",
                         "placeholder": "例如：计算成本高、对稀有事件的采样不足、缺乏可解释性"},
                    ],
                },
                {
                    "id": "conditions", "title": "现有条件",
                    "questions": [
                        {"id": "basis", "type": "textarea", "label": "你目前已有的信息或基础是什么？",
                         "placeholder": "例如：论文、想法、数据、代码、实验结果"},
                        {"id": "resources", "type": "text", "label": "你有哪些可用资源或限制？",
                         "placeholder": "例如：数据、算力、时间、工具、投稿目标"},
                    ],
                },
                {
                    "id": "output", "title": "最终产出",
                    "questions": [
                        {"id": "outcome", "type": "textarea", "label": "你希望最终形成什么成果？",
                         "placeholder": "例如：研究方案、实验结果、论文初稿、代码原型"},
                        {"id": "min_requirement", "type": "textarea", "label": "你对结果有什么最低要求？",
                         "placeholder": "例如：指标提升、可复现实验、能投稿、能开题"},
                    ],
                },
            ],
        },
        "ai": {
            "sections": [
                {
                    "id": "basics", "title": "项目概览",
                    "questions": [
                        {"id": "goal", "type": "textarea", "label": "你想构建什么？它的核心能力是什么？",
                         "placeholder": "例如：一个代码审查助手，能自动检查 PR 并给出改进建议"},
                        {"id": "users", "type": "text", "label": "谁会用？在什么场景下使用？",
                         "placeholder": "例如：开发团队，在提 PR 时自动触发"},
                    ],
                },
                {
                    "id": "capability", "title": "能力设计",
                    "questions": [
                        {"id": "tools", "type": "textarea", "label": "需要具备哪些能力或工具调用？",
                         "placeholder": "例如：读取代码文件、调用 Lint 工具、查询文档、评论 PR"},
                        {"id": "knowledge", "type": "textarea", "label": "需要参考哪些知识或上下文？",
                         "placeholder": "例如：项目编码规范、API 文档、历史 PR 模式"},
                    ],
                },
                {
                    "id": "resources", "title": "开发资源",
                    "questions": [
                        {"id": "model", "type": "text", "label": "使用什么模型或推理服务？",
                         "placeholder": "例如：Claude API、本地开源模型、Azure OpenAI"},
                        {"id": "tech", "type": "text", "label": "技术栈和运行环境是什么？",
                         "placeholder": "例如：Python、Docker、GitHub Actions"},
                    ],
                },
                {
                    "id": "timeline", "title": "计划与交付",
                    "questions": [
                        {"id": "deadline", "type": "text", "label": "期望什么时候可用？",
                         "placeholder": "例如：2 周出原型、6 周正式上线"},
                        {"id": "milestones", "type": "textarea", "label": "有哪些重要的交付节点？",
                         "placeholder": "例如：第 2 周核心逻辑完成、第 4 周集成测试、第 6 周上线"},
                    ],
                },
            ],
        },
        "import": {
            "sections": [
                {
                    "id": "basics", "title": "导入概览",
                    "questions": [
                        {"id": "goal", "type": "textarea", "label": "导入的项目或内容是什么？",
                         "placeholder": "例如：从 GitHub 导入一个开源博客系统"},
                        {"id": "source", "type": "text", "label": "来源是什么？目前的状态如何？",
                         "placeholder": "例如：GitHub 仓库、本地文件夹、导出文件"},
                    ],
                },
                {
                    "id": "scope", "title": "导入范围",
                    "questions": [
                        {"id": "parts", "type": "textarea", "label": "需要导入全部内容还是部分内容？",
                         "placeholder": "例如：只导入源码和文档，不需要导入历史提交"},
                        {"id": "adapt", "type": "textarea", "label": "导入后需要做哪些适配或改造？",
                         "placeholder": "例如：修改配置为本地环境、更新依赖版本"},
                    ],
                },
                {
                    "id": "resources", "title": "环境与工具",
                    "questions": [
                        {"id": "tech", "type": "text", "label": "项目使用的技术栈是什么？",
                         "placeholder": "例如：React、Express、MongoDB"},
                        {"id": "env", "type": "textarea", "label": "运行需要哪些环境或配置？",
                         "placeholder": "例如：Node 18+、Docker、MySQL 8.0"},
                    ],
                },
                {
                    "id": "timeline", "title": "后续计划",
                    "questions": [
                        {"id": "next", "type": "textarea", "label": "导入完成后的下一步计划是什么？",
                         "placeholder": "例如：修复已知 bug、补充测试、部署上线"},
                        {"id": "deadline", "type": "text", "label": "期望什么时候完成导入和适配？",
                         "placeholder": "例如：本周内完成导入，下周完成适配"},
                    ],
                },
            ],
        },
    }

    form = FORMS.get(template, FORMS["blank"])
    return {
        "generated": False,
        "completed": False,
        "greeting": greeting,
        "sections": form["sections"],
        "answers": {},
    }


def _workbench_new_init_session(
    project_id: str,
    project: dict[str, Any],
    now: str | None = None,
) -> dict[str, Any]:
    now = now or _utc_now_iso()
    session = _workbench_new_session(
        project_id,
        "初始化项目",
        "完成项目的基础设置与初始规划。",
        now,
        kind="init",
        status="initializing",
    )
    form = _workbench_default_init_form(project)
    session["init"] = form
    session["agentReply"] = form["greeting"]
    return session


_WORKBENCH_TEMPLATE_LABELS = {
    "blank": "空白项目",
    "product": "产品开发",
    "pm": "项目管理",
    "knowledge": "科学研究",
    "ai": "AI 应用开发",
    "import": "导入项目",
}

_INIT_QUESTION_TYPES = {"text", "textarea", "single", "multi"}


def _workbench_init_workspace_relationship_guidance(project: dict[str, Any]) -> str:
    """Prompt guardrails for non-empty workspaces during project init."""
    template = str(project.get("template") or "").strip()
    template_label = _WORKBENCH_TEMPLATE_LABELS.get(template, template or "空白项目")
    workspace_source = str(project.get("workspacePathSource") or "user").strip().lower()
    user_selected_workspace = workspace_source != "generated"

    if template == "import":
        return (
            "工作区关系判断：用户选择的是“导入项目”类型，可以把已有文件视为导入对象的重要线索，"
            "但仍需要用问题确认导入范围、保留/改造边界和后续目标。"
        )

    if user_selected_workspace:
        return (
            "工作区关系判断：用户为新项目选择/使用了一个已有文件夹，且当前项目类型是"
            f"「{template_label}」。这只说明工作区非空，不等于用户确认这些文件就是本项目，"
            "也不等于用户要围绕这些文件继续开发。尤其当项目类型是“空白项目”时，"
            "它可能只是默认选项，不能当作用户明确声明。\n"
            "生成表单时必须把已有文件当作“待确认线索”，不要把探索到的题材、IP、代码库、"
            "素材或文档直接描述成已确认的项目定位。第一组问题应优先确认：这些文件和新项目的关系"
            "（复用/导入、仅作参考、需要忽略、需要整理归档或另建空目录），以及用户真正想启动的目标。"
        )

    return (
        "工作区关系判断：工作区已有文件，可作为项目现状线索；仍不要把探索结论写成绝对事实，"
        "需要通过问题确认用户希望如何处理已有内容。"
    )


class _WorkbenchGenerationError(RuntimeError):
    """Structured, user-displayable failure from a workbench generation call."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = str(category or "unknown")
        self.message = str(message or "未知错误")


def _workbench_redact_error_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer <redacted>", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "sk-<redacted>", text)
    text = re.sub(
        r'(?i)(api[_ -]?key["\']?\s*[:=]\s*["\']?)[^"\'\s,}]+',
        r"\1<redacted>",
        text,
    )
    return text


def _workbench_generation_error(exc: Exception) -> _WorkbenchGenerationError:
    """Convert low-level model errors into useful, secret-safe UI details."""
    if isinstance(exc, _WorkbenchGenerationError):
        return exc
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException)):
        return _WorkbenchGenerationError("timeout", "模型请求超时。")
    if isinstance(exc, httpx.HTTPStatusError):
        status = int(exc.response.status_code)
        body = re.sub(
            r"\s+",
            " ",
            _workbench_redact_error_text(exc.response.text).strip(),
        )[:500]
        if status in (401, 403):
            category = "authentication"
            summary = f"模型服务鉴权失败（HTTP {status}）。"
        elif status == 429:
            category = "rate_limit"
            summary = "模型服务触发限流（HTTP 429）。"
        elif status >= 500:
            category = "upstream"
            summary = f"模型服务暂时异常（HTTP {status}）。"
        else:
            category = "http"
            summary = f"模型服务返回 HTTP {status}。"
        if body:
            summary += f" 响应：{body}"
        return _WorkbenchGenerationError(category, summary)
    if isinstance(exc, httpx.RequestError):
        return _WorkbenchGenerationError(
            "network",
            _workbench_redact_error_text(format_httpx_error(exc)),
        )
    return _WorkbenchGenerationError(
        "internal",
        _workbench_redact_error_text(
            f"{type(exc).__name__}: {str(exc or '未知错误').strip()}"
        ),
    )


def _workbench_coerce_init_form(raw: Any, base: dict[str, Any]) -> dict[str, Any] | None:
    """Validate/normalize an LLM-produced init form into our schema.

    Returns ``None`` when the payload is unusable so the caller can keep the
    deterministic fallback.
    """
    if not isinstance(raw, dict):
        return None
    raw_sections = raw.get("sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        return None
    sections: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for s_index, section in enumerate(raw_sections):
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or "").strip()
        raw_questions = section.get("questions")
        if not title or not isinstance(raw_questions, list):
            continue
        sid = str(section.get("id") or "").strip() or f"section_{s_index + 1}"
        while sid in used_ids:
            sid = f"{sid}_{s_index + 1}"
        used_ids.add(sid)
        questions: list[dict[str, Any]] = []
        used_q_ids: set[str] = set()
        for q_index, question in enumerate(raw_questions):
            if not isinstance(question, dict):
                continue
            label = str(question.get("label") or question.get("question") or "").strip()
            if not label:
                continue
            qtype = str(question.get("type") or "text").strip().lower()
            if qtype not in _INIT_QUESTION_TYPES:
                qtype = "text"
            qid = str(question.get("id") or "").strip() or f"{sid}_q{q_index + 1}"
            while qid in used_q_ids:
                qid = f"{qid}_{q_index + 1}"
            used_q_ids.add(qid)
            item: dict[str, Any] = {"id": qid, "type": qtype, "label": label[:160]}
            placeholder = str(question.get("placeholder") or "").strip()
            if placeholder:
                item["placeholder"] = placeholder[:160]
            if qtype in ("single", "multi"):
                options = [lbl for o in question.get("options", []) if (lbl := _option_label(o))]
                if not options:
                    qtype = "text"
                    item["type"] = "text"
                else:
                    item["options"] = options[:8]
            questions.append(item)
        if questions:
            sections.append({"id": sid, "title": title[:60], "questions": questions[:6]})
    if not sections:
        return None
    greeting = str(raw.get("greeting") or "").strip() or base.get("greeting", "")
    return {
        "generated": True,
        "completed": bool(base.get("completed")),
        "greeting": greeting,
        "sections": sections[:6],
        "answers": base.get("answers") if isinstance(base.get("answers"), dict) else {},
    }


_WORKBENCH_EMPTY_WORKSPACE_SKIP_DIRS = frozenset({
    ".git", ".github", ".vscode", ".idea", "__pycache__",
    "node_modules", ".venv", "venv", ".tox", ".egg-info",
    "dist", "build", "target", ".next", ".nuxt", ".cache",
})


def _is_workspace_empty(workspace_root: Path | None) -> bool:
    """Return True when the workspace directory is missing, empty, or only
    contains hidden / build-artifact metadata (no actual source files)."""
    if not workspace_root or not workspace_root.is_dir():
        return True
    try:
        for p in workspace_root.iterdir():
            if p.name.startswith(".") or p.name in _WORKBENCH_EMPTY_WORKSPACE_SKIP_DIRS:
                continue
            if p.name in ("LICENSE", "LICENSE.txt", "LICENSE.md"):
                continue
            return False
    except OSError:
        pass
    return True


# Read-only workspace-exploration tools shared by the init-form agent, the task
# plan generator, and the init task-plan agent. Scoped to a project workspace.
_WORKBENCH_EXPLORE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "列出工作区指定路径下的文件和目录。返回文件名/目录名列表，不递归。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对于工作区根目录的路径，例如 '.'（根目录）或 'src'。默认 '.'",
                        "default": ".",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "按字符范围读取工作区中的文本文件。优先读取尚未观察的范围；二进制文件会提示不可读。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对于工作区根目录的文件路径，例如 'README.md' 或 'src/main.py'",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 0,
                        "description": "从第几个字符开始读取，默认 0",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 12000,
                        "default": 4000,
                        "description": "最多读取多少字符，默认 4000",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "按通配符模式搜索工作区中的文件路径。支持 ** 递归匹配。例如：'**/*.py' 查找所有 Python 文件，'*.toml' 查找根目录下的 TOML 文件，'src/**/*.tsx' 查找 src 下所有 React 组件。自动跳过隐藏文件。最多返回 50 条结果。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "glob 搜索模式，相对于工作区根目录",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
]


def _workbench_parse_json_object(text: str) -> dict[str, Any] | None:
    """Parse a JSON object from an LLM reply, tolerating prose / code fences.

    Models often wrap the JSON in a ```json … ``` fence and/or prefix it with
    prose ("以下是总结：…"), so try several extractions before giving up.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    candidates: list[str] = [raw]
    # Content inside a ```json … ``` (or plain ```) fence.
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    if fence and fence.group(1).strip():
        candidates.append(fence.group(1).strip())
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            return parsed

    # Scan every opening brace with JSONDecoder.raw_decode(). This tolerates
    # prose (including stray braces) before/after the actual object without the
    # greedy first-brace-to-last-brace extraction swallowing unrelated text.
    decoder = json.JSONDecoder()
    top_level_object_starts: list[int] = []
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(raw):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                top_level_object_starts.append(index)
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1

    for start in top_level_object_starts:
        try:
            parsed, _end = decoder.raw_decode(raw[start:])
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


async def _workbench_repair_json_response(
    messages: list[dict[str, Any]],
    invalid_content: str,
    *,
    max_tokens: int,
    timeout: float,
    secondary: bool,
) -> dict[str, Any] | None:
    """Ask the model once to convert a malformed final reply into strict JSON."""
    content = str(invalid_content or "").strip()
    repair_messages = list(messages)
    if content:
        repair_messages.append({"role": "assistant", "content": content})
    repair_messages.append({
        "role": "user",
        "content": (
            "你刚才的最终回答无法解析为 JSON。不要继续探索，也不要解释。"
            "请保留原回答的结论和字段，只修正格式，并且只输出一个合法 JSON 对象。"
            "不要使用 Markdown 代码块，不要输出 JSON 之外的任何文字。"
            "（输出必须是单个合法的 json 对象。）"
        ),
    })
    repaired = await asyncio.wait_for(
        _call_llm(
            repair_messages,
            tools=None,
            max_tokens=max_tokens,
            secondary=secondary,
            thinking="disabled",
            response_format=_WORKBENCH_JSON_RESPONSE_FORMAT,
        ),
        timeout=timeout,
    )
    if not isinstance(repaired, dict):
        return None
    return _workbench_parse_json_object(repaired.get("content") or "")


def _workbench_explore_parse_failure(
    response: Any,
    content: Any,
) -> _WorkbenchGenerationError:
    """Classify a final reply that survived parse + repair but is still not a
    JSON object.

    An empty body or a ``finish_reason == "length"`` truncation is a transient
    glitch worth retrying, so it gets its own category rather than the generic
    ``response_format`` verdict (which callers may treat as a hard failure).
    """
    finish_reason = str(response.get("finish_reason") or "") if isinstance(response, dict) else ""
    stripped = _workbench_redact_error_text(str(content or "")).strip()
    preview = re.sub(r"\s+", " ", stripped)[:500]
    if finish_reason == "length":
        detail = "模型在产出 JSON 前被 max_tokens 截断（finish_reason=length）。"
        if preview:
            detail += f" 已生成片段：{preview[:300]}"
        return _WorkbenchGenerationError("truncated", detail)
    if not stripped:
        return _WorkbenchGenerationError("empty_response", "模型返回了空响应。")
    detail = "模型响应不是有效的 JSON 对象。"
    if preview:
        detail += f" 响应片段：{preview}"
    return _WorkbenchGenerationError("response_format", detail)


async def _workbench_run_json_generation(
    prompt: str,
    *,
    max_tokens: int,
    timeout: float,
    secondary: bool = False,
) -> dict[str, Any] | None:
    """Run a no-tool JSON generation call and parse/repair the final object."""
    messages = [{"role": "user", "content": prompt}]
    try:
        response = await asyncio.wait_for(
            _call_llm(
                messages,
                tools=None,
                max_tokens=max_tokens,
                secondary=secondary,
                thinking="disabled",
                response_format=_WORKBENCH_JSON_RESPONSE_FORMAT,
            ),
            timeout=timeout,
        )
    except Exception:
        logger.exception("Workbench JSON generation failed")
        return None
    if not isinstance(response, dict):
        return None
    content = response.get("content") or ""
    parsed = _workbench_parse_json_object(content)
    if parsed is not None:
        return parsed
    try:
        return await _workbench_repair_json_response(
            messages,
            content,
            max_tokens=max_tokens,
            timeout=timeout,
            secondary=secondary,
        )
    except Exception:
        logger.exception("Workbench JSON generation repair failed")
        return None


def _workbench_stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _workbench_hash_json(value: Any) -> str:
    return hashlib.sha256(_workbench_stable_json(value).encode("utf-8")).hexdigest()


def _workbench_workspace_state(
    workspace_root: Path | None,
) -> tuple[str, dict[str, str]]:
    """Cheap tree revision used to invalidate directory/glob observations.

    File observations still carry a content SHA-256. The tree revision uses
    names, types, sizes and mtimes so an unchanged workspace can be recognized
    without rereading every file body before each planning revision.
    """
    if not workspace_root or not workspace_root.is_dir():
        return "missing", {}
    digest = hashlib.sha256()
    snapshot: dict[str, str] = {}
    try:
        for root, dirs, files in os.walk(workspace_root):
            dirs[:] = sorted(
                name for name in dirs
                if not name.startswith(".") and name not in _WORKBENCH_EMPTY_WORKSPACE_SKIP_DIRS
            )
            root_path = Path(root)
            for name in dirs:
                path = root_path / name
                rel = path.relative_to(workspace_root).as_posix()
                try:
                    stat = path.stat()
                    row = ("d", rel, stat.st_mtime_ns)
                except OSError:
                    row = ("d", rel, 0)
                digest.update(_workbench_stable_json(row).encode("utf-8"))
                if len(snapshot) < 5000:
                    snapshot[rel + "/"] = f"d:{row[-1]}"
            for name in sorted(files):
                if name.startswith("."):
                    continue
                path = root_path / name
                rel = path.relative_to(workspace_root).as_posix()
                try:
                    stat = path.stat()
                    row = ("f", rel, stat.st_size, stat.st_mtime_ns)
                except OSError:
                    row = ("f", rel, 0, 0)
                digest.update(_workbench_stable_json(row).encode("utf-8"))
                if len(snapshot) < 5000:
                    snapshot[rel] = f"f:{row[-2]}:{row[-1]}"
    except OSError:
        return "unavailable", {}
    return digest.hexdigest(), snapshot


def _workbench_workspace_revision(workspace_root: Path | None) -> str:
    return _workbench_workspace_state(workspace_root)[0]


def _workbench_planning_thread(
    session: dict[str, Any],
    workspace_root: Path | None,
) -> dict[str, Any]:
    raw = session.get("planningThread")
    thread = raw if isinstance(raw, dict) else {}
    current_root = str(workspace_root or "")
    if thread and str(thread.get("workspaceRoot") or "") not in ("", current_root):
        thread = {}
    if str(thread.get("contractVersion") or "") != _WORKBENCH_PLANNER_CONTRACT_VERSION:
        thread = {}
    thread.setdefault("id", _short_id("planning"))
    thread["contractVersion"] = _WORKBENCH_PLANNER_CONTRACT_VERSION
    thread.setdefault("messages", [])
    thread.setdefault("observationCache", {})
    thread.setdefault("inspectedResources", {})
    thread.setdefault("metrics", [])
    thread["workspaceRoot"] = current_root
    session["planningThread"] = thread
    return thread


def _workbench_planning_checkpoint(
    thread: dict[str, Any],
    latest_assistant_content: str,
) -> list[dict[str, Any]]:
    inspected = thread.get("inspectedResources")
    checkpoint = {
        "type": "planning_checkpoint",
        "goal": thread.get("goal") or "",
        "constraints": thread.get("constraints") or [],
        "currentPlan": thread.get("currentPlan") or [],
        "workspaceRevision": thread.get("workspaceRevision") or "",
        "inspectedResources": inspected if isinstance(inspected, dict) else {},
        "confirmedFacts": thread.get("confirmedFacts") or [],
        "userDecisions": thread.get("userDecisions") or [],
        "doNotRepeat": sorted((inspected or {}).keys()) if isinstance(inspected, dict) else [],
    }
    return [
        {"role": "system", "content": _WORKBENCH_PLANNER_SYSTEM_PROMPT},
        {"role": "user", "content": _workbench_stable_json(checkpoint)},
        {"role": "assistant", "content": latest_assistant_content},
    ]


def _workbench_planning_context_chars(messages: list[dict[str, Any]]) -> int:
    return sum(len(_workbench_stable_json(message)) for message in messages)


def _workbench_maybe_compact_planning_thread(thread: dict[str, Any]) -> None:
    messages = thread.get("messages")
    if not isinstance(messages, list) or not messages:
        return
    if not thread.pop("compactionPending", False):
        return
    latest_content = str(messages[-1].get("content") or "") if isinstance(messages[-1], dict) else ""
    thread["messages"] = _workbench_planning_checkpoint(thread, latest_content)
    thread["compactionCount"] = int(thread.get("compactionCount") or 0) + 1


def _workbench_feedback_needs_workspace(
    feedback: str,
    *,
    requested_operation: str,
) -> bool:
    text = str(feedback or "").strip()
    if not text:
        return False
    resource_signal = re.search(
        r"([A-Za-z0-9_.-]+[/\\][A-Za-z0-9_./\\-]+|"
        r"\.(?:py|js|jsx|ts|tsx|java|go|rs|rb|php|swift|kt|md|toml|json|ya?ml)\b|"
        r"新(?:文件|目录|模块|组件|接口|服务)|新增(?:文件|目录|模块|组件)|"
        r"代码库|源码|工作区|项目结构|实际文件|先检查|先读取|查看文件|重新扫描)",
        text,
        re.IGNORECASE,
    )
    if resource_signal:
        return True
    local_edit_only = re.search(
        r"(步骤|顺序|依赖|描述|标题|验收|删除|移除|合并|拆分|调序|提前|延后|"
        r"第[一二三四五六七八九十\d]+步|保留原计划|改写|精简|详细一点)",
        text,
    )
    if local_edit_only:
        return False
    # A replacement changes the decomposition, not necessarily workspace facts.
    if requested_operation == "replace":
        return False
    return False


def _workbench_plan_tool_bundle(
    session: dict[str, Any],
    workspace_root: Path | None,
    *,
    feedback: str,
    requested_operation: str,
    auto_start: bool,
) -> tuple[str, str, dict[str, str]]:
    current_revision, current_snapshot = _workbench_workspace_state(workspace_root)
    thread = _workbench_planning_thread(session, workspace_root)
    previous_revision = str(thread.get("workspaceRevision") or "")
    has_history = bool(thread.get("messages"))
    workspace_changed = bool(previous_revision and previous_revision != current_revision)
    goal_text = str(session.get("goal") or session.get("title") or "")
    explicitly_independent = bool(re.search(
        r"(与(?:当前|本地)?项目无关|不涉及(?:当前|本地)?项目|不要(?:读取|查看|检查|关联)(?:工作区|项目|文件)|"
        r"旅行计划|健身计划|学习计划|活动策划|会议议程|写作提纲)",
        goal_text,
    ))

    if _is_workspace_empty(workspace_root):
        bundle = _WORKBENCH_PLANNER_NO_TOOLS_VERSION
    elif explicitly_independent:
        bundle = _WORKBENCH_PLANNER_NO_TOOLS_VERSION
    elif auto_start:
        bundle = _WORKBENCH_PLANNER_EXPLORE_VERSION
    elif not has_history:
        bundle = _WORKBENCH_PLANNER_EXPLORE_VERSION
    elif workspace_changed or _workbench_feedback_needs_workspace(
        feedback, requested_operation=requested_operation
    ):
        bundle = _WORKBENCH_PLANNER_EXPLORE_VERSION
    else:
        bundle = _WORKBENCH_PLANNER_NO_TOOLS_VERSION
    return bundle, current_revision, current_snapshot


async def _workbench_exec_explore_tool(
    tc: dict,
    workspace_root: Path | None,
    *,
    observation_cache: dict[str, Any] | None = None,
    runtime_cache: dict[str, str] | None = None,
    metrics: dict[str, int] | None = None,
    workspace_revision: str = "",
    inspected_resources: dict[str, Any] | None = None,
) -> str:
    """Execute one workspace-exploration tool call, confined to workspace_root."""
    name = tc["function"]["name"]
    try:
        args = json.loads(tc["function"].get("arguments") or "{}")
    except json.JSONDecodeError:
        return "Error: invalid tool arguments"

    rel_path = str(args.get("path") or ".").strip()
    if not workspace_root or not workspace_root.is_dir():
        return "Error: workspace directory does not exist or is inaccessible"

    target = (workspace_root / rel_path).resolve()
    try:
        target.relative_to(workspace_root)
    except ValueError:
        return "Error: path is outside the workspace directory"

    observation_cache = observation_cache if isinstance(observation_cache, dict) else {}
    runtime_cache = runtime_cache if isinstance(runtime_cache, dict) else {}
    inspected_resources = inspected_resources if isinstance(inspected_resources, dict) else {}
    metrics = metrics if isinstance(metrics, dict) else {}
    metrics.setdefault("workspaceCacheHits", 0)
    metrics.setdefault("workspaceCacheMisses", 0)
    metrics.setdefault("duplicateCallsBlocked", 0)
    normalized_args: dict[str, Any]
    if name == "read_file":
        try:
            offset = max(0, int(args.get("offset") or 0))
        except (TypeError, ValueError):
            offset = 0
        try:
            limit = min(12000, max(1, int(args.get("limit") or 4000)))
        except (TypeError, ValueError):
            limit = 4000
        normalized_args = {"path": rel_path, "offset": offset, "limit": limit}
    elif name == "glob":
        normalized_args = {"pattern": str(args.get("pattern") or "").strip()}
    else:
        normalized_args = {"path": rel_path}
    logical_key = (
        f"{workspace_root.as_posix()}:{name}:"
        f"{_workbench_stable_json(normalized_args)}"
    )
    if logical_key in runtime_cache:
        metrics["duplicateCallsBlocked"] += 1
        return runtime_cache[logical_key]

    try:
        if name == "list_directory":
            stat = target.stat()
            fingerprint = f"{workspace_revision}:{stat.st_mtime_ns}"
            cached = observation_cache.get(logical_key)
            if isinstance(cached, dict) and cached.get("resourceFingerprint") == fingerprint:
                result = str(cached.get("result") or "")
                runtime_cache[logical_key] = result
                metrics["workspaceCacheHits"] += 1
                return result
            entries: list[str] = []
            for p in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                if p.name.startswith("."):
                    continue
                suffix = "/" if p.is_dir() else ""
                entries.append(f"{p.name}{suffix}")
            result = "\n".join(entries) if entries else "(empty directory)"

        elif name == "read_file":
            if not target.is_file():
                return "Error: not a file or does not exist"
            if target.stat().st_size > 256 * 1024:
                return "Error: file too large (>256KB)"
            stat = target.stat()
            stat_fingerprint = f"{stat.st_size}:{stat.st_mtime_ns}"
            cached = observation_cache.get(logical_key)
            if isinstance(cached, dict) and cached.get("statFingerprint") == stat_fingerprint:
                result = str(cached.get("result") or "")
                runtime_cache[logical_key] = result
                metrics["workspaceCacheHits"] += 1
                return result
            try:
                text = target.read_text(encoding="utf-8", errors="replace")
                file_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                result = text[offset:offset + limit]
                if offset + limit < len(text):
                    result += f"\n\n...(truncated; next offset={offset + limit})"
                fingerprint = file_hash
            except (UnicodeDecodeError, LookupError):
                return "Error: binary file (cannot read as text)"

        elif name == "glob":
            pattern = normalized_args["pattern"]
            if not pattern:
                return "Error: missing glob pattern"
            fingerprint = workspace_revision
            cached = observation_cache.get(logical_key)
            if isinstance(cached, dict) and cached.get("resourceFingerprint") == fingerprint:
                result = str(cached.get("result") or "")
                runtime_cache[logical_key] = result
                metrics["workspaceCacheHits"] += 1
                return result
            it = workspace_root.rglob(pattern.lstrip("/"))
            matches: list[str] = []
            for p in sorted(it):
                if any(part.startswith(".") or part in _WORKBENCH_EMPTY_WORKSPACE_SKIP_DIRS for part in p.relative_to(workspace_root).parts):
                    continue
                rel = str(p.relative_to(workspace_root))
                suffix = "/" if p.is_dir() else ""
                matches.append(f"{rel}{suffix}")
            if len(matches) > 50:
                matches = matches[:50] + [f"... and {len(matches) - 50} more"]
            result = "\n".join(matches) if matches else "(no matches)"
        else:
            return f"Error: unknown tool '{name}'"

    except PermissionError:
        return "Error: permission denied"
    except OSError as e:
        return f"Error: {e}"

    metrics["workspaceCacheMisses"] += 1
    record = {
        "tool": name,
        "canonicalArgs": normalized_args,
        "workspaceRevision": workspace_revision,
        "resourceFingerprint": fingerprint,
        "result": result,
        "facts": [
            (
                f"已读取 {rel_path} 的字符范围 "
                f"{normalized_args.get('offset', 0)}.."
                f"{normalized_args.get('offset', 0) + normalized_args.get('limit', 0)}"
                if name == "read_file"
                else f"已观察 {name} 参数 {_workbench_stable_json(normalized_args)}"
            )
        ],
        "valid": True,
    }
    if name == "read_file":
        record["statFingerprint"] = stat_fingerprint
    observation_cache[logical_key] = record
    runtime_cache[logical_key] = result
    inspected_resources[logical_key] = {
        "resourceFingerprint": fingerprint,
        "workspaceRevision": workspace_revision,
        "facts": record["facts"],
    }
    return result


async def _workbench_run_explore_agent(
    workspace_root: Path | None,
    prompt: str,
    *,
    max_turns: int = 8,
    max_tokens: int | None = 9000,
    timeout: float = 90,
    secondary: bool = False,
    session_id: str = "",
    clean_context: bool = False,
    raise_on_failure: bool = False,
    planning_thread: dict[str, Any] | None = None,
    tool_bundle_version: str = _WORKBENCH_PLANNER_EXPLORE_VERSION,
    workspace_revision: str = "",
) -> dict[str, Any] | None:
    """Run an LLM that may explore the workspace (list_directory/read_file/glob)
    before answering, and return the JSON object it emits (or None on failure).

    Rich workspaces can tempt the model to keep exploring past the turn budget,
    so after ``max_turns`` of tool use we force one final answer WITHOUT tools —
    the model must return the JSON from what it has already gathered.

    When ``session_id`` is given the run is tagged with it (via the agent-state
    ContextVar) so each LLM "thinking" round and exploration tool call publishes
    a live SSE event the workbench task card can stream — otherwise this agent
    works invisibly and the UI can only show a spinner.

    ``clean_context=True`` keeps the model call detached from the task's agent
    session while still publishing explicit tool events to ``session_id``. Use
    this for independent reviewers that must not inherit execution context.
    """
    event_sid = str(session_id or "").strip()
    context_sid = "" if clean_context else event_sid
    binding = (
        bind_run_context(session_id=context_sid)
        if (event_sid or clean_context)
        else None
    )
    thread = planning_thread if isinstance(planning_thread, dict) else None
    use_explore_tools = tool_bundle_version == _WORKBENCH_PLANNER_EXPLORE_VERSION
    tools: list[dict[str, Any]] | None = _WORKBENCH_EXPLORE_TOOLS if use_explore_tools else None
    observation_cache = thread.setdefault("observationCache", {}) if thread is not None else {}
    inspected_resources = thread.setdefault("inspectedResources", {}) if thread is not None else {}
    runtime_cache: dict[str, str] = {}
    call_metrics: dict[str, Any] = {
        "promptBundleVersion": _WORKBENCH_PLANNER_CONTRACT_VERSION if thread is not None else "",
        "toolBundleVersion": tool_bundle_version if thread is not None else "",
        "systemPromptHash": hashlib.sha256(
            _WORKBENCH_PLANNER_SYSTEM_PROMPT.encode("utf-8")
        ).hexdigest() if thread is not None else "",
        "toolsHash": _workbench_hash_json(tools or []),
        "planningThreadId": str(thread.get("id") or "") if thread is not None else "",
        "workspaceRevision": workspace_revision,
        "promptTokens": 0,
        "cachedTokens": 0,
        "workspaceCacheHits": 0,
        "workspaceCacheMisses": 0,
        "duplicateCallsBlocked": 0,
    }

    def _record_usage(response: Any) -> None:
        if not isinstance(response, dict):
            return
        usage = response.get("usage")
        if not isinstance(usage, dict):
            return
        call_metrics["promptTokens"] += int(usage.get("prompt_tokens") or 0)
        call_metrics["cachedTokens"] += int(
            usage.get("prompt_cache_hit_tokens")
            or usage.get("cached_tokens")
            or 0
        )

    def _commit_thread(messages: list[dict[str, Any]], content: str, parsed: dict[str, Any]) -> None:
        if thread is None:
            return
        final_content = str(content or "").strip() or _workbench_stable_json(parsed)
        if not messages or messages[-1].get("role") != "assistant":
            messages.append({"role": "assistant", "content": final_content})
        thread["messages"] = messages
        thread["workspaceRevision"] = workspace_revision
        thread["lastToolBundleVersion"] = tool_bundle_version
        metrics_history = thread.setdefault("metrics", [])
        if isinstance(metrics_history, list):
            metrics_history.append(dict(call_metrics))
            if len(metrics_history) > 50:
                del metrics_history[:-50]
        if _workbench_planning_context_chars(messages) > _WORKBENCH_PLANNING_THREAD_MAX_CHARS:
            thread["compactionPending"] = True
        logger.info("Workbench planning metrics: %s", _workbench_stable_json(call_metrics))

    async def _emit_tool_event(tc: dict[str, Any]) -> None:
        if not event_sid:
            return
        try:
            fn = tc.get("function") or {}
            name = str(fn.get("name") or "").strip()
            if not name:
                return
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
            await debug.publish_event({
                "type": "tool_call",
                "session_id": event_sid,
                "tool": name,
                "args": args,
                "caller": "explore",
                "timestamp": _utc_now_iso(),
            })
        except Exception:
            pass  # live progress is best-effort; never break the run for it

    try:
        if thread is not None:
            prior_messages = thread.get("messages")
            messages = [
                dict(message)
                for message in prior_messages
                if isinstance(message, dict)
            ] if isinstance(prior_messages, list) else []
            if not messages:
                messages.append({"role": "system", "content": _WORKBENCH_PLANNER_SYSTEM_PROMPT})
            messages.append({"role": "user", "content": prompt})
        else:
            messages = [{"role": "user", "content": prompt}]
        for turn in range(max_turns):
            try:
                response = await asyncio.wait_for(
                    _call_llm(
                        messages,
                        tools=tools,
                        max_tokens=max_tokens,
                        secondary=secondary,
                        thinking="disabled",
                    ),
                    timeout=timeout,
                )
            except Exception as exc:
                logger.exception("Workbench explore-agent failed (turn %d)", turn + 1)
                if raise_on_failure:
                    raise _workbench_generation_error(exc)
                return None
            if not isinstance(response, dict):
                error = _WorkbenchGenerationError(
                    "configuration",
                    "模型未配置，或模型服务返回了空响应。",
                )
                if raise_on_failure:
                    raise error
                return None
            _record_usage(response)
            tool_calls = response.get("tool_calls") or []
            if not tool_calls:
                content = response.get("content") or ""
                parsed = _workbench_parse_json_object(content)
                if parsed is not None:
                    _commit_thread(messages, content, parsed)
                    return parsed
                try:
                    repaired = await _workbench_repair_json_response(
                        messages,
                        content,
                        max_tokens=max_tokens,
                        timeout=timeout,
                        secondary=secondary,
                    )
                except Exception as exc:
                    logger.exception("Workbench explore-agent JSON repair failed")
                    if raise_on_failure:
                        raise _workbench_generation_error(exc)
                    return None
                if repaired is not None:
                    _commit_thread(messages, _workbench_stable_json(repaired), repaired)
                    return repaired
                if raise_on_failure:
                    raise _workbench_explore_parse_failure(response, content)
                return None
            if tools is None:
                messages.append({
                    "role": "assistant",
                    "content": response.get("content") or "",
                    "tool_calls": tool_calls,
                })
                for tc in tool_calls:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id") or _short_id("blocked_tool"),
                        "content": "Error: workspace tools are not available for this planning revision; use existing observations.",
                    })
                messages.append({
                    "role": "user",
                    "content": "不要调用工具。请基于已有规划历史和观察结果直接返回最终 JSON。",
                })
                continue
            # The assistant tool-call message MUST be appended before the tool
            # results — a 'tool' message has to follow an assistant message carrying
            # its tool_calls, otherwise the next request is malformed and rejected.
            assistant_entry: dict[str, Any] = {"role": "assistant", "content": response.get("content") or "", "tool_calls": tool_calls}
            if response.get("reasoning_content"):
                assistant_entry["reasoning_content"] = response["reasoning_content"]
            messages.append(assistant_entry)
            for tc in tool_calls:
                await _emit_tool_event(tc)
                result = await _workbench_exec_explore_tool(
                    tc,
                    workspace_root,
                    observation_cache=observation_cache,
                    runtime_cache=runtime_cache,
                    metrics=call_metrics,
                    workspace_revision=workspace_revision,
                    inspected_resources=inspected_resources,
                )
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
            if call_metrics["duplicateCallsBlocked"] >= 2:
                tools = None

        # Turn budget exhausted while still exploring — force a final answer with no
        # tools available, so the model has to emit the JSON now.
        messages.append({
            "role": "user",
            "content": "请停止探索。基于你已经了解到的信息，现在只返回最终的 JSON 对象本身，不要再调用任何工具，也不要任何额外说明或 Markdown 代码块标记。（输出必须是单个合法的 json 对象。）",
        })
        try:
            final = await asyncio.wait_for(
                _call_llm(
                    messages,
                    tools=None,
                    max_tokens=max_tokens,
                    secondary=secondary,
                    thinking="disabled",
                    response_format=_WORKBENCH_JSON_RESPONSE_FORMAT,
                ),
                timeout=timeout,
            )
        except Exception as exc:
            logger.exception("Workbench explore-agent final answer failed")
            if raise_on_failure:
                raise _workbench_generation_error(exc)
            return None
        _record_usage(final)
        if not isinstance(final, dict):
            if raise_on_failure:
                raise _WorkbenchGenerationError(
                    "configuration",
                    "模型未配置，或模型服务返回了空响应。",
                )
            return None
        content = final.get("content") or ""
        parsed = _workbench_parse_json_object(content)
        if parsed is not None:
            _commit_thread(messages, content, parsed)
            return parsed
        try:
            repaired = await _workbench_repair_json_response(
                messages,
                content,
                max_tokens=max_tokens,
                timeout=timeout,
                secondary=secondary,
            )
        except Exception as exc:
            logger.exception("Workbench explore-agent final JSON repair failed")
            if raise_on_failure:
                raise _workbench_generation_error(exc)
            return None
        if repaired is not None:
            _commit_thread(messages, _workbench_stable_json(repaired), repaired)
            return repaired
        if raise_on_failure:
            raise _workbench_explore_parse_failure(final, content)
        return None
    finally:
        if binding is not None:
            binding.reset()


async def _workbench_generate_init_form(
    project: dict[str, Any],
    lang: str = "",
) -> dict[str, Any] | None:
    """Ask an agent (with file-exploration tools) to produce onboarding
    questions tailored to this project.

    If the workspace is empty (no real source files), use the user's project
    description to generate tailored questions without workspace tools. If
    there is no description or generation fails, fall back to the deterministic
    template form.

    ``lang`` is the user's UI language code (e.g. ``"zh"``, ``"en"``) —
    defaults to ``"zh"`` when empty so the prompt instructs the LLM in the
    right language without hardcoding.

    Returns a normalized init form, or ``None`` when generation is unavailable
    (the caller then keeps the deterministic fallback form).
    """
    name = str(project.get("name") or "新项目").strip()
    description = str(project.get("description") or "").strip()
    template = str(project.get("template") or "").strip()
    template_label = _WORKBENCH_TEMPLATE_LABELS.get(template, template)
    base_form = _workbench_default_init_form(project)

    # Map language code to the human-readable name used in the prompt.
    _LANG_NAMES = {"zh": "简体中文", "en": "English", "ja": "日本語"}
    language = _LANG_NAMES.get(lang, _LANG_NAMES.get("zh"))

    details = [f"项目名称：{name}"]
    if description:
        details.append(f"项目描述：{description}")
    if template_label:
        details.append(f"项目类型：{template_label}")
    details_block = "\n".join(details)

    workspace_path = str(project.get("workspacePath") or "").strip()
    workspace_root = Path(workspace_path).expanduser().resolve() if workspace_path else None
    workspace_relationship_guidance = _workbench_init_workspace_relationship_guidance(project)

    init_form_schema = (
        "最后只返回一个 JSON 对象，不要包含任何额外说明或 Markdown 代码块标记。"
        "JSON 结构如下：\n"
        "{\n"
        '  "greeting": "一句友好的开场白，说明你将协助完成项目初始化",\n'
        '  "sections": [\n'
        "    {\n"
        '      "id": "英文小写下划线短标识",\n'
        f'      "title": "分组标题（{language}，简洁）",\n'
        '      "questions": [\n'
        '        {"id": "英文标识", "type": "text|textarea|single|multi", '
        f'"label": "问题（{language}）", "placeholder": "示例答案（text/textarea 适用）", '
        '"options": ["选项1", "选项2"]}\n'
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n\n"
    )

    # ── Empty / no real files → generate from metadata when possible ───
    if _is_workspace_empty(workspace_root):
        logger.info(
            "Workspace %s is empty — generating metadata-based init form for project %s",
            workspace_path or "(none)", project.get("id"),
        )
        if description:
            prompt = (
                "你是一个项目初始化助理。用户刚刚创建了一个全新项目，工作区目前还没有代码或资料文件。"
                "你不能探索文件；请只根据用户提供的项目名称、项目类型和项目描述，"
                "设计一组贴合该项目目标的引导式问题，帮助用户把需求、范围、约束和第一批任务澄清清楚。\n\n"
                f"项目信息：\n{details_block}\n\n"
                + init_form_schema +
                "要求：\n"
                "- 必须围绕项目描述中的具体目标、场景、对象或产出提问，不要只套用通用模板；\n"
                "- 不要重复询问描述中已经明确的项目目标，而要追问边界、优先级、用户/受众、关键约束、验收标准或第一阶段计划；\n"
                "- 根据描述自主决定 3-5 个分组，每个分组 2-4 个问题；\n"
                "- 多数问题用 text 或 textarea；涉及阶段/选择类的用 single 或 multi 并给出 options；\n"
                f"- 全部使用{language}，语气友好专业。最后只返回 JSON。"
            )
            parsed = await _workbench_run_json_generation(prompt, max_tokens=15000, timeout=90)
            generated_form = _workbench_coerce_init_form(parsed, base_form) if parsed else None
            if generated_form:
                return generated_form

        # Return a computed form so the caller doesn't fall back to the
        # full default form which suggests the LLM *might* generate.
        empty_form = _workbench_default_init_form(project)
        empty_form["generated"] = True
        # Override greeting to reflect that there's no existing codebase.
        if language == "English":
            empty_form["greeting"] = (
                "Hi! I'm your project initialization assistant. It looks like this is a "
                "brand-new project with no code in the workspace yet. Let's start with a few "
                "key questions to help you plan the direction and scope."
            )
        else:
            empty_form["greeting"] = (
                "你好！我是你的项目初始化助理。看起来这是一个全新的项目，工作区还没有代码。"
                "我们先从几个关键问题开始，帮你规划好方向和范围。"
            )
        return empty_form

    # ── Has real files → agent explores carefully without assuming intent ─
    prompt = (
        "你是一个项目初始化助理。用户刚刚创建了一个新项目，工作区已有文件。"
        "你需要探索工作区，了解里面可能存在的内容、结构和现状，"
        "然后结合用户的项目描述、项目类型和已有文件线索，设计一组贴合实际的引导式问题，"
        "帮助用户完成项目初始化。\n\n"
        f"项目信息：\n{details_block}\n\n"
        f"{workspace_relationship_guidance}\n\n"
        "你可以使用 list_directory、read_file 和 glob 工具深度探索工作区。\n\n"
        "请多花几轮仔细探索，推荐的探索步骤：\n"
        "1. list_directory('.') — 先了解顶层结构\n"
        "2. glob('**/*') 或按文件类型了解内容分布\n"
        "3. 读 README、配置文件或关键入口文件了解项目概况\n"
        "4. 如果文件较多，深入看几个关键目录的内容\n\n"
        "充分了解后再生成 JSON，不要过早下结论。\n\n"
        + init_form_schema +
        "要求：\n"
        "- greeting 必须保持中性谨慎：可以说“我看到工作区里有一些已有文件/资料”，但不能说“这是一个围绕某某的项目”或“与你描述的空白项目差异较大”，除非用户描述中明确这么说；\n"
        "- 不能把已有文件夹内容当作已确认项目事实；文件探索结论只能作为待确认线索来设计问题；\n"
        "- 根据工作区的实际情况，自主决定需要几个分组以及覆盖哪些方向；\n"
        "- 用户提供的项目描述是最高优先级需求信号；问题必须同时回应项目描述和文件现状，不要只围绕代码结构提问；\n"
        "- 如果项目描述与工作区内容存在缺口或不一致，要设计问题澄清差异和下一步取舍；\n"
        "- 如果用户没有明确说明要导入/复用已有文件，第一组问题必须先确认已有文件与新项目的关系，再追问具体规划；\n"
        "- 每个分组 2-4 个问题，问题要贴合项目实际情况，避免空泛；\n"
        "- 优先围绕项目已有的内容提问（如需要完善的地方、可以补充的方向、后续步骤等）；\n"
        "- 多数问题用 text 或 textarea；涉及阶段/选择类的用 single 或 multi 并给出 options；\n"
        f"- 全部使用{language}，语气友好专业。最后只返回 JSON。"
    )

    parsed = await _workbench_run_explore_agent(workspace_root, prompt, max_tokens=18000, timeout=120)
    if not parsed:
        return None
    return _workbench_coerce_init_form(parsed, base_form)


def _workbench_init_brief(project: dict[str, Any], form: dict[str, Any]) -> str:
    """Render the collected onboarding answers into a Markdown project brief."""
    answers = form.get("answers") if isinstance(form.get("answers"), dict) else {}
    lines = [f"# {project.get('name') or '项目'} · 初始化总结", ""]
    for section in form.get("sections", []):
        section_lines: list[str] = []
        for question in section.get("questions", []):
            qid = question.get("id")
            value = answers.get(qid)
            if isinstance(value, list):
                value = "、".join(str(v) for v in value if str(v).strip())
            text = str(value or "").strip()
            if text:
                section_lines.append(f"- **{question.get('label')}** {text}")
        if section_lines:
            lines.append(f"## {section.get('title')}")
            lines.extend(section_lines)
            lines.append("")
    return "\n".join(lines).strip()


def _workbench_answer_text(form: dict[str, Any], key: str) -> str:
    answers = form.get("answers") if isinstance(form.get("answers"), dict) else {}
    value = answers.get(key)
    if isinstance(value, list):
        return "、".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _workbench_fallback_init_task_plan(project: dict[str, Any], form: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a useful deterministic task plan from onboarding answers."""
    goal = _workbench_answer_text(form, "goal") or str(project.get("description") or "").strip()
    requirements = _workbench_answer_text(form, "requirements")
    tech = _workbench_answer_text(form, "tech")
    out_of_scope = _workbench_answer_text(form, "out_of_scope")
    deadline = _workbench_answer_text(form, "deadline")

    constraints: list[str] = []
    if out_of_scope:
        constraints.append(f"范围限制：{out_of_scope}")
    if deadline:
        constraints.append(f"时间约束：{deadline}")
    if tech:
        constraints.append(f"偏好工具或平台：{tech}")

    base_goal = goal or f"推进 {project.get('name') or '项目'}。"
    tasks = [
        {
            "title": "明确目标与范围",
            "goal": f"整理项目目标、背景和边界，形成清晰的范围定义。{(' 重点覆盖：' + requirements) if requirements else ''}".strip(),
            "priority": "high",
            "constraints": constraints[:],
            "acceptanceCriteria": ["目标清晰", "范围已定义", "优先级已确认"],
        },
        {
            "title": "制定执行方案",
            "goal": f"基于项目信息设计具体执行方案和计划。项目总目标：{base_goal}",
            "priority": "high",
            "constraints": constraints[:],
            "acceptanceCriteria": ["执行方案已形成", "步骤可追踪", "依赖已记录"],
        },
        {
            "title": "推进执行与交付",
            "goal": f"按计划推进执行，完成项目目标。项目总目标：{base_goal}",
            "priority": "medium",
            "constraints": constraints[:],
            "acceptanceCriteria": ["项目目标已完成", "结果可验证", "符合预期要求"],
        },
    ]
    return tasks


def _workbench_coerce_init_task_plan(raw: Any, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source = raw.get("tasks") if isinstance(raw, dict) else raw
    if not isinstance(source, list):
        return fallback
    tasks: list[dict[str, Any]] = []
    for index, item in enumerate(source):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        goal = str(item.get("goal") or item.get("description") or "").strip()
        if not title and goal:
            title = goal[:40]
        if not title:
            continue
        priority = str(item.get("priority") or "medium").strip().lower()
        if priority not in ("high", "medium", "low"):
            priority = "medium"
        constraints = [
            str(value).strip()
            for value in item.get("constraints", [])
            if str(value).strip()
        ] if isinstance(item.get("constraints"), list) else []
        acceptance = item.get("acceptanceCriteria")
        if not isinstance(acceptance, list):
            acceptance = item.get("acceptance")
        acceptance_items = [
            str(value).strip()
            for value in acceptance
            if str(value).strip()
        ] if isinstance(acceptance, list) else []
        tasks.append({
            "id": str(item.get("id") or "").strip() or _short_id("init_task"),
            "title": title[:80],
            "goal": goal[:1200] or title,
            "priority": priority,
            "constraints": constraints[:8],
            "acceptanceCriteria": acceptance_items[:8],
            "order": index + 1,
        })
    return tasks[:8] or fallback


async def _workbench_generate_init_task_plan(
    project: dict[str, Any],
    form: dict[str, Any],
    feedback: str = "",
    current_plan: list[dict[str, Any]] | None = None,
    max_attempts: int = 5,
) -> tuple[list[dict[str, Any]] | None, bool, dict[str, Any] | None]:
    """Ask the initialization agent to split the project into major task sessions.

    Returns ``(plan, from_llm, error)``. No synthetic plan is returned when all
    attempts fail. ``error`` contains a user-displayable summary of every
    attempt so the UI can explain the failure and offer a clean restart.

    When ``current_plan`` is given (a revision), it is shown to the agent so the
    output adjusts the existing plan rather than regenerating from scratch.
    """
    brief = _workbench_init_brief(project, form)
    feedback = str(feedback or "").strip()
    workspace_path = str(project.get("workspacePath") or "").strip()
    workspace_root = Path(workspace_path).expanduser().resolve() if workspace_path else None
    current_plan_block = ""
    if isinstance(current_plan, list) and current_plan:
        try:
            slim = [
                {
                    "title": str(item.get("title") or ""),
                    "goal": str(item.get("goal") or ""),
                    "priority": str(item.get("priority") or "medium"),
                    "constraints": item.get("constraints") or [],
                    "acceptanceCriteria": item.get("acceptanceCriteria") or [],
                }
                for item in current_plan
                if isinstance(item, dict)
            ]
            current_plan_block = (
                "当前任务计划（请在此基础上按反馈调整，保留未被反馈提到的部分，"
                "不要无故重排或删除）：\n"
                + json.dumps(slim, ensure_ascii=False)
                + "\n\n"
            )
        except Exception:
            current_plan_block = ""
    prompt = (
        "你是项目初始化 Agent。用户已经完成初始化问答。请把项目拆解成若干个"
        "可独立推进的大任务，每个大任务后续会创建为一个 workbench session。\n\n"
        f"项目名称：{project.get('name') or '项目'}\n"
        f"项目类型：{_WORKBENCH_TEMPLATE_LABELS.get(str(project.get('template') or ''), str(project.get('template') or ''))}\n"
        f"初始化总结：\n{brief or '暂无'}\n"
        f"{('用户对计划的修改反馈：' + feedback) if feedback else ''}\n\n"
        f"{current_plan_block}"
        "工作区已有文件，你可以使用 list_directory、read_file、glob 工具先探索项目，"
        "让大任务贴合项目实际（尽量引用真实的文件/目录/模块），不要套用空泛模板。\n\n"
        "充分了解后再返回 JSON，只返回一个 JSON 对象，不要 Markdown。结构：\n"
        "{\n"
        '  "tasks": [\n'
        "    {\n"
        '      "title": "大任务标题，中文，动宾短语",\n'
        '      "goal": "这个 session 要完成的目标、边界和上下文",\n'
        '      "priority": "high|medium|low",\n'
        '      "constraints": ["约束"],\n'
        '      "acceptanceCriteria": ["验收标准"]\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "要求：生成 3-6 个大任务；每个任务要能对应一个独立 session；避免过细的步骤；"
        "保留初始化回答中的时间、范围、技术约束。"
    )
    attempts: list[dict[str, Any]] = []
    attempt_limit = max(1, int(max_attempts or 1))
    for attempt in range(1, attempt_limit + 1):
        try:
            parsed = await _workbench_run_explore_agent(
                workspace_root,
                prompt,
                max_tokens=12000,
                timeout=120,
                secondary=True,
                raise_on_failure=True,
            )
            plan = _workbench_coerce_init_task_plan(parsed, [])
            if not plan:
                raise _WorkbenchGenerationError(
                    "response_format",
                    "模型返回的 JSON 中没有可用的 tasks。",
                )
            return plan, True, None
        except Exception as exc:
            error = _workbench_generation_error(exc)
            attempts.append({
                "attempt": attempt,
                "category": error.category,
                "message": error.message,
            })
            logger.warning(
                "Workbench init task-plan attempt %d/%d failed for project %s: %s",
                attempt,
                attempt_limit,
                project.get("id"),
                error.message,
            )
            if attempt < attempt_limit:
                await asyncio.sleep(min(2 ** (attempt - 1), 4))

    last = attempts[-1] if attempts else {
        "category": "unknown",
        "message": "未知错误",
    }
    return None, False, {
        "code": "init_plan_generation_failed",
        "attemptCount": attempt_limit,
        "category": last["category"],
        "summary": last["message"],
        "attempts": attempts,
    }


def _workbench_create_sessions_from_init_plan(
    project: dict[str, Any],
    plan: list[dict[str, Any]],
    now: str | None = None,
) -> list[dict[str, Any]]:
    """Initialization-agent tool: create task sessions from confirmed major tasks."""
    now = now or _utc_now_iso()
    created: list[dict[str, Any]] = []
    sessions = project.setdefault("sessions", [])
    for item in plan:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        session = _workbench_new_session(
            str(project.get("id") or ""),
            title,
            str(item.get("goal") or title).strip(),
            now,
            kind="task",
            status="idle",
        )
        priority = str(item.get("priority") or "medium").strip().lower()
        if priority in ("high", "medium", "low"):
            session["priority"] = priority
        if isinstance(item.get("constraints"), list):
            session["constraints"] = [str(value).strip() for value in item["constraints"] if str(value).strip()][:8]
        if isinstance(item.get("acceptanceCriteria"), list):
            session["acceptanceCriteria"] = [
                {"id": _short_id("accept"), "text": str(value).strip(), "status": "pending"}
                for value in item["acceptanceCriteria"]
                if str(value).strip()
            ][:8]
        session["events"] = [{
            "id": _short_id("event"),
            "type": "CreatedFromInitPlan",
            "createdAt": now,
            "body": "由初始化计划确认后创建。",
        }]
        created.append(session)
    for session in reversed(created):
        sessions.insert(0, session)
    return created


def _configure_workbench_store(db_path: str) -> None:
    global _db_path, _CONFIGURED_WORKBENCH_STORE
    _db_path = str(db_path)
    _CONFIGURED_WORKBENCH_STORE = Path(_WORKBENCH_STORE)


def _workbench_store_uses_sqlite() -> bool:
    return bool(
        _db_path
        and _CONFIGURED_WORKBENCH_STORE is not None
        and Path(_WORKBENCH_STORE) == _CONFIGURED_WORKBENCH_STORE
    )


def _read_workbench_store() -> dict[str, Any]:
    with _WORKBENCH_STORE_LOCK:
        if not _workbench_store_uses_sqlite():
            raw = read_json_safe(_WORKBENCH_STORE)
            if isinstance(raw, dict) and isinstance(raw.get("projects"), list):
                if not raw["projects"]:
                    raw = _workbench_default_project()
                _workbench_ensure_invariants(raw)
                return raw
            return _workbench_default_project()
        try:
            raw = read_document(
                _db_path or str(DB_PATH),
                "projects",
                _workbench_default_project,
                legacy_path=_WORKBENCH_STORE,
            )
            if not isinstance(raw, dict) or not isinstance(raw.get("projects"), list):
                raw = write_document(
                    _db_path or str(DB_PATH),
                    "projects",
                    _workbench_default_project(),
                    _workbench_default_project,
                    legacy_path=_WORKBENCH_STORE,
                    export_path=_WORKBENCH_STORE,
                )
            if not raw["projects"]:
                raw = write_document(
                    _db_path or str(DB_PATH),
                    "projects",
                    _workbench_default_project(),
                    _workbench_default_project,
                    legacy_path=_WORKBENCH_STORE,
                    export_path=_WORKBENCH_STORE,
                    base_value=getattr(raw, "_workbench_base", None),
                )
            _workbench_ensure_invariants(raw)
            return raw
        except Exception:
            logger.exception("Failed to read Workbench state from SQLite")
            raise


def _read_workbench_store_lightweight() -> dict[str, Any]:
    """Read project/task state without running workspace repair scans.

    Project rails, chat entry, and other list surfaces only need the already
    persisted metadata.  The full reader also enforces historical invariants
    and scans project workspaces for artifact backfills; on a populated
    workspace that made the tiny ``?detail=summary`` response take seconds.
    Invalid or empty legacy state still falls back to the full repair path.
    """
    with _WORKBENCH_STORE_LOCK:
        if not _workbench_store_uses_sqlite():
            raw = read_json_safe(_WORKBENCH_STORE)
        else:
            raw = read_document(
                _db_path or str(DB_PATH),
                "projects",
                _workbench_default_project,
                legacy_path=_WORKBENCH_STORE,
            )
        if (
            isinstance(raw, dict)
            and isinstance(raw.get("projects"), list)
            and raw["projects"]
        ):
            return raw
    return _read_workbench_store()


def _workbench_find_project_lightweight(project_id: str) -> dict[str, Any] | None:
    """Look up one project without running read-time repair/backfill work.

    Chat creation and chat-list scoping only need stable project metadata. The
    normal ``_read_workbench_store`` path also enforces task invariants and
    scans project workspaces for historical file artifacts; doing that work for
    a foreign-key check makes a tiny chat request scale with every task and file.
    """
    target_id = str(project_id or "").strip()
    if not target_id:
        return None
    raw = _read_workbench_store_lightweight()
    project = _workbench_find_project(raw, target_id)
    if not isinstance(project, dict):
        return None
    result = dict(project)
    relocated_root = _workbench_workspace_root(result)
    if relocated_root is not None:
        result["workspacePath"] = str(relocated_root)
    return result


def _write_workbench_store(
    payload: dict[str, Any],
    *,
    base_value: dict[str, Any] | None = None,
) -> None:
    with _WORKBENCH_STORE_LOCK:
        if not _workbench_store_uses_sqlite():
            atomic_write_json(_WORKBENCH_STORE, payload)
            return
        merged = write_document(
            _db_path or str(DB_PATH),
            "projects",
            payload,
            _workbench_default_project,
            legacy_path=_WORKBENCH_STORE,
            export_path=_WORKBENCH_STORE,
            base_value=base_value,
        )
        payload.clear()
        payload.update(merged)
        if hasattr(payload, "_workbench_base"):
            payload._workbench_base = getattr(merged, "_workbench_base", dict(merged))


def _persist_workbench_selection(project_id: str | None, session_id: str | None) -> dict[str, Any]:
    """Persist only the active selection, without task/workspace invariant scans."""
    fields: dict[str, Any] = {}
    if project_id is not None:
        fields["activeProjectId"] = str(project_id).strip()
    if session_id is not None:
        fields["activeSessionId"] = str(session_id).strip()
    if not fields:
        return {}

    with _WORKBENCH_STORE_LOCK:
        if not _workbench_store_uses_sqlite():
            payload = read_json_safe(_WORKBENCH_STORE)
            if not isinstance(payload, dict) or not isinstance(payload.get("projects"), list):
                payload = _workbench_default_project()
            payload.update(fields)
            atomic_write_json(_WORKBENCH_STORE, payload)
            return fields
        return patch_document_fields(
            _db_path or str(DB_PATH),
            "projects",
            fields,
            _workbench_default_project,
            legacy_path=_WORKBENCH_STORE,
            export_path=_WORKBENCH_STORE,
        )


def _workbench_ensure_invariants(payload: dict[str, Any]) -> bool:
    changed = False
    projects = payload.setdefault("projects", [])
    now = _utc_now_iso()
    for project in projects:
        project.setdefault("id", _short_id("project"))
        project.setdefault("name", "Workspace")
        project.setdefault("description", "")
        project.setdefault("icon", "spark")
        project.setdefault("color", "")
        project.setdefault("template", "blank")
        project.setdefault("workspacePath", str(WORKSPACE_DIR))
        project.setdefault("status", "active")
        project.setdefault("model", _get_model())
        project.setdefault("accountTier", "Pro")
        project.setdefault("context", {"summary": "", "stack": [], "decisions": [], "knowledgeDocumentIds": []})
        _workbench_task_ensure_shared_context(project)
        project.setdefault("createdAt", now)
        project.setdefault("updatedAt", now)
        relocated_root = _workbench_workspace_root(project)
        if (
            relocated_root is not None
            and str(project.get("workspacePath") or "") != str(relocated_root)
        ):
            project["workspacePath"] = str(relocated_root)
            changed = True
        if not project.get("dataKey"):
            is_legacy_default = (
                str(project.get("name") or "").strip().lower() == "workspace"
                and str(project.get("workspacePath") or "") == str(WORKSPACE_DIR)
                and str((project.get("context") or {}).get("summary") or "").startswith("Workspace at ")
            )
            project["dataKey"] = _WORKBENCH_LEGACY_DATA_KEY if is_legacy_default else _safe_workbench_data_key(project.get("id"))
            changed = True
        if _workbench_project_data_key(project) == _WORKBENCH_LEGACY_DATA_KEY:
            default_name = _workbench_default_project_name()
            if str(project.get("name") or "") in ("", "Workspace", "workspace"):
                project["name"] = default_name
                changed = True
            if not str(project.get("workspacePath") or "").strip():
                project["workspacePath"] = str(WORKSPACE_DIR)
                changed = True
        sessions = project.setdefault("sessions", [])
        if not sessions:
            sessions.append(_workbench_new_session(project["id"], "新任务", "", now))
            changed = True
        for session in sessions:
            session.setdefault("projectId", project["id"])
            session.setdefault("kind", "task")
            session.setdefault("status", "idle")
            session.setdefault("priority", "medium")
            session.setdefault("createdAt", now)
            session.setdefault("updatedAt", now)
            session.setdefault("agentReply", "")
            session.setdefault("plan", [])
            session.setdefault("planRevision", 0)
            session.setdefault("planDefinitionRevision", 0)
            session.setdefault("approvedPlanDefinitionRevision", None)
            session.setdefault("events", [])
            session.setdefault("runs", [])
            session.setdefault("artifacts", [])
            session.setdefault("acceptanceCriteria", [])
            session.setdefault("summary", None)
            session.setdefault("titleLocked", False)
            plan = session.get("plan") if isinstance(session.get("plan"), list) else []
            for index, step in enumerate(plan):
                if not isinstance(step, dict):
                    continue
                if not isinstance(step.get("dependsOn"), list):
                    step["dependsOn"] = []
                    changed = True
                if step.get("order") != index + 1:
                    step["order"] = index + 1
                    changed = True
            if _workbench_prune_non_file_artifacts(session):
                changed = True
            if _workbench_prune_invalid_file_records(project, session):
                changed = True
            if _workbench_backfill_file_artifacts(
                session,
                now,
                _workbench_workspace_root(project),
            ):
                changed = True
            if _workbench_backfill_referenced_file_artifacts(project, session, now):
                changed = True
    if projects and not payload.get("activeProjectId"):
        payload["activeProjectId"] = projects[0].get("id")
        changed = True
    if projects and not payload.get("activeSessionId"):
        first_sessions = projects[0].get("sessions") or []
        payload["activeSessionId"] = first_sessions[0].get("id") if first_sessions else ""
        changed = True
    return changed


def _workbench_find_project(payload: dict[str, Any], project_id: str) -> dict[str, Any] | None:
    for project in payload.get("projects", []):
        if str(project.get("id") or "") == project_id:
            return project
    return None


_WORKBENCH_SESSION_SUMMARY_FIELDS = (
    "id",
    "projectId",
    "kind",
    "title",
    "goal",
    "status",
    "priority",
    "createdAt",
    "updatedAt",
    "summary",
    "titleLocked",
)


def _workbench_session_summary(session: dict[str, Any]) -> dict[str, Any]:
    """Return the rail/list shape for a task session without history payloads."""
    summary = {field: session.get(field) for field in _WORKBENCH_SESSION_SUMMARY_FIELDS if field in session}
    summary["id"] = str(summary.get("id") or session.get("id") or "")
    summary["projectId"] = str(summary.get("projectId") or session.get("projectId") or "")
    summary["isSummary"] = True
    summary["planStepCount"] = len(session.get("plan") or []) if isinstance(session.get("plan"), list) else 0
    summary["eventCount"] = len(session.get("events") or []) if isinstance(session.get("events"), list) else 0
    summary["runCount"] = len(session.get("runs") or []) if isinstance(session.get("runs"), list) else 0
    summary["artifactCount"] = len(session.get("artifacts") or []) if isinstance(session.get("artifacts"), list) else 0
    return summary


def _workbench_lightweight_store(payload: dict[str, Any]) -> dict[str, Any]:
    """Return projects with session summaries, keeping only the active session full."""
    active_project_id = str(payload.get("activeProjectId") or "")
    active_session_id = str(payload.get("activeSessionId") or "")
    projects: list[dict[str, Any]] = []
    for project in payload.get("projects", []):
        if not isinstance(project, dict):
            continue
        next_project = dict(project)
        next_sessions: list[dict[str, Any]] = []
        for session in project.get("sessions") or []:
            if not isinstance(session, dict):
                continue
            if str(project.get("id") or "") == active_project_id and str(session.get("id") or "") == active_session_id:
                full = dict(session)
                full.pop("isSummary", None)
                next_sessions.append(full)
            else:
                next_sessions.append(_workbench_session_summary(session))
        next_project["sessions"] = next_sessions
        projects.append(next_project)
    return {
        **{k: v for k, v in payload.items() if k != "projects"},
        "projects": projects,
    }


def _workbench_project_shell(project: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return project metadata with session summaries only."""
    if not isinstance(project, dict):
        return None
    shell = dict(project)
    shell["sessions"] = [
        _workbench_session_summary(session)
        for session in (project.get("sessions") or [])
        if isinstance(session, dict)
    ]
    return shell


def _workbench_find_session(payload: dict[str, Any], session_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    for project in payload.get("projects", []):
        for session in project.get("sessions", []):
            if str(session.get("id") or "") == session_id:
                return project, session
    return None, None


def update_task_plan_for_session(
    session_id: str,
    operation: str,
    *,
    step_id: str = "",
    step: dict[str, Any] | None = None,
    fields: dict[str, Any] | None = None,
    ordered_step_ids: list[Any] | None = None,
    depends_on: list[Any] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    """Mutate the current Workbench task plan for the main-agent tool.

    The user-facing HTTP mutation endpoint blocks while an agent is running.
    This helper is intentionally separate so the running main task agent can
    update its own pending plan steps when new input changes the plan.
    """
    sid = str(session_id or "").strip()
    op = str(operation or "").strip().lower()
    if not sid:
        return {"ok": False, "error": "no active task session", "code": "no_session"}

    with _WORKBENCH_STORE_LOCK:
        payload = _read_workbench_store()
        project, session = _workbench_find_session(payload, sid)
        if not session or not project:
            return {"ok": False, "error": "session not found", "code": "session_not_found"}
        if str(session.get("kind") or "task") != "task":
            return {"ok": False, "error": "only Workbench task sessions support task plans", "code": "not_task_session"}

        current_revision = int(session.get("planDefinitionRevision") or 0)
        plan = _workbench_normalize_plan(session.get("plan"), task_id=sid)
        by_id = {
            str(item.get("id") or ""): item
            for item in plan
            if isinstance(item, dict)
        }
        field_values = fields if isinstance(fields, dict) else {}
        structure_operation = op in ("add", "reorder", "set_dependencies")
        if op == "update" and any(
            field in field_values for field in ("title", "description", "dependsOn")
        ):
            structure_operation = True
        if structure_operation and _workbench_plan_has_started(plan):
            return {
                "ok": False,
                "error": "计划已经开始执行，只能编辑尚未运行步骤的命令和上下文。",
                "code": "plan_started",
            }

        if op == "add":
            step_input = step if isinstance(step, dict) else {}
            title = str(step_input.get("title") or "").strip()
            if not title:
                return {"ok": False, "error": "步骤标题不能为空。", "code": "empty_step_title"}
            if len(plan) >= 12:
                return {"ok": False, "error": "执行计划最多包含 12 个步骤。", "code": "plan_too_large"}
            new_step = _workbench_new_plan_step(
                title[:160],
                str(step_input.get("description") or "").strip()[:4000],
                len(plan) + 1,
                sid,
            )
            new_step["dependsOn"] = _workbench_dependency_ids(step_input.get("dependsOn"))
            plan.append(new_step)
        elif op == "update":
            target_id = str(step_id or "").strip()
            target = by_id.get(target_id)
            if not target:
                return {"ok": False, "error": "步骤不存在。", "code": "step_not_found"}
            allowed_fields = {"title", "description", "dependsOn", "promptOverride", "contextFiles"}
            if any(field not in allowed_fields for field in field_values):
                return {"ok": False, "error": "包含不允许修改的步骤字段。", "code": "invalid_step_fields"}
            if str(target.get("status") or "pending") != "pending":
                return {"ok": False, "error": "只能编辑尚未运行的步骤。", "code": "step_started"}
            if "title" in field_values:
                title = str(field_values.get("title") or "").strip()
                if not title:
                    return {"ok": False, "error": "步骤标题不能为空。", "code": "empty_step_title"}
                target["title"] = title[:160]
            if "description" in field_values:
                target["description"] = str(field_values.get("description") or "").strip()[:4000]
            if "dependsOn" in field_values:
                target["dependsOn"] = _workbench_dependency_ids(field_values.get("dependsOn"))
            if "promptOverride" in field_values:
                target["promptOverride"] = str(field_values.get("promptOverride") or "")[:12000]
            if "contextFiles" in field_values:
                context_files = field_values.get("contextFiles")
                if not isinstance(context_files, list):
                    return {"ok": False, "error": "contextFiles must be a list", "code": "invalid_context_files"}
                target["contextFiles"] = context_files[:30]
        elif op == "set_dependencies":
            target_id = str(step_id or "").strip()
            target = by_id.get(target_id)
            if not target:
                return {"ok": False, "error": "步骤不存在。", "code": "step_not_found"}
            target["dependsOn"] = _workbench_dependency_ids(depends_on)
        elif op == "delete":
            target_id = str(step_id or "").strip()
            target = by_id.get(target_id)
            if not target:
                return {"ok": False, "error": "步骤不存在。", "code": "step_not_found"}
            if str(target.get("status") or "pending") != "pending":
                return {"ok": False, "error": "只能删除尚未运行的步骤。", "code": "step_started"}
            dependent_titles = [
                str(item.get("title") or "")
                for item in plan
                if target_id in _workbench_dependency_ids(item.get("dependsOn"))
            ]
            if dependent_titles:
                return {
                    "ok": False,
                    "error": "该步骤仍被以下步骤依赖：" + "、".join(dependent_titles),
                    "code": "step_has_dependents",
                }
            plan = [item for item in plan if str(item.get("id") or "") != target_id]
        elif op == "reorder":
            ordered_ids = _workbench_dependency_ids(ordered_step_ids)
            current_ids = [str(item.get("id") or "") for item in plan]
            if len(ordered_ids) != len(current_ids) or set(ordered_ids) != set(current_ids):
                return {"ok": False, "error": "步骤顺序与当前计划不一致。", "code": "invalid_reorder"}
            plan = [by_id[item_id] for item_id in ordered_ids]
        else:
            return {"ok": False, "error": "unsupported plan operation", "code": "unsupported_operation"}

        plan = _workbench_normalize_plan(plan, task_id=sid)
        valid, error_message, error_code = _workbench_validate_plan_graph(plan)
        if not valid:
            return {"ok": False, "error": error_message, "code": error_code}

        now = _utc_now_iso()
        session["plan"] = plan
        session["planRevision"] = int(session.get("planRevision") or 0) + 1
        session["planDefinitionRevision"] = current_revision + 1
        session["approvedPlanDefinitionRevision"] = None
        if str(session.get("status") or "") == "waiting_for_approval":
            session["status"] = "planning"
            session["agentReply"] = "计划已修改，请重新确认后执行。"
        event_body = {
            "add": "Agent 根据当前输入新增执行步骤。",
            "update": "Agent 根据当前输入更新执行步骤。",
            "set_dependencies": "Agent 根据当前输入更新步骤依赖。",
            "delete": "Agent 根据当前输入删除执行步骤。",
            "reorder": "Agent 根据当前输入调整执行步骤顺序。",
        }.get(op, "Agent 根据当前输入更新执行计划。")
        reason_text = str(reason or "").strip()
        if reason_text:
            event_body += " 原因：" + reason_text[:500]
        session["events"] = list(session.get("events") or []) + [{
            "id": _short_id("event"),
            "type": "PlanUpdatedEvent",
            "createdAt": now,
            "body": event_body,
        }]
        session["updatedAt"] = now
        project["updatedAt"] = now
        payload["activeSessionId"] = sid
        _write_workbench_store(payload)
        return {
            "ok": True,
            "project": project,
            "session": session,
            "plan": plan,
            "planRevision": session.get("planRevision"),
            "planDefinitionRevision": session.get("planDefinitionRevision"),
            **payload,
        }


def _launch_update_restart(
    download_progress: dict[str, Any],
    *,
    get_restart_script_fn: Callable[[Path], str] | None = None,
    popen_fn: Any | None = None,
) -> tuple[bool, str, str, int]:
    """Write and spawn the updater script.

    Returns ``(ok, message, code, status_code)``. The caller is responsible for
    exiting only when ``ok`` is true.
    """
    if not bool(download_progress.get("done")):
        return (
            False,
            "Update download has not completed. Download the update before restarting.",
            "update_download_incomplete",
            409,
        )

    dest_str = str(download_progress.get("path") or "").strip()
    if not dest_str:
        return (
            False,
            "No downloaded update package found. Download the update before restarting.",
            "update_package_missing",
            409,
        )

    dest = Path(dest_str)
    try:
        if not dest.is_file():
            return (
                False,
                f"Downloaded update package is missing: {dest}",
                "update_package_missing",
                409,
            )
        file_size = dest.stat().st_size
    except OSError as exc:
        return (
            False,
            f"Unable to inspect downloaded update package: {exc}",
            "update_package_unreadable",
            409,
        )
    if file_size <= 0:
        return (
            False,
            f"Downloaded update package is empty: {dest}",
            "update_package_empty",
            409,
        )
    try:
        expected_size = int(download_progress.get("total") or 0)
    except (TypeError, ValueError):
        expected_size = 0
    if expected_size > 0 and file_size != expected_size:
        return (
            False,
            f"Downloaded update package size mismatch: {file_size} of {expected_size} bytes.",
            "update_package_size_mismatch",
            409,
        )
    expected_sha256 = str(download_progress.get("expected_sha256") or "").strip().lower()
    actual_sha256 = str(download_progress.get("actual_sha256") or "").strip().lower()
    if not expected_sha256:
        return (
            False,
            "Downloaded update package cannot be verified because the release has no sha256 checksum.",
            "update_checksum_missing",
            409,
        )
    if not actual_sha256 or actual_sha256 != expected_sha256:
        return (
            False,
            "Downloaded update package checksum mismatch.",
            "update_checksum_mismatch",
            409,
        )
    if not bool(download_progress.get("verified")):
        return (
            False,
            str(download_progress.get("verification_error") or "Downloaded update package has not passed verification."),
            "update_package_unverified",
            409,
        )

    if get_restart_script_fn is None:
        get_restart_script_fn = importlib.import_module(
            "cyrene.runtime.updater"
        ).get_restart_script
    if popen_fn is None:
        popen_fn = subprocess.Popen

    try:
        script = get_restart_script_fn(dest)
        if not str(script or "").strip():
            return (
                False,
                "Updater script generation returned an empty script.",
                "update_restart_script_empty",
                500,
            )

        if sys.platform == "win32":
            script_path = dest.parent / "update.bat"
            script_path.write_text(script, encoding="utf-8")
            popen_fn(
                ["cmd", "/c", str(script_path)],
                creationflags=(
                    0x00000200 |  # CREATE_NEW_PROCESS_GROUP
                    0x00000008    # DETACHED_PROCESS
                ),
            )
        else:
            script_path = dest.parent / "update.sh"
            script_path.write_text(script, encoding="utf-8")
            script_path.chmod(0o755)
            popen_fn(["bash", str(script_path)], start_new_session=True)
    except Exception as exc:
        logger.warning("Failed to spawn updater script", exc_info=True)
        return (
            False,
            f"Failed to launch updater script: {exc}",
            "update_restart_launch_failed",
            500,
        )

    return True, "", "", 200


def _workbench_extract_constraints(text: str) -> list[str]:
    source = str(text or "")
    constraints: list[str] = []
    patterns = [
        r"不[^\n，。；;,.]{1,32}",
        r"只[^\n，。；;,.]{1,32}",
        r"保留[^\n，。；;,.]{1,32}",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, source):
            item = match.strip()
            if item and item not in constraints:
                constraints.append(item)
    return constraints[:6]


def _workbench_new_plan_step(title: str, description: str, order: int, task_id: str = "") -> dict[str, Any]:
    """A single execution-plan step — always starts pending (no pre-completion)."""
    return {
        "id": _short_id("step"),
        "taskId": task_id,
        "title": str(title or "").strip(),
        "description": str(description or "").strip(),
        "status": "pending",
        "order": order,
        "dependsOn": [],
        "currentAction": "",
        "relatedFiles": [],
        "progressEvents": [],
        "toolCalls": [],
        "artifacts": [],
        "error": None,
    }


def _workbench_plan_from_input(user_input: str, session: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic FALLBACK plan, used only when LLM plan generation is
    unavailable. Every step starts ``pending`` — nothing is pre-marked done."""
    existing = session.get("plan") if isinstance(session.get("plan"), list) else []
    if existing:
        return existing
    base_steps = [
        "理解目标与约束",
        "收集相关信息和上下文",
        "分析现有内容",
        "制定执行方案",
        "推进执行",
        "验证结果并总结",
    ]
    task_id = session.get("id", "")
    return [
        _workbench_new_plan_step(title, "由兜底计划生成，请按需编辑。", index + 1, task_id)
        for index, title in enumerate(base_steps)
    ]


def _workbench_dependency_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        step_id = str(item or "").strip()
        if step_id and step_id not in result:
            result.append(step_id)
    return result


def _workbench_plan_has_started(plan: Any) -> bool:
    if not isinstance(plan, list):
        return False
    for step in plan:
        if not isinstance(step, dict):
            continue
        if str(step.get("status") or "pending") != "pending":
            return True
        if step.get("startedAt") or step.get("completedAt") or step.get("durationSec") is not None:
            return True
        if step.get("progressEvents") or step.get("toolCalls"):
            return True
    return False


def _workbench_validate_plan_graph(
    plan: Any,
    *,
    require_dependency_order: bool = True,
) -> tuple[bool, str, str]:
    if not isinstance(plan, list):
        return False, "计划格式无效。", "invalid_plan"
    step_ids: list[str] = []
    titles: dict[str, str] = {}
    for index, step in enumerate(plan):
        if not isinstance(step, dict):
            return False, f"第 {index + 1} 个步骤格式无效。", "invalid_step"
        step_id = str(step.get("id") or "").strip()
        title = str(step.get("title") or "").strip()
        if not step_id:
            return False, f"第 {index + 1} 个步骤缺少 id。", "missing_step_id"
        if step_id in titles:
            return False, "计划中存在重复的步骤 id。", "duplicate_step_id"
        if not title:
            return False, f"第 {index + 1} 个步骤标题不能为空。", "empty_step_title"
        step_ids.append(step_id)
        titles[step_id] = title

    known = set(step_ids)
    positions = {step_id: index for index, step_id in enumerate(step_ids)}
    indegree = {step_id: 0 for step_id in step_ids}
    followers: dict[str, list[str]] = {step_id: [] for step_id in step_ids}
    for step in plan:
        step_id = str(step.get("id") or "").strip()
        for dependency_id in _workbench_dependency_ids(step.get("dependsOn")):
            if dependency_id == step_id:
                return False, f"步骤「{titles[step_id]}」不能依赖自身。", "self_dependency"
            if dependency_id not in known:
                return False, f"步骤「{titles[step_id]}」引用了不存在的前置步骤。", "missing_dependency"
            if require_dependency_order and positions[dependency_id] >= positions[step_id]:
                return (
                    False,
                    f"步骤「{titles[step_id]}」必须排在前置步骤「{titles[dependency_id]}」之后。",
                    "dependency_order",
                )
            indegree[step_id] += 1
            followers[dependency_id].append(step_id)

    queue = [step_id for step_id in step_ids if indegree[step_id] == 0]
    visited = 0
    while queue:
        current = queue.pop(0)
        visited += 1
        for follower in followers[current]:
            indegree[follower] -= 1
            if indegree[follower] == 0:
                queue.append(follower)
    if visited != len(step_ids):
        return False, "步骤依赖形成了循环，请移除循环依赖。", "dependency_cycle"
    return True, "", ""


def _workbench_normalize_plan(
    plan: Any,
    *,
    task_id: str = "",
) -> list[dict[str, Any]]:
    if not isinstance(plan, list):
        return []
    normalized: list[dict[str, Any]] = []
    for index, raw_step in enumerate(plan):
        if not isinstance(raw_step, dict):
            continue
        step = dict(raw_step)
        step["id"] = str(step.get("id") or "").strip() or _short_id("step")
        step["taskId"] = str(step.get("taskId") or task_id or "").strip()
        step["title"] = str(step.get("title") or "").strip()[:160]
        step["description"] = str(step.get("description") or "").strip()[:4000]
        step["order"] = index + 1
        step["dependsOn"] = _workbench_dependency_ids(step.get("dependsOn"))
        step.pop("_dependsOnProvided", None)
        normalized.append(step)
    return normalized[:12]


def _workbench_keep_ordered_dependencies(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only dependency edges that still point to an earlier retained step."""
    seen: set[str] = set()
    for step in plan:
        step_id = str(step.get("id") or "").strip()
        step["dependsOn"] = [
            dependency_id
            for dependency_id in _workbench_dependency_ids(step.get("dependsOn"))
            if dependency_id in seen
        ]
        if step_id:
            seen.add(step_id)
    return plan


def _workbench_step_dependencies_satisfied(
    plan: Any,
    step_id: str,
) -> tuple[bool, list[str]]:
    if not isinstance(plan, list):
        return False, []
    by_id = {
        str(step.get("id") or ""): step
        for step in plan
        if isinstance(step, dict) and str(step.get("id") or "")
    }
    step = by_id.get(str(step_id or ""))
    if not step:
        return False, []
    unmet: list[str] = []
    for dependency_id in _workbench_dependency_ids(step.get("dependsOn")):
        dependency = by_id.get(dependency_id)
        if not dependency or str(dependency.get("status") or "") not in ("completed", "done"):
            unmet.append(dependency_id)
    return not unmet, unmet


def _workbench_plan_definition_signature(plan: Any) -> str:
    rows: list[dict[str, Any]] = []
    for step in plan if isinstance(plan, list) else []:
        if not isinstance(step, dict):
            continue
        context_files = step.get("contextFiles") if isinstance(step.get("contextFiles"), list) else []
        rows.append({
            "id": str(step.get("id") or ""),
            "title": str(step.get("title") or ""),
            "description": str(step.get("description") or ""),
            "dependsOn": _workbench_dependency_ids(step.get("dependsOn")),
            "promptOverride": str(step.get("promptOverride") or ""),
            "contextFiles": context_files,
        })
    return json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _workbench_coerce_plan_steps(raw: Any, session: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize an LLM plan reply (``{"steps": [...]}`` or a bare list) into
    execution-plan steps. All steps start ``pending``."""
    items: list[Any] = []
    if isinstance(raw, dict) and isinstance(raw.get("steps"), list):
        items = raw["steps"]
    elif isinstance(raw, list):
        items = raw
    task_id = session.get("id", "")
    steps: list[dict[str, Any]] = []
    dependency_indexes: list[list[int]] = []
    for item in items:
        if isinstance(item, dict):
            title = str(item.get("title") or item.get("name") or "").strip()
            description = str(item.get("description") or item.get("detail") or "").strip()
            source_step_id = str(item.get("sourceStepId") or item.get("source_step_id") or "").strip()
            raw_dependencies = item.get("dependsOnStepIndexes")
            if not isinstance(raw_dependencies, list):
                raw_dependencies = item.get("depends_on_step_indexes")
            dependencies_provided = isinstance(raw_dependencies, list)
            dependency_indices: list[int] = []
            if dependencies_provided:
                for value in raw_dependencies:
                    try:
                        dependency_index = int(value)
                    except (TypeError, ValueError):
                        continue
                    if dependency_index > 0 and dependency_index not in dependency_indices:
                        dependency_indices.append(dependency_index)
        else:
            title = str(item or "").strip()
            description = ""
            source_step_id = ""
            dependencies_provided = False
            dependency_indices = []
        if not title:
            continue
        step = _workbench_new_plan_step(title, description, len(steps) + 1, task_id)
        if source_step_id:
            step["sourceStepId"] = source_step_id
        step["_dependsOnProvided"] = dependencies_provided
        steps.append(step)
        dependency_indexes.append(dependency_indices)
        if len(steps) >= 12:
            break
    for index, step in enumerate(steps):
        step["dependsOn"] = [
            steps[dependency_index - 1]["id"]
            for dependency_index in dependency_indexes[index]
            if 0 < dependency_index <= len(steps) and dependency_index - 1 < index
        ]
    return steps


def _workbench_plan_title_key(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def _workbench_plan_reset_requested(feedback: str) -> bool:
    return bool(re.search(
        r"(重新生成|重新规划|重排|重做|从头|替换|清空|不要原计划|不保留原计划|"
        r"完全不一样|完全不同|全新计划|换一套|另一套方案|另一个方案)",
        str(feedback or ""),
    ))


def _workbench_existing_plan_block(session: dict[str, Any]) -> str:
    plan = session.get("plan") if isinstance(session.get("plan"), list) else []
    titles_by_id = {
        str(step.get("id") or ""): str(step.get("title") or "").strip()
        for step in plan
        if isinstance(step, dict) and str(step.get("id") or "")
    }
    rows: list[str] = []
    for index, step in enumerate(plan[:12], 1):
        if not isinstance(step, dict):
            continue
        title = str(step.get("title") or "").strip()
        if not title:
            continue
        status = str(step.get("status") or "pending").strip()
        description = str(step.get("description") or "").strip()
        suffix = f" — {description}" if description else ""
        step_id = str(step.get("id") or "").strip()
        dependency_titles = [
            titles_by_id.get(dependency_id, dependency_id)
            for dependency_id in _workbench_dependency_ids(step.get("dependsOn"))
        ]
        dependency_suffix = (
            "；前置步骤：" + "、".join(dependency_titles)
            if dependency_titles else ""
        )
        rows.append(f"{index}. id={step_id} [{status}] {title}{suffix}{dependency_suffix}")
    if not rows:
        return ""
    return "\n当前已有执行计划（除非用户明确要求删除/重排，请保留并在此基础上调整）：\n" + "\n".join(rows)


def _workbench_session_summary_text(session: dict[str, Any]) -> str:
    """Extract the task's one-line summary (简介), tolerating the dict form the
    store sometimes holds (mirrors the frontend's sessionSummaryText)."""
    raw = session.get("summary")
    if isinstance(raw, dict):
        return str(
            raw.get("text") or raw.get("body") or raw.get("content") or raw.get("summary") or ""
        ).strip()
    return str(raw or "").strip()


def _workbench_follow_up_seed(
    session: dict[str, Any],
    *,
    requested_title: str = "",
    requested_goal: str = "",
) -> dict[str, Any]:
    """Build a deterministic follow-up task from the source task's live state."""
    source_title = str(session.get("title") or "任务").strip() or "任务"
    explicit_goal = str(requested_goal or "").strip()
    title = str(requested_title or "").strip()
    if not title:
        title = f"{source_title} · 后续"

    status_labels = {
        "idle": "未开始",
        "answered": "已回答",
        "acted": "已执行",
        "planning": "规划中",
        "waiting_for_approval": "等待确认",
        "waiting_for_user": "等待用户",
        "running": "执行中",
        "review": "待验收",
        "done": "已完成",
        "completed": "已完成",
        "failed": "失败",
        "blocked": "阻塞",
        "paused": "已暂停",
        "cancelled": "已取消",
    }
    source_status = str(session.get("status") or "idle").strip()
    source_goal = str(session.get("goal") or "").strip()
    source_summary = _workbench_session_summary_text(session)
    source_result = str(session.get("agentReply") or "").strip()

    unresolved_steps: list[str] = []
    for step in session.get("plan") or []:
        if not isinstance(step, dict):
            continue
        status = str(step.get("status") or "pending").strip()
        if status in ("completed", "done", "skipped"):
            continue
        step_title = str(step.get("title") or "").strip()
        if step_title:
            unresolved_steps.append(step_title)
        if len(unresolved_steps) >= 6:
            break

    unresolved_acceptance: list[str] = []
    for item in session.get("acceptanceCriteria") or []:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "pending").strip()
        if status in ("passed", "done", "completed"):
            continue
        text = str(item.get("text") or "").strip()
        if text:
            unresolved_acceptance.append(text)
        if len(unresolved_acceptance) >= 6:
            break

    reflection = session.get("reflection")
    packet = reflection.get("packet") if isinstance(reflection, dict) else None
    next_step = str(packet.get("next_step") or "").strip() if isinstance(packet, dict) else ""

    lines = [f"这是任务「{source_title}」的后续任务。"]
    if explicit_goal:
        lines.append(f"本次后续要求：{explicit_goal}")
    if source_goal:
        lines.append(f"来源任务目标：{source_goal}")
    lines.append(f"来源任务当前状态：{status_labels.get(source_status, source_status or '未知')}")
    if source_summary:
        lines.append(f"来源任务摘要：{source_summary}")
    elif source_result:
        lines.append(f"来源任务当前结果：{source_result[:1200]}")
    if unresolved_steps:
        lines.append("尚未解决的步骤：" + "；".join(unresolved_steps))
    if unresolved_acceptance:
        lines.append("尚未满足的验收项：" + "；".join(unresolved_acceptance))
    if next_step:
        lines.append(f"反思建议的下一步：{next_step}")

    return {
        "title": title[:80],
        "goal": "\n".join(lines),
        "constraints": [
            str(value).strip()
            for value in (session.get("constraints") or [])
            if str(value).strip()
        ],
        "priority": (
            str(session.get("priority") or "medium").strip()
            if str(session.get("priority") or "").strip() in ("high", "medium", "low")
            else "medium"
        ),
        "unresolvedAcceptance": unresolved_acceptance,
        "context": {
            "sourceTitle": source_title,
            "sourceStatus": source_status,
            "sourceSummary": source_summary,
            "unresolvedSteps": unresolved_steps,
            "unresolvedAcceptance": unresolved_acceptance,
            "reflectionNextStep": next_step,
        },
    }


def _workbench_render_task_brief_block(session: dict[str, Any]) -> str:
    """Render the task's identity (title / goal / summary / acceptance) + current
    plan as a prompt block for the agent run.

    These live ONLY in the Workbench store, not in the agent's conversation
    history — without this the agent literally cannot see the plan or goal the UI
    shows, and ends up asking "我没看到执行计划". Injected via ``ephemeral_system``
    (prompt tail), so it stays cache-safe.
    """
    title = str(session.get("title") or "").strip()
    goal = str(session.get("goal") or "").strip()
    summary = _workbench_session_summary_text(session)
    lines: list[str] = ["## 当前任务"]
    if title:
        lines.append(f"- 标题：{title}")
    if goal:
        lines.append(f"- 目标：{goal}")
    if summary:
        lines.append(f"- 简介：{summary}")
    acceptance = session.get("acceptanceCriteria")
    if isinstance(acceptance, list):
        accept_texts = [
            str((a.get("text") if isinstance(a, dict) else a) or "").strip() for a in acceptance
        ]
        accept_texts = [t for t in accept_texts if t][:8]
        if accept_texts:
            lines.append("- 验收标准：" + "；".join(accept_texts))
    body = "\n".join(lines)
    plan_block = _workbench_existing_plan_block(session)
    if plan_block:
        body += "\n" + plan_block.lstrip("\n")
    if session.get("titleLocked"):
        body += (
            "\n（用户已手动设置任务标题，你不能修改标题；如标题/简介与实际工作不符，"
            "可用 set_task_goal 更新简介或目标。）"
        )
    else:
        body += (
            "\n（标题与简介都会显示在任务卡上；若与你实际要做的事不符，可用 set_task_goal 更新。）"
        )
    return body


def _workbench_reconcile_revised_plan(
    existing: list[dict[str, Any]],
    generated: list[dict[str, Any]],
    feedback: str,
    operation: str = "auto",
) -> list[dict[str, Any]]:
    mode = str(operation or "auto").strip().lower()
    if mode not in ("revise", "replace"):
        mode = "replace" if _workbench_plan_reset_requested(feedback) else "revise"
    if not existing or not feedback or mode == "replace":
        return _workbench_normalize_plan(generated)
    if not generated:
        return _workbench_normalize_plan(existing)

    existing_steps = [dict(step) for step in existing if isinstance(step, dict)]
    by_id = {str(step.get("id") or ""): step for step in existing_steps if str(step.get("id") or "")}
    by_title = {
        _workbench_plan_title_key(step.get("title")): step
        for step in existing_steps
        if _workbench_plan_title_key(step.get("title"))
    }
    merged_generated: list[dict[str, Any]] = []
    matched_ids: set[str] = set()
    generated_to_final_id: dict[str, str] = {}
    for index, step in enumerate(generated):
        if not isinstance(step, dict):
            continue
        generated_id = str(step.get("id") or "").strip()
        source_id = str(step.get("sourceStepId") or "").strip()
        original = by_id.get(source_id)
        if original is None:
            original = by_title.get(_workbench_plan_title_key(step.get("title")))
        if original is not None:
            next_step = dict(original)
            next_step["title"] = str(step.get("title") or original.get("title") or "").strip()
            next_step["description"] = str(step.get("description") or "").strip()
            next_step["order"] = index + 1
            if step.get("_dependsOnProvided"):
                next_step["dependsOn"] = _workbench_dependency_ids(step.get("dependsOn"))
            next_step.pop("sourceStepId", None)
            next_step.pop("_dependsOnProvided", None)
            matched_ids.add(str(original.get("id") or ""))
        else:
            next_step = dict(step)
            next_step["order"] = index + 1
            next_step.pop("sourceStepId", None)
            next_step.pop("_dependsOnProvided", None)
        if generated_id:
            generated_to_final_id[generated_id] = str(next_step.get("id") or "")
        merged_generated.append(next_step)

    for step in merged_generated:
        step["dependsOn"] = [
            generated_to_final_id.get(dependency_id, dependency_id)
            for dependency_id in _workbench_dependency_ids(step.get("dependsOn"))
            if generated_to_final_id.get(dependency_id, dependency_id)
        ]

    # A revise response is expected to contain the complete revised plan. Some
    # models still return only the changed/new steps. In that partial-response
    # case, preserve the old plan and append the proposed additions rather than
    # silently deleting work. The model can choose revisionMode=replace when the
    # user's intent is to discard the old plan.
    if not matched_ids:
        merged = existing_steps
        seen = {_workbench_plan_title_key(step.get("title")) for step in merged}
        for step in merged_generated:
            key = _workbench_plan_title_key(step.get("title"))
            if key and key not in seen:
                merged.append(step)
                seen.add(key)
        for index, step in enumerate(merged):
            step["order"] = index + 1
        return _workbench_keep_ordered_dependencies(
            _workbench_normalize_plan(merged[:12])
        )

    for original in existing_steps:
        original_id = str(original.get("id") or "")
        if original_id and original_id not in matched_ids:
            merged_generated.append(original)
    for index, step in enumerate(merged_generated):
        step["order"] = index + 1
    final_plan = _workbench_normalize_plan(merged_generated[:12])
    return _workbench_keep_ordered_dependencies(final_plan)


async def _workbench_generate_plan_steps(
    session: dict[str, Any],
    project: dict[str, Any],
    feedback: str = "",
    auto_start: bool = False,
    requested_operation: str = "auto",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool, str]:
    """Generate a REAL execution plan for a task session from its goal +
    constraints, exploring the project workspace. Returns
    ``(steps, acceptance_criteria, from_llm, operation)``; ``from_llm`` is False
    when generation failed and deterministic fallbacks were used.

    ``auto_start`` (「直接开始」): no goal is given up front — the agent explores
    the project and the LLM proposes a concise goal + title (back-filled onto the
    session) alongside the steps, so the task gets a real, project-relevant
    identity instead of filler."""
    goal = str(session.get("goal") or session.get("title") or "").strip()
    existing_plan = session.get("plan") if isinstance(session.get("plan"), list) else []
    feedback = str(feedback or "").strip()
    requested_operation = str(requested_operation or "auto").strip().lower()
    if requested_operation not in ("auto", "create", "revise", "replace"):
        requested_operation = "auto"
    # 直接开始 with no goal yet → plan toward "work out what the project needs".
    if auto_start and _workbench_is_blank_goal(goal):
        goal = "通读本项目的工作区文件与项目说明，判断当前最应该推进的工作并据此规划"
    fallback = existing_plan if feedback and existing_plan else _workbench_plan_from_input(goal, {"id": session.get("id", "")})
    existing_acceptance = session.get("acceptanceCriteria") if isinstance(session.get("acceptanceCriteria"), list) else []
    # A failed incremental revision preserves both sides of the existing task
    # definition. Replacing verified or hand-edited criteria while retaining the
    # old plan would leave the session internally inconsistent.
    fallback_acceptance = (
        [dict(item) for item in existing_acceptance if isinstance(item, dict)]
        if feedback and existing_plan and existing_acceptance
        else _workbench_fallback_acceptance(session, fallback)
    )
    if not goal:
        return fallback, fallback_acceptance, False, "create"

    constraints = [str(c).strip() for c in (session.get("constraints") or []) if str(c).strip()]
    workspace_path = str(project.get("workspacePath") or "").strip()
    workspace_root = Path(workspace_path).expanduser().resolve() if workspace_path else None
    planning_thread = _workbench_planning_thread(session, workspace_root)
    previous_workspace_revision = str(planning_thread.get("workspaceRevision") or "")
    previous_workspace_snapshot = (
        planning_thread.get("workspaceSnapshot")
        if isinstance(planning_thread.get("workspaceSnapshot"), dict)
        else {}
    )
    tool_bundle_version, workspace_revision, workspace_snapshot = _workbench_plan_tool_bundle(
        session,
        workspace_root,
        feedback=feedback,
        requested_operation=requested_operation,
        auto_start=auto_start,
    )

    constraints_block = ("\n约束：\n" + "\n".join(f"- {c}" for c in constraints)) if constraints else ""
    feedback_block = ("\n用户对计划的修改反馈（请据此调整）：" + feedback) if feedback else ""
    workspace_delta_block = ""
    if previous_workspace_revision and previous_workspace_revision != workspace_revision:
        changed_files = sorted(
            path
            for path in set(previous_workspace_snapshot) | set(workspace_snapshot)
            if previous_workspace_snapshot.get(path) != workspace_snapshot.get(path)
        )
        workspace_delta_block = (
            "\n工作区增量："
            + _workbench_stable_json({
                "type": "workspace_delta",
                "baseRevision": previous_workspace_revision,
                "revision": workspace_revision,
                "changedFiles": changed_files[:200],
                "invalidatedObservations": ["directory/glob observations"],
            })
        )
    # A full replacement must be generated independently. Showing the complete
    # old plan strongly anchors the model and often makes it repeat the same
    # steps with fresh IDs.
    existing_plan_block = (
        _workbench_existing_plan_block(session)
        if feedback and requested_operation != "replace"
        else ""
    )
    reflection_text = _workbench_render_reflection_block(session)
    reflection_block = (
        "\n\n## 深度反思结论（必须据此调整计划）\n"
        "下面是对既往尝试的复盘。请避开其中的 excluded_paths（已被证明是死路的做法），"
        "优先采用 promising_directions（更有希望的方向），并参考 next_step：\n"
        + reflection_text
    ) if reflection_text else ""
    # 规划契约、JSON 结构、步骤/验收数量、revise/replace 输出规则、语言要求都由
    # _WORKBENCH_PLANNER_SYSTEM_PROMPT（线程稳定前缀）承载。这里只追加“增量”：本轮
    # 任务数据 + 随 Bundle 切换的探索指令 + 系统提示未覆盖的情境化要求，避免与系统
    # 提示重复，也避免在 no-tools 轮里诱导模型调用并不存在的工具。
    if tool_bundle_version == _WORKBENCH_PLANNER_EXPLORE_VERSION:
        explore_directive = (
            "如确有必要，可用 list_directory、read_file、glob 探索工作区；"
            "已观察且未变化的内容不要重复读取，够用即止。"
        )
    else:
        explore_directive = (
            "本次不提供工作区探索工具，请基于规划历史、既往观察结果和下面的信息直接给出计划，不要尝试调用工具。"
        )
    past_reports_block = _workbench_render_past_task_reports(project)
    reports_section = f"\n\n{past_reports_block}" if past_reports_block else ""
    if auto_start:
        if tool_bundle_version == _WORKBENCH_PLANNER_EXPLORE_VERSION:
            lead_in = (
                "这是「直接开始」的任务——用户没有明确给出目标。"
                "请先用 list_directory、read_file、glob 通读这个项目（工作区文件 + 项目说明），"
                "判断当前最应该推进的一件工作，再据此给出 goal、title 和执行步骤；"
                "已观察且未变化的内容不要重复读取。"
            )
        else:
            lead_in = (
                "这是「直接开始」的任务——用户没有明确给出目标，且本次没有可用的工作区探索工具。"
                "请基于规划方向和已有信息，判断当前最应该推进的一件工作，再据此给出 goal、title 和执行步骤。"
            )
        prompt = (
            f"{lead_in}\n\n"
            f"规划方向：{goal}{constraints_block}{workspace_delta_block}{reflection_block}{reports_section}\n\n"
            "goal 要具体、贴合本项目实际、不要泛泛而谈，并尽量引用真实文件/目录/模块；"
            "验收标准要可独立核验，避免“目标清晰”这类过程性描述。"
            "按系统提示约定的 JSON 结构，只返回一个 JSON 对象，不要 Markdown 代码块标记。"
        )
    else:
        prompt = (
            "请把下面这个任务拆解成清晰、有顺序、可逐步执行的步骤。\n"
            f"{explore_directive}\n\n"
            f"任务目标：{goal}{constraints_block}{existing_plan_block}{feedback_block}{workspace_delta_block}{reflection_block}{reports_section}\n\n"
            "任务涉及当前项目时，尽量引用真实文件、目录或模块；"
            "与当前项目无关时，围绕任务本身规划，不要引入无关的文件或代码操作。"
            "revisionMode 自行判断：仅补充、删改、调序或改变局部做法时用 revise；"
            "要求完全不同、全新、换一套、从头重做，或新目标与原计划明显不符时用 replace。"
            "按系统提示约定的 JSON 结构，只返回一个 JSON 对象，不要 Markdown 代码块标记。"
        )
        if requested_operation == "replace":
            prompt += (
                "\n这是用户主动点击的「重新生成」：必须从最终任务目标重新独立拆解，"
                "至少一半步骤应采用不同的拆解方式或执行路径，不能只是改写措辞。"
            )

    parsed = await _workbench_run_explore_agent(
        workspace_root, prompt, max_tokens=12000, timeout=120,
        session_id=str(session.get("id") or ""),
        planning_thread=planning_thread,
        tool_bundle_version=tool_bundle_version,
        workspace_revision=workspace_revision,
    )
    if not isinstance(parsed, dict):
        fallback_operation = "replace" if requested_operation == "replace" else "revise" if feedback else "create"
        return fallback, fallback_acceptance, False, fallback_operation
    steps = _workbench_coerce_plan_steps(parsed, session)
    if not steps:
        fallback_operation = "replace" if requested_operation == "replace" else "revise" if feedback else "create"
        return fallback, fallback_acceptance, False, fallback_operation
    operation = "create"
    if feedback:
        agent_operation = str(parsed.get("revisionMode") or "").strip().lower()
        if requested_operation in ("revise", "replace"):
            operation = requested_operation
        elif agent_operation in ("revise", "replace"):
            operation = agent_operation
        else:
            operation = "replace" if _workbench_plan_reset_requested(feedback) else "revise"
        steps = _workbench_reconcile_revised_plan(existing_plan, steps, feedback, operation)
        revised_goal = str(parsed.get("goal") or "").strip()
        if revised_goal:
            session["goal"] = revised_goal
    else:
        steps = _workbench_normalize_plan(steps, task_id=str(session.get("id") or ""))
    valid_plan, _, _ = _workbench_validate_plan_graph(steps)
    if not valid_plan:
        for step in steps:
            step["dependsOn"] = []
    # 直接开始: back-fill the LLM-proposed goal/title onto the session (only while
    # still blank) so the task gets a real identity rather than filler.
    if auto_start:
        derived_goal = str(parsed.get("goal") or "").strip()
        derived_title = str(parsed.get("title") or "").strip()
        if derived_goal and _workbench_is_blank_goal(session.get("goal")):
            session["goal"] = derived_goal
        if _workbench_is_default_title(session.get("title")):
            session["title"] = (derived_title or _workbench_derive_title(session.get("goal") or ""))[:80]

    planning_thread["goal"] = str(session.get("goal") or goal)
    planning_thread["constraints"] = constraints
    planning_thread["workspaceSnapshot"] = workspace_snapshot
    planning_thread["currentPlan"] = [
        {
            "id": str(step.get("id") or ""),
            "title": str(step.get("title") or ""),
            "description": str(step.get("description") or ""),
            "dependsOn": _workbench_dependency_ids(step.get("dependsOn")),
        }
        for step in steps
        if isinstance(step, dict)
    ]
    if feedback:
        decisions = planning_thread.setdefault("userDecisions", [])
        if isinstance(decisions, list):
            decisions.append(feedback[:2000])
            if len(decisions) > 30:
                del decisions[:-30]
    _workbench_maybe_compact_planning_thread(planning_thread)

    acceptance_session = dict(session)
    acceptance_session["plan"] = steps
    acceptance_fallback = _workbench_fallback_acceptance(acceptance_session, steps)
    raw_acceptance = parsed.get("acceptanceCriteria")
    has_generated_acceptance = isinstance(raw_acceptance, list) and any(
        str(item.get("text") if isinstance(item, dict) else item).strip()
        for item in raw_acceptance
    )
    if has_generated_acceptance:
        acceptance = _workbench_coerce_acceptance_criteria(parsed, acceptance_fallback)
    else:
        # Keep revision latency bounded. The planner already had the complete task
        # context; if it omitted criteria, use deterministic criteria for the exact
        # final plan instead of launching a second exploratory agent.
        acceptance = acceptance_fallback
    return steps, acceptance, True, operation


def _workbench_acceptance_from_session(session: dict[str, Any]) -> list[dict[str, Any]]:
    existing = session.get("acceptanceCriteria")
    if isinstance(existing, list) and existing:
        return existing
    constraints = session.get("constraints") if isinstance(session.get("constraints"), list) else []
    items = [str(item) for item in constraints if str(item).strip()]
    if not items:
        items = ["任务目标已明确", "计划已生成", "执行进度可追踪", "最终总结已生成"]
    return [
        {"id": _short_id("accept"), "text": item, "status": "pending"}
        for item in items[:8]
    ]


def _workbench_fallback_acceptance(session: dict[str, Any], steps: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Build deterministic criteria when the acceptance agent is unavailable."""
    constraints = [
        str(item).strip()
        for item in (session.get("constraints") or [])
        if str(item).strip()
    ]
    goal = str(session.get("goal") or session.get("title") or "").strip()
    items = constraints[:4]
    if goal:
        items.append(f"任务目标已完成：{goal[:240]}")
    if steps:
        items.append("计划中的执行步骤均已完成或有明确处理结论")
    items.extend(["相关变更或产物可追踪", "最终结果已验证并形成总结"])

    unique: list[str] = []
    for item in items:
        if item and item not in unique:
            unique.append(item)
        if len(unique) >= 8:
            break
    return [
        {"id": _short_id("accept"), "text": item, "status": "pending"}
        for item in unique
    ]


def _workbench_coerce_acceptance_criteria(
    raw: Any,
    fallback: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize agent-produced acceptance criteria into session records."""
    source = raw.get("acceptanceCriteria") if isinstance(raw, dict) else raw
    if not isinstance(source, list):
        return fallback
    criteria: list[dict[str, Any]] = []
    for item in source:
        text = str(item.get("text") if isinstance(item, dict) else item).strip()
        if not text:
            continue
        criteria.append({
            "id": _short_id("accept"),
            "text": text[:300],
            "status": "pending",
        })
        if len(criteria) >= 8:
            break
    return criteria or fallback


async def _workbench_generate_acceptance_criteria(
    session: dict[str, Any],
    project: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    """Ask an agent to derive verifiable criteria from the current task plan."""
    plan = session.get("plan") if isinstance(session.get("plan"), list) else []
    fallback = _workbench_fallback_acceptance(session, plan)
    goal = str(session.get("goal") or session.get("title") or "").strip()
    constraints = [
        str(item).strip()
        for item in (session.get("constraints") or [])
        if str(item).strip()
    ]
    plan_lines = "\n".join(
        f"- {step.get('title') or ''}：{step.get('description') or ''}"
        for step in plan
        if isinstance(step, dict)
    )
    workspace_path = str(project.get("workspacePath") or "").strip()
    workspace_root = Path(workspace_path).expanduser().resolve() if workspace_path else None
    prompt = (
        "你是任务验收设计 Agent。请根据任务目标、约束、当前执行计划和工作区实际内容，"
        "生成清晰、具体、可核验的验收标准。你可以使用 list_directory、read_file、glob "
        "工具探索项目，标准应尽量对应真实文件、功能、测试或产物，避免“目标清晰”这类过程性描述。\n\n"
        f"任务目标：{goal or '暂无明确目标'}\n"
        f"约束：{json.dumps(constraints, ensure_ascii=False)}\n"
        f"当前计划：\n{plan_lines or '暂无计划'}\n\n"
        "只返回一个 JSON 对象，不要 Markdown。结构：\n"
        "{\n"
        '  "acceptanceCriteria": ["可独立核验的验收标准"]\n'
        "}\n\n"
        "要求：生成 3-8 条；每条只表达一个可验证结果；覆盖核心功能、约束和必要验证；"
        "全部使用简体中文。"
    )
    parsed = await _workbench_run_explore_agent(
        workspace_root,
        prompt,
        max_tokens=6000,
        timeout=120,
        session_id=str(session.get("id") or ""),
    )
    if not isinstance(parsed, dict):
        return fallback, False
    criteria = _workbench_coerce_acceptance_criteria(parsed, fallback)
    raw_criteria = parsed.get("acceptanceCriteria")
    generated = isinstance(raw_criteria, list) and any(
        str(item.get("text") if isinstance(item, dict) else item).strip()
        for item in raw_criteria
    )
    return criteria, generated


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


def _workbench_workspace_root(project: dict[str, Any] | None) -> Path | None:
    project_id = str((project or {}).get("id") or "").strip()
    workspace_source = str(
        (project or {}).get("workspacePathSource") or ""
    ).strip().lower()
    if workspace_source == "generated" and project_id:
        return (WORKSPACE_DIR / "projects" / project_id).resolve()
    if (
        _workbench_project_data_key(project or {}) == _WORKBENCH_LEGACY_DATA_KEY
        and not workspace_source
    ):
        raw_legacy = str((project or {}).get("workspacePath") or "").replace(
            "\\", "/"
        ).rstrip("/")
        if raw_legacy.lower().endswith("/workspace"):
            return WORKSPACE_DIR.resolve()
    workspace_path = str((project or {}).get("workspacePath") or "").strip()
    if not workspace_path:
        return None
    try:
        return Path(workspace_path).expanduser().resolve()
    except OSError:
        return None


def _workbench_display_path(path_value: Any, workspace_root: Path | None = None) -> str:
    raw = str(path_value or "").strip()
    if not raw:
        return ""
    try:
        path = Path(raw).expanduser()
        if workspace_root:
            root = workspace_root.resolve()
            resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
            try:
                return resolved.relative_to(root).as_posix()
            except ValueError:
                return ""
        if path.is_absolute():
            return path.resolve().as_posix()
        return path.as_posix().lstrip("./")
    except Exception:
        return ""


def _workbench_file_change(path_value: Any, status: str, workspace_root: Path | None = None, source: str = "") -> dict[str, Any] | None:
    path = _workbench_display_path(path_value, workspace_root)
    if not path:
        return None
    return {
        "id": _short_id("file"),
        "path": path,
        "status": status,
        "changeType": status,
        "source": source,
    }


def _workbench_file_changes_from_tool_event(event: dict[str, Any], workspace_root: Path | None = None) -> list[dict[str, Any]]:
    tool = str(event.get("tool") or "").strip()
    args = event.get("args") if isinstance(event.get("args"), dict) else {}
    result = str(event.get("result") or "")
    changes: list[dict[str, Any]] = []

    if tool == "Write" and isinstance(args, dict):
        change = _workbench_file_change(args.get("path"), "created/updated", workspace_root, tool)
        if change:
            changes.append(change)
    elif tool == "Edit" and isinstance(args, dict):
        change = _workbench_file_change(args.get("path"), "modified", workspace_root, tool)
        if change:
            changes.append(change)
    elif tool == "send_file" and isinstance(args, dict):
        # send_file is an explicit declaration that an existing workspace file
        # is a user-facing deliverable. It may not mutate the file, but it is the
        # strongest available artifact signal.
        change = _workbench_file_change(args.get("path"), "produced", workspace_root, tool)
        if change:
            changes.append(change)

    # Tool output is a useful fallback for older/remote tool names and for
    # cases where the arguments were redacted or shaped differently.
    for match in re.finditer(r"\b(Wrote|Edited)\s+([^\n]+?)(?:\. Replacements:.*)?$", result, flags=re.MULTILINE):
        verb = match.group(1)
        path_text = match.group(2).strip()
        status = "modified" if verb == "Edited" else "created/updated"
        change = _workbench_file_change(path_text, status, workspace_root, tool)
        if change:
            changes.append(change)
    return _workbench_merge_file_changes(changes)


def _workbench_merge_file_changes(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    rank = {"produced": 5, "created": 4, "modified": 3, "deleted": 3, "renamed": 3, "created/updated": 2}
    for item in changes:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or item.get("name") or "").strip()
        if not path:
            continue
        key = path
        if key not in merged:
            merged[key] = dict(item)
            order.append(key)
            continue
        old = merged[key]
        new_status = str(item.get("status") or item.get("changeType") or "")
        old_status = str(old.get("status") or old.get("changeType") or "")
        old_source = str(old.get("source") or "").strip().lower()
        new_source = str(item.get("source") or "").strip().lower()
        inferred_cannot_override_explicit = (
            new_source == "git"
            and old_source in {"write", "edit", "send_file"}
        )
        if not inferred_cannot_override_explicit and rank.get(new_status, 0) > rank.get(old_status, 0):
            old["status"] = new_status
            old["changeType"] = new_status
            if new_status == "produced" and item.get("source"):
                old["source"] = item.get("source")
        if item.get("source") and not old.get("source"):
            old["source"] = item.get("source")
        if item.get("diff") and not old.get("diff"):
            old["diff"] = item.get("diff")
            if item.get("diffSource"):
                old["diffSource"] = item.get("diffSource")
        if item.get("diffUnavailableReason") and not old.get("diff") and not old.get("diffUnavailableReason"):
            old["diffUnavailableReason"] = item.get("diffUnavailableReason")
    return [merged[key] for key in order]


_WORKBENCH_SNAPSHOT_IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".venv", "__pycache__", "node_modules",
}
_WORKBENCH_TEXT_SNAPSHOT_MAX_BYTES = 1_000_000
_WORKBENCH_TEXT_SNAPSHOT_MAX_TOTAL_BYTES = 8_000_000
_WORKBENCH_TEXT_SNAPSHOT_MAX_FILES = 500


def _workbench_workspace_file_snapshot(workspace_root: Path | None) -> dict[str, tuple[int, int]]:
    """Capture cheap file identity for shell/subagent output detection."""
    if not workspace_root:
        return {}
    try:
        root = workspace_root.resolve()
    except OSError:
        return {}
    if not root.exists() or not root.is_dir():
        return {}

    snapshot: dict[str, tuple[int, int]] = {}
    try:
        for current, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                name for name in dirnames
                if name not in _WORKBENCH_SNAPSHOT_IGNORED_DIRS and not name.startswith(".")
            ]
            current_path = Path(current)
            for filename in filenames:
                if filename.startswith("."):
                    continue
                target = current_path / filename
                try:
                    if not target.is_file() or target.is_symlink():
                        continue
                    stat = target.stat()
                    rel = target.relative_to(root).as_posix()
                except (OSError, ValueError):
                    continue
                snapshot[rel] = (int(stat.st_mtime_ns), int(stat.st_size))
    except OSError:
        return snapshot
    return snapshot


def _workbench_workspace_text_snapshot(workspace_root: Path | None) -> dict[str, str]:
    """Capture bounded UTF-8 file content so Workbench can diff without Git."""
    if not workspace_root:
        return {}
    try:
        root = workspace_root.resolve()
    except OSError:
        return {}
    if not root.exists() or not root.is_dir():
        return {}

    snapshot: dict[str, str] = {}
    total_bytes = 0
    try:
        for current, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                name for name in dirnames
                if name not in _WORKBENCH_SNAPSHOT_IGNORED_DIRS and not name.startswith(".")
            ]
            if len(snapshot) >= _WORKBENCH_TEXT_SNAPSHOT_MAX_FILES:
                break
            current_path = Path(current)
            for filename in filenames:
                if len(snapshot) >= _WORKBENCH_TEXT_SNAPSHOT_MAX_FILES:
                    break
                if filename.startswith("."):
                    continue
                target = current_path / filename
                try:
                    if not target.is_file() or target.is_symlink():
                        continue
                    stat = target.stat()
                    if stat.st_size > _WORKBENCH_TEXT_SNAPSHOT_MAX_BYTES:
                        continue
                    if total_bytes + stat.st_size > _WORKBENCH_TEXT_SNAPSHOT_MAX_TOTAL_BYTES:
                        return snapshot
                    rel = target.relative_to(root).as_posix()
                    data = target.read_bytes()
                except OSError:
                    continue
                if b"\x00" in data:
                    continue
                try:
                    snapshot[rel] = data.decode("utf-8")
                    total_bytes += len(data)
                except UnicodeDecodeError:
                    continue
    except OSError:
        return snapshot
    return snapshot


def _workbench_workspace_snapshot_delta(
    before: dict[str, tuple[int, int]],
    after: dict[str, tuple[int, int]],
    evidence: str = "",
    before_text: dict[str, str] | None = None,
    after_text: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return workspace changes and mark files explicitly named as outputs."""
    evidence_text = str(evidence or "")
    before_text = before_text or {}
    after_text = after_text or {}
    changes: list[dict[str, Any]] = []
    for path, signature in after.items():
        previous = before.get(path)
        if previous == signature:
            continue
        status = "created" if previous is None else "modified"
        name = path.rsplit("/", 1)[-1]
        explicitly_named = path in evidence_text or name in evidence_text
        change = _workbench_file_change(
            path,
            "produced" if explicitly_named else status,
            source="workspace_output" if explicitly_named else "workspace",
        )
        if change:
            if path in after_text and (previous is None or path in before_text):
                diff = _workbench_unified_diff(
                    before_text.get(path, ""),
                    after_text[path],
                    f"a/{path}" if previous is not None else "/dev/null",
                    f"b/{path}",
                )
                if diff.strip():
                    change["diff"] = diff
                    change["diffSource"] = "workspace_snapshot"
                else:
                    change["diffUnavailableReason"] = "no_text_difference"
            else:
                change["diffUnavailableReason"] = "text_snapshot_unavailable"
            changes.append(change)
    for path, previous in before.items():
        if path in after:
            continue
        change = _workbench_file_change(path, "deleted", source="workspace")
        if change:
            if path in before_text:
                diff = _workbench_unified_diff(
                    before_text[path],
                    "",
                    f"a/{path}",
                    "/dev/null",
                )
                if diff.strip():
                    change["diff"] = diff
                    change["diffSource"] = "workspace_snapshot"
                else:
                    change["diffUnavailableReason"] = "no_text_difference"
            else:
                change["diffUnavailableReason"] = "text_snapshot_unavailable"
            changes.append(change)
    return changes


def _workbench_collect_run_file_changes(
    tool_events: list[dict[str, Any]],
    git_before: dict[str, str],
    git_after: dict[str, str],
    workspace_before: dict[str, tuple[int, int]],
    workspace_after: dict[str, tuple[int, int]],
    workspace_root: Path | None,
    evidence: str = "",
    workspace_text_before: dict[str, str] | None = None,
    workspace_text_after: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    return _workbench_merge_file_changes([
        *[change for event in tool_events for change in (event.get("fileChanges") or [])],
        *_workbench_git_status_delta(git_before, git_after, workspace_root),
        *_workbench_workspace_snapshot_delta(
            workspace_before,
            workspace_after,
            evidence,
            before_text=workspace_text_before,
            after_text=workspace_text_after,
        ),
    ])


def _workbench_git_context(workspace_root: Path | None) -> tuple[Path, str] | None:
    if not workspace_root:
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(workspace_root), "rev-parse", "--show-toplevel", "--show-prefix"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    lines = proc.stdout.splitlines()
    if not lines:
        return None
    try:
        repo_root = Path(lines[0]).expanduser().resolve()
        prefix = (lines[1] if len(lines) > 1 else "").replace("\\", "/")
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        return repo_root, prefix
    except OSError:
        return None


def _workbench_git_status_snapshot(workspace_root: Path | None) -> dict[str, str]:
    context = _workbench_git_context(workspace_root)
    if not workspace_root or context is None:
        return {}
    _, prefix = context
    try:
        proc = subprocess.run(
            [
                "git", "-C", str(workspace_root), "status",
                "--porcelain=v1", "-z", "--no-renames", "--untracked-files=all",
                "--", ".",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return {}
    if proc.returncode != 0:
        return {}
    snapshot: dict[str, str] = {}
    for record in proc.stdout.split("\0"):
        if len(record) < 4:
            continue
        code = record[:2]
        repo_path = record[3:].replace("\\", "/")
        if prefix:
            if not repo_path.startswith(prefix):
                continue
            path = repo_path[len(prefix):]
        else:
            path = repo_path
        normalized = _workbench_display_path(path, workspace_root)
        if normalized:
            snapshot[normalized] = code
    return snapshot


def _workbench_git_status_change_type(code: str) -> str:
    if "D" in code:
        return "deleted"
    if "R" in code:
        return "renamed"
    if "A" in code or code == "??":
        return "created"
    return "modified"


def _workbench_git_status_delta(before: dict[str, str], after: dict[str, str], workspace_root: Path | None = None) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for path, code in after.items():
        if before.get(path) == code:
            continue
        change = _workbench_file_change(path, _workbench_git_status_change_type(code), workspace_root, "git")
        if change:
            changes.append(change)
    return changes


def _workbench_resolve_workspace_file(workspace_root: Path | None, path_value: Any) -> Path:
    if not workspace_root:
        raise ValueError("workspace directory is not configured")
    root = workspace_root.resolve()
    raw = str(path_value or "").strip()
    if not raw:
        raise ValueError("path is required")
    path = Path(raw).expanduser()
    target = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError("path is outside the workspace directory")
    return target


def _workbench_artifact_download_target(
    project: dict[str, Any],
    session: dict[str, Any],
    artifact_id: str,
) -> tuple[dict[str, Any], Path]:
    artifact = next(
        (
            item
            for item in (session.get("artifacts") or [])
            if isinstance(item, dict) and str(item.get("id") or "") == artifact_id
        ),
        None,
    )
    if artifact is None:
        raise LookupError("artifact not found")
    if artifact.get("type") != "file_change":
        raise ValueError("artifact is not a downloadable file")
    target = _workbench_resolve_workspace_file(
        _workbench_workspace_root(project),
        artifact.get("path") or artifact.get("name"),
    )
    if not target.exists() or not target.is_file():
        raise FileNotFoundError("artifact file not found")
    return artifact, target


def _workbench_unified_diff(left_text: str, right_text: str, left_label: str, right_label: str) -> str:
    return "".join(difflib.unified_diff(
        left_text.splitlines(keepends=True),
        right_text.splitlines(keepends=True),
        fromfile=left_label,
        tofile=right_label,
    ))


def _workbench_relabel_diff_paths(diff: str, old_path: str, new_path: str) -> str:
    if not diff or not old_path or not new_path or old_path == new_path:
        return diff
    old_left = f"--- a/{old_path}"
    old_right = f"+++ b/{old_path}"
    new_left = f"--- a/{new_path}"
    new_right = f"+++ b/{new_path}"
    old_created = f"+++ b/{old_path}"
    lines = diff.splitlines(keepends=True)
    for idx, line in enumerate(lines[:4]):
        suffix = "\n" if line.endswith("\n") else ""
        bare = line[:-1] if suffix else line
        if bare == old_left:
            lines[idx] = new_left + suffix
        elif bare == old_right or bare == old_created:
            lines[idx] = new_right + suffix
    return "".join(lines)


_WORKBENCH_DIFF_SNAPSHOT_MAX_BYTES = 1_000_000


def _workbench_current_file_snapshot_diff(target: Path, rel: str) -> str:
    """Return a displayable text snapshot when no historical/git diff exists."""
    try:
        if not target.is_file() or target.stat().st_size > _WORKBENCH_DIFF_SNAPSHOT_MAX_BYTES:
            return ""
        right_text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    if not right_text:
        return ""
    return _workbench_unified_diff("", right_text, "/dev/null", f"b/{rel}")


def _workbench_recorded_diff_for_path(session: dict[str, Any], path_value: Any, workspace_root: Path | None = None) -> dict[str, Any] | None:
    rel = _workbench_display_path(path_value, workspace_root) or str(path_value or "").strip()
    if not rel:
        return None

    candidates: list[dict[str, Any]] = []
    for run in reversed(session.get("runs") or []):
        if isinstance(run, dict):
            candidates.extend(
                item for item in reversed(run.get("fileChanges") or [])
                if isinstance(item, dict)
            )
    for step in reversed(session.get("plan") or []):
        if isinstance(step, dict):
            candidates.extend(
                item for item in reversed(step.get("relatedFiles") or [])
                if isinstance(item, dict)
            )
    candidates.extend(
        item for item in reversed(session.get("artifacts") or [])
        if isinstance(item, dict) and item.get("type") == "file_change"
    )

    for item in candidates:
        item_path = _workbench_display_path(item.get("path") or item.get("name"), workspace_root)
        if item_path != rel:
            continue
        diff = str(item.get("diff") or "")
        if diff.strip():
            return {
                "path": rel,
                "diff": diff,
                "has_changes": True,
                "source": str(item.get("diffSource") or "recorded"),
            }
        reason = str(item.get("diffUnavailableReason") or "").strip()
        if reason:
            return {
                "path": rel,
                "diff": "",
                "has_changes": False,
                "source": reason,
                "reason": reason,
            }
    return None


async def _workbench_git_diff_for_path(workspace_root: Path | None, path_value: Any) -> dict[str, Any]:
    target = _workbench_resolve_workspace_file(workspace_root, path_value)
    root = workspace_root.resolve() if workspace_root else None
    rel = target.relative_to(root).as_posix() if root else str(path_value)
    context = _workbench_git_context(root)
    if context is None:
        diff = _workbench_current_file_snapshot_diff(target, rel)
        return {"path": rel, "diff": diff, "has_changes": bool(diff.strip()), "source": "snapshot" if diff else "none"}
    repo_root, prefix = context
    git_rel = f"{prefix}{rel}" if prefix else rel

    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "diff",
            "--",
            git_rel,
            cwd=str(repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
    except asyncio.TimeoutError:
        raise TimeoutError("git diff timed out")
    except FileNotFoundError:
        raise RuntimeError("git not available")

    if proc.returncode not in (0, 1):
        raise RuntimeError(stderr.decode("utf-8", errors="replace") or "git diff failed")

    diff = stdout.decode("utf-8", errors="replace")
    diff_source = "git" if diff.strip() else "none"
    if not diff.strip() and target.is_file():
        staged = await asyncio.create_subprocess_exec(
            "git",
            "diff",
            "--cached",
            "--",
            git_rel,
            cwd=str(repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        staged_stdout, _ = await staged.communicate()
        if staged.returncode in (0, 1):
            diff = staged_stdout.decode("utf-8", errors="replace")
            if diff.strip():
                diff_source = "git"
    if not diff.strip() and target.is_file():
        tracked = await asyncio.create_subprocess_exec(
            "git",
            "ls-files",
            "--error-unmatch",
            "--",
            git_rel,
            cwd=str(repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await tracked.communicate()
        if tracked.returncode != 0:
            diff = _workbench_current_file_snapshot_diff(target, rel)
            if diff.strip():
                diff_source = "snapshot"
    if not diff.strip() and target.is_file():
        diff = _workbench_current_file_snapshot_diff(target, rel)
        if diff.strip():
            diff_source = "snapshot"

    return {"path": rel, "diff": diff, "has_changes": bool(diff.strip()), "source": diff_source}


def _workbench_apply_step_file_changes(session: dict[str, Any], step_id: str, file_changes: list[dict[str, Any]]) -> None:
    if not step_id or not file_changes:
        return
    plan = session.get("plan") if isinstance(session.get("plan"), list) else []
    for step in plan:
        if not isinstance(step, dict) or str(step.get("id") or "") != step_id:
            continue
        existing = step.get("relatedFiles") if isinstance(step.get("relatedFiles"), list) else []
        step["relatedFiles"] = _workbench_merge_file_changes([*existing, *file_changes])
        break


def _workbench_is_artifact_change(change: dict[str, Any]) -> bool:
    """Return whether a file event explicitly identifies a deliverable.

    Git/workspace diffs are useful for related-file tracking but do not prove
    task ownership. Only an explicit file creation/write or send_file action is
    strong enough to auto-promote a file into the artifact panel.
    """
    source = str(change.get("source") or "").strip().lower()
    change_type = str(change.get("status") or change.get("changeType") or "").strip().lower()
    if source in {"send_file", "workspace_output"}:
        return change_type == "produced"
    return False


def _workbench_prune_non_file_artifacts(session: dict[str, Any]) -> bool:
    """Keep the artifact collection limited to unique downloadable files."""
    artifacts = session.get("artifacts")
    if not isinstance(artifacts, list):
        session["artifacts"] = []
        return artifacts is not None

    kept: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or artifact.get("type") != "file_change":
            continue
        path = str(artifact.get("path") or artifact.get("name") or "").strip()
        if not path or path in seen_paths:
            continue
        name = path.rsplit("/", 1)[-1].lower()
        looks_temporary = name.startswith(("test_", "temp_", "tmp_", "scratch_"))
        if looks_temporary:
            reported = False
            for run in session.get("runs") or []:
                if not isinstance(run, dict):
                    continue
                response = str(run.get("agentResponse") or "")
                index = response.find(path)
                if index < 0:
                    index = response.find(path.rsplit("/", 1)[-1])
                if index < 0:
                    continue
                context = response[max(0, index - 180):index + len(path) + 180]
                if _WORKBENCH_OUTPUT_EVIDENCE.search(context):
                    reported = True
                    break
            if not reported:
                continue
        seen_paths.add(path)
        kept.append(artifact)

    if kept == artifacts:
        return False
    session["artifacts"] = kept
    return True


def _workbench_promote_file_artifacts(session: dict[str, Any], file_changes: list[dict[str, Any]], now: str, workspace_root: Path | None = None) -> int:
    """Surface explicitly produced files as task artifacts (dedup by path).

    When ``workspace_root`` is provided, files declared via ``send_file``
    (changeType ``produced``) are copied into ``deliverables/`` for artifact
    download.  The source must remain in place: moving it after the Agent has
    verified the requested path can silently invalidate the task's result.
    """
    _workbench_prune_non_file_artifacts(session)
    if not file_changes:
        return 0
    artifacts = session.get("artifacts") if isinstance(session.get("artifacts"), list) else []
    known_paths = {
        str(a.get("path") or a.get("name") or "").strip()
        for a in artifacts
        if isinstance(a, dict)
    }
    status_map = {
        "created": "created",
        "created/updated": "created",
        "modified": "modified",
        "renamed": "modified",
    }
    deliverables_dir = (workspace_root / "deliverables").resolve() if workspace_root else None
    added = 0
    for change in file_changes:
        if not isinstance(change, dict):
            continue
        path = str(change.get("path") or change.get("name") or "").strip()
        if not path or path in known_paths:
            continue
        if not _workbench_is_artifact_change(change):
            continue
        change_type = str(change.get("status") or change.get("changeType") or "")
        status = status_map.get(change_type)
        if change_type == "produced":
            status = "ready"
        if not status:
            continue
        original_path = path
        # Copy send_file deliverables into deliverables/ for download while
        # preserving the Agent-verified source path. Skip files already there.
        if change_type == "produced" and deliverables_dir:
            src_path = (workspace_root / path).resolve()  # type: ignore[union-attr]
            try:
                src_path.relative_to(deliverables_dir)
            except ValueError:
                if src_path.exists():
                    deliverables_dir.mkdir(parents=True, exist_ok=True)
                    dest_name = path.rsplit("/", 1)[-1] or path
                    dest_path = deliverables_dir / dest_name
                    if dest_path.exists():
                        stem = Path(dest_name).stem
                        suffix = Path(dest_name).suffix or ".bin"
                        dest_path = deliverables_dir / f"{stem}_{_short_id('f')}{suffix}"
                    if src_path != dest_path:
                        shutil.copy2(str(src_path), str(dest_path))
                        path = str(dest_path.relative_to(workspace_root))  # type: ignore[union-attr]
        known_paths.add(path)
        artifact = {
            "id": _short_id("artifact"),
            "type": "file_change",
            "name": path.rsplit("/", 1)[-1] or path,
            "path": path,
            "status": status,
            "createdAt": now,
            "summary": path,
            "source": change.get("source"),
        }
        if change.get("diff"):
            artifact["diff"] = _workbench_relabel_diff_paths(str(change.get("diff") or ""), original_path, path)
            if change.get("diffSource"):
                artifact["diffSource"] = change.get("diffSource")
        artifacts.append(artifact)
        added += 1
    session["artifacts"] = artifacts
    return added


def _workbench_backfill_file_artifacts(
    session: dict[str, Any],
    now: str,
    workspace_root: Path | None = None,
) -> int:
    """Derive file_change artifacts from a session's already-recorded runs and
    plan steps, for tasks that ran before file promotion existed."""
    changes: list[dict[str, Any]] = []
    for run in session.get("runs") or []:
        if isinstance(run, dict):
            changes.extend(c for c in (run.get("fileChanges") or []) if isinstance(c, dict))
    for step in session.get("plan") or []:
        if isinstance(step, dict):
            changes.extend(c for c in (step.get("relatedFiles") or []) if isinstance(c, dict))
    merged = _workbench_merge_file_changes(changes)
    if workspace_root is not None:
        existing: list[dict[str, Any]] = []
        for change in merged:
            if not _workbench_is_artifact_change(change):
                existing.append(change)
                continue
            try:
                target = _workbench_resolve_workspace_file(
                    workspace_root,
                    change.get("path") or change.get("name"),
                )
            except (OSError, ValueError):
                continue
            if target.is_file():
                existing.append(change)
        merged = existing
    return _workbench_promote_file_artifacts(
        session,
        merged,
        now,
        workspace_root,
    )


_WORKBENCH_OUTPUT_EVIDENCE = re.compile(
    r"(已生成|成功生成|生成完成|已导出|成功导出|已保存|文件路径|可直接交付|"
    r"generated|created|exported|saved|produced|deliverable)",
    flags=re.IGNORECASE,
)
_WORKBENCH_INPUT_EVIDENCE = re.compile(
    r"(输入|源文件|读取|基于|转换自|input|source|read from|converted from)",
    flags=re.IGNORECASE,
)


def _workbench_backfill_referenced_file_artifacts(
    project: dict[str, Any],
    session: dict[str, Any],
    now: str,
) -> int:
    """Recover real files explicitly reported as outputs by historical runs."""
    root = _workbench_workspace_root(project)
    snapshot = _workbench_workspace_file_snapshot(root)
    if not snapshot:
        return 0
    changes: list[dict[str, Any]] = []
    for run in session.get("runs") or []:
        if not isinstance(run, dict):
            continue
        response = str(run.get("agentResponse") or "")
        if not response or not _WORKBENCH_OUTPUT_EVIDENCE.search(response):
            continue
        for path in snapshot:
            name = path.rsplit("/", 1)[-1]
            positions = [
                index for token in (path, name)
                if token and (index := response.find(token)) >= 0
            ]
            if not positions:
                continue
            index = min(positions)
            prefix = response[max(0, index - 80):index]
            if _WORKBENCH_INPUT_EVIDENCE.search(prefix):
                continue
            context = response[max(0, index - 180):index + len(name) + 180]
            if not _WORKBENCH_OUTPUT_EVIDENCE.search(context):
                continue
            change = _workbench_file_change(path, "produced", root, "workspace_output")
            if change:
                changes.append(change)
    return _workbench_promote_file_artifacts(
        session,
        _workbench_merge_file_changes(changes),
        now,
        root,
    )


_WORKBENCH_FINAL_KNOWLEDGE_STATUSES = {"review", "completed", "done"}


def _workbench_final_artifact_file_changes(session: dict[str, Any]) -> list[dict[str, Any]]:
    """Return final deliverables that should be promoted into knowledge.

    ``fileChanges`` is a process log and may contain intermediate build files.
    ``session.artifacts`` is the curated deliverable surface shown to the user,
    so final knowledge ingestion must read from it instead.
    """
    artifacts = session.get("artifacts") if isinstance(session.get("artifacts"), list) else []
    changes: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or artifact.get("type") != "file_change":
            continue
        path = str(artifact.get("path") or artifact.get("name") or "").strip()
        if not path or path in seen_paths:
            continue
        status = str(artifact.get("status") or "").strip().lower()
        if status in {"deleted", "removed", "missing"}:
            continue
        seen_paths.add(path)
        changes.append({
            "path": path,
            "status": "produced",
            "source": artifact.get("source") or "final_artifact",
        })
    return changes


def _workbench_prune_invalid_file_records(
    project: dict[str, Any],
    session: dict[str, Any],
) -> bool:
    """Remove historical file records that cannot belong to this workspace.

    Older builds ran ``git status`` from a nested workspace without a pathspec.
    Git then reported changes from the parent repository, which were persisted
    as step files and artifacts. Absolute paths outside the workspace and
    inferred Git modifications that do not exist under the workspace are the
    reliable signatures of that bug.
    """
    workspace_root = _workbench_workspace_root(project)
    if workspace_root is None:
        return False
    try:
        root = workspace_root.resolve()
    except OSError:
        return False

    changed = False
    rejected_paths: set[str] = set()

    def valid(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        raw = str(item.get("path") or item.get("name") or "").strip()
        if not raw:
            return False
        path = Path(raw).expanduser()
        try:
            target = path.resolve() if path.is_absolute() else (root / path).resolve()
            target.relative_to(root)
        except (OSError, ValueError):
            rejected_paths.add(raw)
            return False
        source = str(item.get("source") or "").strip().lower()
        status = str(item.get("status") or item.get("changeType") or "").strip().lower()
        if source == "git" and status != "deleted" and not target.exists():
            rejected_paths.add(raw)
            return False
        return True

    def prune(container: dict[str, Any], key: str) -> None:
        nonlocal changed
        items = container.get(key)
        if not isinstance(items, list):
            return
        kept = [item for item in items if valid(item)]
        if len(kept) != len(items):
            container[key] = kept
            changed = True

    for step in session.get("plan") or []:
        if isinstance(step, dict):
            prune(step, "relatedFiles")
    for run in session.get("runs") or []:
        if not isinstance(run, dict):
            continue
        prune(run, "fileChanges")
        for event in run.get("events") or []:
            if isinstance(event, dict):
                prune(event, "fileChanges")
    for event in session.get("events") or []:
        if isinstance(event, dict):
            prune(event, "fileChanges")

    # Reconcile old auto-promoted artifacts with the remaining trustworthy
    # provenance. Explicit legacy artifacts with no matching file-change record
    # are preserved.
    all_changes: list[dict[str, Any]] = []
    for run in session.get("runs") or []:
        if isinstance(run, dict):
            all_changes.extend(item for item in (run.get("fileChanges") or []) if isinstance(item, dict))
    for step in session.get("plan") or []:
        if isinstance(step, dict):
            all_changes.extend(item for item in (step.get("relatedFiles") or []) if isinstance(item, dict))
    known_paths = {
        str(item.get("path") or item.get("name") or "").strip()
        for item in all_changes
        if str(item.get("path") or item.get("name") or "").strip()
    }
    artifact_paths = {
        str(item.get("path") or item.get("name") or "").strip()
        for item in all_changes
        if _workbench_is_artifact_change(item)
    }
    artifacts = session.get("artifacts")
    if isinstance(artifacts, list):
        kept_artifacts = []
        for artifact in artifacts:
            if not isinstance(artifact, dict) or artifact.get("type") != "file_change":
                kept_artifacts.append(artifact)
                continue
            path = str(artifact.get("path") or artifact.get("name") or "").strip()
            if path in rejected_paths or (path in known_paths and path not in artifact_paths):
                changed = True
                continue
            kept_artifacts.append(artifact)
        session["artifacts"] = kept_artifacts
    return changed


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
            # workbench_knowledge_service._resolve_workspace_id.
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
    "- 不要声明源代码、脚本、.tex、缓存、依赖、构建目录或中间数据，除非用户明确要求这些也是交付物。\n"
    "- 例：代码生成数据分析报告时，默认只交付最终报告（如 PDF/HTML/Markdown），不交付分析脚本；"
    "LaTeX 生成文档时，默认只交付编译后的 PDF，不交付 .tex/.aux/.log。\n"
    "- 如果最终交付文件是通过 Bash/shell/命令行生成的，也必须用 send_file 声明，否则不会出现在「产物」面板。\n"
    "- 交付物请写到 deliverables/ 子目录下；用 send_file 声明的文件会被自动归档到 deliverables/。\n"
    "- 不要只在回复里写出文件路径就当作已经交付。"
)

_WORKBENCH_TASK_REPLY_DIRECTIVE = (
    "## 本轮任务对话回复模式\n"
    "用户这次是在任务里继续对话或提问。优先根据当前任务上下文直接回复，并把完整回复放入 "
    "quit(reply)。不要因为处于任务页就自动查看文件、执行命令或更新计划。只有用户明确要求"
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
) -> tuple[str, str]:
    """Return (run_fixed_memory, volatile_tail_memory) for this Workbench session.

    A session snapshots the project-memory ids it first saw. Those memories stay
    in the run-fixed block for cache stability. Memories created later in the
    same session are rendered in the volatile tail so they remain visible without
    invalidating the already-established fixed prefix. A new session snapshots
    again and promotes them back into the fixed block.
    """
    if not project:
        return "", ""
    memory_key = _workbench_project_memory_key(project)
    try:
        current_ids = _memory_service().memory_injection_ids(memory_key)
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
        )
        volatile = _memory_service().render_memory_for_injection(
            memory_key,
            include_ids=new_ids,
            preserve_id_order=True,
            header="## 本 session 新增项目记忆（刚写入，放在最后供本轮参考；与当前任务无关则忽略）",
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
) -> str:
    """Assemble run-fixed context for a Workbench agent run.

    The coordinator inserts this block before the current user turn, not into the
    base system prefix and not at the absolute prompt tail. That keeps volatile
    Workbench context out of cross-run system caching while allowing tool rounds
    in this run to reuse the full previous prompt as a prefix.

    Blocks (in order): Workbench task shared context → project memory snapshot →
    reflection seed → step context cascade → workspace state.
    """
    parts: list[str] = []
    # 1. Workbench task shared context: project blocks first, then session task /
    # plan / acceptance.  This applies only to Workbench task sessions.
    shared_task_context = _workbench_task_build_main_context(project, session)
    if shared_task_context:
        parts.append(shared_task_context)
    # 2. Project durable memories: snapshot at session start for cache stability.
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
) -> str:
    """Context that intentionally stays at the absolute prompt tail."""
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
        "allowCustom": bool(q.get("allow_custom", True)),
        "kind": str(meta.get("kind") or ""),
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
_WORKBENCH_AGENT_EDITABLE_META = ("goal", "title", "summary", "titleLocked")


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
        workspace_dir=workspace_dir or "",
    )
    try:
        return await answer_pending_question(
            question_id, answer_text, _bot, _CHAT_ID, _db_path,
            permission_mode=permission_mode,
        )
    finally:
        binding.reset()


def _workbench_resolve_workspace_dir(project: dict[str, Any] | None) -> str:
    """Resolve a project's confined workspace dir (created if missing). Empty →
    the global WORKSPACE_DIR. Mirrors the logic in ``_workbench_agent_reply``."""
    ws_raw = str((project or {}).get("workspacePath") or "").strip()
    if not ws_raw:
        return ""
    try:
        ws_path = Path(ws_raw).expanduser()
        ws_path.mkdir(parents=True, exist_ok=True)
        return str(ws_path.resolve())
    except OSError:
        logger.warning("Workbench workspace unavailable, using global: %s", ws_raw)
        return ""


async def _check_budget_gate(session_id: str) -> dict | None:
    """Shared budget gate.  Returns ``None`` if OK, or a dict
    ``{"error": str, "code": str}`` describing why the request is blocked."""
    from cyrene.runtime.settings_store import get_all as _get_all_settings
    from cyrene.agent.budget import check_budget_and_block as _check_budget
    from cyrene.agent.budget import _start_budget_windows

    settings = _get_all_settings()
    monthly = float(settings.get("budget_monthly") or 0)
    result = await _check_budget(
        _db_path or str(DB_PATH),
        monthly=monthly,
        enabled=bool(settings.get("budget_enabled", False)),
    )
    blocked = None
    if result:
        blocked = {"error": result["message"], "code": result["code"]}
        logger.warning("Budget block for %s: %s", session_id, result["code"])
    elif monthly > 0:
        # Start hard-reset windows for any request that passes the gate,
        # regardless of budget action (warn/block) or enabled state.
        _start_budget_windows()
    return blocked


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
        return _bgt

    normalized = _workbench_normalize_attachments(attachments)
    public_attachments = [build_public_attachment_payload(item) for item in normalized] or None
    message = str(user_input or "")
    attachment_binding = None
    if normalized:
        message = (message or "[Attachment upload]") + _attachment_prompt_block(normalized)
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

    try:
        return await run_agent(
            user_message=message,
            bot=_bot,
            chat_id=_CHAT_ID,
            db_path=_db_path,
            session_id=session_id,
            permission_mode=mode,
            command=str(command or "").strip(),
            # Preserve an intentionally empty public message for attachment-only
            # turns. ``None`` means "show the model-facing message", which also
            # contains the private attachment instruction block.
            public_user_message=str(user_input or ""),
            public_attachments=public_attachments,
            workspace_dir=workspace_dir,
            ephemeral_system=str(ephemeral_system or ""),
            volatile_ephemeral_system=str(volatile_ephemeral_system or ""),
            static_system_extra=str(static_system_extra or ""),
        )
    except Exception:
        logger.exception("Workbench agent run failed for session %s", session_id)
        return str(user_input or "").strip()
    finally:
        if attachment_binding is not None:
            attachment_binding.reset()


def _remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


async def _clear_knowledge_data(store_dir: Path) -> None:
    """Stop indexing and remove every workspace-scoped knowledge database."""
    from cyrene.knowledge import ingest

    knowledge = importlib.import_module("cyrene.workbench.knowledge")
    await ingest.cancel_pending_tasks()

    knowledge_paths: set[Path] = set()
    for pattern in ("kb_*.db", "kb_*.db-wal", "kb_*.db-shm", "kb_*.db-journal"):
        knowledge_paths.update(store_dir.glob(pattern))
    for path in knowledge_paths:
        _remove_path(path)

    knowledge._kb_initialized.clear()


async def _reset_app_data() -> dict[str, Any]:
    """Wipe user-modifiable runtime data and restore first-run defaults."""
    from cyrene import agent as cy_agent
    from cyrene.config import write_env_keys
    from cyrene.runtime.database import init_db, init_knowledge_db
    from cyrene.runtime.inbox import clear_all_inboxes
    from cyrene.runtime.settings_store import reset_all as reset_web_settings

    await clear_session_id()

    for task in list(cy_agent._pending_compressors):
        task.cancel()
    cy_agent._pending_compressors.clear()
    await asyncio.sleep(0)

    _scheduler_service().reset_lottery()
    await clear_all_inboxes()
    reset_web_settings()
    reset_onboarding_state()

    from cyrene.config import STORE_DIR

    for path in (
        STATE_FILE,
        DATA_DIR / "short_term.json",
        DATA_DIR / "lottery_state.json",
        DATA_DIR / "web_settings.json",
        DATA_DIR / "onboarding_state.json",
        DATA_DIR / ".setup_done",
        # Legacy Workbench JSON exports. The authoritative rows are removed
        # below when the SQLite database itself is reset.
        _WORKBENCH_STORE,
        DATA_DIR / "workbench_chats.json",
        DATA_DIR / "workbench_notifications.json",
    ):
        _remove_path(path)

    # Legacy per-workspace memory exports.
    for mem_path in STORE_DIR.glob("wb_memory_*.json"):
        _remove_path(mem_path)

    # Remove both the pre-refactor database name and retained rollback copies.
    # Otherwise the next launch would correctly interpret them as upgrade data
    # and undo the user's explicit full reset.
    _remove_path(STORE_DIR / "cyrene.db")
    for legacy_db_backup in STORE_DIR.glob(
        "cyrene.db.pre-runtime-database-migration*.bak*"
    ):
        _remove_path(legacy_db_backup)

    await _clear_knowledge_data(STORE_DIR)
    await init_knowledge_db(str(STORE_DIR / "kb_default.db"))

    for path in (
        CONVERSATIONS_DIR,
        _UPLOADS_DIR,
        _EXPORTS_DIR,
        PATTERNS_DIR,
    ):
        _remove_path(path)

    db_path = Path(_db_path or str(DB_PATH))
    _remove_path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    await init_db(str(db_path))

    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
    soul_path = get_soul_path()
    soul_path.parent.mkdir(parents=True, exist_ok=True)
    soul_path.write_text(get_default_soul_content(), encoding="utf-8")

    write_env_keys({
        "OPENAI_API_KEY": "",
        "OPENAI_BASE_URL": DEFAULT_OPENAI_BASE_URL,
        "OPENAI_MODEL": DEFAULT_OPENAI_MODEL,
        "TELEGRAM_BOT_TOKEN": "",
    })

    return {
        "ok": True,
        "onboarding": get_onboarding_status(),
        "sessions": _build_sessions(),
    }


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


def _consume_cc_input_buffer(buffer: str, data: str) -> tuple[str, list[str]]:
    current = str(buffer or "")
    submitted: list[str] = []
    if not data:
        return current, submitted

    index = 0
    while index < len(data):
        char = data[index]
        if char == "\x1b":
            break
        if char in ("\r", "\n"):
            text = current.strip()
            if text:
                submitted.append(text)
            current = ""
        elif char in ("\x7f", "\b"):
            current = current[:-1]
        elif char == "\t":
            current += "\t"
        elif ord(char) >= 32:
            current += char
        index += 1
    return current, submitted


async def _publish_cc_learning(text: str, tmux_session: str = "") -> None:
    prompt = str(text or "").strip()
    if not prompt:
        return

    status = get_cc_status(_CC_PROJECT_DIR)
    latest_jsonl = str(status.get("latest_jsonl") or "").strip()
    await debug.publish_event(
        {
            "type": "cc_learning",
            "phase": "started",
            "tmux_session": tmux_session,
            "user_input": prompt[:200],
            "latest_jsonl": latest_jsonl,
        }
    )
    if not latest_jsonl:
        return

    try:
        result = await asyncio.to_thread(learn_from_session, Path(latest_jsonl))
    except Exception:
        logger.exception("Failed learning from Claude Code transcript %s", latest_jsonl)
        await debug.publish_event(
            {
                "type": "cc_learning",
                "phase": "error",
                "tmux_session": tmux_session,
                "user_input": prompt[:200],
                "latest_jsonl": latest_jsonl,
            }
        )
        return

    summary = result.get("summary", {})
    await debug.publish_event(
        {
            "type": "cc_learning",
            "phase": "completed",
            "tmux_session": tmux_session,
            "user_input": prompt[:200],
            "latest_jsonl": latest_jsonl,
            "highlights": summary.get("highlights", []),
            "top_tools": summary.get("top_tools", []),
            "top_tasks": summary.get("top_tasks", []),
        }
    )


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


def _stream_agent_reply(run_coro_factory, user_message: str) -> StreamingResponse:
    async def event_stream():
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        saw_reply_events = False
        run_failed = False

        async def publish_reply_event(event: dict[str, Any]) -> None:
            await queue.put(dict(event))

        binding = bind_run_context(reply_stream_writer=publish_reply_event)
        try:
            task = asyncio.create_task(run_coro_factory())
        finally:
            binding.reset()

        # Broadcast running status so the topbar status light updates in real-time
        await debug.publish_event({"type": "session_update", "status": "running"})

        try:
            while True:
                if task.done() and queue.empty():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
                if str(event.get("type") or "").startswith("reply_"):
                    saw_reply_events = True
                yield _ndjson_line(event)

            try:
                response = await task
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A model-call failure (timeout, 5xx, rate limit, network error)
                # must surface to the client. Without this the NDJSON stream just
                # ends silently and the round renders as a bland "done" — the
                # exact symptom of issue #7 ("model failure only replies done").
                run_failed = True
                logger.exception("Streaming chat run failed: %s", format_httpx_error(exc))
                yield _ndjson_line({
                    "type": "error",
                    "error": "model_call_failed",
                    "message": str(exc).strip() or exc.__class__.__name__,
                })
                await debug.publish_event({"type": "session_update", "status": "error"})
                return
            if response == _AWAITING_USER_SENTINEL:
                yield _ndjson_line({"type": "awaiting_user", "awaiting_user": True, "pending_question": get_pending_question()})
                return

            # Stream the response text FIRST — before any I/O (archive_exchange)
            # or SSE events, so the frontend gets reply_delta events without delay
            # and avoids the race where refreshSessions() clears pending messages
            # before the stream completes.
            if not saw_reply_events:
                yield _ndjson_line({"type": "reply_start"})
                for chunk in _reply_stream_chunks(response):
                    yield _ndjson_line({"type": "reply_delta", "delta": chunk})
                yield _ndjson_line({"type": "reply_done", "response": response})

            # Archive the exchange after streaming — file I/O must not delay
            # response delivery to the frontend.
            labels = get_session_labels()
            await archive_exchange(
                user_message,
                response,
                _CHAT_ID,
                session_title=labels.get("session_title", ""),
                round_title=labels.get("round_title", ""),
                round_id=labels.get("round_id", ""),
                archive_session_id=labels.get("archive_session_id", ""),
            )

            # Signal done last, so the SSE-triggered refreshSessions() call
            # runs after the NDJSON stream has already delivered reply_done.
            await debug.publish_event({"type": "session_update", "status": "done"})
        finally:
            if not task.done():
                task.cancel()
            # Publish "done" on success/cancellation. On a model-call failure we
            # already published "error" above and must not overwrite it with a
            # misleading "done" (issue #7).
            if not run_failed:
                await debug.publish_event({"type": "session_update", "status": "done"})

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache"},
    )


def _safe_upload_name(filename: str) -> str:
    return safe_attachment_filename(filename, fallback_stem="upload")


def _retry_safe_guide_round_id(guide_round_id: str, retry: bool) -> str:
    """A retry regenerates a reply; it must not target the old completed round."""
    return "" if retry else str(guide_round_id or "").strip()


def _image_dimensions(path: Path) -> tuple[int | None, int | None]:
    try:
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception:
        return None, None


async def _deduplicate_chat_upload_after_response(
    target: Path,
    *,
    display_name: str,
    content_type: str,
    kind: str,
    size: int,
) -> None:
    """Register a durable chat upload in the knowledge base after upload.

    Knowledge-base deduplication owns database rows, not chat attachment files.
    The exact upload path is persisted in chat/session state and may still be
    referenced by the composer, transcript, retries, and AnalyzeAttachment, so
    it must remain valid even when identical content already exists in the KB.
    """
    try:
        from cyrene.config import get_knowledge_db_path
        from cyrene.knowledge import ingest, store

        if not target.exists() or not target.is_file():
            logger.warning("Skipping knowledge registration for missing chat upload: %s", target)
            return

        kb_db_path = str(get_knowledge_db_path())
        content_hash = await asyncio.to_thread(store.content_hash_file, target)
        doc = await store.upsert_document_by_path(
            kb_db_path,
            path=str(target.resolve()),
            source="chat_upload",
            name=display_name,
            content_type=content_type,
            kind=kind,
            size=size,
            content_hash=content_hash,
        )
        if doc.get("status") in {"pending", "error"}:
            asyncio.create_task(ingest.index_document(kb_db_path, doc["id"]))
    except Exception:
        logger.exception("Failed to register chat upload in knowledge base: %s", target)


def _attachment_prompt_block(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    lines = [
        "",
        "[Uploaded attachments]",
        "The user uploaded the following files into the local workspace-accessible runtime data directory.",
        "Before answering anything about these files, you MUST inspect the relevant attachment with AnalyzeAttachment.",
        "Do not answer from the filename, extension, or metadata alone.",
        "After AnalyzeAttachment returns extracted content, use that extracted content to answer the user.",
        "If AnalyzeAttachment reports that an uploaded file is missing or unavailable, stop attachment analysis and ask the user to upload it again.",
        "Do NOT use Glob, Grep, Bash, find, or directory scans to search for a replacement file elsewhere on the device.",
    ]
    for item in items:
        lines.append(f'- {item["name"]} ({item["content_type"]}): {item["path"]}')
    return "\n".join(lines)


async def _chat_with_uploaded_images(message: str, attachments: list[dict[str, Any]]) -> str:
    prompt = str(message or "").strip() or "Describe the uploaded image in detail and extract any visible text."
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for item in attachments:
        path = Path(str(item.get("path") or "")).resolve()
        mime = str(item.get("content_type") or mimetypes.guess_type(str(path))[0] or "image/png")
        image_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}})
    try:
        response = await _call_llm([{"role": "user", "content": content}], tools=None, max_tokens=None)
    except httpx.HTTPError as exc:
        detail = format_httpx_error(exc).lower()
        if any(token in detail for token in ("image", "vision", "multimodal", "unsupported", "invalid content")):
            result = await run_vision_chat(content, content_prompt=prompt)
            return str(result.get("vision_text") or "").strip() or "The vision fallback model returned no usable image analysis."
        raise
    response_text = str((response.get("content") if isinstance(response.get("content"), str) else "") or "").strip()
    if response_text:
        return response_text
    parts: list[str] = []
    if isinstance(response.get("content"), list):
        for item in response.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
    merged = "".join(parts).strip()
    return merged or "The model returned no usable image analysis."


async def _persist_direct_image_chat(
    message: str,
    response: str,
    public_attachments: list[dict[str, Any]],
    client_request_id: str,
) -> None:
    round_id = f"round_{int(time.time() * 1000)}"
    user_entry: dict[str, Any] = {
        "role": "user",
        "content": str(message or ""),
        "attachments": [dict(item) for item in public_attachments],
        "round_id": round_id,
    }
    if client_request_id:
        user_entry["client_request_id"] = client_request_id
    await _append_session_message(user_entry)
    await append_system_message(
        response,
        message_meta={
            "system_initiated": False,
            "round_id": round_id,
            **({"client_request_id": client_request_id} if client_request_id else {}),
        },
        publish_event={
            "type": "chat_message",
            "round_id": round_id,
            "client_request_id": client_request_id,
        },
    )


# ---- Workbench global search helpers -------------------------------------


def _normalize_search_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def _search_matches(query: str, text: str) -> bool:
    """Case-insensitive, whitespace-normalized substring match.

    Also supports a tiny space-removed fallback so "helloworld" matches
    "hello world".
    """
    if not query or not text:
        return False
    haystack = _normalize_search_text(text)
    needle = _normalize_search_text(query)
    if not needle:
        return False
    if needle in haystack:
        return True
    if needle.replace(" ", "") and needle.replace(" ", "") in haystack.replace(" ", ""):
        return True
    return False


def _search_snippet(text: str, query: str, length: int = 140) -> str:
    """Return a short snippet centered on the first match."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    q = str(query or "").strip()
    if not q:
        return raw[:length] + ("…" if len(raw) > length else "")
    # Direct case-insensitive substring match.
    idx = raw.lower().find(q.lower())
    if idx < 0:
        # Fall back to flexible whitespace: "hello world" matches "hello  world".
        try:
            pattern = re.compile(re.sub(r"\s+", r"\\s+", re.escape(q)), re.IGNORECASE)
            match = pattern.search(raw)
            if match:
                idx = match.start()
        except re.error:
            pass
    if idx < 0:
        return raw[:length] + ("…" if len(raw) > length else "")
    start = max(0, idx - length // 2)
    end = min(len(raw), start + length)
    snippet = raw[start:end]
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(raw) else ""
    return prefix + snippet + suffix


async def _search_workbench_items(query: str, types: set[str], per_type_limit: int) -> dict[str, list[dict[str, Any]]]:
    """Search across Workbench data sources and return grouped results."""
    groups: dict[str, list[dict[str, Any]]] = {t: [] for t in types}
    if not query:
        return groups

    # Search only needs persisted project/session metadata.  The full reader
    # also performs historical invariant repair and scans every project
    # workspace for artifact backfills, which made each keystroke scale with
    # the number of tasks and files.  Keep that repair work off the search hot
    # path and move the SQLite read off the event loop.
    store = await asyncio.to_thread(_read_workbench_store_lightweight)
    projects = store.get("projects", [])
    project_by_id: dict[str, dict[str, Any]] = {str(p.get("id") or ""): p for p in projects if p.get("id")}
    project_names: dict[str, str] = {
        pid: str(p.get("name") or p.get("id") or "").strip() or "Workspace"
        for pid, p in project_by_id.items()
    }
    # Knowledge is keyed on the project id (memory key), not dataKey. For the
    # legacy default project these differ, so knowledge search must iterate the
    # id-based keys to read the same kb_<id>.db the Workbench UI reads.
    project_kb_keys: dict[str, str] = {
        pid: _workbench_project_memory_key(p) for pid, p in project_by_id.items()
    }

    # Build reverse map -> project id. Index by BOTH the data key (scheduled
    # tasks / entity project_id) and the memory/knowledge key (memory + knowledge
    # search) so the default project, whose two keys differ, resolves either way.
    data_key_to_project: dict[str, str] = {}
    for pid, p in project_by_id.items():
        data_key_to_project.setdefault(_workbench_project_data_key(p), pid)
        data_key_to_project.setdefault(_workbench_project_memory_key(p), pid)

    # ---- projects ----
    if "project" in types:
        for project in projects:
            pid = str(project.get("id") or "")
            name = str(project.get("name") or "")
            desc = str(project.get("description") or "")
            summary = str((project.get("context") or {}).get("summary") or "")
            if _search_matches(query, name) or _search_matches(query, desc) or _search_matches(query, summary):
                groups["project"].append({
                    "id": pid,
                    "type": "project",
                    "title": name or "Workspace",
                    "snippet": _search_snippet(desc or summary, query),
                    "projectId": pid,
                    "projectName": project_names.get(pid, ""),
                    "updatedAt": project.get("updatedAt") or project.get("createdAt") or "",
                })
                if len(groups["project"]) >= per_type_limit:
                    break

    # ---- tasks (workbench sessions) ----
    if "task" in types:
        for project in projects:
            pid = str(project.get("id") or "")
            for session in project.get("sessions", []):
                sid = str(session.get("id") or "")
                title = str(session.get("title") or "")
                goal = str(session.get("goal") or "")
                if _search_matches(query, title) or _search_matches(query, goal):
                    groups["task"].append({
                        "id": sid,
                        "type": "task",
                        "title": title or "New task",
                        "snippet": _search_snippet(goal or title, query),
                        "projectId": pid,
                        "projectName": project_names.get(pid, ""),
                        "sessionId": sid,
                        "status": session.get("status") or "idle",
                        "updatedAt": session.get("updatedAt") or session.get("createdAt") or "",
                    })
                    if len(groups["task"]) >= per_type_limit:
                        break
            if len(groups["task"]) >= per_type_limit:
                break

    # ---- chats ----
    if "chat" in types:
        try:
            read_chats_store = importlib.import_module(
                "cyrene.workbench.chat"
            )._read_chats_store

            def _search_chats() -> list[dict[str, Any]]:
                found: list[dict[str, Any]] = []
                chats_payload = read_chats_store()
                for chat in chats_payload.get("chats", []):
                    chat_id = str(chat.get("id") or "")
                    pid = str(chat.get("projectId") or "")
                    title = str(chat.get("title") or "")
                    preview = str(chat.get("preview") or "")
                    matched = _search_matches(query, title) or _search_matches(query, preview)
                    if not matched and isinstance(chat.get("messages"), list):
                        for message in chat["messages"]:
                            if _search_matches(query, str(message.get("content") or message.get("body") or "")):
                                matched = True
                                break
                    if not matched:
                        continue
                    found.append({
                        "id": chat_id,
                        "type": "chat",
                        "title": title or "New chat",
                        "snippet": _search_snippet(preview or title, query),
                        "projectId": pid,
                        "projectName": project_names.get(pid, "Workspace"),
                        "chatId": chat_id,
                        "updatedAt": chat.get("updatedAt") or chat.get("createdAt") or "",
                    })
                    if len(found) >= per_type_limit:
                        break

                return found

            groups["chat"].extend(await asyncio.to_thread(_search_chats))
        except Exception:
            logger.exception("Workbench chat search failed")

    # ---- knowledge ----
    if "knowledge" in types:
        try:
            from cyrene.config import get_knowledge_db_path
            from cyrene.knowledge import retrieve
            from cyrene.runtime.database import init_knowledge_db

            seen_docs: set[str] = set()
            for pid, dk in project_kb_keys.items():
                db_path_kb = str(get_knowledge_db_path(dk))
                try:
                    await init_knowledge_db(db_path_kb)
                    kb_results = await retrieve.search_knowledge(db_path_kb, query, k=per_type_limit * 3)
                    for item in kb_results:
                        doc_id = str(item.get("document_id") or "")
                        if not doc_id:
                            continue
                        key = f"{dk}:{doc_id}"
                        if key in seen_docs:
                            continue
                        seen_docs.add(key)
                        groups["knowledge"].append({
                            "id": doc_id,
                            "type": "knowledge",
                            "title": str(item.get("document_name") or doc_id),
                            "snippet": _search_snippet(str(item.get("content") or ""), query),
                            "projectId": pid,
                            "projectName": project_names.get(pid, "Workspace"),
                            "docId": doc_id,
                            "chunkId": item.get("chunk_id"),
                            "score": item.get("score"),
                        })
                        if len(groups["knowledge"]) >= per_type_limit:
                            break
                except Exception:
                    logger.exception("Knowledge search failed for workspace %s", dk)
                if len(groups["knowledge"]) >= per_type_limit:
                    break
        except Exception:
            logger.exception("Workbench knowledge search failed")

    # ---- memory ----
    if "memory" in types:
        try:
            from cyrene.config import STORE_DIR
            from cyrene.workbench.store import list_document_keys, read_document
            memory_service = _memory_service()
            entry_id = memory_service._entry_id
            is_user_visible_entry = memory_service._is_user_visible_entry

            def _search_memories() -> list[dict[str, Any]]:
                found: list[dict[str, Any]] = []
                memory_keys = {
                    key[len("memory:"):]
                    for key in list_document_keys(_db_path or str(DB_PATH), prefix="memory:")
                }
                memory_keys.update(
                    path.stem[len("wb_memory_"):]
                    for path in STORE_DIR.glob("wb_memory_*.json")
                )
                for dk in sorted(memory_keys):
                    if len(found) >= per_type_limit:
                        break
                    pid = data_key_to_project.get(dk, "")
                    data = read_document(
                        _db_path or str(DB_PATH),
                        f"memory:{dk}",
                        list,
                        legacy_path=STORE_DIR / f"wb_memory_{dk}.json",
                    )
                    entries = data if isinstance(data, list) else []
                    for entry in entries:
                        if not isinstance(entry, dict) or not is_user_visible_entry(entry):
                            continue
                        content = str(entry.get("content") or "")
                        tags = [str(t) for t in (entry.get("tags") or [])]
                        tag_text = " ".join(tags)
                        if not (_search_matches(query, content) or _search_matches(query, tag_text)):
                            continue
                        mem_id = entry_id(entry)
                        found.append({
                            "id": mem_id,
                            "type": "memory",
                            "title": content[:80] or "Memory",
                            "snippet": _search_snippet(content, query),
                            "projectId": pid,
                            "projectName": project_names.get(pid, "Workspace"),
                            "memId": mem_id,
                            "category": entry.get("category") or entry.get("type") or "fact",
                            "tags": tags,
                            "updatedAt": entry.get("last_mentioned") or entry.get("first_seen") or "",
                        })
                        if len(found) >= per_type_limit:
                            break

                return found

            groups["memory"].extend(await asyncio.to_thread(_search_memories))
        except Exception:
            logger.exception("Workbench memory search failed")

    # ---- schedule (scheduled tasks + entity deadlines) ----
    if "schedule" in types:
        try:
            from cyrene.runtime import database as cy_db
            from cyrene.tool_impl.entity.store import list_entities

            # Scheduled tasks across all projects.
            try:
                all_tasks = await cy_db.get_all_tasks(_db_path)
                for task in all_tasks:
                    prompt = str(task.get("prompt") or "")
                    if _search_matches(query, prompt):
                        dk = str(task.get("project_id") or "default")
                        pid = data_key_to_project.get(dk, "")
                        groups["schedule"].append({
                            "id": str(task.get("id") or ""),
                            "type": "schedule",
                            "title": prompt or "Scheduled task",
                            "snippet": _search_snippet(prompt, query),
                            "projectId": pid,
                            "projectName": project_names.get(pid, "Workspace"),
                            "taskId": str(task.get("id") or ""),
                            "scheduleType": task.get("schedule_type") or "once",
                            "scheduleValue": task.get("schedule_value") or "",
                            "nextRun": task.get("next_run") or "",
                            "category": "task_recurring" if task.get("schedule_type") != "once" else "task_once",
                        })
                        if len(groups["schedule"]) >= per_type_limit:
                            break
            except Exception:
                logger.exception("Scheduled task search failed")

            # Entity deadlines across all projects.
            if len(groups["schedule"]) < per_type_limit:
                try:
                    entities = await list_entities(_db_path, has_due_date=True, limit=500)
                    for entity in entities:
                        title = str(entity.get("title") or "")
                        content = str(entity.get("content") or "")
                        if _search_matches(query, title) or _search_matches(query, content):
                            dk = str(entity.get("project_id") or "default")
                            pid = data_key_to_project.get(dk, "")
                            groups["schedule"].append({
                                "id": str(entity.get("id") or ""),
                                "type": "schedule",
                                "title": title or "Event",
                                "snippet": _search_snippet(content or title, query),
                                "projectId": pid,
                                "projectName": project_names.get(pid, "Workspace"),
                                "entityId": str(entity.get("id") or ""),
                                "dueDate": entity.get("due_date") or "",
                                "category": "entity_due",
                            })
                            if len(groups["schedule"]) >= per_type_limit:
                                break
                except Exception:
                    logger.exception("Entity deadline search failed")
        except Exception:
            logger.exception("Workbench schedule search failed")

    return groups


# ---------------------------------------------------------------------------
# UI data builders
# ---------------------------------------------------------------------------


def _resolve_ui_tz(tz_name: str = ""):
    name = str(tz_name or "").strip()
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return datetime.now().astimezone().tzinfo or timezone.utc


async def _build_ui_data(tz_name: str = "") -> dict:
    """Assemble the full DATA payload the SPA expects."""
    sessions = _build_sessions()
    if not sessions:
        sessions = [_empty_session()]
    ui_tz = _resolve_ui_tz(tz_name)
    return {
        "user": _build_user(),
        "assistantName": ASSISTANT_NAME,
        "appVersion": get_version_label(),
        "dashboard": await _build_dashboard(ui_tz),
        "sessions": sessions,
        "status": await _build_status(),
        "skills": _build_skills(),
        "settings": _build_settings_meta(),
        "onboarding": get_onboarding_status(),
        "entities": await _build_entities_summary(),
    }


async def _build_entities_summary() -> list:
    """Return active entities for the SPA bootstrap payload."""
    try:
        from cyrene.tool_impl.entity.store import list_entities
        return await list_entities(_db_path, status="active", limit=100)
    except Exception:
        logger.exception("Failed to build entities summary")
        return []


def _build_user() -> dict:
    """User identity from the stored profile, falling back to the local account name."""
    from cyrene.runtime.settings_store import get as get_setting
    name = str(get_setting("profile_name", "") or "").strip() or _resolve_local_username()
    handle = re.sub(r"[^a-z0-9._-]+", "", name.lower().replace(" ", "")) or "user"
    parts = [part for part in re.split(r"[\s._-]+", name) if part]
    initials = "".join(part[0].upper() for part in parts[:2]) or name[:2].upper() or "U"
    return {
        "name": name,
        "handle": handle,
        "initials": initials,
        "avatar": str(get_setting("profile_avatar", "") or ""),
        "avatar_emoji": str(get_setting("profile_avatar_emoji", "") or ""),
        "avatar_color": str(get_setting("profile_avatar_color", "") or ""),
        "bio": str(get_setting("profile_bio", "") or ""),
    }


def _resolve_local_username() -> str:
    """Best-effort local account name for the current machine."""
    candidates = [
        os.environ.get("USER"),
        os.environ.get("USERNAME"),
        os.environ.get("LOGNAME"),
    ]
    try:
        candidates.append(getpass.getuser())
    except Exception:
        pass

    for candidate in candidates:
        if candidate and candidate.strip():
            return candidate.strip()

    return "user"


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


# Per-session CC preview cache — archived sessions keep their initial snapshot
_cc_preview_cache: dict[str, list] = {}


async def _delete_chat_session(session_id: str) -> tuple[dict[str, Any], int]:
    """Delete/reset a legacy chat session and return its API payload/status."""
    if session_id == "run_live":
        await clear_session_id()
        return {"ok": True, "sessions": _build_sessions()}, 200

    if session_id.startswith("archive_"):
        suffix = session_id[len("archive_"):]
        date_str, _, archive_session_id = suffix.partition("_")
        filepath = CONVERSATIONS_DIR / f"{date_str}.md"
        if not filepath.exists():
            return {"error": "session not found"}, 404
        try:
            content = filepath.read_text(encoding="utf-8")
            sections = _parse_archive_sections(content)
            kept_sections = [
                section
                for section in sections
                if str(section.get("archive_session_id", "")).strip() != archive_session_id
            ]
            if len(kept_sections) == len(sections):
                return {"error": "session not found"}, 404
            _write_archive_sections(filepath, date_str, kept_sections)
        except Exception as exc:
            return {"error": str(exc)}, 500
        return {"ok": True, "sessions": _build_sessions()}, 200

    return {"error": "unknown session id"}, 400


def _build_sessions() -> list[dict]:
    """Build session list — current state.json + parsed conversation archives."""
    sessions: list[dict] = []

    # 1. Current active session from state.json
    current = _build_current_session()
    if current:
        sessions.append(current)

    # 2. Historical sessions from conversation archives (one per day, most recent first)
    skip_archive_ids: set[str] = set()
    current_archive_session_id = str(current.get("archiveSessionId", "")).strip() if current else ""
    current_archive_date = str(current.get("archiveDate", "")).strip() if current else ""
    if current_archive_session_id and current_archive_date:
        skip_archive_ids.add(f"{current_archive_date}:{current_archive_session_id}")

    archive_sessions = _build_archive_sessions(skip_archive_ids=skip_archive_ids)
    sessions.extend(archive_sessions)

    # Per-session CC preview: live session always fresh, archives use cached snapshot
    for session in sessions:
        sid = session["id"]
        for shell in session.get("shells", []):
            if shell.get("kind") == "cc":
                if sid == "run_live":
                    _cc_preview_cache[sid] = list(shell.get("lines", []))
                elif sid in _cc_preview_cache:
                    shell["lines"] = list(_cc_preview_cache[sid])
                else:
                    _cc_preview_cache[sid] = list(shell.get("lines", []))

    return sessions


def _build_summary(raw_msgs: list[dict]) -> dict:
    usage = _usage_totals(raw_msgs)
    return {
        "tokens": _format_tokens(usage),
        "spend": _calc_messages_spend(raw_msgs),
        "toolCalls": _count_tool_calls(raw_msgs),
        "requests": usage["requests"],
        "total_tokens": usage["total_tokens"],
    }


def _build_current_session() -> dict | None:
    """Build a session object from state.json + live subagents.

    Always returns a run_live entry — when state.json is missing or empty,
    returns an empty placeholder so the Chat page shows a clean "start a new
    conversation" view instead of falling back to an old archive.
    """
    state: dict[str, Any] = {}
    raw_msgs: list[dict] = []
    if STATE_FILE.exists():
        try:
            loaded = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            state = loaded if isinstance(loaded, dict) else {}
            raw_msgs = state.get("messages", []) or []
        except Exception:
            raw_msgs = []
            state = {}

    pending_question = _ui_pending_question(state.get("pending_question", {}))
    messages = _convert_messages(raw_msgs) if raw_msgs else []
    current_round_id = _latest_round_id_from_messages(raw_msgs)
    current_round_title = next(
        (
            str(msg.get("round_title", "")).strip()
            for msg in reversed(raw_msgs)
            if str(msg.get("round_id", "")).strip() == current_round_id and msg.get("round_title")
        ),
        "",
    )

    from cyrene.subagent import registry_snapshot
    subagent_registry = _infer_subagent_entries(raw_msgs, registry_snapshot())
    subagents = []
    for agent_id, info in subagent_registry.items():
        status = info.get("status", "running")
        ui_status = {"running": "running", "waiting": "queued", "resumed": "running",
                     "done": "done", "timeout": "err", "incomplete": "err"}.get(status, status)
        created_at = info.get("created_at")
        subagents.append({
            "id": agent_id,
            "name": agent_id,
            "status": ui_status,
            "task": info.get("task", ""),
            "roundId": str(info.get("round_id", "")).strip(),
            "tokens": len(info.get("messages", [])),
            "elapsed": _elapsed_since(created_at),
            "progress": _status_progress(status),
            "result": info.get("result", ""),
            "messageCount": len(info.get("messages", [])),
            "createdAt": _short_time(created_at),
            "updatedAt": _short_time(info.get("updated_at")),
        })

    subagents.sort(key=lambda item: (item.get("createdAt") == "—", item.get("createdAt"), item["name"]))
    live_rounds = get_live_rounds()

    session_start = _session_started_at(raw_msgs)
    started_at = datetime.fromtimestamp(session_start, tz=timezone.utc).strftime("%H:%M")
    duration = _format_duration(time.time() - session_start)
    last_msg = messages[-1] if messages else None

    is_empty = not messages
    if live_rounds and any(str(item.get("status", "")) == "running" for item in live_rounds):
        live_status = "running"
    elif pending_question:
        live_status = "queued"
    elif live_rounds and any(int(item.get("pendingGuidance", 0) or 0) > 0 for item in live_rounds):
        live_status = "queued"
    elif is_empty:
        live_status = "idle"  # nothing happening yet — fresh session
    else:
        # Check if the main agent is actively processing (no live_rounds exist
        # during Phase 1/2 of the main agent loop)
        recent = debug.get_recent_events(200)
        now_ts = datetime.now(timezone.utc)
        if _has_recent_main_agent_activity(recent, now_ts):
            live_status = "running"
        else:
            live_status = "done"

    live_summary = _build_summary(raw_msgs)
    # Save main-agent-only total_tokens BEFORE merging subagent usage
    main_agent_total_tokens = live_summary.get("total_tokens")
    subagent_usage = _merge_usage_totals(*[
        _usage_totals(info.get("messages", []))
        for info in subagent_registry.values()
    ])
    combined_live_usage = _merge_usage_totals(_usage_totals(raw_msgs), subagent_usage)
    if combined_live_usage.get("requests") is not None:
        live_summary["requests"] = combined_live_usage.get("requests")
        live_summary["tokens"] = _format_tokens(combined_live_usage)
        live_summary["spend"] = _calc_messages_spend([
            *raw_msgs,
            *[
                message
                for info in subagent_registry.values()
                for message in info.get("messages", [])
            ],
        ])
        live_summary["toolCalls"] = live_summary["toolCalls"] + sum(
            _count_tool_calls(info.get("messages", []))
            for info in subagent_registry.values()
        )
        live_summary["total_tokens"] = combined_live_usage.get("total_tokens")

    # Set timestamp filter so CC preview only shows entries from this session
    set_cc_since(started_at)

    visible_shells = [] if is_empty else list_live_shells(include_exited=False)

    return {
        "id": "run_live",
        "title": str(state.get("session_title", "")).strip() or ("new session" if is_empty else "current session"),
        "status": live_status,
        "started": started_at,
        "archiveDate": datetime.now().astimezone().strftime("%Y-%m-%d"),
        "archiveSessionId": str(state.get("archive_session_id", "")).strip(),
        "dur": duration,
        "preview": (last_msg["body"][:80] + "…") if last_msg and last_msg.get("body") else "—",
        "model": _get_model(),
        "ctx_limit": _get_current_model_ctx_limit(),
        "currentRoundId": current_round_id,
        "currentRoundTitle": current_round_title,
        "pendingQuestion": pending_question,
        "summary": live_summary,
        "main_agent_total_tokens": main_agent_total_tokens,
        "main_agent_context_tokens": _last_request_context_tokens(raw_msgs),
        "chat": {
            "contextChips": _build_context_chips(),
            "messages": messages,
        },
        "liveRounds": live_rounds,
        "shells": visible_shells,
        "subagents": subagents,
        "flow": _build_live_flow(raw_msgs, messages, subagents, subagent_registry),
    }


def _build_archive_sessions(
    skip_dates: set[str] | None = None,
    skip_archive_ids: set[str] | None = None,
) -> list[dict]:
    """Build session entries from conversation archives (one per archived session)."""
    if not CONVERSATIONS_DIR.exists():
        return []

    sessions = []
    files = sorted(CONVERSATIONS_DIR.glob("*.md"), reverse=True)
    for filepath in files[:10]:  # cap at 10 most recent days
        date_str = filepath.stem
        if skip_dates and date_str in skip_dates:
            continue
        try:
            content = filepath.read_text(encoding="utf-8")
        except Exception:
            continue
        sections = _parse_archive_sections(content)
        if not sections:
            continue

        file_session_title = _parse_archive_session_title(content)
        groups: dict[str, list[dict[str, Any]]] = {}
        order: list[str] = []
        for index, section in enumerate(sections):
            archive_session_id = str(section.get("archive_session_id", "")).strip() or f"legacy_{date_str}"
            if archive_session_id not in groups:
                groups[archive_session_id] = []
                order.append(archive_session_id)
            groups[archive_session_id].append({**section, "_order": index})

        for archive_session_id in reversed(order):
            archive_key = f"{date_str}:{archive_session_id}"
            if skip_archive_ids and archive_key in skip_archive_ids:
                continue
            group_sections = groups[archive_session_id]
            messages = _messages_from_archive_sections(group_sections)
            if not messages:
                continue
            last_user = next((m for m in messages if m["role"] == "user"), None)
            group_session_title = next(
                (str(section.get("session_title", "")).strip() for section in group_sections if section.get("session_title")),
                "",
            )
            is_legacy = archive_session_id.startswith("legacy_")
            title = group_session_title or (file_session_title if is_legacy else "") or ((last_user["body"][:60] + ("…" if len(last_user["body"]) > 60 else "")) if last_user else date_str)
            preview = messages[-1].get("body", "")[:80] if messages else ""
            current_round_id = next((str(m.get("round_id", "")).strip() for m in reversed(messages) if m.get("round_id")), "")
            current_round_title = next(
                (
                    str(m.get("round_title", "")).strip()
                    for m in reversed(messages)
                    if str(m.get("round_id", "")).strip() == current_round_id and m.get("round_title")
                ),
                "",
            )

            sessions.append({
                "id": f"archive_{date_str}_{archive_session_id}",
                "title": title,
                "status": "done",
                "started": date_str,
                "dur": "—",
                "preview": preview,
                "model": _get_model(),
                "currentRoundId": current_round_id,
                "currentRoundTitle": current_round_title,
                "summary": {
                    "tokens": f"{len(messages)} msgs",
                    "spend": "—",
                    "toolCalls": 0,
                },
                "chat": {
                    "contextChips": [{"icon": "📅", "label": date_str}],
                    "messages": messages,
                },
                "liveRounds": [],
                "shells": [],
                "subagents": [],
                "flow": _build_simple_flow(messages),
            })
    return sessions


def _parse_archive_session_title(content: str) -> str:
    return _parse_archive_meta(content, "session_title")


def _parse_archive_sections(content: str) -> list[dict[str, Any]]:
    """Parse a conversations/YYYY-MM-DD.md file into archive sections with metadata."""
    sections_out: list[dict[str, Any]] = []
    round_index = 0

    for section in _split_archive_entry_blocks(content):
        if "**User**:" not in section:
            continue
        ts_match = re.search(r"##\s*(\S+\s+UTC)", section)
        dialogue_match = re.search(r"\*\*User\*\*:\s*(.*?)\n+\*\*[^*]+\*\*:\s*(.*)\Z", section, re.DOTALL)
        if not ts_match or not dialogue_match:
            continue

        ts = ts_match.group(1).strip()
        user_body = dialogue_match.group(1).strip()
        assistant_body = dialogue_match.group(2).strip()
        round_id = _parse_archive_meta(section, "round_id") or f"archive_round_{round_index}"
        round_title = _parse_archive_meta(section, "round_title")
        archive_session_id = _parse_archive_meta(section, "archive_session_id")
        session_title = _parse_archive_meta(section, "session_title")
        body_start = section.find("## ")
        raw_entry = section[body_start:].strip() if body_start >= 0 else section.strip()
        sections_out.append({
            "timestamp": ts,
            "user_body": user_body,
            "assistant_body": assistant_body,
            "round_id": round_id,
            "round_title": round_title,
            "archive_session_id": archive_session_id,
            "session_title": session_title,
            "raw_entry": raw_entry,
        })
        round_index += 1
    return sections_out


def _messages_from_archive_sections(sections: list[dict[str, Any]]) -> list[dict]:
    messages: list[dict] = []
    for index, section in enumerate(sections):
        messages.append({
            "id": f"m{index}u",
            "role": "user",
            "time": section["timestamp"],
            "body": section["user_body"],
            "round_id": section["round_id"],
            "round_title": section["round_title"],
        })
        messages.append({
            "id": f"m{index}a",
            "role": "agent",
            "time": section["timestamp"],
            "body": section["assistant_body"],
            "round_id": section["round_id"],
            "round_title": section["round_title"],
        })
    return messages


def _parse_archive_file(content: str) -> list[dict]:
    """Parse a conversations/YYYY-MM-DD.md file into UI-formatted messages."""
    return _messages_from_archive_sections(_parse_archive_sections(content))


def _write_archive_sections(filepath: Path, date_str: str, sections: list[dict[str, Any]]) -> None:
    if not sections:
        if filepath.exists():
            filepath.unlink()
        return
    first_session_title = next((str(section.get("session_title", "")).strip() for section in sections if section.get("session_title")), "")
    content = _upsert_archive_session_title(f"# Conversations - {date_str}\n\n", date_str, first_session_title)
    content += "\n---\n\n".join(section["raw_entry"] for section in sections if section.get("raw_entry")) + "\n\n---\n"
    filepath.write_text(content, encoding="utf-8")


def _is_hidden_internal_message(message: dict[str, Any]) -> bool:
    if bool(message.get("hidden_from_ui")):
        return True
    role = str(message.get("role", "")).strip()
    content = str(message.get("content", "") or "").strip()
    if role != "user" or not content:
        return False
    return (
        content.startswith("## Research Materials\n\nBelow are the research findings gathered on this question.")
        or content.startswith("[Decision-phase correction] You attempted unavailable tool(s):")
    )


def _convert_messages(raw_msgs: list[dict]) -> list[dict]:
    """Convert state.json raw messages → UI message format."""
    out = []
    compacted_marker_emitted = False
    tool_outputs = _tool_output_map(raw_msgs)
    for i, m in enumerate(raw_msgs):
        if _is_hidden_internal_message(m):
            continue
        if isinstance(m, dict) and m.get("compacted_block"):
            if not compacted_marker_emitted:
                cid = str(m.get("message_id", "")).strip() or ("compacted" + str(i))
                out.append({"id": cid, "messageId": cid, "role": "system", "kind": "compacted", "compacted": True})
                compacted_marker_emitted = True
            continue
        role = m.get("role", "")
        if role not in ("user", "assistant"):
            continue
        content = (m.get("content") or "").strip()
        has_live_detail = bool(m.get("reasoning_content") or m.get("tool_calls"))
        has_attachments = isinstance(m.get("attachments"), list) and bool(m.get("attachments"))
        if role == "user" and not content and not m.get("attachments"):
            continue
        if role == "assistant" and not content and not has_live_detail and not has_attachments:
            continue
        ui_role = "user" if role == "user" else "agent"
        message_id = str(m.get("message_id", "")).strip() or f"m{i}"
        ui_msg = {"id": message_id, "messageId": message_id, "role": ui_role, "time": "—"}
        if content:
            ui_msg["body"] = content
        if isinstance(m.get("attachments"), list):
            ui_msg["attachments"] = [
                {
                    "id": str(item.get("id") or "").strip(),
                    "name": str(item.get("name") or "file"),
                    "content_type": str(item.get("content_type") or "application/octet-stream"),
                    "size": int(item.get("size") or 0),
                    "kind": str(item.get("kind") or "file"),
                    "url": str(item.get("url") or "").strip(),
                    **({"width": int(item.get("width"))} if str(item.get("width", "")).strip().isdigit() else {}),
                    **({"height": int(item.get("height"))} if str(item.get("height", "")).strip().isdigit() else {}),
                }
                for item in m.get("attachments")
                if isinstance(item, dict)
            ]
        if bool(m.get("intermediate_reply")):
            ui_msg["intermediateReply"] = True
        if bool(m.get("question_prompt")):
            ui_msg["questionPrompt"] = True
        question_id = str(m.get("question_id", "")).strip()
        if question_id:
            ui_msg["questionId"] = question_id
        round_id = str(m.get("round_id", "")).strip()
        if round_id:
            ui_msg["roundId"] = round_id
        client_request_id = str(m.get("client_request_id", "")).strip()
        if client_request_id:
            ui_msg["clientRequestId"] = client_request_id
        queued_guidance_id = str(m.get("queued_guidance_id", "")).strip()
        if queued_guidance_id:
            ui_msg["queuedGuidanceId"] = queued_guidance_id
        guidance_ack_for_guidance_id = str(m.get("guidance_ack_for_guidance_id", "")).strip()
        if guidance_ack_for_guidance_id:
            ui_msg["guidanceAckForGuidanceId"] = guidance_ack_for_guidance_id
        in_reply_to_guidance_id = str(m.get("in_reply_to_guidance_id", "")).strip()
        if in_reply_to_guidance_id:
            ui_msg["inReplyToGuidanceId"] = in_reply_to_guidance_id
        if m.get("reasoning_content"):
            ui_msg["thinking"] = m["reasoning_content"]
        if m.get("tool_calls"):
            tools = []
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                raw_args = fn.get("arguments", "")
                parsed_args = _safe_json_loads(raw_args) if isinstance(raw_args, str) else raw_args
                args = raw_args
                if isinstance(args, str) and len(args) > 80:
                    args = args[:80] + "…"
                tool_call_id = str(tc.get("id") or "")
                tools.append({
                    "name": fn.get("name", "?"),
                    "arg": str(args)[:120],
                    "status": "done",
                    "out": tool_outputs.get(tool_call_id, ""),
                    "toolCallId": tool_call_id,
                    "rawArgs": parsed_args if parsed_args is not None else raw_args,
                })
            ui_msg["tools"] = tools
        out.append(ui_msg)
    return _collapse_duplicate_user_messages(
        _merge_adjacent_trace_only_messages(_dedupe_repeated_messages(out))
    )


def _session_started_at(raw_msgs: list[dict]) -> float:
    return _session_view_started_at(raw_msgs, _SERVER_STARTED_AT)


def _build_simple_flow(messages: list[dict]) -> dict:
    """Archive flow grouped by conversation round, without live tool traces."""
    rounds: list[list[dict]] = []
    current: list[dict] = []
    current_round_id = ""

    for msg in messages:
        round_id = str(msg.get("round_id", "")).strip() or current_round_id or "archive_round_0"
        if current and round_id != current_round_id:
            rounds.append(current)
            current = []
        current.append(msg)
        current_round_id = round_id
    if current:
        rounds.append(current)

    nodes: list[dict] = []
    edges: list[dict] = []
    y_offset = 0
    multiple_rounds = len(rounds) > 1

    for round_index, round_msgs in enumerate(rounds or [messages]):
        prefix = f"r{round_index}_" if multiple_rounds else ""
        last_user = next((m for m in round_msgs if m["role"] == "user"), None)
        last_agent = next((m for m in reversed(round_msgs) if m["role"] == "agent"), None)
        round_title = next((str(m.get("round_title", "")).strip() for m in round_msgs if m.get("round_title")), "") or "user request"
        user_id = f"{prefix}n_user"
        main_id = f"{prefix}n_main"
        out_id = f"{prefix}n_out"

        nodes.extend([
            {
                "id": user_id, "kind": "input", "x": 40, "y": y_offset + 80,
                "title": round_title, "status": "done",
                "detail": {
                    "role": "User",
                    "text": last_user["body"] if last_user else "",
                    "tokens": 0,
                    "time": last_user["time"] if last_user else "—",
                },
            },
            {
                "id": main_id, "kind": "main", "x": 320, "y": y_offset + 70,
                "title": f"main agent · {ASSISTANT_NAME}",
                "subtitle": "archive",
                "status": "done",
                "model": _get_model(),
                "detail": {
                    "systemPrompt": f"You are {ASSISTANT_NAME}, an AI companion. Use SOUL.md to maintain persona.",
                    "reasoning": "Loaded session from archive — no live reasoning trace.",
                    "tokensIn": 0, "tokensOut": 0,
                    "model": _get_model(), "temp": 0.2,
                },
            },
            {
                "id": out_id, "kind": "output", "x": 660, "y": y_offset + 90,
                "title": "response", "status": "done",
                "detail": {
                    "kind": "Output",
                    "content": (last_agent["body"][:600] if last_agent else "—"),
                },
            },
        ])
        edges.extend([
            {"from": user_id, "to": main_id},
            {"from": main_id, "to": out_id},
        ])
        y_offset += 180

    return {"nodes": nodes, "edges": edges}


def _build_live_flow(raw_msgs: list[dict], messages: list[dict], subagents: list[dict], registry: dict[str, dict]) -> dict:
    """Build a richer flow for the current session, stacked by conversation round."""
    rounds = _split_raw_rounds(raw_msgs)
    recent_events = debug.get_recent_events(250)
    if not rounds and raw_msgs:
        rounds = [raw_msgs]
    if not rounds:
        synthetic_round = _synthetic_live_round(registry, recent_events)
        if synthetic_round:
            rounds = [synthetic_round]
    if not rounds:
        return {"nodes": [], "edges": []}

    rounds, active_round_index = _prune_flow_rounds(rounds)
    if not rounds:
        return {"nodes": [], "edges": []}

    nodes: list[dict] = []
    edges: list[dict] = []
    next_y = 0
    multiple_rounds = len(rounds) > 1

    for round_index, round_raw in enumerate(rounds):
        is_current_round = round_index == active_round_index
        round_messages = _convert_messages(round_raw)
        round_id = _latest_round_id_from_messages(round_raw)
        round_registry = _round_registry_for_flow(round_raw, registry if is_current_round else {})
        related_agents = _related_round_agent_names(set(round_registry), round_id=round_id)
        if is_current_round and subagents:
            candidate_subagents = [
                sa for sa in subagents
                if _subagent_matches_round(sa, round_id) and (not round_registry or sa["name"] in related_agents)
            ]
            for sa in candidate_subagents:
                entry = round_registry.setdefault(sa["name"], {
                    "task": sa.get("task", ""),
                    "status": "done",
                    "result": sa.get("result", ""),
                    "messages": [],
                    "created_at": None,
                    "updated_at": None,
                    "round_id": round_id,
                })
                entry["task"] = entry.get("task") or sa.get("task", "")
                entry["status"] = _registry_status_from_ui(sa.get("status", entry.get("status", "done")))
                entry["result"] = entry.get("result") or sa.get("result", "")
        if is_current_round and not round_registry and registry:
            round_registry = {
                agent_id: dict(info)
                for agent_id, info in registry.items()
                if not round_id or info.get("round_id") in ("", round_id)
            }
        round_subagents = _subagent_cards_from_registry(round_registry)
        round_recent_events = _events_for_round(recent_events, round_id) if is_current_round else []
        prefix = f"r{round_index}_" if multiple_rounds else ""
        round_nodes, round_edges, round_bottom = _build_live_flow_round(
            prefix=prefix,
            raw_msgs=round_raw,
            messages=round_messages,
            subagents=round_subagents,
            registry=round_registry,
            recent_events=round_recent_events,
            y_offset=next_y,
            round_id=round_id,
        )
        nodes.extend(round_nodes)
        edges.extend(round_edges)
        next_y = round_bottom + 180

    return {"nodes": nodes, "edges": edges}


def _synthetic_live_round(registry: dict[str, dict], recent_events: list[dict]) -> list[dict]:
    if not registry:
        return []
    round_id = next((str(info.get("round_id", "")).strip() for info in registry.values() if info.get("round_id")), "")
    latest_phase = next((e for e in reversed(recent_events) if e.get("type") == "phase_transition"), None)
    latest_llm = next((e for e in reversed(recent_events) if e.get("type") == "llm_call" and e.get("caller") == "main_agent"), None)
    prompt = (
        latest_phase.get("detail")
        if latest_phase and latest_phase.get("detail")
        else latest_llm.get("response")
        if latest_llm and latest_llm.get("response")
        else "Live round in progress"
    )
    entry: dict[str, Any] = {"role": "user", "content": prompt}
    if round_id:
        entry["round_id"] = round_id
    return [entry]


def _round_registry_for_flow(raw_msgs: list[dict], live_registry: dict[str, dict]) -> dict[str, dict]:
    round_id = _latest_round_id_from_messages(raw_msgs)
    entries: dict[str, dict] = _snapshot_entries_from_messages(raw_msgs, round_id=round_id)
    for msg in raw_msgs:
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            if fn.get("name") != "spawn_subagent":
                continue
            args = _safe_json_loads(fn.get("arguments") or "{}")
            if not isinstance(args, dict):
                continue
            agent_id = str(args.get("agent_id") or "").strip()
            if not agent_id:
                continue
            live = dict(live_registry.get(agent_id, {}))
            if round_id and live.get("round_id") and live.get("round_id") != round_id:
                live = {}
            task = str(args.get("task") or live.get("task") or "")
            _merge_subagent_record(entries, agent_id, {
                "task": task,
                "status": live.get("status", entries.get(agent_id, {}).get("status", "done")),
                "result": live.get("result", entries.get(agent_id, {}).get("result", "")),
                "messages": list(live.get("messages", [])) or list(entries.get(agent_id, {}).get("messages", [])),
                "created_at": live.get("created_at", entries.get(agent_id, {}).get("created_at")),
                "updated_at": live.get("updated_at", entries.get(agent_id, {}).get("updated_at")),
                "round_id": round_id or live.get("round_id", entries.get(agent_id, {}).get("round_id", "")),
            })
    for agent_id, live in live_registry.items():
        live_round_id = str(live.get("round_id", "")).strip()
        if round_id and live_round_id and live_round_id != round_id:
            continue
        _merge_subagent_record(entries, agent_id, {
            "task": live.get("task", ""),
            "status": live.get("status", "done"),
            "result": live.get("result", ""),
            "messages": list(live.get("messages", [])),
            "created_at": live.get("created_at"),
            "updated_at": live.get("updated_at"),
            "round_id": round_id or live_round_id,
        })
    return entries


def _related_round_agent_names(seed_ids: set[str], round_id: str = "") -> set[str]:
    if not seed_ids:
        return set()
    related = set(seed_ids)
    inbox_root = DATA_DIR / "inbox"
    if not inbox_root.exists():
        return related

    changed = True
    while changed:
        changed = False
        for msg_file in inbox_root.glob("*/*.json"):
            try:
                payload = json.loads(msg_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if round_id and str(payload.get("round_id", "")) != round_id:
                continue
            from_agent = str(payload.get("from", ""))
            to_agent = str(payload.get("to", ""))
            if from_agent in related or to_agent in related:
                size_before = len(related)
                if from_agent:
                    related.add(from_agent)
                if to_agent:
                    related.add(to_agent)
                changed = changed or len(related) != size_before
    return related


def _round_id_from_messages(raw_msgs: list[dict]) -> str:
    for msg in raw_msgs:
        round_id = str(msg.get("round_id", "")).strip()
        if round_id:
            return round_id
    return ""


def _latest_round_id_from_messages(raw_msgs: list[dict]) -> str:
    for msg in reversed(raw_msgs):
        round_id = str(msg.get("round_id", "")).strip()
        if round_id:
            return round_id
    return ""


def _events_for_round(recent_events: list[dict], round_id: str) -> list[dict]:
    if not round_id:
        return list(recent_events)
    return [
        event for event in recent_events
        if str(event.get("round_id", "")).strip() == round_id
    ]


def _subagent_matches_round(subagent: dict[str, Any], round_id: str) -> bool:
    if not round_id:
        return True
    subagent_round_id = str(subagent.get("roundId") or subagent.get("round_id") or "").strip()
    return not subagent_round_id or subagent_round_id == round_id


def _registry_status_from_ui(status: str) -> str:
    return {
        "running": "running",
        "queued": "waiting",
        "done": "done",
        "err": "timeout",
    }.get(status, status)


def _is_summary_agent_id(agent_id: str) -> bool:
    return str(agent_id or "").startswith("agent_summary_")


def _iter_flow_snapshots(raw_msgs: list[dict], round_id: str = "") -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for msg in raw_msgs:
        snapshot = msg.get("subagent_flow_snapshot")
        if not isinstance(snapshot, dict):
            continue
        snapshot_round_id = str(snapshot.get("round_id", "")).strip() or str(msg.get("round_id", "")).strip()
        if round_id and snapshot_round_id and snapshot_round_id != round_id:
            continue
        snapshots.append(snapshot)
    return snapshots


def _merge_subagent_record(entries: dict[str, dict[str, Any]], agent_id: str, meta: dict[str, Any]) -> None:
    incoming = dict(meta)
    incoming_round_id = str(incoming.get("round_id", "")).strip()
    existing = entries.get(agent_id)
    if existing is None:
        entries[agent_id] = incoming
        return

    existing_round_id = str(existing.get("round_id", "")).strip()
    if incoming_round_id and existing_round_id and incoming_round_id != existing_round_id:
        entries[agent_id] = incoming
        return

    merged = dict(existing)
    for key, value in incoming.items():
        if key == "messages":
            if value:
                merged["messages"] = value
            else:
                merged.setdefault("messages", [])
            continue
        if value not in (None, "", []):
            merged[key] = value
        else:
            merged.setdefault(key, value)
    entries[agent_id] = merged


def _snapshot_entries_from_messages(raw_msgs: list[dict], round_id: str = "") -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for snapshot in _iter_flow_snapshots(raw_msgs, round_id=round_id):
        agents = snapshot.get("agents") or {}
        if not isinstance(agents, dict):
            continue
        snapshot_round_id = str(snapshot.get("round_id", "")).strip()
        for agent_id, info in agents.items():
            if not isinstance(info, dict):
                continue
            meta = dict(info)
            meta.setdefault("round_id", snapshot_round_id)
            meta.setdefault("messages", [])
            _merge_subagent_record(entries, str(agent_id), meta)
    return entries


def _snapshot_comm_messages_from_messages(raw_msgs: list[dict], round_id: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for snapshot in _iter_flow_snapshots(raw_msgs, round_id=round_id):
        comm_messages = snapshot.get("comm_messages") or []
        if not isinstance(comm_messages, list):
            continue
        for item in comm_messages:
            if not isinstance(item, dict):
                continue
            from_agent = str(item.get("from", "")).strip()
            to_agent = str(item.get("to", "")).strip()
            body = str(item.get("content", ""))
            message_id = str(item.get("message_id") or "").strip()
            dedupe_key = (message_id, from_agent, to_agent, body)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            items.append(dict(item))
    items.sort(key=lambda item: str(item.get("timestamp") or ""))
    return items


def _subagent_cards_from_registry(round_registry: dict[str, dict]) -> list[dict]:
    cards: list[dict] = []
    for agent_id, info in round_registry.items():
        status = info.get("status", "done")
        ui_status = {"running": "running", "waiting": "queued", "resumed": "running",
                     "done": "done", "timeout": "err", "incomplete": "err"}.get(status, status)
        created_at = info.get("created_at")
        cards.append({
            "id": agent_id,
            "name": agent_id,
            "status": ui_status,
            "task": info.get("task", ""),
            "tokens": len(info.get("messages", [])),
            "elapsed": _elapsed_since(created_at),
            "progress": _status_progress(status),
            "result": info.get("result", ""),
            "messageCount": len(info.get("messages", [])),
            "createdAt": _short_time(created_at),
            "updatedAt": _short_time(info.get("updated_at")),
        })
    return cards


def _build_live_flow_round(
    prefix: str,
    raw_msgs: list[dict],
    messages: list[dict],
    subagents: list[dict],
    registry: dict[str, dict],
    recent_events: list[dict],
    y_offset: int,
    round_id: str,
) -> tuple[list[dict], list[dict], int]:
    main_x = 320
    main_y = y_offset + 70
    main_tool_x = 600
    subagent_x = 900
    subagent_tool_x = 1220
    output_x = 1540
    subagent_base_y = y_offset + 40
    subagent_gap_y = 220

    last_user = next((m for m in messages if m["role"] == "user"), None)
    latest_main_llm = next((e for e in reversed(recent_events) if e.get("type") == "llm_call" and e.get("caller") == "main_agent"), None)
    latest_phase = next((e for e in reversed(recent_events) if e.get("type") == "phase_transition"), None)
    latest_agent = next((m for m in reversed(messages) if m["role"] == "agent"), None)
    latest_assistant_raw = next((m for m in reversed(raw_msgs) if m.get("role") == "assistant"), None)
    round_title = next((str(m.get("round_title", "")).strip() for m in raw_msgs if m.get("round_title")), "") or "user request"
    system_initiated = any(bool(m.get("system_initiated")) for m in raw_msgs if isinstance(m, dict))
    if system_initiated and round_title == "user request":
        round_title = "proactive check-in"
    main_usage = _usage_totals(raw_msgs)
    main_tool_base_y = main_y + 150

    main_id = f"{prefix}n_main"
    user_id = f"{prefix}n_user"
    output_id = f"{prefix}n_out"
    main_completed = bool(latest_agent)

    _llm_resp = latest_main_llm.get("response") if latest_main_llm else None
    _llm_text = (
        str(_llm_resp.get("reasoning_content") or _llm_resp.get("content") or "")
        if isinstance(_llm_resp, dict) else ""
    )
    _main_reasoning = (
        str(latest_assistant_raw.get("reasoning_content") or "")
        if latest_assistant_raw and latest_assistant_raw.get("reasoning_content")
        else _llm_text
        if _llm_text
        else str(latest_phase.get("detail") or "")
        if latest_phase and latest_phase.get("detail")
        else "Session step completed."
    )

    tool_nodes, tool_edges = _build_tool_nodes_for_owner(
        owner_node_id=main_id,
        owner_title=f"main agent · {ASSISTANT_NAME}",
        owner_x=main_x,
        owner_y=main_y,
        raw_messages=raw_msgs,
        recent_events=recent_events,
        caller_prefix="main_agent",
        x=main_tool_x,
        base_y=main_tool_base_y,
        owner_completed=main_completed,
    )
    main_status = (
        "running"
        if any(sa["status"] == "running" for sa in subagents) or any(node["status"] == "running" for node in tool_nodes)
        else ("done" if main_completed else "queued")
    )

    nodes = [
        {
            "id": main_id, "kind": "main", "x": main_x, "y": main_y,
            "title": f"main agent · {ASSISTANT_NAME}",
            "subtitle": latest_phase["to"] if latest_phase and latest_phase.get("to") else "orchestrator",
            "status": main_status,
            "model": _get_model(),
            "detail": {
                "systemPrompt": (
                    f"You are {ASSISTANT_NAME}. Two-phase loop: one fixed wire bundle, "
                    "Phase 1 policy gating, then progressive module discovery in Phase 2. "
                    "Chat filter applies SOUL.md voice."
                ),
                "reasoning": _main_reasoning,
                "tokensIn": main_usage.get("prompt_tokens") or "—",
                "tokensOut": main_usage.get("completion_tokens") or "—",
                "model": _get_model(), "temp": 0.2,
            },
        },
    ]
    edges: list[dict[str, Any]] = []
    if last_user and not system_initiated:
        user_text = str(last_user.get("body") or "").strip() or (
            "[Uploaded attachment]"
            if last_user.get("attachments")
            else "—"
        )
        nodes.insert(0, {
            "id": user_id, "kind": "input", "x": 40, "y": y_offset + 80,
            "title": round_title, "status": "done",
            "detail": {
                "role": "User",
                "text": user_text,
                "tokens": 0,
                "time": last_user["time"] if last_user else "—",
            },
        })
        edges.append({"from": user_id, "to": main_id, "kind": "active" if main_status == "running" else None})
    nodes.extend(tool_nodes)
    edges.extend(tool_edges)

    agent_node_ids: dict[str, str] = {}
    subagent_bottoms: list[int] = []
    subagent_y = subagent_base_y
    for i, sa in enumerate(subagents):
        nid = f"{prefix}n_sa_{i}"
        agent_node_ids[sa["name"]] = nid
        is_summary_agent = _is_summary_agent_id(sa["name"])
        info = registry.get(sa["name"], {})
        agent_messages = info.get("messages", [])
        latest_subassistant = next((m for m in reversed(agent_messages) if m.get("role") == "assistant"), None)
        sub_usage = _usage_totals(agent_messages)
        sub_tool_count = _count_tool_nodes_for_owner(
            raw_messages=agent_messages,
            recent_events=recent_events,
            caller_prefix=f"subagent_{sa['name']}",
        )
        nodes.append({
            "id": nid, "kind": "subagent",
            "x": subagent_x, "y": subagent_y,
            "title": f"{'summary subagent' if is_summary_agent else 'subagent'} · {sa['name']}",
            "subtitle": ("synthesizer" if is_summary_agent else sa["task"][:30]),
            "status": sa["status"],
            "detail": {
                "name": sa["name"],
                "task": sa["task"],
                "parent": "main agent",
                "role": "summary" if is_summary_agent else "worker",
                "spawnedAt": sa.get("createdAt", "—"),
                "tokensIn": sub_usage.get("prompt_tokens") or "—",
                "tokensOut": sub_usage.get("completion_tokens") or "—",
                "model": _get_model(),
                "reasoning": latest_subassistant.get("reasoning_content") if latest_subassistant else "",
                "result": sa.get("result", ""),
            },
        })
        edges.append({
            "from": main_id,
            "to": nid,
            "kind": "dashed" if is_summary_agent else ("active" if sa["status"] == "running" else None),
        })

        sub_nodes, sub_edges = _build_tool_nodes_for_owner(
            owner_node_id=nid,
            owner_title=f"subagent · {sa['name']}",
            owner_x=subagent_x,
            owner_y=subagent_y,
            raw_messages=agent_messages,
            recent_events=recent_events,
            caller_prefix=f"subagent_{sa['name']}",
            x=subagent_tool_x,
            base_y=subagent_y,
            owner_completed=sa["status"] in {"done", "err"},
        )
        nodes.extend(sub_nodes)
        edges.extend(sub_edges)
        lane_height = _agent_lane_height(sub_tool_count)
        subagent_bottoms.append(subagent_y + lane_height)
        subagent_y += lane_height + subagent_gap_y

    summary_agent_name = next((name for name in agent_node_ids if _is_summary_agent_id(name)), "")
    if summary_agent_name:
        summary_node_id = agent_node_ids[summary_agent_name]
        for agent_name, node_id in agent_node_ids.items():
            if agent_name == summary_agent_name:
                continue
            edges.append({"from": node_id, "to": summary_node_id, "kind": "dashed"})

    edges.extend(_build_comm_edges(
        agent_node_ids,
        agent_entries=registry,
        round_id=round_id,
        persisted_messages=_snapshot_comm_messages_from_messages(raw_msgs, round_id=round_id),
    ))

    output_content = str(latest_agent.get("body") or "") if latest_agent else ""
    output_status = "done" if output_content else ("running" if subagents else "queued")
    if output_content or subagents:
        flow_bottom = max(subagent_bottoms) if subagent_bottoms else (main_tool_base_y + _agent_lane_height(max(1, len(tool_nodes))))
        output_y = y_offset + 90 if not subagents else max(y_offset + 90, int((main_y + flow_bottom) / 2) - 43)
        nodes.append({
            "id": output_id, "kind": "output", "x": output_x, "y": output_y,
            "title": "response", "status": output_status,
            "detail": {
                "kind": "Output",
                "content": output_content or "Waiting for subagent synthesis…",
            },
        })
        edges.append({
            "from": main_id,
            "to": output_id,
            "kind": "active" if output_status == "running" else None,
        })
        if summary_agent_name:
            edges.append({
                "from": agent_node_ids[summary_agent_name],
                "to": output_id,
                "kind": "dashed",
            })

    bottom = max((node["y"] + 86) for node in nodes) if nodes else y_offset
    return nodes, edges, bottom


def _empty_session() -> dict:
    """Placeholder when no real session exists yet."""
    return {
        "id": "run_empty",
        "title": "no active session",
        "status": "queued",
        "started": "—",
        "dur": "—",
        "preview": "Send a message to start a session.",
        "model": _get_model(),
        "summary": {"tokens": "0", "spend": "$0.00", "toolCalls": 0},
        "chat": {
            "contextChips": _build_context_chips(),
            "messages": [],
        },
        "liveRounds": [],
        "shells": [],
        "subagents": [],
        "flow": {
            "nodes": [
                {
                    "id": "n_main", "kind": "main", "x": 200, "y": 80,
                    "title": f"main agent · {ASSISTANT_NAME}",
                    "subtitle": "idle", "status": "queued",
                    "model": _get_model(),
                    "detail": {
                        "systemPrompt": f"You are {ASSISTANT_NAME}.",
                        "reasoning": "Waiting for user input.",
                        "tokensIn": 0, "tokensOut": 0,
                        "model": _get_model(), "temp": 0.2,
                    },
                }
            ],
            "edges": [],
        },
    }


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


async def _build_status() -> dict:
    """Status data for the Status / Dashboard page."""
    return {
        "phase": "evolve",
        "state": "进化",
        "metrics": [],
        "sparkData": [],
        "workers": [],
        "logs": [],
        "services": [],
        "model": _get_model(),
        "base_url": _get_base_url(),
        "short_term_entries": 0,
        "session_messages": 0,
        "scheduled_tasks": 0,
        "soul_exists": SOUL_PATH.exists(),
    }


async def _build_memory() -> dict:
    """Assemble full memory state for the Memory page."""
    import re
    from datetime import datetime, timezone

    # --- SOUL.md ---
    soul_content = read_soul()
    soul_exists = bool(soul_content)
    sections: list[dict] = []
    current_section: dict | None = None
    temporary_count = 0
    temporary_expired = 0
    now = datetime.now(timezone.utc)

    for line in soul_content.splitlines() if soul_content else []:
        trimmed = line.strip()
        if trimmed.startswith("## ") and not trimmed.startswith("### "):
            if current_section:
                sections.append(current_section)
            name = trimmed[3:].strip()
            current_section = {"name": name, "entries": [], "entry_count": 0}
        elif current_section is not None:
            if trimmed and not trimmed.startswith("<!--"):
                current_section["entries"].append(trimmed)
                current_section["entry_count"] += 1
                if current_section["name"] == "TEMPORARY":
                    temporary_count += 1
                    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", trimmed)
                    if date_match:
                        try:
                            item_date = datetime.strptime(date_match.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                            if (now - item_date).days >= 1:
                                temporary_expired += 1
                        except ValueError:
                            pass
    if current_section:
        sections.append(current_section)

    # --- Short-term memory ---
    st_entries = load_entries()
    short_term = {
        "entries": sorted(st_entries, key=lambda e: e.get("last_mentioned", ""), reverse=True),
        "total": len(st_entries),
    }

    # --- Context window ---
    session_msgs: list = []
    if STATE_FILE.exists():
        try:
            session_msgs = json.loads(STATE_FILE.read_text(encoding="utf-8")).get("messages", [])
        except Exception:
            session_msgs = []
    from cyrene.runtime.config_store import get_current_ctx_limit
    from cyrene.model_runtime.client import message_token_estimate
    _ctx_limit = get_current_ctx_limit()
    context_window = {
        "messages": len(session_msgs),
        "max": 40,
        "tokens": sum(message_token_estimate(m) for m in session_msgs) if session_msgs else 0,
        "ctx_limit": _ctx_limit,
        "trigger_tokens": int(_ctx_limit * 0.6) if _ctx_limit else 0,
        "compacted_blocks": sum(1 for m in session_msgs if isinstance(m, dict) and m.get("compacted_block")),
    }

    # --- Conversation archive ---
    archive_days = 0
    today_exchanges = 0
    if CONVERSATIONS_DIR.exists():
        archive_files = sorted(CONVERSATIONS_DIR.glob("*.md"))
        archive_days = len(archive_files)
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_file = CONVERSATIONS_DIR / f"{today_str}.md"
        if today_file.exists():
            try:
                raw = today_file.read_text(encoding="utf-8")
                today_exchanges = raw.count("## ") - 1
            except Exception:
                pass

    return {
        "soul": {
            "exists": soul_exists,
            "path": str(get_soul_path()),
            "sections": sections,
            "temporary_count": temporary_count,
            "temporary_expired": temporary_expired,
        },
        "short_term": short_term,
        "context_window": context_window,
        "archive": {
            "days": archive_days,
            "today_exchanges": max(0, today_exchanges),
        },
    }


async def _build_dashboard(ui_tz=None) -> dict:
    """Aggregate homepage data from memory, soul, archive, and scheduler state."""
    from cyrene.runtime import database as cy_db
    from cyrene.subagent import registry_snapshot
    subagent_registry = registry_snapshot()

    ui_tz = ui_tz or (datetime.now().astimezone().tzinfo or timezone.utc)
    now_local = datetime.now(ui_tz)

    st_entries = load_entries()
    try:
        tasks = await cy_db.get_all_tasks(_db_path)
    except Exception:
        tasks = []

    today = now_local.strftime("%Y-%m-%d")
    soul_content = read_soul()
    soul_path = get_soul_path()
    soul_stat = soul_path.stat() if soul_path.exists() else None
    soul_lines = [line.strip() for line in soul_content.splitlines() if line.strip().startswith("- ")]
    recent_soul_items = soul_lines[-3:]
    recent_memories = sorted(
        st_entries,
        key=lambda entry: (str(entry.get("last_mentioned", "")), int(entry.get("mention_count", 0))),
        reverse=True,
    )[:6]

    today_entries = [
        entry for entry in st_entries
        if str(entry.get("last_mentioned", "")).strip() == today
    ]
    learned_today = sorted(
        today_entries,
        key=lambda entry: (int(entry.get("mention_count", 0)), abs(int(entry.get("emotional_valence", 0)))),
        reverse=True,
    )[:4]

    session_msgs: list[dict[str, Any]] = []
    if STATE_FILE.exists():
        try:
            session_state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            session_msgs = session_state.get("messages", []) if isinstance(session_state, dict) else []
        except Exception:
            session_msgs = []
    session_usage = _usage_totals(session_msgs)
    subagent_usage = _merge_usage_totals(*[
        _usage_totals(info.get("messages", []))
        for info in subagent_registry.values()
    ])
    reminder_items = []
    for task in sorted(tasks, key=lambda item: str(item.get("next_run") or "")):
        next_run = str(task.get("next_run") or "").strip()
        status = str(task.get("status") or "").strip()
        if not next_run or status not in {"active", "paused"}:
            continue
        reminder_items.append({
            "id": str(task.get("id") or ""),
            "prompt": str(task.get("prompt") or "").strip(),
            "next_run": next_run,
            "schedule_type": str(task.get("schedule_type") or "").strip(),
            "status": status,
        })
    reminder_items = reminder_items[:6]

    archive_snippets: list[dict[str, Any]] = []
    for filepath in sorted(CONVERSATIONS_DIR.glob("*.md"), reverse=True)[:7]:
        date_str = filepath.stem
        try:
            sections = _parse_archive_sections(filepath.read_text(encoding="utf-8"))
        except Exception:
            continue
        for section in reversed(sections):
            user_body = str(section.get("user_body", "")).strip()
            assistant_body = str(section.get("assistant_body", "")).strip()
            if user_body or assistant_body:
                archive_snippets.append({
                    "date": date_str,
                    "title": str(section.get("round_title") or section.get("session_title") or "").strip(),
                    "user": user_body,
                    "assistant": assistant_body,
                })
    archive_snippets = archive_snippets[:6]

    hist_days = 27
    day_from = (now_local - timedelta(days=hist_days)).strftime("%Y-%m-%d")
    day_to = today
    stats_rows = await cy_db.get_daily_stats_range(_db_path, day_from, day_to)
    stats_by_day = {
        str(row.get("day") or ""): row
        for row in stats_rows
        if str(row.get("day") or "").strip()
    }
    model_stats_rows = await cy_db.get_model_stats_range(_db_path, day_from, day_to)
    topic_rows = await cy_db.get_topic_counts_range(_db_path, day_from, day_to, limit=18)
    tool_rows = await cy_db.get_tool_counts_range(_db_path, day_from, day_to, limit=5)
    task_time = await cy_db.get_task_time_totals(_db_path)
    archive_day_count = await cy_db.count_stat_days(_db_path)

    # 从 daily_stats 汇总全量历史数据（与 timeline 同源）
    historical_prompt = sum((r.get("prompt_tokens") or 0) for r in stats_by_day.values())
    historical_completion = sum((r.get("completion_tokens") or 0) for r in stats_by_day.values())
    historical_total = sum((r.get("total_tokens") or 0) for r in stats_by_day.values())
    historical_cache_hit = sum((r.get("cache_hit_tokens") or 0) for r in stats_by_day.values())
    historical_cache_miss = sum((r.get("cache_miss_tokens") or 0) for r in stats_by_day.values())
    historical_requests = sum((r.get("llm_requests") or 0) for r in stats_by_day.values())

    # 按模型计算总花费（不同模型定价不同）。默认价格以人民币计算；
    # 显式 $ 配价会先按美元估算，再折算成人民币供 UI 统一切换显示。
    from cyrene.model_runtime.pricing import CNY_PER_USD, effective_price, estimate_cost

    total_spend_cny = 0.0
    total_spend_usd = 0.0
    for row in model_stats_rows:
        mdl = str(row.get("model") or "").strip().lower()
        pt = int(row.get("prompt_tokens") or 0)
        ct = int(row.get("completion_tokens") or 0)
        pricing = effective_price(mdl)
        cost = estimate_cost(pricing, pt, ct)
        if str(pricing.get("currency") or "CNY").upper() == "USD":
            total_spend_usd += cost
            total_spend_cny += cost * CNY_PER_USD
        else:
            total_spend_cny += cost
            total_spend_usd += cost / CNY_PER_USD
    spend_str = "<¥0.01" if 0 < total_spend_cny < 0.01 else f"¥{total_spend_cny:.2f}"

    # 情感数据从 short_term 条目按 last_mentioned 日期聚合，不依赖数据库
    emotion_by_day: dict[str, list[float]] = {}
    for entry in st_entries:
        day = str(entry.get("last_mentioned", "")).strip()
        if day:
            valence = int(entry.get("emotional_valence", 0) or 0)
            emotion_by_day.setdefault(day, []).append(valence)

    emotion_series = []
    for offset in range(hist_days, -1, -1):
        day = (now_local - timedelta(days=offset)).strftime("%Y-%m-%d")
        vals = emotion_by_day.get(day, [])
        avg = round(sum(vals) / len(vals), 2) if vals else 0.0
        emotion_series.append({
            "date": day,
            "value": avg,
            "count": len(vals),
        })

    token_timeline: dict[str, dict[str, int]] = {}
    for offset in range(hist_days, -1, -1):
        day = (now_local - timedelta(days=offset)).strftime("%Y-%m-%d")
        row = stats_by_day.get(day) or {}
        token_timeline[day] = {
            "prompt": int(row.get("prompt_tokens") or 0),
            "completion": int(row.get("completion_tokens") or 0),
            "requests": int(row.get("llm_requests") or 0),
        }

    heatmap_days = [
        (now_local - timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in range(hist_days, -1, -1)
    ]
    heatmap_row_defs = [
        ("00:00", 0, 4),
        ("04:00", 4, 8),
        ("08:00", 8, 12),
        ("12:00", 12, 16),
        ("16:00", 16, 20),
        ("20:00", 20, 24),
    ]
    heatmap_column_map = {
        "00:00": "activity_00_04",
        "04:00": "activity_04_08",
        "08:00": "activity_08_12",
        "12:00": "activity_12_16",
        "16:00": "activity_16_20",
        "20:00": "activity_20_24",
    }
    heatmap_buckets: dict[str, list[int]] = {}
    for label, _, _ in heatmap_row_defs:
        column = heatmap_column_map[label]
        heatmap_buckets[label] = [
            int((stats_by_day.get(day) or {}).get(column) or 0)
            for day in heatmap_days
        ]

    activity_heatmap = {
        "days": heatmap_days,
        "rows": [
            {"label": label, "values": heatmap_buckets[label]}
            for label, _, _ in heatmap_row_defs
        ],
    }

    return {
        "today": {
            "learned": learned_today,
            "learned_count": len(today_entries),
            "memory_count": len(st_entries),
            "archive_days": archive_day_count,
        },
        "soul": {
            "path": str(soul_path),
            "updated_at": datetime.fromtimestamp(soul_stat.st_mtime, tz=timezone.utc).isoformat() if soul_stat else "",
            "recent_items": recent_soul_items,
            "section_count": soul_content.count("\n## ") + (1 if soul_content.strip().startswith("# ") else 0),
        },
        "topic_cloud": topic_rows,
        "emotion": emotion_series,
        "usage": {
            "requests": historical_requests,
            "tokens": _format_tokens({
                "prompt_tokens": historical_prompt,
                "completion_tokens": historical_completion,
                "total_tokens": historical_total,
            }),
            "spend": spend_str,
            "spend_cny": round(total_spend_cny, 6),
            "spend_usd": round(total_spend_usd, 6),
            "prompt_tokens": historical_prompt,
            "completion_tokens": historical_completion,
            "total_tokens": historical_total,
            "cache_hit_tokens": historical_cache_hit,
            "cache_miss_tokens": historical_cache_miss,
            "total_messages": (session_usage.get("requests") or 0) + (subagent_usage.get("requests") or 0),
            "active_days": sum(1 for row in stats_by_day.values() if int(row.get("llm_requests") or 0) > 0),
            "current_streak": _calc_current_streak(stats_by_day, today),
            "longest_streak": _calc_longest_streak(stats_by_day),
            "peak_hour": _calc_peak_hour(stats_by_day),
            "task_time": task_time,
            "top_tools": tool_rows,
            "timeline": [
                {
                    "date": day,
                    "prompt": values["prompt"],
                    "completion": values["completion"],
                    "requests": values["requests"],
                }
                for day, values in token_timeline.items()
            ],
        },
        "reminders": reminder_items,
        "recent_memories": recent_memories,
        "recent_archive": archive_snippets,
        "activity_heatmap": activity_heatmap,
        "model_stats": model_stats_rows,
    }


def _extract_topic_terms(text: str, limit: int = 12) -> list[str]:
    """Extract simple high-signal topic terms from mixed Chinese/English text."""
    source = (text or "").lower()
    english_stop = {
        "the", "and", "for", "that", "this", "with", "from", "have", "about",
        "what", "when", "your", "just", "into", "then", "they", "them", "their",
        "would", "could", "should", "there", "here", "been", "were", "will",
        "some", "more", "than", "after", "before", "need", "want", "like",
        "today", "yesterday", "tomorrow", "really", "also", "maybe", "because",
        "http", "https", "assistant", "cyrene", "user",
    }
    chinese_stop = {
        "今天", "最近", "这个", "那个", "一下", "已经", "我们", "你们", "然后",
        "需要", "可以", "还是", "就是", "一个", "没有", "什么", "怎么", "如果",
        "现在", "自己", "因为", "所以", "以及", "但是", "进行", "相关", "问题",
        "工作", "页面", "功能", "内容",
    }
    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[a-z][a-z0-9_-]{2,}", source)
    results: list[str] = []
    for token in tokens:
        if token in english_stop or token in chinese_stop:
            continue
        if token.isascii() and len(token) < 4:
            continue
        results.append(token)
        if len(results) >= limit:
            break
    return results


def _read_recent_logs() -> list[dict]:
    """Read the most recent debug log file and convert to status log rows."""
    from cyrene.config import DATA_DIR
    if not DATA_DIR.exists():
        return _placeholder_logs()
    log_files = sorted(DATA_DIR.glob("debug_*.jsonl"), reverse=True)
    if not log_files:
        return _placeholder_logs()
    latest = log_files[0]
    rows: list[dict] = []
    try:
        with open(latest, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except Exception:
        return _placeholder_logs()
    for line in lines[-40:]:
        try:
            entry = json.loads(line)
        except Exception:
            continue
        kind = entry.get("type", "info")
        ts = entry.get("timestamp", "")[11:19]
        if kind == "llm_call":
            caller = entry.get("caller", "?")
            phase = entry.get("phase", "?")
            duration = entry.get("duration_ms", 0)
            rows.append({"t": ts, "lvl": "info", "msg": f"{caller} · {phase} · {duration}ms"})
        elif kind == "tool_call":
            caller = entry.get("caller", "?")
            tool = entry.get("tool", "?")
            rows.append({"t": ts, "lvl": "ok", "msg": f"{caller} → {tool}"})
        elif kind == "session_start":
            rows.append({"t": ts, "lvl": "info", "msg": "session started"})
    return list(reversed(rows[-20:]))


def _placeholder_logs() -> list[dict]:
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    return [{"t": now, "lvl": "info", "msg": "no debug logs yet — verbose mode is enabled, logs appear after agent runs"}]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def _build_settings_meta() -> dict:
    return {
        "sections": [
            {"id": "general", "label": "General"},
            {"id": "channels", "label": "Channels"},
            {"id": "models", "label": "Models"},
            {"id": "agents", "label": "Agents"},
            {"id": "appearance", "label": "Appearance"},
            {"id": "capabilities", "label": "Capabilities"},
            {"id": "data", "label": "Data"},
            {"id": "about", "label": "About"},
        ],
    }


def _build_config() -> dict:
    settings = get_web_settings()
    live_model, live_base_url = _live_llm_config()
    return {
        "model": live_model,
        "base_url": live_base_url,
        "assistant_name": ASSISTANT_NAME,
        "base_dir": str(BASE_DIR),
        "data_dir": str(DATA_DIR),
        "soul_path": str(SOUL_PATH),
        "workspace_dir": str(WORKSPACE_DIR),
        "soul_content": _read_soul(),
        "search_mode": "builtin",
        "search_external_url": "",
        "spawn_policy": settings.get("spawn_policy", "conservative"),
        "heartbeat_interval": settings.get("heartbeat_interval", 1800),
        "agent_proactive": settings.get("agent_proactive", True),
        "app_language": settings.get("app_language", ""),
        "max_tool_rounds": settings.get("max_tool_rounds", 15),
        "subagent_execution_max_tool_calls": settings.get("subagent_execution_max_tool_calls", 200),
        "subagent_execution_max_wall_seconds": settings.get("subagent_execution_max_wall_seconds", 1800),
        "subagent_execution_no_progress_turns": settings.get("subagent_execution_no_progress_turns", 3),
        "subagent_execution_checkpoint_calls": settings.get("subagent_execution_checkpoint_calls", 20),
        "subagent_execution_max_cost_usd": settings.get("subagent_execution_max_cost_usd", 5.0),
        "subagent_execution_max_context_tokens": settings.get("subagent_execution_max_context_tokens", 0),
        "subagent_discussion_max_rounds": settings.get("subagent_discussion_max_rounds", 5),
        "subagent_discussion_max_messages_per_agent": settings.get("subagent_discussion_max_messages_per_agent", 4),
        "subagent_discussion_max_total_messages": settings.get("subagent_discussion_max_total_messages", 20),
        "subagent_discussion_max_message_chars": settings.get("subagent_discussion_max_message_chars", 2000),
        "subagent_discussion_max_wall_seconds": settings.get("subagent_discussion_max_wall_seconds", 600),
        "subagent_discussion_max_tool_calls": settings.get("subagent_discussion_max_tool_calls", 50),
        "subagent_discussion_no_new_info_rounds": settings.get("subagent_discussion_no_new_info_rounds", 2),
        "notify_telegram": settings.get("notify_telegram", True),
        "notify_wechat": settings.get("notify_wechat", True),
        "redact_secrets": settings.get("redact_secrets", True),
        "beta_updates": settings.get("beta_updates", False),
        "auto_update": settings.get("auto_update", True),
        "budget_enabled": settings.get("budget_enabled", False),
        "budget_monthly": settings.get("budget_monthly", 50),
        "budget_currency": settings.get("budget_currency", "CNY"),
        "budget_action": settings.get("budget_action", "warn"),
        "budget_mode": settings.get("budget_mode", "normal"),
        "budget_start_day": settings.get("budget_start_day", 1),
        "search_port": str(SEARXNG_PORT),
        "search_host": SEARXNG_HOST,
    }


def _build_context_chips() -> list[dict]:
    """Build context chips reflecting current SOUL.md and workspace state."""
    from cyrene.runtime.settings_store import is_workspace_active, is_soul_active
    chips = []
    if is_soul_active():
        chips.append({"icon": "🧠", "label": "SOUL.md", "key": "soul"})
    if is_workspace_active():
        chips.append({"icon": "📁", "label": "workspace", "key": "workspace"})
    return chips


def _build_search_config() -> dict:
    return {
        "search_mode": "builtin",
        "search_external_url": "",
        "auto_start_enabled": os.getenv("SEARXNG_AUTO_START", "1") not in ("0", "false", "no"),
    }


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


def _load_messages() -> list[dict]:
    msgs = _load_state_messages()
    if msgs:
        result = []
        for m in msgs:
            role = m.get("role", "")
            if role not in ("user", "assistant"):
                continue
            content = m.get("content", "")
            if not content or not content.strip():
                continue
            result.append({"role": role, "content": content})
        if result:
            return result

    archive_msgs = _parse_conversation_archive()
    if archive_msgs:
        return archive_msgs

    return []


def _load_state_messages() -> list[dict]:
    if not STATE_FILE.exists():
        return []
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data.get("messages", []) or []
    except Exception:
        return []


def _infer_subagent_entries(raw_msgs: list[dict], registry: dict[str, dict]) -> dict[str, dict]:
    entries: dict[str, dict] = _snapshot_entries_from_messages(raw_msgs)
    for agent_id, info in registry.items():
        _merge_subagent_record(entries, agent_id, dict(info))
    for entry in entries.values():
        entry.setdefault("messages", [])

    spawned: dict[str, dict[str, str]] = {}
    for msg in raw_msgs:
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            if fn.get("name") != "spawn_subagent":
                continue
            args = _safe_json_loads(fn.get("arguments") or "{}")
            if not isinstance(args, dict):
                continue
            agent_id = str(args.get("agent_id") or "").strip()
            if not agent_id:
                continue
            spawned[agent_id] = {
                "task": str(args.get("task") or ""),
                "round_id": str(msg.get("round_id", "")).strip(),
            }

    for agent_id, meta in spawned.items():
        entry = entries.setdefault(agent_id, {})
        meta_round_id = str(meta.get("round_id", "")).strip()
        existing_round_id = str(entry.get("round_id", "")).strip()
        if meta_round_id and existing_round_id and meta_round_id != existing_round_id:
            # Treat a reused agent ID in a later round as a fresh live subagent.
            entry["task"] = meta["task"] or entry.get("task", "")
            entry["round_id"] = meta_round_id
            entry["status"] = "running"
            entry["result"] = ""
            entry["messages"] = []
            entry["created_at"] = None
            entry["updated_at"] = None
            continue
        entry.setdefault("task", meta["task"])
        entry.setdefault("round_id", meta_round_id)
        entry.setdefault("status", "done")
        entry.setdefault("result", "")
        entry.setdefault("messages", [])
        entry.setdefault("created_at", None)
        entry.setdefault("updated_at", None)

    inbox_meta = _scan_inbox_agents()
    for agent_id, meta in inbox_meta.items():
        entry = entries.setdefault(agent_id, {})
        entry.setdefault("task", spawned.get(agent_id, {}).get("task", "Discuss with other subagents"))
        entry.setdefault("status", "done")
        entry.setdefault("result", "")
        if not entry.get("messages"):
            entry["messages"] = [{}] * int(meta.get("message_count") or 0)
        if meta.get("created_at") and not entry.get("created_at"):
            entry["created_at"] = meta["created_at"]
        if meta.get("updated_at") and not entry.get("updated_at"):
            entry["updated_at"] = meta["updated_at"]
        if meta.get("round_id") and not entry.get("round_id"):
            entry["round_id"] = meta["round_id"]

    return entries


def _parse_conversation_archive() -> list[dict]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filepath = CONVERSATIONS_DIR / f"{today}.md"
    if not filepath.exists():
        return []
    content = filepath.read_text(encoding="utf-8")
    messages = []
    current_user = None
    current_lines: list[str] = []
    in_assistant = False
    for line in content.split("\n"):
        if line.startswith("**User**: "):
            if current_user and current_lines:
                messages.append({"role": "user", "content": current_user})
                messages.append({"role": "assistant", "content": "\n".join(current_lines).strip()})
            current_user = line[len("**User**: "):].strip()
            current_lines = []
            in_assistant = False
        elif line.startswith("**") and "**: " in line and not line.startswith("**User**"):
            in_assistant = True
            idx = line.index("**: ")
            current_lines = [line[idx + len("**: "):]]
        elif in_assistant:
            if line.strip() == "---":
                if current_user and current_lines:
                    messages.append({"role": "user", "content": current_user})
                    messages.append({"role": "assistant", "content": "\n".join(current_lines).strip()})
                current_user = None
                current_lines = []
                in_assistant = False
            else:
                current_lines.append(line)
    if current_user and current_lines:
        messages.append({"role": "user", "content": current_user})
        messages.append({"role": "assistant", "content": "\n".join(current_lines).strip()})
    return messages


def _read_soul() -> str:
    try:
        if SOUL_PATH.exists():
            return SOUL_PATH.read_text(encoding="utf-8")
    except Exception:
        pass
    return ""


def _model_pricing(model: str = "") -> dict[str, float] | None:
    """Return token pricing for an actual response model, or the active model.

    Missing or invalid configured prices use the built-in catalog when known;
    unknown models resolve to zero.
    """
    from cyrene.model_runtime.pricing import effective_price

    return effective_price(str(model or _get_model()))


def _calc_spend(
    usage: dict[str, int | None] | None,
    model: str = "",
) -> str:
    if not isinstance(usage, dict):
        return "—"
    pricing = _model_pricing(model)
    if pricing is None:
        return "—"
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    cache_hit_tokens = usage.get("prompt_cache_hit_tokens")
    cache_miss_tokens = usage.get("prompt_cache_miss_tokens")
    from cyrene.model_runtime.pricing import estimate_cost

    cost = estimate_cost(
        pricing,
        int(prompt_tokens or 0),
        int(completion_tokens or 0),
        cache_hit_tokens=int(cache_hit_tokens or 0),
        cache_miss_tokens=int(cache_miss_tokens or 0),
    )
    currency = pricing.get("currency", "USD")
    if currency == "CNY":
        sym = "¥"
        threshold = 0.07  # ~$0.01 in CNY
    else:
        sym = "$"
        threshold = 0.01
    if cost == 0:
        return f"{sym}0.00"
    if cost < threshold:
        return f"<{sym}{threshold:.2g}"
    return f"{sym}{cost:.2f}"


def _calc_messages_spend(messages: list[dict[str, Any]]) -> str:
    """Sum usage with each response's actual model price.

    Fallback can change models between calls in one session.  Aggregating all
    tokens first and applying the configured primary price misprices those
    mixed-model sessions, so calculate each recorded response independently.
    """
    from cyrene.model_runtime.pricing import CNY_PER_USD, estimate_cost

    totals = {"CNY": 0.0, "USD": 0.0}
    found = False
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        model = str(usage.get("model") or message.get("model") or _get_model()).strip()
        pricing = _model_pricing(model)
        if pricing is None:
            continue
        found = True
        cost = estimate_cost(
            pricing,
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
            cache_hit_tokens=int(usage.get("prompt_cache_hit_tokens") or 0),
            cache_miss_tokens=int(usage.get("prompt_cache_miss_tokens") or 0),
        )
        currency = str(pricing.get("currency") or "CNY").upper()
        totals[currency if currency in totals else "CNY"] += cost
    if not found:
        return "—"
    if totals["CNY"] and totals["USD"]:
        cost = totals["CNY"] + totals["USD"] * CNY_PER_USD
        currency = "CNY"
    elif totals["USD"]:
        cost = totals["USD"]
        currency = "USD"
    else:
        cost = totals["CNY"]
        currency = "CNY"
    symbol = "¥" if currency == "CNY" else "$"
    threshold = 0.07 if currency == "CNY" else 0.01
    if cost == 0:
        return f"{symbol}0.00"
    return f"<{symbol}{threshold:.2g}" if cost < threshold else f"{symbol}{cost:.2f}"


def _calc_current_streak(stats_by_day: dict[str, dict], today: str) -> int:
    streak = 0
    for offset in range(366):
        day = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=offset)).strftime("%Y-%m-%d")
        row = stats_by_day.get(day)
        if row and int(row.get("llm_requests") or 0) > 0:
            streak += 1
        else:
            break
    return streak


def _calc_longest_streak(stats_by_day: dict[str, dict]) -> int:
    longest = 0
    current = 0
    for offset in range(365):
        day = (datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d")
        row = stats_by_day.get(day)
        if row and int(row.get("llm_requests") or 0) > 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


_ACTIVITY_COLUMNS = [
    ("activity_00_04", "00:00-04:00"),
    ("activity_04_08", "04:00-08:00"),
    ("activity_08_12", "08:00-12:00"),
    ("activity_12_16", "12:00-16:00"),
    ("activity_16_20", "16:00-20:00"),
    ("activity_20_24", "20:00-24:00"),
]


def _calc_peak_hour(stats_by_day: dict[str, dict]) -> str:
    totals: dict[str, int] = {}
    for col, _label in _ACTIVITY_COLUMNS:
        totals[col] = sum(int(row.get(col) or 0) for row in stats_by_day.values())
    best_col = max(totals, key=totals.get) if any(totals.values()) else ""
    for col, label in _ACTIVITY_COLUMNS:
        if col == best_col:
            return label
    return "—"


def _build_shells_from_messages(raw_msgs: list[dict]) -> list[dict]:
    """Extract bash/shell tool calls from raw messages and build shell entries."""
    shells: list[dict] = []
    tool_results: dict[str, str] = {}
    for msg in raw_msgs:
        if msg.get("role") == "tool" and msg.get("tool_call_id"):
            tool_results[str(msg["tool_call_id"])] = str(msg.get("content") or "")

    shell_index = 0
    for msg in raw_msgs:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            if name.lower() not in ("bash", "shell", "cmd", "terminal"):
                continue
            args_str = fn.get("arguments", "{}")
            try:
                args = json.loads(args_str) if isinstance(args_str, str) else args_str
            except Exception:
                args = {}
            if not isinstance(args, dict):
                args = {}
            cmd = args.get("command") or args.get("cmd") or json.dumps(args)
            cwd = args.get("cwd") or args.get("workdir") or "workspace/"
            result = tool_results.get(str(tc.get("id")), "")
            lines: list[dict] = [
                {"kind": "shell-prompt", "text": f"$ {cmd}"},
            ]
            if result:
                for line in result.strip().split("\n")[:30]:
                    lines.append({"kind": "shell-out", "text": line})
            else:
                lines.append({"kind": "shell-out", "text": "(running…)"})

            shells.append({
                "id": f"shell_{shell_index}",
                "cwd": cwd,
                "pid": "—",
                "lines": lines,
            })
            shell_index += 1

    return shells


def _build_tool_nodes_for_owner(
    owner_node_id: str,
    owner_title: str,
    owner_x: int,
    owner_y: int,
    raw_messages: list[dict],
    recent_events: list[dict],
    caller_prefix: str,
    x: int,
    base_y: int,
    owner_completed: bool = False,
) -> tuple[list[dict], list[dict]]:
    nodes: list[dict] = []
    edges: list[dict] = []
    tool_outputs = _tool_output_map(raw_messages)
    tool_output_ids = _tool_output_ids(raw_messages)
    tool_index = 0

    for msg_index, msg in enumerate(raw_messages):
        tool_calls = msg.get("tool_calls") or []
        for call_index, tc in enumerate(tool_calls):
            fn = tc.get("function", {})
            raw_args = fn.get("arguments") or "{}"
            parsed_args = _safe_json_loads(raw_args) if isinstance(raw_args, str) else raw_args
            tool_call_id = str(tc.get("id") or "")
            output = tool_outputs.get(tool_call_id, "")
            has_output = tool_call_id in tool_output_ids
            has_followup = any(
                later.get("role") in {"assistant", "tool", "user"}
                for later in raw_messages[msg_index + 1:]
            )
            status = "done" if has_output or has_followup or owner_completed else "running"
            if has_output:
                output_detail = output or "Completed with no captured output."
            elif status == "done":
                output_detail = "Completed after follow-up activity; no tool output was captured."
            else:
                output_detail = "Running…"
            nid = f"{owner_node_id}_tool_{msg_index}_{call_index}"
            nodes.append({
                "id": nid,
                "kind": "tool",
                "x": x,
                "y": base_y + tool_index * 112,
                "title": fn.get("name", "tool"),
                "subtitle": _summarize_text(str(raw_args), 36) if raw_args else "",
                "status": status,
                "detail": {
                    "name": fn.get("name", "tool"),
                    "owner": owner_title,
                    "input": parsed_args if parsed_args is not None else raw_args,
                    "output": output_detail,
                    "duration": "—",
                },
            })
            edges.append({
                "from": owner_node_id,
                "to": nid,
                "kind": "active" if status == "running" else None,
            })
            tool_index += 1

    overlay_events = [
        event for event in recent_events
        if event.get("type") == "tool_call" and str(event.get("caller", "")).startswith(caller_prefix)
    ][-6:]
    for event_index, event in enumerate(overlay_events):
        event_signature = _tool_args_signature(event.get("args", {}))
        if any(
            node["detail"].get("name") == event.get("tool")
            and _tool_args_signature(node["detail"].get("input", {})) == event_signature
            for node in nodes
        ):
            continue
        nid = f"{owner_node_id}_live_tool_{event_index}"
        nodes.append({
            "id": nid,
            "kind": "tool",
            "x": x,
            "y": base_y + tool_index * 112,
            "title": event.get("tool", "tool"),
            "subtitle": _summarize_text(json.dumps(event.get("args", {}), ensure_ascii=False), 36),
            "status": "done",
            "detail": {
                "name": event.get("tool", "tool"),
                "owner": owner_title,
                "input": event.get("args", {}),
                "output": event.get("result_preview", "Completed."),
                "duration": "recent",
                "eventKey": f"{event.get('tool')}::{event_signature}",
            },
        })
        edges.append({"from": owner_node_id, "to": nid})
        tool_index += 1

    return nodes, edges


def _count_tool_nodes_for_owner(
    raw_messages: list[dict],
    recent_events: list[dict],
    caller_prefix: str,
) -> int:
    count = sum(len(msg.get("tool_calls") or []) for msg in raw_messages)
    message_keys = {
        (
            tc.get("function", {}).get("name", "tool"),
            json.dumps(
                _safe_json_loads(tc.get("function", {}).get("arguments") or "{}")
                if isinstance(tc.get("function", {}).get("arguments"), str)
                else (tc.get("function", {}).get("arguments") or {}),
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        for msg in raw_messages
        for tc in (msg.get("tool_calls") or [])
    }
    overlay_events = [
        event for event in recent_events
        if event.get("type") == "tool_call" and str(event.get("caller", "")).startswith(caller_prefix)
    ][-6:]
    overlay_count = 0
    for event in overlay_events:
        event_key = (
            event.get("tool", "tool"),
            json.dumps(event.get("args", {}), ensure_ascii=False, sort_keys=True),
        )
        if event_key in message_keys:
            continue
        overlay_count += 1
    return count + overlay_count


def _agent_lane_height(tool_count: int) -> int:
    base_height = 86
    if tool_count <= 0:
        return base_height
    return max(base_height, base_height + (tool_count - 1) * 112)


def _build_comm_edges(
    agent_node_ids: dict[str, str],
    agent_entries: dict[str, dict[str, Any]] | None = None,
    round_id: str = "",
    persisted_messages: list[dict[str, Any]] | None = None,
) -> list[dict]:
    edges: list[dict] = []
    if not agent_node_ids:
        return edges

    # Track per-pair messages for threading and weight
    pair_messages: dict[tuple[str, str], list[dict]] = {}
    # Map to deduplicate: (from_agent, to_agent, content[:80]) -> edge_index
    content_index: dict[tuple[str, str, str], int] = {}

    def _add_message_to_pair(
        from_agent: str,
        to_agent: str,
        body: str,
        *,
        label: str = "chat",
        timestamp: str = "",
        source: str = "",
        summary: str = "",
        priority: str = "normal",
        raw_timestamp: str = "",
    ) -> None:
        if from_agent not in agent_node_ids or to_agent not in agent_node_ids:
            return
        if not body.strip():
            return

        pair_key = (from_agent, to_agent)
        content_key = (from_agent, to_agent, body[:80])

        if content_key in content_index:
            # Update existing edge with richer metadata
            idx = content_index[content_key]
            existing_msg = edges[idx].setdefault("message", {})
            if (not existing_msg.get("time") or existing_msg.get("time") == "—") and timestamp:
                existing_msg["time"] = _short_time(timestamp)
            if summary and not existing_msg.get("summary"):
                existing_msg["summary"] = summary
            if priority == "high":
                existing_msg["priority"] = "high"
            # Increment weight even for duplicates (counts total messages)
            edges[idx]["weight"] = edges[idx].get("weight", 1) + 1
            pair_messages.setdefault(pair_key, []).append({
                "from": from_agent,
                "to": to_agent,
                "body": body,
                "label": label,
                "time": _short_time(timestamp) if timestamp else "—",
                "summary": summary,
                "priority": priority,
                "source": source,
            })
            return

        edge_summary = summary if summary else _summarize_text(body, 90)
        edge_label = label
        if priority == "high":
            edge_label = label + " !"

        edge_entry = {
            "from": agent_node_ids[from_agent],
            "to": agent_node_ids[to_agent],
            "kind": "comm",
            "label": edge_label,
            "weight": 1,
            "message": {
                "time": _short_time(timestamp) if timestamp else "—",
                "raw_timestamp": raw_timestamp or timestamp or "",
                "summary": edge_summary,
                "body": body,
                "source": source or "tool_call",
                "msg_type": label,
                "priority": priority,
            },
        }
        edges.append(edge_entry)
        content_index[content_key] = len(edges) - 1
        pair_messages.setdefault(pair_key, []).append({
            "from": from_agent,
            "to": to_agent,
            "body": body,
            "label": label,
            "time": _short_time(timestamp) if timestamp else "—",
            "raw_timestamp": raw_timestamp or timestamp or "",
            "summary": edge_summary,
            "priority": priority,
            "source": source,
        })

    for agent_name, info in (agent_entries or {}).items():
        if agent_name not in agent_node_ids:
            continue
        messages = info.get("messages", []) or []
        tool_outputs = {
            str(msg.get("tool_call_id") or ""): str(msg.get("content") or "")
            for msg in messages
            if isinstance(msg, dict) and msg.get("role") == "tool" and msg.get("tool_call_id")
        }
        for msg in messages:
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                tool_name = str(fn.get("name") or "").strip()
                if tool_name not in ("send_agent_message", "broadcast_agent_message"):
                    continue
                args = _safe_json_loads(fn.get("arguments") or "{}")
                if not isinstance(args, dict):
                    continue
                output = tool_outputs.get(str(tc.get("id") or ""), "")
                output_lower = output.lower()
                if output and "message sent to" not in output_lower and "broadcast sent to" not in output_lower:
                    continue
                body = str(args.get("content") or "")
                if tool_name == "broadcast_agent_message":
                    # Broadcast edges go to each peer
                    peer_ids = [aid for aid in agent_node_ids if aid != agent_name]
                    for peer_id in peer_ids:
                        _add_message_to_pair(agent_name, peer_id, body, label="progress", source="tool_call")
                else:
                    to_agent = str(args.get("to") or "").strip()
                    _add_message_to_pair(agent_name, to_agent, body, source="tool_call")

    for payload in persisted_messages or []:
        if not isinstance(payload, dict):
            continue
        if round_id and str(payload.get("round_id", "")).strip() != round_id:
            continue
        _add_message_to_pair(
            str(payload.get("from", "")).strip(),
            str(payload.get("to", "")).strip(),
            str(payload.get("content", "")),
            label=str(payload.get("type", "chat") or "chat"),
            timestamp=str(payload.get("timestamp", "") or ""),
            source="snapshot_log",
            summary=str(payload.get("summary", "") or ""),
            priority=str(payload.get("priority", "normal") or "normal"),
        )

    for agent_name in agent_node_ids:
        inbox_dir = DATA_DIR / "inbox" / agent_name
        if not inbox_dir.exists():
            continue
        for msg_file in sorted(inbox_dir.glob("msg_*.json")):
            try:
                payload = json.loads(msg_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            from_agent = str(payload.get("from", ""))
            to_agent = str(payload.get("to", ""))
            if round_id and str(payload.get("round_id", "")) != round_id:
                continue
            _add_message_to_pair(
                from_agent,
                to_agent,
                str(payload.get("content", "")),
                label=str(payload.get("type", "chat") or "chat"),
                timestamp=str(payload.get("timestamp", "") or ""),
                source="inbox_log",
                summary=str(payload.get("summary", "") or ""),
                priority=str(payload.get("priority", "normal") or "normal"),
            )

    # Attach all messages for each pair to the edge
    for i, edge in enumerate(edges):
        pair = None
        for (f, t), msgs in pair_messages.items():
            if edge["from"] == agent_node_ids.get(f) and edge["to"] == agent_node_ids.get(t):
                pair = (f, t)
                edge["messages"] = msgs
                break
        if pair:
            edge["weight"] = len(pair_messages.get(pair, []))

    return edges


def _scan_inbox_agents() -> dict[str, dict[str, Any]]:
    agents: dict[str, dict[str, Any]] = {}
    inbox_root = DATA_DIR / "inbox"
    if not inbox_root.exists():
        return agents

    for inbox_dir in sorted(path for path in inbox_root.iterdir() if path.is_dir()):
        agent_id = inbox_dir.name
        timestamps: list[str] = []
        round_ids: list[str] = []
        msg_count = 0
        for msg_file in sorted(inbox_dir.glob("msg_*.json")):
            try:
                payload = json.loads(msg_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            msg_count += 1
            timestamp = payload.get("timestamp")
            if isinstance(timestamp, str) and timestamp:
                timestamps.append(timestamp)
            round_id = str(payload.get("round_id", "")).strip()
            if round_id:
                round_ids.append(round_id)

        if msg_count == 0:
            continue

        timestamps.sort()
        agents[agent_id] = {
            "message_count": msg_count,
            "created_at": timestamps[0] if timestamps else None,
            "updated_at": timestamps[-1] if timestamps else None,
            "round_id": round_ids[-1] if round_ids else "",
        }

    return agents


__all__ = tuple(name for name in globals() if not name.startswith('__'))
