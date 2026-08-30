"""Skills-owned behavior telemetry, repeat detection, and learned skills.

The primary learning path is intentionally small:

- persist every executed round as a short purpose plus its detailed tool chain;
- retrieve the five most relevant project-local candidates with lexical and
  keyword matching, then let one background learning-agent call decide;
- observe the first occurrence, ask on the second, auto-learn on the third;
- prefer agent-generated Python or shell implementations for complex,
  non-interactive continuous workflows;
- retain declarative parameterized tool steps as provenance and fallback;
- replay safe learned workflows through the live Plugin Runtime.

The learner intentionally has no fingerprint bucket, automatic merge threshold,
semantic runtime router, or separate review layer. Local retrieval scores only
shortlist candidates; the learning agent still owns the merge/new decision.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3

import aiosqlite
from collections import defaultdict
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from cyrene.localization import app_language, localized

from .candidate import CandidatePorts, CandidateService, CandidateSkillDraft
from .candidate import (
    extract_json_object as _extract_json_object,
    is_meaningful_candidate_item as _is_meaningful_candidate_item,
    is_skillworthy_chain as _is_skillworthy_chain,
    normalize_slot as _normalize_slot,
    normalize_whitespace as _normalize_whitespace,
    parameter_type_for_value as _parameter_type_for_value,
    safe_slug as _safe_slug,
    should_expose_stable_arg as _should_expose_stable_arg,
    should_parameterize_arg as _should_parameterize_arg,
)
from .capture import (
    CapturePorts,
    CaptureService,
)
from .lifecycle import (
    CORRECTION_TERMS,
    clone_json_value as _clone_json_value,
    LifecyclePorts,
    LifecycleService,
)
from .repository import (
    LearningConnection,
    LearningRepository,
)
from .replay import (
    INTERNAL_LEARNING_MESSAGE_PREFIXES,
    INTERNAL_TOOLS,
    TRIVIAL_SKILL_TOOLS,
    is_reusable_skill_definition,
    normalize_generated_script_source,
)

logger = logging.getLogger(__name__)

_DATA_DIR: Path | None = None
_WORKSPACE_DIR: Path | None = None
_DB_FILE: Path | None = None
# SQLite busy-wait (seconds). Block instead of failing instantly when another
# connection holds a write lock — e.g. background learning processing running
# concurrently with the per-tool-call record_action writes.
_SQLITE_BUSY_TIMEOUT = 15.0
_INIT_DONE = False
_PROCESS_LOCK: asyncio.Lock | None = None
_PROCESS_LOCK_LOOP: asyncio.AbstractEventLoop | None = None

_current_session_id: ContextVar[str] = ContextVar("behavior_session_id", default="")
_current_turn_id: ContextVar[str] = ContextVar("behavior_turn_id", default="")
_current_round_id: ContextVar[str] = ContextVar("behavior_round_id", default="")

_CANDIDATE_USER_DECISION_COUNT = 2
_CANDIDATE_AUTO_LEARN_COUNT = 3
_CANDIDATE_RETRIEVAL_LIMIT = 5
_MAX_PURPOSE_CHARS = 20
_INTERNAL_PROACTIVE_PROMPT_PREFIX = "This is a scheduler-initiated proactive check-in."
_SCHEDULED_CHECK_IN_LABEL = "Scheduled proactive check-in"
_IMAGE_ARTIFACT_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_MAX_IMAGE_ARTIFACT_BYTES = 20 * 1024 * 1024
_MAX_IMAGE_ARTIFACTS_PER_ACTION = 8
_IMAGE_ARTIFACT_PATH_RE = re.compile(
    r"(?P<path>(?:/[^\s\"'<>]+|[A-Za-z]:\\[^\s\"'<>]+)\.(?:png|jpg|jpeg|webp|gif))",
    re.IGNORECASE,
)

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"




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




def _conn() -> LearningConnection:
    if not _INIT_DONE or _DB_FILE is None:
        raise RuntimeError("cyrene_skills learning service is not initialized")
    return LearningConnection(_DB_FILE, _SQLITE_BUSY_TIMEOUT)


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
    if any(term in lowered for term in CORRECTION_TERMS):
        return "correction"
    return ""


def _default_skill_stats() -> dict[str, Any]:
    return {
        "total_runs": 0,
        "actual_runs": 0,
        "active_success": 0,
        "active_failure": 0,
        "last_run_at": "",
        "consistency_avg": 0.0,
    }


async def _ensure_tables() -> None:
    if _DB_FILE is None:
        raise RuntimeError("cyrene_skills learning database is not configured")
    await LearningRepository(_DB_FILE, _SQLITE_BUSY_TIMEOUT).initialize()


async def init(data_dir: Path, workspace_dir: Path) -> None:
    global _DATA_DIR, _WORKSPACE_DIR, _DB_FILE, _INIT_DONE
    _DATA_DIR = Path(data_dir).expanduser().resolve()
    _WORKSPACE_DIR = Path(workspace_dir).expanduser().resolve()
    _DB_FILE = _DATA_DIR / "behavior-learning.db"
    _DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    await _ensure_tables()
    _INIT_DONE = True


async def shutdown() -> None:
    """Drop process references when the owning Plugin pack is disabled."""

    global _DATA_DIR, _WORKSPACE_DIR, _DB_FILE, _INIT_DONE
    global _PROCESS_LOCK, _PROCESS_LOCK_LOOP, _STATS_LOCK, _STATS_LOCK_LOOP
    _INIT_DONE = False
    _DATA_DIR = None
    _WORKSPACE_DIR = None
    _DB_FILE = None
    _PROCESS_LOCK = None
    _PROCESS_LOCK_LOOP = None
    _STATS_LOCK = None
    _STATS_LOCK_LOOP = None
    _current_session_id.set("")
    _current_turn_id.set("")
    _current_round_id.set("")


async def ensure_initialized(data_dir: Path, workspace_dir: Path) -> None:
    """Initialize the process learning store once for the active data root."""
    configured = Path(data_dir).expanduser().resolve()
    if _INIT_DONE and _DATA_DIR is not None and Path(_DATA_DIR).resolve() == configured:
        return
    await init(configured, Path(workspace_dir).expanduser().resolve())


def _project_scope_for_session(session_id: str | None) -> dict[str, str]:
    sid = str(session_id or "").strip()
    if not sid:
        return {"project_id": "global", "project_key": "global", "session_kind": "global"}
    try:
        from cyrene.workbench.sessions.context import (
            resolve_workbench_project_id_for_data_key,
            resolve_workbench_session_scope,
        )

        resolved_scope = resolve_workbench_session_scope(sid)
        project_id = str(resolved_scope.get("project_id") or "").strip()
        project_key = str(resolved_scope.get("project_key") or "default").strip()
        session_kind = str(resolved_scope.get("session_kind") or "").strip()
    except Exception:
        project_id = ""
        project_key = ""
        session_kind = ""
    project_id = project_id or project_key or "global"
    project_key = project_key or project_id
    # When falling back to dataKey, resolve to project UUID so stored
    # project_id is consistent with the cyrene_skills application routes.
    # (which resolves dataKey -> UUID). Without this, a project whose
    # dataKey == "default" but UUID != "default" would never see chains
    # from non-project-scoped sessions.
    if project_id == project_key and project_key:
        try:
            _resolved = str(
                resolve_workbench_project_id_for_data_key(project_key) or ""
            ).strip()
            if _resolved:
                project_id = _resolved
        except Exception:
            pass
    return {
        "project_id": project_id,
        "project_key": project_key,
        "session_kind": session_kind or "global",
    }















































def _capture_service() -> CaptureService:
    if not _INIT_DONE or _DATA_DIR is None:
        raise RuntimeError("cyrene_skills learning service is not initialized")
    return CaptureService(CapturePorts(
        data_dir=_DATA_DIR,
        default_data_dir=_DATA_DIR,
        init_done=_INIT_DONE,
        connect=_conn,
        current_round_id=_current_round_id,
        current_session_id=_current_session_id,
        current_turn_id=_current_turn_id,
        ensure_tables=_ensure_tables,
        history_summary=_history_summary,
        json_dumps=_json_dumps,
        json_loads=_json_loads,
        new_id=_new_id,
        now_iso=_now_iso,
        project_scope_for_session=_project_scope_for_session,
        truncate_text=_truncate_text,
        turn_feedback_from_message=_turn_feedback_from_message,
    ))


async def _persist_image_artifacts(turn_id: str, value: Any) -> str:
    return await _capture_service()._persist_image_artifacts(turn_id, value)


async def _project_scope_for_turn(turn_id: str) -> dict[str, str]:
    return await _capture_service()._project_scope_for_turn(turn_id)


async def project_scope_for_turn(turn_id: str) -> dict[str, str]:
    """Return the persisted project scope for a completed learning turn."""
    return await _project_scope_for_turn(turn_id)


async def _latest_turn_for_session_round(session_id: str, round_id: str='') -> str:
    return await _capture_service()._latest_turn_for_session_round(session_id, round_id)


async def open_turn(session_id: str, round_id: str) -> dict[str, str] | None:
    return await _capture_service().open_turn(session_id, round_id)


async def _upsert_behavior_session(conn: aiosqlite.Connection, *, session_id: str, scope: dict[str, str], now: str, session_title: str, user_message: str) -> None:
    return await _capture_service()._upsert_behavior_session(conn, session_id=session_id, scope=scope, now=now, session_title=session_title, user_message=user_message)


async def _mark_previous_turn_corrected(conn: aiosqlite.Connection, session_id: str, now: str) -> None:
    return await _capture_service()._mark_previous_turn_corrected(conn, session_id, now)


async def begin_turn(
    *,
    session_id: str,
    round_id: str,
    user_message: str,
    history: list[dict[str, Any]],
    session_title: str = '',
    system_initiated: bool = False,
    defer_processing: bool = False,
) -> dict[str, Any]:
    return await _capture_service().begin_turn(
        session_id=session_id,
        round_id=round_id,
        user_message=user_message,
        history=history,
        session_title=session_title,
        system_initiated=system_initiated,
        defer_processing=defer_processing,
    )


def clear_turn_context(context: dict[str, Any]) -> None:
    return _capture_service().clear_turn_context(context)


def current_turn_id() -> str:
    return _capture_service().current_turn_id()


async def _rebuild_tool_chain_for_turn(turn_id: str) -> dict[str, Any] | None:
    return await _capture_service()._rebuild_tool_chain_for_turn(turn_id)


def _map_tool_to_action(tool_name: str) -> tuple[str, str, str, int]:
    return _capture_service()._map_tool_to_action(tool_name)


async def record_action(
    tool_name: str,
    args: dict[str, Any],
    caller: str,
    round_id: str,
    duration_ms: float,
    *,
    result: Any = '',
    success: bool = True,
    error: str = '',
    session_id: str = '',
    turn_id: str = '',
) -> None:
    return await _capture_service().record_action(
        tool_name,
        args,
        caller,
        round_id,
        duration_ms,
        result=result,
        success=success,
        error=error,
        session_id=session_id,
        turn_id=turn_id,
    )


async def record_browser_user_event(*, session_id: str='', round_id: str='', event_kind: str, payload: dict[str, Any] | None=None, browser_url: str='', browser_title: str='', target: dict[str, Any] | None=None) -> None:
    return await _capture_service().record_browser_user_event(session_id=session_id, round_id=round_id, event_kind=event_kind, payload=payload, browser_url=browser_url, browser_title=browser_title, target=target)


async def list_recent_browser_user_events(*, session_id: str='', round_id: str='', limit: int=30) -> list[dict[str, Any]]:
    return await _capture_service().list_recent_browser_user_events(session_id=session_id, round_id=round_id, limit=limit)


async def _classify_turn_outcome(turn_id: str) -> str:
    return await _capture_service()._classify_turn_outcome(turn_id)


async def complete_turn(*, turn_id: str, assistant_response: str, session_title: str='', round_title: str='') -> None:
    return await _capture_service().complete_turn(turn_id=turn_id, assistant_response=assistant_response, session_title=session_title, round_title=round_title)


async def abort_turn(*, turn_id: str, reason: str = '') -> None:
    return await _capture_service().abort_turn(turn_id=turn_id, reason=reason)


async def _call_llm_json(prompt: str, *, caller: str = "behavior_learning") -> dict[str, Any]:
    from cyrene.core.plugin import application_plugin_service
    from cyrene.model.messages import assistant_text

    try:
        gateway = application_plugin_service("model")
        complete = getattr(gateway, "complete", None)
        if not callable(complete):
            raise RuntimeError("active model Plugin gateway is unavailable")
        response = await complete(
            [{"role": "user", "content": prompt}],
            tools=None,
            max_tokens=6000,
            response_format={"type": "json_object"},
            route="secondary",
            caller=caller,
            session_id="behavior-learning",
        )
        return _extract_json_object(assistant_text(response))
    except Exception:
        logger.debug("behavior learning LLM JSON call failed", exc_info=True)
        return {}


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












async def _collect_template_action_groups(
    turn_ids: list[str],
) -> list[list[dict[str, Any]]]:
    action_groups: list[list[dict[str, Any]]] = []
    for turn_id in turn_ids:
        group: list[dict[str, Any]] = []
        for action in await _action_rows_for_turn(turn_id):
            if action["tool_name"] in INTERNAL_TOOLS or action["tool_name"] in TRIVIAL_SKILL_TOOLS:
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
    return action_groups


def _select_template_group(
    action_groups: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
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
    return max(
        matching_groups,
        key=lambda group: sum(len(item.get("_items") or [item.get("args") or {}]) for item in group),
    )


async def _derive_parameter_templates(turn_ids: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    action_groups = await _collect_template_action_groups(turn_ids)
    if not action_groups:
        return [], []
    template_group = _select_template_group(action_groups)
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
                "description": f"{template['tool_name']} via learned candidate",
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








def _generated_skill_script_dir(skill_id: str) -> Path:
    if not _INIT_DONE or _DATA_DIR is None:
        raise RuntimeError("cyrene_skills learning service is not initialized")
    base = _DATA_DIR
    path = Path(base) / "learned_skill_scripts" / _safe_slug(skill_id, default="skill")
    path.mkdir(parents=True, exist_ok=True)
    return path










def _persist_learning_agent_script(
    skill_id: str,
    implementation: dict[str, Any],
    original_steps: list[dict[str, Any]],
    skill_name: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    language = str(implementation.get("language") or "")
    source = normalize_generated_script_source(language, implementation.get("source"))
    if not source:
        return original_steps, {"kind": "tool_chain"}
    filename = "run.py" if language == "python" else "run.sh"
    script_path = _generated_skill_script_dir(skill_id) / filename
    script_path.write_text(source, encoding="utf-8")
    try:
        os.chmod(script_path, 0o700)
    except Exception:
        pass
    source_sha256 = hashlib.sha256(source.encode("utf-8")).hexdigest()
    persisted_implementation = {
        "kind": f"{language}_script",
        "language": language,
        "filename": filename,
        "script_path": str(script_path),
        "source_sha256": source_sha256,
        "generated_by": "skill_learning_agent",
        "requires_runtime_approval": True,
    }
    display_name = skill_name or localized("learned skill", "学习技能")
    wrapper = {
        "step_id": "script_1",
        "title": localized(
            "Execute {skill_name}",
            "执行{skill_name}",
            skill_name=display_name,
        ),
        "type": "run_command",
        "subtype": f"generated_{language}_script",
        "description": localized(
            "Run the learning-agent-generated {implementation_language} implementation.",
            "运行学习 Agent 生成的 {implementation_language} 实现。",
            implementation_language=language,
        ),
        "enabled": True,
        "requires_llm": False,
        "implementation_kind": "script",
        "implementation_reference": {
            **persisted_implementation,
            "learning_agent_generated": True,
            "original_steps": _clone_json_value(original_steps),
        },
        "failure_policy": "fail",
    }
    return [wrapper], persisted_implementation



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
                    t.metadata_json AS turn_metadata_json
                FROM behavior_turn_tool_chains c
                LEFT JOIN behavior_turns t ON t.turn_id = c.turn_id
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
                    t.metadata_json AS turn_metadata_json
                FROM behavior_turn_tool_chains c
                LEFT JOIN behavior_turns t ON t.turn_id = c.turn_id
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
                "purpose": str(item.get("purpose") or ""),
                "user_message": str(item.get("user_message") or ""),
                "context_summary": str(item.get("context_summary") or ""),
                "agent_response": str(item.get("agent_response") or metadata.get("assistant_preview") or ""),
                "session_title": str(metadata.get("session_title") or ""),
                "round_title": str(metadata.get("round_title") or ""),
                "system_initiated": bool(metadata.get("system_initiated")),
                "chain": _json_loads(item.get("chain_json"), []),
                "summary": _json_loads(item.get("summary_json"), {}),
                "created_at": str(item.get("created_at") or ""),
                "updated_at": str(item.get("updated_at") or ""),
            }
        )
    return result






























































def _reusable_turn_chain(
    turn: dict[str, Any],
    chain_record: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return cheap, locally validated learning evidence before any LLM call."""
    if str(turn.get("outcome_status") or "") != "success":
        return []
    metadata = _json_loads(turn.get("metadata_json"), {})
    message = str(turn.get("user_message") or "").strip()
    if bool(metadata.get("system_initiated")) or any(
        message.startswith(prefix) for prefix in INTERNAL_LEARNING_MESSAGE_PREFIXES
    ):
        return []
    meaningful = [
        item
        for item in (chain_record.get("chain") or [])
        if _is_meaningful_candidate_item(item)
    ]
    return meaningful if _is_skillworthy_chain(meaningful) else []




































def _lifecycle_service() -> LifecycleService:
    return LifecycleService(LifecyclePorts(
        connect=_conn,
        default_skill_stats=_default_skill_stats,
        get_stats_lock=_get_stats_lock,
        is_reusable_skill_definition=is_reusable_skill_definition,
        json_dumps=_json_dumps,
        json_loads=_json_loads,
        new_id=_new_id,
        normalize_slot=_normalize_slot,
        now_iso=_now_iso,
        call_llm_json=_call_llm_json,
        current_session_id=_current_session_id,
        current_turn_id=_current_turn_id,
        project_scope_for_session=_project_scope_for_session,
    ))


async def _unique_skill_name(conn: aiosqlite.Connection, preferred_name: str, *, skill_id: str='') -> str:
    return await _lifecycle_service()._unique_skill_name(conn, preferred_name, skill_id=skill_id)


def _infer_skill_risk_level(steps: list[dict[str, Any]]) -> str:
    return _lifecycle_service()._infer_skill_risk_level(steps)


def _skill_stats_with_usage_counters(stats: dict[str, Any] | None) -> dict[str, Any]:
    return _lifecycle_service()._skill_stats_with_usage_counters(stats)


def _skill_row_to_definition(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return _lifecycle_service()._skill_row_to_definition(row)


async def _save_skill_version(*, conn: aiosqlite.Connection, skill_id: str, version: int, parent_version: int | None, definition: dict[str, Any], change_type: str, change_summary: str, patch_list: list[dict[str, Any]] | None=None, test_result: dict[str, Any] | None=None, rollback_target: int | None=None) -> None:
    return await _lifecycle_service()._save_skill_version(conn=conn, skill_id=skill_id, version=version, parent_version=parent_version, definition=definition, change_type=change_type, change_summary=change_summary, patch_list=patch_list, test_result=test_result, rollback_target=rollback_target)


async def manual_activate_skill(skill_id: str) -> bool:
    return await _lifecycle_service().manual_activate_skill(skill_id)


async def manual_deprecate_skill(skill_id: str) -> bool:
    return await _lifecycle_service().manual_deprecate_skill(skill_id)


async def delete_learned_skill(skill_id: str) -> bool:
    return await _lifecycle_service().delete_learned_skill(skill_id)


async def _update_skill_run_stats(skill_id: str, *, execution_status: str, consistency_score: float=0.0) -> None:
    return await _lifecycle_service()._update_skill_run_stats(skill_id, execution_status=execution_status, consistency_score=consistency_score)


async def _create_patch_proposal(skill_id: str, base_version: int, patch_type: str, reason: str, patch_content: dict[str, Any]) -> None:
    return await _lifecycle_service()._create_patch_proposal(skill_id, base_version, patch_type, reason, patch_content)


async def _maybe_propose_patch(skill_id: str, version: int, failure_reason: str) -> None:
    return await _lifecycle_service()._maybe_propose_patch(skill_id, version, failure_reason)


async def list_learned_skills(project_id: str='') -> list[dict[str, Any]]:
    return await _lifecycle_service().list_learned_skills(project_id)


async def build_learned_skill_block(session_id: str='', max_skills: int=20, *, scope: dict[str, str] | None=None) -> str:
    return await _lifecycle_service().build_learned_skill_block(session_id, max_skills, scope=scope)


async def session_start_fingerprint(session_id: str='', max_skills: int=20) -> list[tuple[str, int, str, str]]:
    return await _lifecycle_service().session_start_fingerprint(session_id, max_skills)


async def get_learned_skill(skill_id: str) -> dict[str, Any] | None:
    return await _lifecycle_service().get_learned_skill(skill_id)


async def get_learned_skill_by_name(name: str, session_id: str='') -> dict[str, Any] | None:
    return await _lifecycle_service().get_learned_skill_by_name(name, session_id)


async def record_manual_skill_run(skill_id: str, version: int, *, execution_status: str='success', consistency_score: float=0.0) -> None:
    return await _lifecycle_service().record_manual_skill_run(skill_id, version, execution_status=execution_status, consistency_score=consistency_score)


async def list_learned_skill_versions(skill_id: str) -> list[dict[str, Any]]:
    return await _lifecycle_service().list_learned_skill_versions(skill_id)


async def list_learned_skill_patches(skill_id: str, status: str='all') -> list[dict[str, Any]]:
    return await _lifecycle_service().list_learned_skill_patches(skill_id, status)


async def list_learned_skill_runs(skill_id: str, limit: int=50) -> list[dict[str, Any]]:
    return await _lifecycle_service().list_learned_skill_runs(skill_id, limit)


async def _sanitize_skill_definition(definition: dict[str, Any]) -> dict[str, Any]:
    return await _lifecycle_service()._sanitize_skill_definition(definition)


async def _persist_skill_version(conn: aiosqlite.Connection, *, skill_id: str, current_row: sqlite3.Row, definition: dict[str, Any], change_type: str, change_summary: str, patch_list: list[dict[str, Any]] | None=None, test_result: dict[str, Any] | None=None, rollback_target: int | None=None) -> dict[str, Any]:
    return await _lifecycle_service()._persist_skill_version(conn, skill_id=skill_id, current_row=current_row, definition=definition, change_type=change_type, change_summary=change_summary, patch_list=patch_list, test_result=test_result, rollback_target=rollback_target)


def _extract_with_rules(user_message: str, schema_item: dict[str, Any]) -> tuple[Any, float]:
    return _lifecycle_service()._extract_with_rules(user_message, schema_item)


async def _extract_with_llm(*, user_message: str, context_summary: str, input_schema: list[dict[str, Any]], partial_params: dict[str, Any]) -> dict[str, Any]:
    return await _lifecycle_service()._extract_with_llm(user_message=user_message, context_summary=context_summary, input_schema=input_schema, partial_params=partial_params)


async def extract_skill_parameters(*, user_message: str, context_summary: str, input_schema: list[dict[str, Any]], llm_fallback: bool=True, overrides: dict[str, Any] | None=None) -> dict[str, Any]:
    return await _lifecycle_service().extract_skill_parameters(user_message=user_message, context_summary=context_summary, input_schema=input_schema, llm_fallback=llm_fallback, overrides=overrides)


async def update_learned_skill(skill_id: str, updates: dict[str, Any], *, reason: str='') -> dict[str, Any] | None:
    return await _lifecycle_service().update_learned_skill(skill_id, updates, reason=reason)


async def apply_skill_patch(skill_id: str, patch_id: str) -> dict[str, Any]:
    return await _lifecycle_service().apply_skill_patch(skill_id, patch_id)


async def reject_skill_patch(skill_id: str, patch_id: str) -> bool:
    return await _lifecycle_service().reject_skill_patch(skill_id, patch_id)


async def rollback_learned_skill(skill_id: str, rollback_version: int) -> dict[str, Any]:
    return await _lifecycle_service().rollback_learned_skill(skill_id, rollback_version)


def _candidate_service() -> CandidateService:
    return CandidateService(CandidatePorts(
        connect=_conn,
        call_llm_json=_call_llm_json,
        default_skill_stats=_default_skill_stats,
        derive_parameter_templates=_derive_parameter_templates,
        infer_skill_risk_level=_infer_skill_risk_level,
        json_dumps=_json_dumps,
        json_loads=_json_loads,
        new_id=_new_id,
        now_iso=_now_iso,
        persist_learning_agent_script=_persist_learning_agent_script,
        rebuild_tool_chain_for_turn=_rebuild_tool_chain_for_turn,
        reusable_turn_chain=_reusable_turn_chain,
        save_skill_version=_save_skill_version,
        truncate_text=_truncate_text,
        unique_skill_name=_unique_skill_name,
        auto_learn_count=_CANDIDATE_AUTO_LEARN_COUNT,
        retrieval_limit=_CANDIDATE_RETRIEVAL_LIMIT,
        user_decision_count=_CANDIDATE_USER_DECISION_COUNT,
        max_purpose_chars=_MAX_PURPOSE_CHARS,
    ))


async def _load_tool_chain_for_turn(turn_id: str) -> dict[str, Any]:
    return await _candidate_service()._load_tool_chain_for_turn(turn_id)


async def _ensure_turn_purpose(turn: dict[str, Any], chain_record: dict[str, Any]) -> str:
    return await _candidate_service()._ensure_turn_purpose(turn, chain_record)


async def _candidate_evidence_for_turn(turn_id: str) -> dict[str, Any] | None:
    return await _candidate_service()._candidate_evidence_for_turn(turn_id)


async def _candidate_turn_examples(candidate_id: str) -> list[dict[str, Any]]:
    return await _candidate_service()._candidate_turn_examples(candidate_id)


def _candidate_search_terms(value: Any) -> set[str]:
    return _candidate_service()._candidate_search_terms(value)


def _candidate_retrieval_score(query: str, row: dict[str, Any]) -> tuple[float, float]:
    return _candidate_service()._candidate_retrieval_score(query, row)


async def _retrieve_candidate_ids(project_id: str, query: str, *, limit: int=_CANDIDATE_RETRIEVAL_LIMIT) -> list[str]:
    return await _candidate_service()._retrieve_candidate_ids(project_id, query, limit=limit)


async def _candidate_catalog(project_id: str, candidate_ids: list[str] | None=None) -> list[dict[str, Any]]:
    return await _candidate_service()._candidate_catalog(project_id, candidate_ids)


async def _assign_candidate(evidence: dict[str, Any]) -> dict[str, Any] | None:
    return await _candidate_service()._assign_candidate(evidence)


def _candidate_fallback_name(message: str) -> str:
    return _candidate_service()._candidate_fallback_name(message)


async def _build_candidate_script(candidate_id: str) -> dict[str, Any]:
    return await _candidate_service()._build_candidate_script(candidate_id)


async def _refresh_candidate_script(candidate_id: str) -> dict[str, Any]:
    return await _candidate_service()._refresh_candidate_script(candidate_id)


async def _candidate_skill_draft(candidate_id: str) -> CandidateSkillDraft | str | None:
    return await _candidate_service()._candidate_skill_draft(candidate_id)


async def _persist_candidate_skill(draft: CandidateSkillDraft, *, auto: bool) -> str:
    return await _candidate_service()._persist_candidate_skill(draft, auto=auto)


async def _create_skill_from_candidate(candidate_id: str, *, auto: bool) -> str | None:
    return await _candidate_service()._create_skill_from_candidate(candidate_id, auto=auto)


async def _record_candidate_occurrence(evidence: dict[str, Any]) -> dict[str, Any]:
    return await _candidate_service()._record_candidate_occurrence(evidence)


async def list_skill_candidates(project_id: str='', status: str='all') -> list[dict[str, Any]]:
    return await _candidate_service().list_skill_candidates(project_id, status)


async def decide_skill_candidate(candidate_id: str, decision: str) -> dict[str, Any]:
    return await _candidate_service().decide_skill_candidate(candidate_id, decision)


def _fresh_learning_stats() -> dict[str, int]:
    return {
        "processed_turns": 0,
        "candidates_created": 0,
        "candidates_awaiting_user": 0,
        "candidates_auto_learned": 0,
        "skills_created": 0,
    }


def _background_skill_learning_enabled() -> bool:
    """Read the live toggle without coupling learning module import to settings."""
    try:
        from cyrene.platform.config_store import get_setting

        return bool(get_setting("background_skill_learning", True))
    except Exception:
        # A transient settings read failure should not silently disable the
        # long-standing default behavior.
        return True


async def process_unprocessed_turns(force: bool = False, project_id: str = "") -> dict[str, Any]:
    if not force and not _background_skill_learning_enabled():
        return _fresh_learning_stats()
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

    # Rebuild provenance locally first. Purpose generation is intentionally
    # deferred until the cheap eligibility checks in
    # _candidate_evidence_for_turn pass; single-tool, failed, internal, and
    # otherwise non-reusable rounds must not spend an LLM call merely to label
    # telemetry.
    await _rebuild_tool_chain_for_turn(turn_id)
    async with _conn() as conn:
        cursor = await conn.execute(
            "SELECT * FROM behavior_turns WHERE turn_id = ?",
            (turn_id,),
        )
        turn_row = await cursor.fetchone()
    current_turn = dict(turn_row) if turn_row is not None else {}
    chain_record = await _load_tool_chain_for_turn(turn_id)
    if _reusable_turn_chain(current_turn, chain_record):
        purpose = await _ensure_turn_purpose(current_turn, chain_record)
        if not purpose:
            # A genuinely reusable turn stays pending when its model-owned
            # purpose could not be generated. Non-reusable turns never enter
            # this branch and therefore never spend an LLM call.
            return False
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
    if not bool(candidate_result.get("processed", True)):
        return False
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
        return {
            "processed_turns": 0,
            "skills_created": 0,
            "code": "turn_id_required",
            "error": localized(
                "A turn_id is required.",
                "必须提供 turn_id。",
            ),
        }

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
            update_reason=localized(
                "User-initiated single-turn skill learning.",
                "用户发起的单轮技能学习。",
            ),
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
            for skill_id in skill_ids:
                await conn.execute("DELETE FROM learned_skill_patches WHERE skill_id = ?", (skill_id,))
                await conn.execute("DELETE FROM learned_skill_runs WHERE skill_id = ?", (skill_id,))
                await conn.execute("DELETE FROM learned_skill_versions WHERE skill_id = ?", (skill_id,))
            for candidate_id in candidate_ids:
                await conn.execute("DELETE FROM behavior_skill_candidate_turns WHERE candidate_id = ?", (candidate_id,))
            await conn.execute("DELETE FROM behavior_skill_candidates WHERE project_id = ?", (pid,))
            await conn.execute("DELETE FROM learned_skills WHERE project_id = ?", (pid,))
            if reprocess_all_turns:
                await conn.execute("UPDATE behavior_turns SET processed_status = 0 WHERE project_id = ?", (pid,))
        else:
            await conn.execute("DELETE FROM behavior_skill_candidate_turns")
            await conn.execute("DELETE FROM behavior_skill_candidates")
            await conn.execute("DELETE FROM learned_skill_patches")
            await conn.execute("DELETE FROM learned_skill_runs")
            await conn.execute("DELETE FROM learned_skill_versions")
            await conn.execute("DELETE FROM learned_skills")
            if reprocess_all_turns:
                await conn.execute("UPDATE behavior_turns SET processed_status = 0, updated_at = updated_at")
        if reprocess_all_turns:
            pass
        await conn.commit()
    stats = await process_unprocessed_turns(force=True, project_id=pid)
    learned = await list_learned_skills(pid)
    return {
        **stats,
        "learned_skills": learned,
        "skill_candidates": await list_skill_candidates(pid),
    }


async def run_learned_skill_result(
    skill_id: str,
    param_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    skill = await get_learned_skill(skill_id)
    if skill is None:
        return {
            "ok": False,
            "code": "learned_skill_not_found",
            "result": localized(
                "Learned skill {skill_id} was not found.",
                "未找到学习技能 {skill_id}。",
                skill_id=skill_id,
            ),
        }
    from cyrene.core.plugin import PluginContext, application_plugin_scope

    host = application_plugin_scope()
    if host is None:
        return {
            "ok": False,
            "code": "plugin_host_unavailable",
            "result": localized(
                "The learned skill cannot run because the Plugin application host is unavailable.",
                "插件应用宿主不可用，无法运行学习技能。",
            ),
        }

    replay_id = _new_id("learning_replay")
    session_id = f"learning:{skill_id}"
    project_id = str(skill.get("project_id") or "").strip()
    if not _INIT_DONE or _WORKSPACE_DIR is None:
        raise RuntimeError("cyrene_skills learning service is not initialized")
    workspace = _WORKSPACE_DIR
    if project_id:
        try:
            from cyrene.workbench.sessions.context import read_projects
            from cyrene.workbench.projects.project_repository import (
                resolve_project_workspace_dir,
            )

            project = next(
                (
                    item
                    for item in read_projects()
                    if isinstance(item, dict)
                    and str(item.get("id") or "").strip() == project_id
                ),
                None,
            )
            resolved_workspace = resolve_project_workspace_dir(project)
            if resolved_workspace:
                workspace = Path(resolved_workspace).expanduser().resolve()
        except Exception:
            logger.debug(
                "Could not resolve learned-skill project workspace; using default",
                exc_info=True,
            )
    called = await host.runtime.call_canonical(
        "RunLearnedSkill",
        {
            "name": str(skill.get("name") or ""),
            "params": dict(param_overrides or {}),
        },
        PluginContext(
            workspace=workspace,
            data={
                "bot": host.bot,
                "chat_id": 0,
                "db_path": host.db_path,
                "notify_state": {},
                "session_id": session_id,
                "learning_skill_id": skill_id,
                "learning_replay": True,
                "language": app_language(),
                "run_context": {
                    "agent_id": "main",
                    "caller": "learning_replay",
                    "conversation_source": "learning",
                    "project_id": project_id,
                    "round_id": replay_id,
                    "session_id": session_id,
                    "workspace_dir": str(workspace),
                    "language": app_language(),
                },
            },
            services=dict(host.active_services),
        ),
        call_id=replay_id,
    )
    if not called.success:
        logger.error(
            "Learned skill Plugin invocation failed: %s",
            called.error,
        )
        return {
            "ok": False,
            "code": "learned_skill_execution_failed",
            "result": localized(
                "The learned skill could not be run.",
                "无法运行学习技能。",
            ),
        }

    raw = called.value
    if isinstance(raw, dict):
        payload = raw
    else:
        try:
            decoded = json.loads(str(raw or ""))
        except (TypeError, ValueError):
            decoded = None
        payload = decoded if isinstance(decoded, dict) else {}
    if not payload:
        return {
            "ok": False,
            "code": "learned_skill_result_invalid",
            "result": localized(
                "The learned skill returned an invalid Plugin result.",
                "学习技能返回了无效的插件结果。",
            ),
        }
    if not bool(payload.get("ok")):
        return {
            "ok": False,
            "code": str(
                payload.get("code") or "learned_skill_execution_failed"
            ),
            "result": str(
                payload.get("error")
                or localized(
                    "The learned skill could not be run.",
                    "无法运行学习技能。",
                )
            ),
        }

    outputs = [
        f"{str(item.get('tool') or 'Plugin')}: {_truncate_text(item.get('output'), 500)}"
        for item in payload.get("results") or []
        if isinstance(item, dict)
    ]
    return {
        "ok": True,
        "result": (
            "\n".join(outputs)
            if outputs
            else localized(
                "Skill {skill_id} completed.",
                "技能 {skill_id} 已完成。",
                skill_id=skill_id,
            )
        ),
    }


async def run_learned_skill(
    skill_id: str,
    param_overrides: dict[str, Any] | None = None,
) -> str:
    """Compatibility wrapper returning the user-facing execution summary."""

    result = await run_learned_skill_result(skill_id, param_overrides)
    return str(result.get("result") or "")
