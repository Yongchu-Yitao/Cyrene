"""Workspace-scoped conversation (对话) API for the Workbench UI.

This service is intentionally independent from the historical global chat API
(``/api/chat`` in ``route/agent/chat.py``), which remains available for API
compatibility. It exposes project-scoped endpoints under
``/api/workbench/chats*`` while reusing the same per-session agent runtime
(``run_agent(session_id=...)``).

Data model: every Workbench project (workspace) owns two kinds of sessions —
task sessions and chat sessions persisted transactionally in SQLite.
Each chat keeps a public transcript (user / assistant messages with
attachments, tool trace and token usage) that survives agent-side context
compaction; the agent's own raw context lives in
``data/sessions/<chat_id>/state.json`` like any other per-session run.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib
import json
import logging
import mimetypes
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from cyrene.call_llm import NETWORK_RETRY_LIMIT
from cyrene.config import DATA_DIR, WORKSPACE_DIR, cyrene_dir
from cyrene.agent_runtime import builtin as _agent_runtime_builtin
from cyrene.agent_runtime.capabilities import normalize_capabilities as _normalize_capabilities
from cyrene.runtime.memory.conversations import archive_session_exchange
from cyrene.runtime.io import atomic_write_json, read_json_safe
from cyrene.workbench.store import read_document, write_document
from cyrene.workbench.workspace_changes import (
    WorkspaceSnapshot,
    build_change_set,
    capture_workspace_snapshot,
    list_chat_change_sets,
    save_change_set,
)
from cyrene.workbench.chat_runs import ChatRun, ChatRunManager
from cyrene.workbench.compat import runtime_service
from cyrene.workbench.notifications import append_notification

logger = logging.getLogger(__name__)

_CHATS_STORE = DATA_DIR / "workbench_chats.json"
_STORE_DB_PATH = ""
_CONFIGURED_CHATS_STORE = None
_CHAT_RUN_MANAGER = ChatRunManager()


async def generate_chat_group_metadata(
    members: list[dict[str, Any]],
    *,
    lang: str = "zh",
    title_locked: bool = False,
    current_title: str = "",
) -> dict[str, str]:
    """Generate a concise title and summary for a client-managed chat group."""
    from cyrene.agent.model_service import call_agent_model
    from cyrene.model_runtime.messages import assistant_text

    target_lang = "en" if str(lang or "").strip().lower() == "en" else "zh"

    def _collapse(value: Any, max_len: int) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()[:max_len]

    cleaned: list[dict[str, str]] = []
    for raw in (members or [])[:50]:
        if not isinstance(raw, dict):
            continue
        title = _collapse(raw.get("title"), 160)
        preview = _collapse(raw.get("preview"), 800)
        if title or preview:
            cleaned.append({"title": title, "preview": preview})
    if len(cleaned) < 2:
        raise ValueError("at least two chat members are required")

    if target_lang == "en":
        language_rule = (
            "Write both fields in English. Keep title under 48 characters and "
            "summary under 110 characters."
        )
    else:
        language_rule = "标题和摘要必须使用简体中文。标题不超过 18 个汉字，摘要不超过 45 个汉字。"
    title_rule = (
        "The user manually locked the title. Return an empty title and only update the summary."
        if title_locked
        else (
            "Generate a specific shared-topic title; do not return generic placeholders such as "
            "Chat group, New chat group, 对话组, or 新对话组."
        )
    )
    title = ""
    summary = ""
    for attempt in range(2):
        corrective = (
            "Your previous attempt returned an empty title or summary. "
            "Both fields are required and must be non-empty strings."
            if attempt
            else ""
        )
        prompt = (
            "You maintain metadata for a group of related AI conversations. "
            "Infer their shared intent from the supplied titles and previews. "
            "Return one JSON object only with string fields title and summary. "
            "Write the title first, then the summary. "
            "The summary should describe the group's combined subject, not list every conversation.\n"
            f"{language_rule}\n{title_rule}\n{corrective}\n"
            "Current title: " + str(current_title or "")[:160] + "\n"
            "Members JSON:\n" + json.dumps(cleaned, ensure_ascii=False)
        )
        response = await call_agent_model(
            [{"role": "user", "content": prompt}],
            tools=None,
            max_tokens=512,
            caller="workbench_chat_group_metadata",
            secondary=True,
            thinking="low",
            response_format={"type": "json_object"},
        )
        raw_text = assistant_text(response).strip()
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", raw_text)
            parsed = json.loads(match.group(0)) if match else {}
        if not isinstance(parsed, dict):
            parsed = {}
        title = _collapse(parsed.get("title"), 60)
        summary = _collapse(parsed.get("summary"), 160)
        if (title_locked or title) and summary:
            break
        logger.warning(
            "Chat group metadata attempt %s produced empty fields (title=%r, summary=%r)",
            attempt + 1,
            title,
            summary,
        )
    if not title_locked and not title:
        title = next(
            (
                _collapse(item.get("title"), 60)
                for item in cleaned
                if str(item.get("title") or "").strip()
            ),
            "",
        )
    if not summary:
        summary = next(
            (
                _collapse(item.get("preview"), 160)
                for item in cleaned
                if str(item.get("preview") or "").strip()
            ),
            "",
        )
    return {
        "title": "" if title_locked else title,
        "summary": summary,
        "lang": target_lang,
    }


class _WorkspaceChangesLockEntry:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.active: dict[str, _WorkspaceChangesBaseline] = {}


_WORKSPACE_CHANGES_LOCKS: dict[str, _WorkspaceChangesLockEntry] = {}


@dataclass
class _WorkspaceChangesBaseline:
    snapshot: WorkspaceSnapshot | None
    lock_entry: _WorkspaceChangesLockEntry | None = None
    workspace_key: str = ""
    run_id: str = ""
    overlapping_run_ids: set[str] = field(default_factory=set)
    released: bool = False


def _settle_chat_running_status(chat_id: str) -> None:
    """Repair a stale persisted running flag after a run disappears."""
    payload = _read_chats_store()
    chat = _find_chat(payload, str(chat_id))
    if chat and chat.get("status") == "running":
        chat["status"] = "idle"
        chat.pop("pendingQuestion", None)
        chat["updatedAt"] = _utc_now_iso()
        _write_chats_store(payload)


def _record_chat_run_outcome(
    chat_id: str,
    *,
    run_id: str,
    status: str,
    termination_reason: str = "",
    outcome_kind: str = "",
    created_at: str = "",
) -> None:
    """Persist the last exchange outcome alongside the chat summary.

    The event store remains the detailed audit log.  This compact projection is
    the list/topbar source of truth, so a finished error or interruption does not
    collapse back to an indistinguishable ``idle`` chat after the live run is
    released by :class:`ChatRunManager`.
    """
    payload = _read_chats_store()
    chat = _find_chat(payload, str(chat_id))
    if not chat:
        return
    previous = chat.get("lastRun") if isinstance(chat.get("lastRun"), dict) else {}
    previous_created_at = str(previous.get("createdAt") or "")
    if previous_created_at and created_at and previous_created_at > created_at:
        return
    completed_at = _utc_now_iso()
    chat["lastRun"] = {
        "id": str(run_id or ""),
        "status": str(status or "idle"),
        "terminationReason": str(termination_reason or ""),
        "outcome": str(outcome_kind or ""),
        "createdAt": str(created_at or ""),
        "completedAt": completed_at,
    }
    if chat.get("status") == "running":
        chat["status"] = "idle"
    chat["updatedAt"] = completed_at
    _write_chats_store(payload)

# Internal control tools that say nothing useful in a progress trace.
_TRACE_SKIP_TOOLS = {"use_tools", "quit", "send_message", "update_plan_progress"}

# Whitelist for the client-persisted live trace (assembled from SSE tool
# events). Keeps large/private fields (input/output payloads) out of storage.
_DURABLE_TRACE_FIELDS = (
    "kind",
    "toolCallId",
    "text",
    "tool",
    "preview",
    "status",
    "failed",
    "progress",
    "progressCurrent",
    "progressTotal",
    "startedAt",
    "reasoningOffset",
    "detailKey",
    "detailParams",
    "presentation",
)


def _sanitize_durable_traces(traces: list[Any]) -> list[list[dict[str, Any]]]:
    """Sanitize client-uploaded live traces before persisting them.

    Only known scalar fields survive (strings/numbers truncated); nested
    objects are JSON-serialized with a size cap. Per-card entry count mirrors
    the live UI's own ``slice(-40)`` so a card can never grow unbounded.
    """
    sanitized: list[list[dict[str, Any]]] = []
    if not isinstance(traces, list):
        return sanitized
    for raw_trace in traces[:100]:
        entries: list[dict[str, Any]] = []
        if isinstance(raw_trace, list):
            for raw_entry in raw_trace[:40]:
                if not isinstance(raw_entry, dict):
                    continue
                entry: dict[str, Any] = {}
                for key in _DURABLE_TRACE_FIELDS:
                    if key not in raw_entry or raw_entry[key] is None:
                        continue
                    value = raw_entry[key]
                    if isinstance(value, bool):
                        entry[key] = value
                    elif isinstance(value, str):
                        entry[key] = value[:400]
                    elif isinstance(value, (int, float)):
                        if not isinstance(value, bool):
                            entry[key] = value
                    elif isinstance(value, (dict, list)):
                        try:
                            serialized = json.dumps(value, ensure_ascii=False)[:2000]
                        except (TypeError, ValueError):
                            continue
                        if serialized:
                            entry[key] = serialized
                if entry:
                    entries.append(entry)
        sanitized.append(entries)
    return sanitized
_USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


_VISIBLE_PLAN_STATUSES = {"proposed", "active", "paused"}
_PLAN_STEP_STATUSES = {"pending", "in_progress", "completed", "failed", "skipped"}


def _plan_markdown(plan: dict[str, Any]) -> str:
    lines = [f"# {plan.get('title') or '执行计划'}", ""]
    if plan.get("summary"):
        lines.extend([str(plan["summary"]), ""])
    lines.extend([
        f"- 状态：{plan.get('status') or 'proposed'}",
        f"- 对话：{plan.get('chatId') or ''}",
        f"- 计划 ID：{plan.get('planId') or ''}",
        "",
        "## 步骤",
        "",
    ])
    icons = {
        "pending": " ",
        "in_progress": "~",
        "completed": "x",
        "failed": "!",
        "skipped": "-",
    }
    for index, step in enumerate(plan.get("steps") or [], start=1):
        status = str(step.get("status") or "pending")
        lines.append(f"- [{icons.get(status, ' ')}] {index}. {step.get('title') or '步骤'}")
        for task in step.get("tasks") or []:
            lines.append(f"  - {task}")
        if step.get("note"):
            lines.append(f"  - 进度备注：{step['note']}")
    lines.append("")
    return "\n".join(lines)


def _write_plan_markdown(plan: dict[str, Any]) -> None:
    raw_path = str(plan.get("markdownPath") or "").strip()
    if not raw_path:
        return
    path = _resolve_managed_plan_path(raw_path)
    if path != Path(raw_path):
        plan["markdownPath"] = str(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_plan_markdown(plan), encoding="utf-8")


def _resolve_managed_plan_path(raw_path: str) -> Path:
    """Rebase plan mirrors to the workspace's hidden ``.cyrene/plan`` dir.

    Current installs store plan mirrors under ``.cyrene/plan``. Legacy records
    carry either the pre-migration location under the workspace root
    (``workspace/plan/...``, ``workspace/projects/.../plan/...``) or a
    cross-machine path containing a ``/workspace/`` segment; both rebase into
    the hidden location so stored markdown paths stay valid. Paths outside the
    workspace (user-selected folders) are left untouched.
    """
    raw = str(raw_path or "").strip()
    direct = Path(raw).expanduser()
    try:
        resolved = direct.resolve()
        managed_plan_root = (cyrene_dir(WORKSPACE_DIR) / "plan").resolve()
        if resolved == managed_plan_root or managed_plan_root in resolved.parents:
            return resolved
    except Exception:
        pass

    normalized = raw.replace("\\", "/")
    marker = "/workspace/"
    marker_index = normalized.lower().rfind(marker)
    if marker_index >= 0 and (
        normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized)
    ):
        relative = normalized[marker_index + len(marker):]
        if relative == "plan" or relative.startswith(("plan/", "projects/")):
            candidate = (cyrene_dir(WORKSPACE_DIR) / Path(relative)).resolve()
            root = WORKSPACE_DIR.resolve()
            if candidate == root or root in candidate.parents:
                return candidate
    return direct


def _normalize_chat_plan(
    chat_id: str,
    plan: dict[str, Any],
    *,
    round_id: str = "",
    workspace_dir: str | Path | None = None,
) -> dict[str, Any]:
    out = dict(plan or {})
    plan_id = str(out.get("planId") or f"plan_{uuid.uuid4().hex[:10]}")
    now = _utc_now_iso()
    steps: list[dict[str, Any]] = []
    for index, raw_step in enumerate(out.get("steps") or [], start=1):
        if not isinstance(raw_step, dict):
            continue
        step = dict(raw_step)
        step["id"] = str(step.get("id") or f"step_{index}")
        status = str(step.get("status") or "pending")
        step["status"] = status if status in _PLAN_STEP_STATUSES else "pending"
        step["note"] = str(step.get("note") or "")
        step["tasks"] = [str(item) for item in (step.get("tasks") or [])]
        steps.append(step)
    out.update({
        "planId": plan_id,
        "chatId": chat_id,
        "roundId": str(out.get("roundId") or round_id),
        "status": str(out.get("status") or "proposed"),
        "steps": steps,
        "createdAt": str(out.get("createdAt") or now),
        "updatedAt": now,
    })
    if workspace_dir and not out.get("markdownPath"):
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", plan_id).strip("-") or "plan"
        out["markdownPath"] = str(cyrene_dir(workspace_dir) / "plan" / f"{slug}.md")
    return out


def persist_chat_plan(
    chat_id: str,
    plan: dict[str, Any],
    *,
    round_id: str = "",
    workspace_dir: str | Path | None = None,
) -> dict[str, Any]:
    payload = _read_chats_store()
    chat = _find_chat(payload, str(chat_id or ""))
    if not chat:
        return plan
    stored = _normalize_chat_plan(chat["id"], plan, round_id=round_id, workspace_dir=workspace_dir)
    chat["activePlan"] = stored
    chat["updatedAt"] = stored["updatedAt"]
    _write_chats_store(payload)
    _write_plan_markdown(stored)
    return stored


def _mutate_chat_plan(chat_id: str, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any] | None:
    payload = _read_chats_store()
    chat = _find_chat(payload, str(chat_id or ""))
    plan = chat.get("activePlan") if chat and isinstance(chat.get("activePlan"), dict) else None
    if not chat or not plan:
        return None
    mutate(plan)
    plan["updatedAt"] = _utc_now_iso()
    chat["updatedAt"] = plan["updatedAt"]
    _write_chats_store(payload)
    _write_plan_markdown(plan)
    return dict(plan)


def activate_chat_plan(chat_id: str, fallback_plan: dict[str, Any] | None = None) -> dict[str, Any]:
    def mutate(plan: dict[str, Any]) -> None:
        plan["status"] = "active"
        if plan.get("steps") and not any(step.get("status") == "in_progress" for step in plan["steps"]):
            plan["steps"][0]["status"] = "in_progress"

    updated = _mutate_chat_plan(chat_id, mutate)
    return updated or dict(fallback_plan or {})


def reject_chat_plan(chat_id: str, fallback_plan: dict[str, Any] | None = None) -> dict[str, Any]:
    def mutate(plan: dict[str, Any]) -> None:
        plan["status"] = "rejected"

    updated = _mutate_chat_plan(chat_id, mutate)
    return updated or dict(fallback_plan or {})


def update_chat_plan_progress(
    chat_id: str,
    step_number: int,
    status: str,
    note: str = "",
) -> dict[str, Any] | None:
    if status not in _PLAN_STEP_STATUSES - {"pending"}:
        return None

    def mutate(plan: dict[str, Any]) -> None:
        if str(plan.get("status") or "") not in {"active", "paused"}:
            raise ValueError("plan is not active")
        steps = plan.get("steps") or []
        if step_number < 1 or step_number > len(steps):
            raise IndexError("plan step out of range")
        step = steps[step_number - 1]
        step["status"] = status
        step["note"] = note
        if status == "in_progress":
            plan["status"] = "active"
            for index, other in enumerate(steps):
                if index != step_number - 1 and other.get("status") == "in_progress":
                    other["status"] = "pending"

    try:
        return _mutate_chat_plan(chat_id, mutate)
    except (ValueError, IndexError):
        return None


def complete_chat_plan(chat_id: str) -> dict[str, Any] | None:
    def mutate(plan: dict[str, Any]) -> None:
        if str(plan.get("status") or "") not in {"active", "paused"}:
            return
        plan["status"] = "completed"
        for step in plan.get("steps") or []:
            if step.get("status") in {"pending", "in_progress"}:
                step["status"] = "completed"

    return _mutate_chat_plan(chat_id, mutate)


def _workbench_http_status_error(exc: Exception) -> httpx.HTTPStatusError | None:
    """Find an upstream HTTP response even when an integration wrapped it."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, httpx.HTTPStatusError):
            return current
        current = current.__cause__ or current.__context__
    return None


def _workbench_chat_run_error_message(exc: Exception, lang: str = "") -> str:
    """Return a user-facing message after bounded model-network retries."""
    http_error = _workbench_http_status_error(exc)
    if http_error is not None:
        status = int(http_error.response.status_code)
        english = str(lang or "").lower() == "en"
        if status in (401, 403):
            if english:
                return "The model service could not be authenticated. Check its API key or sign-in, then try again."
            return "无法访问模型服务：鉴权失败。请检查 API Key 或登录状态后重试。"
    if isinstance(exc, httpx.TransportError):
        if str(lang or "").lower() == "en":
            return (
                f"The network connection still failed after {NETWORK_RETRY_LIMIT} automatic retries. "
                "Please send this message again."
            )
        return f"网络连接异常，已自动重试 {NETWORK_RETRY_LIMIT} 次仍未成功。请重新发送这条消息。"
    return str(exc).strip() or exc.__class__.__name__


_WORKBENCH_CHAT_ERROR_I18N_KEYS = {
    "quota_exhausted": "workbenchChat.error.quotaExhausted",
    "authentication_expired": "workbenchChat.error.authenticationExpired",
    "model_unavailable": "workbenchChat.error.modelUnavailable",
    "model_not_configured": "workbenchChat.error.modelNotConfigured",
    "model_authentication_failed": "workbenchChat.error.modelAuthenticationFailed",
}


def _workbench_chat_error_metadata(exc: Exception) -> dict[str, str]:
    """Attach stable error metadata so clients can localize known failures.

    The Workbench stream may outlive the request that started it, so sending a
    translated string from the server is not enough: the client needs a stable
    error code/key to render the banner in its current language. Unknown errors
    intentionally return no metadata and keep their diagnostic message.
    """
    failure_kind = str(getattr(exc, "kind", "") or "").strip()
    if failure_kind:
        failure_key = _WORKBENCH_CHAT_ERROR_I18N_KEYS.get(failure_kind, "")
        if failure_key:
            return {"code": failure_kind, "detail_key": failure_key}
        return {"code": failure_kind, "failureKind": failure_kind}
    direct_code = str(getattr(exc, "code", "") or "").strip()
    direct_key = _WORKBENCH_CHAT_ERROR_I18N_KEYS.get(direct_code, "")
    if direct_code and direct_key:
        return {"code": direct_code, "detail_key": direct_key}

    http_error = _workbench_http_status_error(exc)
    if http_error is not None and int(http_error.response.status_code) in (401, 403):
        return {
            "code": "model_authentication_failed",
            "detail_key": "workbenchChat.error.modelAuthenticationFailed",
        }

    try:
        from cyrene.model_runtime.codex_provider import codex_availability_error

        availability = codex_availability_error(exc)
    except Exception:
        availability = None
    if availability is None:
        return {}

    code = str(getattr(availability, "kind", "") or "").strip()
    detail_key = _WORKBENCH_CHAT_ERROR_I18N_KEYS.get(code, "")
    if not code or not detail_key:
        return {}
    return {"code": code, "detail_key": detail_key}


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

# Legacy JSON chats store persists whole-file atomic replaces with no built-in
# serialization, so concurrent read-modify-write sequences (e.g. the route's
# inline finalize and the detached workspace finalize) can clobber each other.
# SQLite mode uses write_document's own merge lock and ignores this one.
_CHATS_STORE_JSON_LOCK = threading.Lock()


def _read_chats_store() -> dict[str, Any]:
    if not _STORE_DB_PATH or _CONFIGURED_CHATS_STORE != _CHATS_STORE:
        data = read_json_safe(_CHATS_STORE)
        if isinstance(data, dict) and isinstance(data.get("chats"), list):
            return data
        return {"chats": []}
    data = read_document(
        _STORE_DB_PATH,
        "chats",
        lambda: {"chats": []},
        legacy_path=_CHATS_STORE,
    )
    if isinstance(data, dict) and isinstance(data.get("chats"), list):
        return data
    return {"chats": []}


def _write_chats_store(payload: dict[str, Any]) -> None:
    if not _STORE_DB_PATH or _CONFIGURED_CHATS_STORE != _CHATS_STORE:
        atomic_write_json(_CHATS_STORE, payload)
        return
    merged = write_document(
        _STORE_DB_PATH,
        "chats",
        payload,
        lambda: {"chats": []},
        legacy_path=_CHATS_STORE,
        export_path=_CHATS_STORE,
    )
    payload.clear()
    payload.update(merged)
    if hasattr(payload, "_workbench_base"):
        payload._workbench_base = getattr(merged, "_workbench_base", dict(merged))


def _reconcile_inbox_guidance_messages(db_path: str) -> int:
    """Repair the narrow crash window between inbox and transcript commits.

    Guidance is durable before it is delivered to the agent.  Its public
    transcript entry is a separate Workbench document write, so a process can
    stop between the two commits.  The durable inbox row carries the stable
    public message id needed to restore that entry exactly once on startup.
    """
    from cyrene.workbench.inbox import read_workbench_guidance_records

    records = read_workbench_guidance_records(db_path)
    if not records:
        return 0
    payload = _read_chats_store()
    chats = {
        str(chat.get("id") or ""): chat
        for chat in payload.get("chats", []) or []
        if isinstance(chat, dict)
    }
    repaired = 0
    for record in records:
        chat = chats.get(str(record.get("sessionId") or ""))
        if chat is None:
            continue
        messages = chat.setdefault("messages", [])
        event_id = str(record.get("eventId") or "")
        message_id = str(record.get("messageId") or "")
        if any(
            str(item.get("id") or "") == message_id
            or (
                event_id
                and str(item.get("guidanceEventId") or "") == event_id
            )
            for item in messages
            if isinstance(item, dict)
        ):
            continue
        entry = {
            "id": message_id,
            "role": "user",
            "content": str(record.get("content") or ""),
            "createdAt": str(record.get("createdAt") or _utc_now_iso()),
            "guidance": True,
            "guidanceEventId": event_id,
            "runId": str(record.get("runId") or ""),
        }
        client_request_id = str(record.get("clientRequestId") or "")
        if client_request_id:
            entry["clientRequestId"] = client_request_id
        _merge_chat_messages_chronologically(chat, [entry])
        chat["updatedAt"] = max(
            str(chat.get("updatedAt") or ""), str(entry["createdAt"])
        )
        repaired += 1
    if repaired:
        _write_chats_store(payload)
    return repaired


def configure_store(db_path: str) -> None:
    global _STORE_DB_PATH, _CONFIGURED_CHATS_STORE
    _STORE_DB_PATH = str(db_path or "")
    _CONFIGURED_CHATS_STORE = _CHATS_STORE


def startup_chat_runs() -> None:
    _CHAT_RUN_MANAGER.startup()


async def shutdown_chat_runs() -> None:
    await _CHAT_RUN_MANAGER.shutdown()
    try:
        from cyrene.workbench.chat_runs import drain_post_reply_bookkeeping_tasks

        await drain_post_reply_bookkeeping_tasks()
    except Exception:
        logger.exception("Workbench post-reply bookkeeping drain failed")


async def _capture_workspace_changes_baseline(
    workspace_dir: str | Path | None,
    run_id: str = "",
) -> _WorkspaceChangesBaseline:
    """Register a run and take its pre-run image.

    Only the snapshot/registry transition is serialized. Agent execution is not:
    another conversation in the same workspace must be able to start while this
    one is still producing output. When run intervals overlap, both baselines
    record that fact so their eventual change sets can report non-exclusive
    attribution instead of silently claiming another run's edits.
    """
    if not workspace_dir:
        return _WorkspaceChangesBaseline(snapshot=None)
    try:
        workspace_key = str(Path(workspace_dir).expanduser().resolve())
    except OSError:
        return _WorkspaceChangesBaseline(snapshot=None)
    entry = _WORKSPACE_CHANGES_LOCKS.setdefault(
        workspace_key, _WorkspaceChangesLockEntry()
    )
    normalized_run_id = str(run_id or f"snapshot_{uuid.uuid4().hex}")
    async with entry.lock:
        try:
            snapshot = await asyncio.to_thread(
                capture_workspace_snapshot, workspace_key
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to capture Workbench workspace baseline")
            if not entry.active:
                _WORKSPACE_CHANGES_LOCKS.pop(workspace_key, None)
            return _WorkspaceChangesBaseline(snapshot=None)
        if snapshot is None:
            if not entry.active:
                _WORKSPACE_CHANGES_LOCKS.pop(workspace_key, None)
            return _WorkspaceChangesBaseline(snapshot=None)
        baseline = _WorkspaceChangesBaseline(
            snapshot=snapshot,
            lock_entry=entry,
            workspace_key=workspace_key,
            run_id=normalized_run_id,
        )
        for other_run_id, other in entry.active.items():
            baseline.overlapping_run_ids.add(other_run_id)
            other.overlapping_run_ids.add(normalized_run_id)
        entry.active[normalized_run_id] = baseline
        return baseline


async def _complete_workspace_changes_baseline(
    before: _WorkspaceChangesBaseline | None,
    workspace_dir: str | Path | None,
) -> WorkspaceSnapshot | None:
    """Take the post-run image and atomically unregister the active interval."""
    if before is None or before.released:
        return None
    entry = before.lock_entry
    if entry is None:
        before.released = True
        return await asyncio.to_thread(
            capture_workspace_snapshot,
            workspace_dir,
            previous=before.snapshot,
        )
    async with entry.lock:
        if before.released:
            return None
        try:
            return await asyncio.to_thread(
                capture_workspace_snapshot,
                workspace_dir,
                previous=before.snapshot,
            )
        finally:
            before.released = True
            if entry.active.get(before.run_id) is before:
                entry.active.pop(before.run_id, None)
            if (
                before.workspace_key
                and not entry.active
                and _WORKSPACE_CHANGES_LOCKS.get(before.workspace_key) is entry
            ):
                _WORKSPACE_CHANGES_LOCKS.pop(before.workspace_key, None)


async def _finalize_workspace_changes(
    *,
    chat_id: str,
    run_id: str,
    workspace_dir: str | Path | None,
    before: _WorkspaceChangesBaseline | None,
    status: str,
    run: ChatRun | None = None,
) -> dict[str, Any] | None:
    """Persist and publish the non-Git change set for one run.

    Snapshot attribution is exact for an exclusive interval. If another run
    overlaps in the same workspace, the change set explicitly carries that
    ambiguity instead of delaying either conversation.
    """
    try:
        if before is None or before.snapshot is None:
            return None
        after = await _complete_workspace_changes_baseline(before, workspace_dir)
        overlapping_run_ids = sorted(before.overlapping_run_ids)
        change_set = await asyncio.to_thread(
            build_change_set,
            chat_id=chat_id,
            run_id=run_id,
            before=before.snapshot,
            after=after,
            status=status,
            attribution="overlapping" if overlapping_run_ids else "exclusive",
            overlapping_run_ids=overlapping_run_ids,
        )
        if change_set.get("fileCount"):
            await asyncio.to_thread(save_change_set, _STORE_DB_PATH, change_set)
            await asyncio.to_thread(
                _sync_chat_generated_files,
                chat_id,
                change_set,
            )
        event = {
            "type": "workspace_changes",
            "chatId": chat_id,
            "runId": run_id,
            "changeSetId": change_set.get("id"),
            "status": status,
            "fileCount": int(change_set.get("fileCount") or 0),
            "additions": int(change_set.get("additions") or 0),
            "deletions": int(change_set.get("deletions") or 0),
            "attribution": str(change_set.get("attribution") or "exclusive"),
            "overlappingRunIds": list(
                change_set.get("overlappingRunIds") or []
            ),
        }
        if run is not None:
            await run.publish(event)
        from cyrene.observability import debug

        await debug.publish_event(event, session_id=chat_id)
        return change_set
    except Exception:
        # Change tracking is observability. It must never hide or replace the
        # agent's real reply, pending question, or error outcome.
        logger.exception("Failed to finalize workspace changes for chat %s", chat_id)
        return None
    finally:
        if before is not None and not before.released:
            await _complete_workspace_changes_baseline(before, workspace_dir)


def _sync_chat_generated_files(
    chat_id: str,
    change_set: dict[str, Any] | None = None,
) -> None:
    """Keep a current file index for the conversation's right-hand Files panel.

    Workspace change sets are run-scoped and optimized for diffs.  The panel,
    however, needs one de-duplicated view of files that still exist after all
    runs.  Persist only workspace-relative metadata; the download route resolves
    and confines the path against the project's current workspace root.
    """
    with _CHATS_STORE_JSON_LOCK:
        _sync_chat_generated_files_locked(chat_id, change_set)


def _sync_chat_generated_files_locked(
    chat_id: str,
    change_set: dict[str, Any] | None = None,
) -> None:
    payload = _read_chats_store()
    chat = _find_chat(payload, chat_id)
    if not chat:
        return
    existing: dict[str, dict[str, Any]] = {
        str(item.get("path") or ""): dict(item)
        for item in chat.get("generatedFiles") or []
        if isinstance(item, dict) and str(item.get("path") or "")
    }
    change_sets: list[dict[str, Any]] = []
    if "generatedFiles" not in chat:
        # One-time migration for conversations created before the Files panel
        # indexed workspace output. Apply oldest -> newest to preserve deletes.
        change_sets.extend(reversed(list_chat_change_sets(_STORE_DB_PATH, chat_id)))
    if change_set is not None:
        change_sets.append(change_set)
    changed_paths: list[str] = []
    for item in change_sets:
        for change in item.get("files") or []:
            if not isinstance(change, dict):
                continue
            path = str(change.get("path") or "").strip().replace("\\", "/")
            if not path:
                continue
            if path in changed_paths:
                changed_paths.remove(path)
            changed_paths.append(path)
            if str(change.get("changeType") or "") == "deleted":
                existing.pop(path, None)
                continue
            name = Path(path).name or path
            existing[path] = {
                "id": str(change.get("id") or f"workspace_{uuid.uuid4().hex[:12]}"),
                "name": name,
                "path": path,
                "content_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
                "size": int(change.get("afterSize") or 0),
                "kind": "file",
                "source": "agent",
            }
    ordered = [existing[path] for path in changed_paths if path in existing]
    ordered.extend(item for path, item in existing.items() if path not in changed_paths)
    chat["generatedFiles"] = ordered[:200]
    _write_chats_store(payload)


def _mark_user_activity(chat: dict[str, Any], timestamp: str) -> None:
    """Record real user activity and restart the proactive lottery window."""
    from importlib import import_module

    chat["lastUserMessageAt"] = timestamp
    chat["updatedAt"] = timestamp
    import_module("cyrene.runtime.scheduler").reset_lottery()


async def append_proactive_message(chat_id: str, text: str) -> dict[str, str] | None:
    """Persist a proactive assistant reply in an existing public transcript.

    Kept for compatibility with older callers. New scheduler deliveries use
    :func:`create_proactive_chat` so autonomous work never lands in a user's
    existing conversation session.
    """
    from cyrene.observability import debug
    from cyrene.agent.state import sanitize_public_agent_text

    payload = _read_chats_store()
    chat = _find_chat(payload, str(chat_id or ""))
    content = sanitize_public_agent_text(text)
    if not chat or not content:
        return None
    now = _utc_now_iso()
    message = {
        "id": _short_id("msg"),
        "role": "assistant",
        "content": content,
        "createdAt": now,
        "model": str(chat.get("model") or ""),
        "proactive": True,
        "systemInitiated": True,
    }
    chat.setdefault("messages", []).append(message)
    chat["status"] = "idle"
    chat["updatedAt"] = now
    _write_chats_store(payload)

    result = {
        "chat_id": str(chat.get("id") or ""),
        "project_id": str(chat.get("projectId") or ""),
        "title": str(chat.get("title") or "新对话"),
    }
    await debug.publish_event({
        "type": "workbench_proactive_message",
        "session_id": result["chat_id"],
        "chat_id": result["chat_id"],
        "project_id": result["project_id"],
        "updated_at": now,
        "message": _public_message(message),
    })
    return result


async def create_proactive_chat(
    project_id: str,
    text: str,
    *,
    chat_id: str = "",
    model: str = "",
    source_chat_id: str = "",
    lang: str = "",
) -> dict[str, str] | None:
    """Create a dedicated Workbench chat containing one proactive reply."""
    from cyrene.agent.state import sanitize_public_agent_text
    from cyrene.observability import debug

    content = sanitize_public_agent_text(text)
    project_id = str(project_id or "").strip()
    if not project_id or not content:
        return None

    title = "Proactive work" if str(lang or "").lower() == "en" else "主动工作"
    from cyrene.workbench.project_memory_prompt import current_snapshot

    chat = _new_chat(
        project_id,
        title,
        str(model or ""),
        project_memory_snapshot=current_snapshot(project_id),
    )
    if chat_id:
        chat["id"] = str(chat_id)
    chat["proactive"] = True
    if source_chat_id:
        chat["sourceChatId"] = str(source_chat_id)

    now = _utc_now_iso()
    message = {
        "id": _short_id("msg"),
        "role": "assistant",
        "content": content,
        "createdAt": now,
        "model": str(model or ""),
        "proactive": True,
        "systemInitiated": True,
    }
    chat["messages"] = [message]
    chat["updatedAt"] = now

    payload = _read_chats_store()
    payload.setdefault("chats", []).insert(0, chat)
    _write_chats_store(payload)

    result = {
        "chat_id": str(chat["id"]),
        "project_id": project_id,
        "title": str(chat["title"]),
    }
    await debug.publish_event({
        "type": "workbench_chat_changed",
        "change": "created",
        "session_id": result["chat_id"],
        "chat_id": result["chat_id"],
        "project_id": project_id,
    }, session_id=result["chat_id"])
    await debug.publish_event({
        "type": "workbench_proactive_message",
        "session_id": result["chat_id"],
        "chat_id": result["chat_id"],
        "project_id": project_id,
        "updated_at": now,
        "message": _public_message(message),
    }, session_id=result["chat_id"])
    return result


def _new_chat(
    project_id: str,
    title: str = "",
    model: str = "",
    *,
    project_memory_snapshot: dict[str, Any] | None = None,
    agent: dict[str, Any] | None = None,
    model_access: dict[str, Any] | None = None,
    capabilities: dict[str, Any] | None = None,
    soul_active: bool | None = None,
    workspace_active: bool | None = None,
    reasoning_effort: str = "",
) -> dict[str, Any]:
    now = _utc_now_iso()
    supplied_title = str(title or "").strip()
    chat = {
        "id": _short_id("wbchat"),
        "projectId": str(project_id or ""),
        "kind": "chat",
        "title": supplied_title[:60] or "新对话",
        "titleLocked": bool(supplied_title),
        "status": "idle",
        "model": model,
        "permissionMode": "auto",
        "createdAt": now,
        "updatedAt": now,
        "messages": [],
        "completedTurnCount": 0,
    }
    if soul_active is None or workspace_active is None:
        from cyrene.runtime.settings_store import is_soul_active, is_workspace_active

        if soul_active is None:
            soul_active = bool(is_soul_active())
        if workspace_active is None:
            workspace_active = bool(is_workspace_active())
    chat["soulActive"] = bool(soul_active)
    chat["workspaceActive"] = bool(workspace_active)
    if reasoning_effort:
        chat["reasoningEffort"] = str(reasoning_effort)
    if project_memory_snapshot is not None:
        chat["projectMemorySnapshot"] = {
            "prompt": str(project_memory_snapshot.get("prompt") or ""),
            "modifiedAt": str(project_memory_snapshot.get("modifiedAt") or ""),
            "hash": str(project_memory_snapshot.get("hash") or ""),
        }
    # Agent binding / model source / capability snapshot. Legacy calls without
    # agent fields normalize to the built-in Cyrene Agent (backward compatible).
    chat.update(
        _agent_runtime_builtin.normalize_agent_fields(
            agent,
            model_access,
            default_model=model,
            capabilities_raw=capabilities,
        )
    )
    return chat


def _chat_soul_active(chat: dict[str, Any]) -> bool:
    if isinstance(chat.get("soulActive"), bool):
        return bool(chat["soulActive"])
    from cyrene.runtime.settings_store import is_soul_active

    return bool(is_soul_active())


def _chat_workspace_active(chat: dict[str, Any]) -> bool:
    if isinstance(chat.get("workspaceActive"), bool):
        return bool(chat["workspaceActive"])
    from cyrene.runtime.settings_store import is_workspace_active

    return bool(is_workspace_active())


def _normalize_workspace_override(path: Any) -> str:
    """Return a validated absolute directory selected for one conversation."""
    raw = str(path or "").strip()
    if not raw:
        return ""
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise ValueError("workspace override must be an absolute path")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("workspace override does not exist") from exc
    if not resolved.is_dir():
        raise ValueError("workspace override must be a directory")
    return str(resolved)


def _resolve_chat_workspace_dir(
    chat: dict[str, Any],
    project: dict[str, Any],
    project_workspace_resolver: Callable[[dict[str, Any] | None], str],
) -> str:
    """Resolve a chat override before falling back to its project workspace."""
    override = str(chat.get("workspaceOverride") or "").strip()
    if not override:
        return project_workspace_resolver(project)
    normalized = _normalize_workspace_override(override)
    return project_workspace_resolver({"workspacePath": normalized})


def _completed_turn_count(chat: dict[str, Any]) -> int:
    """Return durable completed user→final-assistant exchanges for one chat."""
    stored = chat.get("completedTurnCount")
    if isinstance(stored, int) and not isinstance(stored, bool) and stored >= 0:
        return stored
    return sum(
        1
        for message in chat.get("messages") or []
        if isinstance(message, dict)
        and str(message.get("role") or "") == "assistant"
        and "processingDurationMs" in message
        and not bool(message.get("systemInitiated"))
    )


def _next_completed_turn_count(
    chat: dict[str, Any],
    *,
    retry: bool = False,
    command: str = "",
    is_side_agent: bool = False,
) -> int:
    count = _completed_turn_count(chat)
    if not retry and not command and not is_side_agent:
        count += 1
    return count


def _find_chat(payload: dict[str, Any], chat_id: str) -> dict[str, Any] | None:
    for chat in payload.get("chats", []):
        if str(chat.get("id") or "") == chat_id:
            return chat
    return None


def get_workbench_chat(chat_id: str) -> dict[str, Any] | None:
    """Return a defensive snapshot of one persisted Workbench conversation."""
    chat = _find_chat(_read_chats_store(), str(chat_id or ""))
    return copy.deepcopy(chat) if chat is not None else None


def completed_turn_count(chat: dict[str, Any]) -> int:
    """Public boundary for counting completed conversation turns."""
    return _completed_turn_count(chat)


def _persist_agent_fields(chat: dict[str, Any], fields: dict[str, Any]) -> None:
    chat.update(fields)
    chat["updatedAt"] = _utc_now_iso()


def apply_chat_agent_binding(
    chat_id: str,
    *,
    agent: dict[str, Any] | None = None,
    model_access: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Attach a draft agent binding/model access to an existing empty chat.

    Used by the composer draft-binding flow (handoff §8.3) when the frontend
    created the chat lazily.  Refuses (returns ``None``) once the chat has
    messages or a locked binding — the caller maps that to the ``409
    agent_binding_locked`` contract.  Returns the updated chat when applied.
    """
    payload = _read_chats_store()
    chat = _find_chat(payload, str(chat_id or ""))
    if not chat:
        return None
    existing_agent = chat.get("agent") if isinstance(chat.get("agent"), dict) else {}
    if bool(existing_agent.get("bindingLocked")) or bool(chat.get("messages")):
        return None
    _persist_agent_fields(
        chat,
        _agent_runtime_builtin.normalize_agent_fields(
            agent,
            model_access,
            default_model=str(chat.get("model") or ""),
        ),
    )
    _write_chats_store(payload)
    return chat


def lock_chat_agent_binding(chat_id: str) -> dict[str, Any] | None:
    """Lock the persisted agent binding after the first message is queued."""
    payload = _read_chats_store()
    chat = _find_chat(payload, str(chat_id or ""))
    if not chat:
        return None
    agent = dict(_agent_runtime_builtin.chat_agent_fields(chat)["agent"])
    agent["bindingLocked"] = True
    chat["agent"] = agent
    chat["updatedAt"] = _utc_now_iso()
    _write_chats_store(payload)
    return chat


def set_chat_external_session_id(
    chat_id: str,
    external_session_id: str,
) -> dict[str, Any] | None:
    """Persist the Agent-side session id on the chat binding (§14)."""
    payload = _read_chats_store()
    chat = _find_chat(payload, str(chat_id or ""))
    if not chat:
        return None
    agent = dict(_agent_runtime_builtin.chat_agent_fields(chat)["agent"])
    agent["externalSessionId"] = str(external_session_id or "").strip()
    chat["agent"] = agent
    chat["updatedAt"] = _utc_now_iso()
    _write_chats_store(payload)
    return chat


def update_chat_agent_context_report(
    chat_id: str,
    report: dict[str, Any],
) -> dict[str, Any] | None:
    """Persist a bounded context-window report supplied by an external Agent."""
    payload = _read_chats_store()
    chat = _find_chat(payload, str(chat_id or ""))
    if not chat:
        return None

    def safe_int(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    normalized_segments: list[dict[str, Any]] = []
    raw_segments = report.get("segments")
    if isinstance(raw_segments, list):
        for index, item in enumerate(raw_segments[:32]):
            if not isinstance(item, dict):
                continue
            tokens = safe_int(item.get("tokens") or item.get("tokens_est") or item.get("used"))
            if tokens <= 0:
                continue
            key = str(item.get("key") or item.get("id") or item.get("type") or f"segment_{index + 1}").strip()[:80]
            label = str(item.get("label") or item.get("name") or key).strip()[:120]
            normalized_segments.append({"key": key, "label": label, "tokens": tokens})

    normalized = {
        "used": safe_int(report.get("used") or report.get("totalTokens")),
        "size": safe_int(report.get("size") or report.get("limit") or report.get("contextWindow")),
        "segments": normalized_segments,
        "updatedAt": _utc_now_iso(),
    }
    if normalized["used"] <= 0 and normalized_segments:
        normalized["used"] = sum(item["tokens"] for item in normalized_segments)
    if normalized["used"] <= 0 and normalized["size"] <= 0 and not normalized_segments:
        return chat
    chat["agentContextReport"] = normalized
    chat["updatedAt"] = _utc_now_iso()
    _write_chats_store(payload)
    return chat


def update_chat_capabilities(
    chat_id: str,
    capabilities: dict[str, Any],
    *,
    revision: int | None = None,
) -> dict[str, Any] | None:
    """Persist a normalized capability snapshot with a bumped revision.

    Probe results update capabilities over the chat lifetime; every update
    requires an increasing ``capabilitiesRevision`` (handoff §14).  When
    ``revision`` is omitted it is derived from the stored value + 1.
    """
    payload = _read_chats_store()
    chat = _find_chat(payload, str(chat_id or ""))
    if not chat:
        return None
    stored = chat.get("capabilitiesRevision")
    if not isinstance(revision, int) or revision < 0:
        revision = (
            stored
            if isinstance(stored, int) and not isinstance(stored, bool) and stored >= 0
            else 0
        ) + 1
    chat["capabilities"] = _normalize_capabilities(capabilities)
    chat["capabilitiesRevision"] = revision
    chat["updatedAt"] = _utc_now_iso()
    _write_chats_store(payload)
    return chat


_FORK_METADATA_FIELDS = ("forkedFromChatId", "forkedAtMessageId", "forkMessage")


def _clear_fork_metadata(chat: dict[str, Any]) -> bool:
    changed = False
    for metadata_field in _FORK_METADATA_FIELDS:
        if metadata_field in chat:
            chat.pop(metadata_field, None)
            changed = True
    return changed


def _prune_orphaned_fork_metadata(payload: dict[str, Any]) -> bool:
    """Drop branch metadata when the source chat no longer exists."""
    chats = payload.get("chats") if isinstance(payload.get("chats"), list) else []
    chat_ids = {
        str(chat.get("id") or "")
        for chat in chats
        if isinstance(chat, dict) and str(chat.get("id") or "")
    }
    changed = False
    for chat in chats:
        if not isinstance(chat, dict):
            continue
        parent_id = str(chat.get("forkedFromChatId") or "").strip()
        if parent_id and parent_id not in chat_ids:
            changed = _clear_fork_metadata(chat) or changed
    return changed


def _message_event_time(message: dict[str, Any]) -> datetime | None:
    """Parse a transcript timestamp for stable chronological repair."""
    raw = str(message.get("createdAt") or message.get("created_at") or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _messages_in_chronological_order(messages: list[Any]) -> list[Any]:
    """Repair fully timestamped transcripts without disturbing legacy data.

    Older records can lack event times, so those transcripts retain their
    stored order. Modern transcripts are stably sorted, which also repairs
    records written before intermediate-message dedupe became round-scoped.
    """
    if not messages:
        return list(messages)
    event_times: list[datetime] = []
    for message in messages:
        if not isinstance(message, dict):
            return list(messages)
        event_time = _message_event_time(message)
        if event_time is None:
            return list(messages)
        event_times.append(event_time)
    ordered = sorted(
        enumerate(messages),
        key=lambda pair: (event_times[pair[0]], pair[0]),
    )
    return [message for _index, message in ordered]


def _chat_preview(chat: dict[str, Any]) -> str:
    for message in reversed(chat.get("messages") or []):
        text = str(message.get("content") or "").strip()
        if text:
            return text.replace("\n", " ")[:80]
    return ""


def _chat_first_message(chat: dict[str, Any]) -> str:
    """Opening line of a conversation — the branch tree's root node label.

    Prefers the first user message; falls back to the first non-empty entry of
    any role so empty-prompt edge cases still surface something.
    """
    messages = chat.get("messages") or []
    for message in messages:
        if str(message.get("role") or "") != "user":
            continue
        text = str(message.get("content") or "").strip()
        if text:
            return text.replace("\n", " ")[:80]
    for message in messages:
        text = str(message.get("content") or "").strip()
        if text:
            return text.replace("\n", " ")[:80]
    return ""


def _side_agent_parent_transcript(chat: dict[str, Any] | None) -> str:
    """Serialize the complete public parent conversation for a side agent."""
    if not isinstance(chat, dict):
        return ""
    sections: list[str] = []
    for index, message in enumerate(chat.get("messages") or [], start=1):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "message").strip() or "message"
        content = str(
            message.get("content")
            or message.get("body")
            or message.get("reasoning")
            or ""
        ).strip()
        attachment_names = [
            str(item.get("name") or item.get("title") or "").strip()
            for item in (message.get("attachments") or [])
            if isinstance(item, dict)
            and str(item.get("name") or item.get("title") or "").strip()
        ]
        if attachment_names:
            attachment_line = "Attachments: " + ", ".join(attachment_names)
            content = f"{content}\n{attachment_line}".strip()
        if not content:
            continue
        sections.append(f"[{index}. {role}]\n{content}")
    return "\n\n".join(sections)


def _public_chat_light(chat: dict[str, Any]) -> dict[str, Any]:
    """Listing payload — transcript omitted to keep the rail cheap."""
    usage = _aggregate_usage(chat.get("messages") or [])
    chat_id = str(chat.get("id") or "")
    active_run = _CHAT_RUN_MANAGER.get(chat_id) if chat_id else None
    persisted_status = str(chat.get("status") or "idle")
    last_run = chat.get("lastRun") if isinstance(chat.get("lastRun"), dict) else {}
    if active_run is not None:
        run_status = str(getattr(active_run, "status", "") or "running")
    else:
        last_status = str(last_run.get("status") or "").lower()
        last_outcome = str(last_run.get("outcome") or "").lower()
        termination_reason = str(last_run.get("terminationReason") or "").lower()
        if last_status == "error" or last_outcome == "error":
            run_status = "failed"
        elif last_status in {"cancelled", "interrupted"} or termination_reason in {
            "cancelled", "user_interrupted", "shutdown_timeout"
        }:
            run_status = "cancelled"
        elif (
            last_outcome == "awaiting" or termination_reason == "awaiting_user"
        ) and isinstance(chat.get("pendingQuestion"), dict) and bool(chat.get("pendingQuestion")):
            run_status = "awaiting_user"
        elif last_status in {"done", "completed", "success"} or last_outcome == "reply":
            run_status = "completed"
        else:
            run_status = "idle" if persisted_status == "running" else persisted_status
    payload = {
        "id": chat.get("id"),
        "projectId": chat.get("projectId"),
        "kind": str(chat.get("kind") or "chat"),
        "title": chat.get("title"),
        "status": chat.get("status") or "idle",
        "runStatus": run_status,
        "lastRun": last_run or None,
        "model": chat.get("model") or "",
        "lastModel": chat.get("lastModel") or "",
        "modelSelectionId": chat.get("modelSelectionId") or "",
        "reasoningEffort": chat.get("reasoningEffort") or "",
        "completedTurnCount": _completed_turn_count(chat),
        "projectMemoryEnabled": isinstance(chat.get("projectMemorySnapshot"), dict),
        "projectMemoryModifiedAt": str((chat.get("projectMemorySnapshot") or {}).get("modifiedAt") or ""),
        "projectMemoryHash": str((chat.get("projectMemorySnapshot") or {}).get("hash") or ""),
        "permissionMode": chat.get("permissionMode") or "default",
        "workspaceOverride": str(chat.get("workspaceOverride") or ""),
        "soulActive": _chat_soul_active(chat),
        "workspaceActive": _chat_workspace_active(chat),
        "remoteDeviceIds": [
            str(device_id)
            for device_id in (chat.get("remoteDeviceIds") or [])
            if str(device_id or "").strip()
        ],
        "createdAt": chat.get("createdAt"),
        "updatedAt": chat.get("updatedAt"),
        "preview": _chat_preview(chat),
        "messageCount": len(chat.get("messages") or []),
        "usage": usage,
        "pendingQuestion": chat.get("pendingQuestion") or None,
    }
    if chat.get("parentChatId"):
        payload["parentChatId"] = str(chat.get("parentChatId") or "")
    if chat.get("sourceQuote"):
        payload["sourceQuote"] = str(chat.get("sourceQuote") or "")
    active_plan = chat.get("activePlan")
    if isinstance(active_plan, dict) and str(active_plan.get("status") or "") in _VISIBLE_PLAN_STATUSES:
        payload["activePlan"] = active_plan
    # Branch-tree fields: firstMessage anchors the lineage root node; forkMessage
    # is the immutable divergence snippet stored when this chat was forked.
    payload["firstMessage"] = _chat_first_message(chat)
    if chat.get("forkedFromChatId"):
        payload["forkedFromChatId"] = chat.get("forkedFromChatId")
    if chat.get("forkedAtMessageId"):
        payload["forkedAtMessageId"] = chat.get("forkedAtMessageId")
    if chat.get("forkMessage"):
        payload["forkMessage"] = str(chat.get("forkMessage"))[:80]
    # Agent binding / model source / capabilities snapshot (handoff §14).
    # Legacy chats without agent fields surface the built-in Cyrene Agent.
    agent_fields = _agent_runtime_builtin.chat_agent_fields(chat)
    payload["agent"] = agent_fields["agent"]
    payload["modelAccess"] = agent_fields["modelAccess"]
    payload["capabilities"] = agent_fields["capabilities"]
    payload["capabilitiesRevision"] = agent_fields["capabilitiesRevision"]
    payload["agentConfigOptions"] = chat.get("agentConfigOptions") or []
    payload["agentConfigValues"] = chat.get("agentConfigValues") or {}
    payload["agentCommands"] = chat.get("agentCommands") or []
    if chat.get("agentMode") is not None:
        payload["agentMode"] = chat.get("agentMode")
    return payload


def _public_message(message: dict[str, Any]) -> dict[str, Any]:
    """Transcript entry without server-private fields (local upload paths)."""
    if isinstance(message, dict) and "agentAttachments" in message:
        return {k: v for k, v in message.items() if k != "agentAttachments"}
    return message


def _public_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expand legacy inline transport warnings into notification transcript rows."""
    from cyrene.agent_runtime.notices import split_leading_operational_notices

    public: list[dict[str, Any]] = []
    for raw_message in messages:
        message = _public_message(raw_message)
        if not isinstance(message, dict) or message.get("role") != "assistant":
            public.append(message)
            continue
        content = str(message.get("content") or "")
        notices, visible = split_leading_operational_notices(content)
        if not notices:
            public.append(message)
            continue
        message_id = str(message.get("id") or "message")
        created_at = str(message.get("createdAt") or message.get("created_at") or "")
        for index, notice in enumerate(notices):
            public.append({
                "id": f"{message_id}_notice_{index}",
                "role": "assistant",
                "content": "",
                "createdAt": created_at,
                "notificationCard": True,
                "notification": notice,
                "intermediate": True,
                "model": message.get("model"),
            })
        public.append({**message, "content": visible})
    return public


def _merge_chat_messages_chronologically(
    chat: dict[str, Any], additions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge an ordered causal batch into the timestamped transcript.

    Guidance can be persisted while an agent run is still active.  The assistant
    messages that happened before that guidance are discovered only when the run
    is checkpointed/finalized, so timestamps still choose each batch item's
    initial insertion point. The order inside ``additions`` is authoritative,
    though: it is extracted from assistant/tool causality and must not be
    reversed by small persistence-time races such as ``send_message`` publishing
    visible prose before a sibling search while being committed a few
    microseconds later.
    """
    messages = chat.setdefault("messages", [])
    messages[:] = _messages_in_chronological_order(messages)

    def indexes() -> tuple[dict[str, int], dict[str, int]]:
        return (
            {
                str(existing.get("id") or ""): index
                for index, existing in enumerate(messages)
                if isinstance(existing, dict) and str(existing.get("id") or "")
            },
            {
                key: index
                for index, existing in enumerate(messages)
                if isinstance(existing, dict) and bool(existing.get("intermediate"))
                if (key := _live_segment_dedupe_key(existing))
            },
        )

    known_ids, known_intermediate_keys = indexes()
    causal_floor = 0
    for item in additions:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        intermediate_key = _live_segment_dedupe_key(item) if bool(item.get("intermediate")) else ""
        existing_index = known_ids.get(item_id, -1) if item_id else -1
        if existing_index < 0 and intermediate_key:
            existing_index = known_intermediate_keys.get(intermediate_key, -1)
        if existing_index >= 0:
            existing = messages[existing_index]
            merged = {
                **existing,
                **item,
                "id": existing.get("id") or item_id,
            }
            messages[existing_index] = merged
            if existing_index < causal_floor:
                messages.pop(existing_index)
                existing_index = causal_floor - 1
                messages.insert(existing_index, merged)
            causal_floor = existing_index + 1
            known_ids, known_intermediate_keys = indexes()
            continue

        item_time = _message_event_time(item)
        insert_at = len(messages)
        if item_time is not None:
            for index, current in enumerate(messages):
                if not isinstance(current, dict):
                    continue
                current_time = _message_event_time(current)
                if current_time is not None and current_time > item_time:
                    insert_at = index
                    break
        insert_at = max(causal_floor, insert_at)
        messages.insert(insert_at, item)
        causal_floor = insert_at + 1
        known_ids, known_intermediate_keys = indexes()
    return messages


def _persist_live_public_message(chat_id: str, message: dict[str, Any]) -> None:
    """Checkpoint an already-visible intermediate reply immediately."""
    if not isinstance(message, dict) or not str(message.get("id") or "").strip():
        return
    payload = _read_chats_store()
    chat = _find_chat(payload, str(chat_id or ""))
    if not chat:
        return
    entry = dict(message)
    entry["intermediate"] = True
    # Tool activity is owned by the separately timestamped activity card. Keep
    # the stream event unchanged for reconnect fallback, but never checkpoint a
    # second non-interactive copy on the visible message itself.
    entry.pop("trace", None)
    # Live-only hint used by the client to move the current LLM call's reasoning
    # to the activity that follows a visible tool preamble. It is not transcript
    # content and must not survive as stale metadata after finalization.
    entry.pop("opensActivity", None)
    _merge_chat_messages_chronologically(chat, [entry])
    chat["updatedAt"] = str(entry.get("createdAt") or chat.get("updatedAt") or _utc_now_iso())
    _write_chats_store(payload)


def _pending_question_message(
    pending: dict[str, Any],
    *,
    trace: list[dict[str, Any]] | None = None,
    usage: dict[str, int] | None = None,
    files: list[dict[str, Any]] | None = None,
    model: str = "",
) -> dict[str, Any]:
    """Create the durable transcript entry for an ask/permission tool prompt."""
    question_id = str(pending.get("id") or "")
    entry: dict[str, Any] = {
        "id": f"msg_question_{question_id}" if question_id else _short_id("msg"),
        "role": "assistant",
        "content": str(pending.get("text") or ""),
        "createdAt": _utc_now_iso(),
        "model": model,
        "questionPrompt": True,
        "questionId": question_id,
        "questionKind": str(pending.get("kind") or ""),
    }
    if trace:
        entry["trace"] = trace
    if usage and any(usage.values()):
        entry["usage"] = usage
    if files:
        entry["attachments"] = files
    return entry


def _remove_retry_replaced_messages(
    chat: dict[str, Any], after_id: str, replaced_ids: set[str]
) -> None:
    """Remove only the transcript tail that existed when retry began."""
    messages = chat.setdefault("messages", [])
    cut = next(
        (
            index for index, item in enumerate(messages)
            if str(item.get("id") or "") == str(after_id or "")
        ),
        -1,
    )
    if cut < 0:
        return
    messages[cut + 1:] = [
        item for item in messages[cut + 1:]
        if str(item.get("id") or "") not in replaced_ids
    ]


def _public_chat_full(chat: dict[str, Any]) -> dict[str, Any]:
    payload = _public_chat_light(chat)
    ordered_messages = _messages_in_chronological_order(chat.get("messages") or [])
    payload["messages"] = _public_messages(ordered_messages)
    payload["files"] = [
        dict(item)
        for item in (chat.get("generatedFiles") or [])
        if isinstance(item, dict) and str(item.get("path") or "").strip()
    ]
    return payload


def _subagent_tool_args(tool_call: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(tool_call, dict):
        return "", {}
    function = tool_call.get("function")
    if not isinstance(function, dict):
        return "", {}
    name = str(function.get("name") or "").strip()
    raw = function.get("arguments")
    if isinstance(raw, dict):
        return name, raw
    try:
        parsed = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = {}
    return name, parsed if isinstance(parsed, dict) else {}


def _subagent_round_title(messages: list[dict[str, Any]], round_id: str) -> str:
    for message in messages:
        if str(message.get("round_id") or "").strip() != round_id:
            continue
        if str(message.get("role") or "") != "user":
            continue
        title = str(message.get("content") or "").replace("\n", " ").strip()
        if title:
            return title[:72]
    return ""


def _subagent_public_agent(agent_id: str, info: dict[str, Any], round_id: str) -> dict[str, Any]:
    return {
        "id": agent_id,
        "name": agent_id,
        "task": str(info.get("task") or "").strip(),
        "status": str(info.get("status") or "done").strip(),
        "result": str(info.get("result") or "").strip(),
        "roundId": str(info.get("round_id") or round_id).strip(),
        "createdAt": info.get("created_at"),
        "updatedAt": info.get("updated_at"),
        "messageCount": len(info.get("messages") or []),
    }


def _subagent_messages_from_agent(
    agent_id: str, info: dict[str, Any], round_id: str
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    created_at = str(info.get("created_at") or "")
    for index, message in enumerate(info.get("messages") or []):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for tool_call in message.get("tool_calls") or []:
            name, args = _subagent_tool_args(tool_call)
            if name not in ("send_agent_message", "broadcast_agent_message", "send_message_to_user"):
                continue
            content = str(args.get("content") or args.get("text") or "").strip()
            if not content:
                continue
            target = "all" if name == "broadcast_agent_message" else (
                "user" if name == "send_message_to_user" else str(args.get("to") or "").strip()
            )
            messages.append({
                "id": str(tool_call.get("id") or f"{agent_id}_message_{index}"),
                "type": "broadcast" if target == "all" else "message",
                "from": agent_id,
                "to": target,
                "content": content,
                "timestamp": message.get("created_at") or created_at,
                "roundId": round_id,
            })
    result = str(info.get("result") or "").strip()
    if result and result not in ("Done.", "无结果"):
        messages.append({
            "id": f"{agent_id}_result",
            "type": "result",
            "from": agent_id,
            "to": "",
            "content": result,
            "timestamp": info.get("updated_at") or created_at,
            "roundId": round_id,
        })
    return messages


def _workbench_subagent_payload(chat_id: str, requested_round_id: str = "") -> dict[str, Any]:
    """Build Workbench-only subagent data from this chat's own session state.

    This intentionally does not call the legacy Chat UI payload builders. It
    reconstructs completed rounds from ``subagent_flow_snapshot`` and overlays
    live registry entries scoped by the Workbench chat session id.
    """
    raw_messages = _session_state_messages(chat_id)
    rounds: dict[str, dict[str, Any]] = {}

    def ensure_round(round_id: str, order: int) -> dict[str, Any] | None:
        rid = str(round_id or "").strip()
        if not rid:
            return None
        if rid not in rounds:
            rounds[rid] = {
                "id": rid,
                "title": _subagent_round_title(raw_messages, rid),
                "order": order,
                "agents": {},
                "messages": [],
            }
        return rounds[rid]

    for index, message in enumerate(raw_messages):
        if not isinstance(message, dict):
            continue
        round_id = str(message.get("round_id") or "").strip()
        for tool_call in message.get("tool_calls") or []:
            name, args = _subagent_tool_args(tool_call)
            if name != "spawn_subagent":
                continue
            round_data = ensure_round(round_id, index)
            agent_id = str(args.get("agent_id") or "").strip()
            if not round_data or not agent_id:
                continue
            round_data["agents"].setdefault(agent_id, {
                "task": str(args.get("task") or "").strip(),
                "status": "done",
                "result": "",
                "messages": [],
                "round_id": round_id,
            })

        snapshot = message.get("subagent_flow_snapshot")
        if not isinstance(snapshot, dict):
            continue
        snapshot_round_id = str(snapshot.get("round_id") or round_id).strip()
        round_data = ensure_round(snapshot_round_id, index)
        if not round_data:
            continue
        for agent_id, info in (snapshot.get("agents") or {}).items():
            if isinstance(info, dict):
                round_data["agents"][str(agent_id)] = dict(info)
        for comm in snapshot.get("comm_messages") or []:
            if not isinstance(comm, dict):
                continue
            content = str(comm.get("content") or "").strip()
            if not content:
                continue
            round_data["messages"].append({
                "id": str(comm.get("message_id") or f"comm_{len(round_data['messages'])}"),
                "type": "broadcast" if str(comm.get("type") or "") == "broadcast" else "message",
                "from": str(comm.get("from") or ""),
                "to": str(comm.get("to") or ""),
                "content": content,
                "timestamp": comm.get("timestamp"),
                "roundId": snapshot_round_id,
            })

    from cyrene.subagent import active_subagent_task_ids, registry_snapshot
    live_task_ids = active_subagent_task_ids()
    for agent_id, info in registry_snapshot(session_id=chat_id).items():
        round_id = str(info.get("round_id") or "").strip()
        round_data = ensure_round(round_id, len(raw_messages) + 1)
        if not round_data:
            continue
        round_data["agents"][agent_id] = dict(info)

    public_rounds: list[dict[str, Any]] = []
    for round_data in rounds.values():
        agents = [
            _subagent_public_agent(agent_id, info, round_data["id"])
            for agent_id, info in round_data["agents"].items()
            if not str(agent_id).startswith("agent_summary_")
        ]
        for agent in agents:
            if (
                agent["id"] not in live_task_ids
                and agent["status"] in ("running", "resumed", "waiting")
                and agent.get("result")
            ):
                agent["status"] = "done"
        agents.sort(key=lambda item: (str(item.get("createdAt") or ""), item["name"]))
        live_messages = list(round_data["messages"])
        for agent in agents:
            info = round_data["agents"].get(agent["id"], {})
            live_messages.extend(_subagent_messages_from_agent(agent["id"], info, round_data["id"]))
        seen_message_ids: set[str] = set()
        messages: list[dict[str, Any]] = []
        for entry in live_messages:
            message_id = str(entry.get("id") or "")
            if message_id and message_id in seen_message_ids:
                continue
            if message_id:
                seen_message_ids.add(message_id)
            messages.append(entry)
        messages.sort(key=lambda item: str(item.get("timestamp") or ""))
        active = sum(1 for item in agents if item["status"] in ("running", "resumed", "waiting"))
        public_rounds.append({
            "id": round_data["id"],
            "title": round_data["title"] or round_data["id"],
            "status": "running" if active else "done",
            "agentCount": len(agents),
            "activeCount": active,
            "agents": agents,
            "messages": messages,
            "_order": round_data["order"],
        })
    public_rounds.sort(key=lambda item: item["_order"], reverse=True)
    for item in public_rounds:
        item.pop("_order", None)

    selected_round_id = str(requested_round_id or "").strip()
    if not any(item["id"] == selected_round_id for item in public_rounds):
        running_round = next((item for item in public_rounds if item["status"] == "running"), None)
        selected_round_id = (running_round or (public_rounds[0] if public_rounds else {})).get("id", "")
    selected = next((item for item in public_rounds if item["id"] == selected_round_id), None)
    return {
        "rounds": [
            {key: value for key, value in item.items() if key not in ("agents", "messages")}
            for item in public_rounds
        ],
        "activeRoundId": selected_round_id,
        "agents": list(selected.get("agents") or []) if selected else [],
        "messages": list(selected.get("messages") or []) if selected else [],
    }


def _legacy_message(message: dict[str, Any], index: int) -> dict[str, Any]:
    role = "assistant" if message.get("role") in ("agent", "assistant") else str(message.get("role") or "user")
    out: dict[str, Any] = {
        "id": str(message.get("id") or f"legacy_msg_{index}"),
        "role": role,
        "content": str(message.get("content") or message.get("body") or ""),
        "createdAt": str(message.get("createdAt") or message.get("time") or ""),
        "legacy": True,
    }
    if isinstance(message.get("attachments"), list):
        out["attachments"] = message.get("attachments")
    return out


def _legacy_chat_from_session(session: dict[str, Any], project_id: str, *, full: bool = False) -> dict[str, Any]:
    raw_messages = ((session.get("chat") or {}).get("messages") or []) if isinstance(session.get("chat"), dict) else []
    messages = [_legacy_message(message, index) for index, message in enumerate(raw_messages)]
    chat = {
        "id": "legacy:" + project_id + ":" + str(session.get("id") or ""),
        "projectId": project_id,
        "kind": "chat",
        "title": str(session.get("title") or "旧对话"),
        "status": session.get("status") or "done",
        "model": session.get("model") or "",
        "createdAt": session.get("started") or "",
        "updatedAt": session.get("started") or "",
        "preview": str(session.get("preview") or ""),
        "messageCount": len(messages),
        "usage": {},
        "legacy": True,
    }
    if full:
        chat["messages"] = messages
    return chat


def _legacy_chats(project_id: str, *, full_id: str = "") -> list[dict[str, Any]]:
    try:
        R = runtime_service()
        out: list[dict[str, Any]] = []
        for session in R._build_sessions():
            # ``_build_current_session`` always returns a ``run_live`` placeholder,
            # even after the user clears that legacy session. Do not surface the
            # empty placeholder as a Workbench chat, otherwise it immediately
            # reappears after deletion and looks impossible to remove.
            raw_messages = (
                (session.get("chat") or {}).get("messages") or []
                if isinstance(session.get("chat"), dict)
                else []
            )
            if str(session.get("id") or "") == "run_live" and not raw_messages:
                continue
            chat_id = "legacy:" + project_id + ":" + str(session.get("id") or "")
            if full_id and chat_id != full_id:
                continue
            out.append(_legacy_chat_from_session(session, project_id, full=bool(full_id)))
        return out
    except Exception:
        logger.exception("Failed to build legacy chats for workbench")
        return []


def _aggregate_usage(messages: list[dict[str, Any]]) -> dict[str, int]:
    totals = {key: 0 for key in _USAGE_KEYS}
    for message in messages:
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        for key in _USAGE_KEYS:
            try:
                totals[key] += int(usage.get(key) or 0)
            except (TypeError, ValueError):
                continue
    if not totals["total_tokens"]:
        totals["total_tokens"] = totals["prompt_tokens"] + totals["completion_tokens"]
    return totals


# ---------------------------------------------------------------------------
# Agent-state helpers (usage + tool trace for one exchange)
# ---------------------------------------------------------------------------

def _session_state_messages(session_id: str) -> list[dict[str, Any]]:
    from cyrene.agent.context import session_state_file
    data = read_json_safe(session_state_file(session_id))
    if isinstance(data, dict) and isinstance(data.get("messages"), list):
        return data["messages"]
    return []


# Categories surfaced in the Workbench overview's "context breakdown" bar. The
# order is the visual stacking order (oldest/system first, live turns last).
_CONTEXT_SEGMENT_KEYS = ("compacted", "system", "user", "assistant", "tool")


def _context_segment_tokens(messages: list[dict[str, Any]]) -> dict[str, int]:
    """Split the agent's RAW context into per-category token estimates.

    This mirrors ``call_llm.message_token_estimate`` field-by-field so the sum
    equals what the compactor measures against the context window — the gauge and
    the 60% compaction trigger therefore share one honest denominator. Each
    message's tokens are attributed to a UI bucket:

    - ``compacted`` — append-only summary blocks of older history
    - ``system``    — live system messages (non-compacted)
    - ``user`` / ``assistant`` — prose by author (assistant prose only)
    - ``tool``      — assistant tool-call args + tool-result bodies (the bulk)
    """
    from cyrene.model_runtime.client import approx_token_count

    seg = {key: 0 for key in _CONTEXT_SEGMENT_KEYS}
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        base = 4 + approx_token_count(role)
        if message.get("compacted_block"):
            seg["compacted"] += base + approx_token_count(message.get("content") or "")
            continue
        content = message.get("content")
        if isinstance(content, str):
            content_tokens = approx_token_count(content)
        elif isinstance(content, list):
            content_tokens = sum(
                approx_token_count(block.get("text") or "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        else:
            content_tokens = approx_token_count(content or "")
        tool_tokens = 0
        for tool_call in message.get("tool_calls") or []:
            fn = tool_call.get("function", {}) if isinstance(tool_call, dict) else {}
            tool_tokens += approx_token_count(fn.get("name") or "")
            tool_tokens += approx_token_count(fn.get("arguments") or "")
        if role == "user":
            seg["user"] += base + content_tokens
        elif role == "assistant":
            seg["assistant"] += base + content_tokens + approx_token_count(message.get("reasoning_content") or "")
            seg["tool"] += tool_tokens
        elif role == "tool":
            seg["tool"] += base + content_tokens + approx_token_count(message.get("tool_call_id") or "")
        else:
            seg["system"] += base + content_tokens
    return seg


def _chat_context_payload(
    state_id: str,
    model_name: str,
    *,
    ctx_limit: int | None = None,
) -> dict[str, Any]:
    """Live context-window composition for one chat, computed from raw state.

    Per-conversation by construction (state lives at ``sessions/<id>/state.json``)
    and cheap enough to poll while a run streams, so the overview updates in
    real time as the agent appends turns. API callers pass the automatic
    compactor's active-primary-model budget via ``ctx_limit`` so a fallback model
    recorded on the latest assistant message cannot move the gauge's 60% marker.
    """
    from cyrene.model_runtime.compaction import COMPACT_TRIGGER_RATIO
    from cyrene.runtime.config_store import effective_ctx_limit_for_model

    messages = _session_state_messages(state_id)
    actual_model = ""
    for message in reversed(messages):
        if not isinstance(message, dict) or str(message.get("role") or "") != "assistant":
            continue
        usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
        actual_model = str(usage.get("model") or message.get("model") or "").strip()
        if actual_model:
            break
    selected_model = str(model_name or "").strip()
    effective_model = selected_model or actual_model
    seg = _context_segment_tokens(messages)
    used = sum(seg.values())
    limit = (
        int(ctx_limit)
        if ctx_limit is not None
        else effective_ctx_limit_for_model(effective_model)
    )
    compacted_blocks = sum(
        1 for m in messages if isinstance(m, dict) and m.get("compacted_block")
    )
    distilled = any(
        isinstance(m, dict) and m.get("compacted_block") and m.get("llm_compacted")
        for m in messages
    )
    return {
        # ``model`` is the conversation's current selection so the overview
        # changes immediately, before the first response from the new model.
        # Keep the last response model separately for fallback diagnostics.
        "model": effective_model,
        "actualModel": actual_model,
        "usage": _aggregate_usage(messages),
        "ctxLimit": limit,
        "ctxUsed": used,
        "ratio": (used / limit) if limit > 0 else None,
        "compactTriggerRatio": COMPACT_TRIGGER_RATIO,
        "messageCount": len(messages),
        "segments": [{"key": key, "tokens": seg[key]} for key in _CONTEXT_SEGMENT_KEYS],
        "compaction": {
            "active": compacted_blocks > 0,
            "blocks": compacted_blocks,
            "tokens": seg["compacted"],
            "distilled": distilled,
        },
    }


# Stable public helpers for runtime integrations. Keep the underscored aliases
# above for compatibility with older callers while avoiding new private-module
# dependencies across package boundaries.
workbench_subagent_payload = _workbench_subagent_payload
context_segment_tokens = _context_segment_tokens
chat_context_payload = _chat_context_payload


def _tool_args_preview(raw_arguments: str) -> str:
    try:
        args = json.loads(raw_arguments or "{}")
    except Exception:
        return ""
    if not isinstance(args, dict):
        return ""
    parts = [str(value) for value in args.values() if value not in (None, "", [], {})]
    preview = ", ".join(parts)
    return preview[:80]


def _exchange_usage() -> dict[str, int]:
    return {key: 0 for key in _USAGE_KEYS}


def _tool_result_is_error(result: str) -> bool:
    """Best-effort detection of a tool call that failed at the tool layer.

    Conservative on purpose: only clear tool-level error strings count, so a
    trace mark of "failed" is trustworthy. (A non-zero Bash exit code is JSON,
    not an ``Error:`` prefix, so it is left unmarked.)
    """
    text = str(result or "").strip().lower()
    if not text:
        return False
    try:
        parsed = json.loads(str(result or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, dict):
        return str(parsed.get("status") or "").strip().lower() in {"error", "failed", "failure", "uncertain"}
    return text.startswith(("error", "tool failed", "failed to", "failed:"))


def _build_tool_result_map(state_messages: list[dict[str, Any]]) -> dict[str, str]:
    """Map ``tool_call_id`` -> tool result content so a trace entry can know
    whether its call succeeded."""
    out: dict[str, str] = {}
    for message in state_messages:
        if not isinstance(message, dict) or str(message.get("role") or "") != "tool":
            continue
        tcid = str(message.get("tool_call_id") or "").strip()
        if tcid:
            out[tcid] = str(message.get("content") or "")
    return out


def _accumulate_usage(message: dict[str, Any], usage: dict[str, int]) -> None:
    raw_usage = message.get("usage")
    if isinstance(raw_usage, dict):
        for key in _USAGE_KEYS:
            try:
                usage[key] += int(raw_usage.get(key) or 0)
            except (TypeError, ValueError):
                continue


def _accumulate_tools(
    message: dict[str, Any],
    trace: list[dict[str, Any]],
    result_map: dict[str, str] | None = None,
) -> None:
    for tool_call in message.get("tool_calls") or []:
        fn = tool_call.get("function") if isinstance(tool_call, dict) else None
        name = str((fn or {}).get("name") or "").strip()
        if not name or name in _TRACE_SKIP_TOOLS:
            continue
        entry: dict[str, Any] = {
            "tool": name,
            "preview": _tool_args_preview(str((fn or {}).get("arguments") or "")),
        }
        tcid = str(tool_call.get("id") or "").strip() if isinstance(tool_call, dict) else ""
        if result_map and tcid and _tool_result_is_error(result_map.get(tcid, "")):
            entry["failed"] = True
        trace.append(entry)


def _accumulate_attachments(
    message: dict[str, Any],
    files: list[dict[str, Any]],
    seen_file_urls: set[str],
) -> None:
    for item in message.get("attachments") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        key = url or str(item.get("name") or "")
        if not key or key in seen_file_urls:
            continue
        seen_file_urls.add(key)
        files.append({
            "id": str(item.get("id") or "").strip(),
            "name": str(item.get("name") or "file"),
            "content_type": str(item.get("content_type") or "application/octet-stream"),
            "size": int(item.get("size") or 0),
            "kind": str(item.get("kind") or "file"),
            "url": url,
        })


def _has_traceable_tools(message: dict[str, Any]) -> bool:
    """True if the message calls at least one real (non-control) tool — i.e. it
    does actual work, not just ``quit`` / ``use_tools`` plumbing."""
    for tool_call in message.get("tool_calls") or []:
        fn = tool_call.get("function") if isinstance(tool_call, dict) else None
        name = str((fn or {}).get("name") or "").strip()
        if name and name not in _TRACE_SKIP_TOOLS:
            return True
    return False


def _append_exchange_meta(
    message: dict[str, Any],
    trace: list[dict[str, Any]],
    usage: dict[str, int],
    files: list[dict[str, Any]],
    seen_file_urls: set[str],
    result_map: dict[str, str] | None = None,
) -> None:
    _accumulate_usage(message, usage)
    _accumulate_tools(message, trace, result_map)
    _accumulate_attachments(message, files, seen_file_urls)


def _reorder_tool_produced_replies(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Move tool-delivered replies to *after* the tool call that produced them.

    A delivery tool (``send_file`` / ``send_wechat_file``) inserts its
    intermediate reply into session state *during* tool execution, before the
    assistant tool-call message that triggered it is committed — and the live
    write wins the merge position. The reply therefore lands just *before* its
    own tool call in storage. Left as-is the transcript renders the delivered
    file above the "sent file" tool card. Re-sequence each such reply to sit
    after its triggering tool call and that call's tool results so rendering
    reads [tool card] -> [delivered file]. Storage (the LLM history) is
    untouched; this only reshapes the rendered transcript.

    When a single tool-call message delivers *several* files at once (e.g. eight
    ``send_file`` calls batched into one turn), all of their replies stack up
    consecutively right before that one tool-call message. Move the whole run as
    a unit so the tool card lands above *all* the delivered files, not just the
    last one. A single forward pass (rather than move-and-rescan) keeps replies
    from distinct delivery turns separate — across turns each reply is split off
    from the next by its own tool call and results, so they never form one run.
    """
    if not isinstance(messages, list):
        return messages

    def _is_delivered_reply(m: Any) -> bool:
        return (
            isinstance(m, dict)
            and str(m.get("role") or "") == "assistant"
            and bool(m.get("intermediate_reply"))
            and bool(m.get("attachments"))
        )

    def _is_tool_call_msg(m: Any) -> bool:
        return (
            isinstance(m, dict)
            and str(m.get("role") or "") == "assistant"
            and bool(m.get("tool_calls"))
        )

    out: list[dict[str, Any]] = []
    i = 0
    n = len(messages)
    while i < n:
        msg = messages[i]
        if _is_delivered_reply(msg):
            run_end = i                   # maximal run of consecutive replies
            while run_end < n and _is_delivered_reply(messages[run_end]):
                run_end += 1
            if run_end < n and _is_tool_call_msg(messages[run_end]):
                out.append(messages[run_end])     # the triggering tool-call msg
                j = run_end + 1                   # then its tool-result messages
                while j < n and str(messages[j].get("role") or "") == "tool":
                    out.append(messages[j])
                    j += 1
                out.extend(messages[i:run_end])   # then the delivered replies
                i = j
                continue
            out.extend(messages[i:run_end])        # not a delivery — leave as-is
            i = run_end
            continue
        out.append(msg)
        i += 1
    return out


def _make_reply_segment(
    message: dict[str, Any],
    trace: list[dict[str, Any]],
    usage: dict[str, int],
    files: list[dict[str, Any]],
    *,
    fallback_id: str = "",
) -> dict[str, Any]:
    """Build one rendered reply block carrying the tool card (trace), token
    usage and attachments that accumulated up to it."""
    entry: dict[str, Any] = {
        "id": str(message.get("message_id") or message.get("id") or fallback_id or _short_id("msg")),
        "role": "assistant",
        "content": str(message.get("content") or ""),
        "createdAt": str(message.get("created_at") or message.get("createdAt") or _utc_now_iso()),
        "intermediate": True,
    }
    round_id = str(message.get("round_id") or message.get("roundId") or "").strip()
    if round_id:
        entry["roundId"] = round_id
    model_name = str(
        (message.get("usage") or {}).get("model")
        if isinstance(message.get("usage"), dict)
        else ""
    ).strip() or str(message.get("model") or "").strip()
    if model_name:
        entry["model"] = model_name
    if trace:
        entry["trace"] = trace[:40]
    if any(usage.values()):
        if not usage["total_tokens"]:
            usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        entry["usage"] = dict(usage)
    attachments = list(files)
    attachment_keys = {
        str(item.get("url") or item.get("name") or "")
        for item in attachments
    }
    for item in message.get("attachments") or []:
        if not isinstance(item, dict):
            continue
        attachment = {
            "id": str(item.get("id") or "").strip(),
            "name": str(item.get("name") or "file"),
            "content_type": str(item.get("content_type") or "application/octet-stream"),
            "size": int(item.get("size") or 0),
            "kind": str(item.get("kind") or "file"),
            "url": str(item.get("url") or "").strip(),
        }
        key = str(attachment.get("url") or attachment.get("name") or "")
        if key and key not in attachment_keys:
            attachment_keys.add(key)
            attachments.append(attachment)
    if attachments:
        entry["attachments"] = attachments
    return entry


def _segment_fallback_id(message: dict[str, Any], index: int) -> str:
    """Stable UI id for live-scanned assistant segments without message_id.

    During a tool round, the live scanner can observe an assistant preamble
    before the agent persistence path has assigned ``message_id``. A random id
    here makes every scanner tick look like a new segment, so use a deterministic
    fingerprint until the durable id appears.
    """
    payload = {
        "index": index,
        "role": message.get("role"),
        "content": message.get("content"),
        "tool_calls": message.get("tool_calls"),
        "attachments": message.get("attachments"),
        "intermediate_reply": bool(message.get("intermediate_reply")),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return "msg_live_" + hashlib.sha1(encoded.encode("utf-8")).hexdigest()[:16]


def _live_segment_dedupe_key(entry: dict[str, Any]) -> str:
    """Stable semantic key for live-published reply segments.

    The live scanner may first see an assistant tool preamble before the agent
    assigns ``message_id`` and then see the same segment again with its durable
    id. Deduping only by id lets the Workbench render the same prose repeatedly
    while the run is active. Keep this key deliberately narrow: visible text plus
    delivered attachment identity, excluding trace because trace can grow around
    the same preamble as tools settle.
    """
    content = re.sub(r"\s+", " ", str(entry.get("content") or "")).strip()
    attachments: list[str] = []
    for item in entry.get("attachments") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("url") or item.get("id") or item.get("name") or "").strip()
        if key:
            attachments.append(key)
    if not content and not attachments:
        return ""
    payload = {"content": content, "attachments": sorted(set(attachments))}
    round_id = str(entry.get("roundId") or entry.get("round_id") or "").strip()
    if round_id:
        payload["roundId"] = round_id
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return "msg_sem_" + hashlib.sha1(encoded.encode("utf-8")).hexdigest()[:16]


def _extract_exchange_segments(
    state_messages: list[dict[str, Any]],
    state_ids_before: set[str],
    *,
    include_open_tool_preamble: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    """Split one agent exchange into ordered reply blocks + a trailing block.

    Walks the exchange chronologically and emits a reply block whenever the
    agent says something mid-run — both tool-delivered replies
    (``intermediate_reply``) and a model turn that carries prose *alongside*
    its tool calls (a "let me check…" preamble that was previously dropped).
    Tool calls accumulate into the trace card shown with the next reply.

    Uses message IDs to identify which messages belong to this exchange, so
    it works correctly even when session compaction reduces the total message
    count during the agent run (*state_len_before* would overshoot).

    ``include_open_tool_preamble`` is for the live stream only: while the agent
    is still running, the latest assistant turn may be a real tool-call preamble
    (content + non-control tools) even though no later assistant turn exists yet.
    The finalized transcript keeps the conservative default so a terminal answer
    is never duplicated.
    """
    state_messages = _reorder_tool_produced_replies(state_messages)
    result_map = _build_tool_result_map(state_messages)
    # The last in-exchange assistant turn's content is what the caller persists
    # as the final reply (``reply_text``); never also emit it as a mid-run block.
    last_assistant_idx = -1
    for idx, message in enumerate(state_messages):
        mid = str(message.get("message_id") or message.get("id") or "").strip()
        if mid and mid in state_ids_before:
            continue
        if str(message.get("role") or "") == "assistant" and not bool(message.get("intermediate_reply")):
            last_assistant_idx = idx
    segments: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    usage = _exchange_usage()
    files: list[dict[str, Any]] = []
    seen_file_urls: set[str] = set()

    for idx, message in enumerate(state_messages):
        # Skip messages that existed before this exchange started — their IDs
        # are in *state_ids_before*.  Compacted blocks (role=system) are
        # implicitly skipped by the role check below.
        mid = str(message.get("message_id") or message.get("id") or "").strip()
        if mid and mid in state_ids_before:
            continue
        if str(message.get("role") or "") != "assistant":
            continue
        # A pending question's text is persisted on its own questionPrompt entry
        # (``_pending_question_message``); extracting it again here makes the
        # paused ask_user path render the same text twice.
        if bool(message.get("question_prompt")):
            continue

        if bool(message.get("intermediate_reply")):
            segments.append(_make_reply_segment(
                message,
                trace,
                usage,
                files,
                fallback_id=_segment_fallback_id(message, idx),
            ))
            trace, usage, files, seen_file_urls = [], _exchange_usage(), [], set()
            continue

        # Every completed, non-final assistant turn with visible prose belongs in
        # the transcript. This includes plain-text guidance acknowledgements as
        # well as tool preambles. The live scanner may additionally expose the
        # currently-open last turn only when it carries real tools; a text-only
        # last turn is the final answer and must stay with the caller.
        is_completed_mid_turn = idx != last_assistant_idx
        is_open_tool_preamble = (
            include_open_tool_preamble
            and idx == last_assistant_idx
            and _has_traceable_tools(message)
        )
        if (
            (is_completed_mid_turn or is_open_tool_preamble)
            and str(message.get("content") or "").strip()
        ):
            _accumulate_usage(message, usage)
            segment = _make_reply_segment(
                message,
                trace,
                usage,
                files,
                fallback_id=_segment_fallback_id(message, idx),
            )
            # Content in an assistant tool-call message is emitted before that
            # message's tools execute. Tell the live client to close the prior
            # activity at the prose, then open a new clickable activity for the
            # current call's reasoning and tools.
            if _has_traceable_tools(message):
                segment["opensActivity"] = True
            segments.append(segment)
            trace, usage, files, seen_file_urls = [], _exchange_usage(), [], set()
            _accumulate_tools(message, trace, result_map)
            continue

        _append_exchange_meta(message, trace, usage, files, seen_file_urls, result_map)

    if not usage["total_tokens"]:
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    return segments, trace[:40], usage, files[:20]


def _extract_exchange_timeline(
    state_messages: list[dict[str, Any]],
    state_ids_before: set[str],
    *,
    include_open_tool_preamble: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    """Build the same causal timeline shown while an exchange is running.

    Reasoning, visible intermediate replies, and tool activity are separate
    events.  In particular, ``send_message`` is executed before its substantive
    sibling tools, even though its inserted session row can sit immediately
    *before* the assistant tool-call row that created it.  Pair those rows and
    emit the real sequence: reasoning -> visible reply -> tools.  A later LLM
    call's reasoning starts a new event instead of being appended to the tools
    that just completed.
    """
    messages = _reorder_tool_produced_replies(state_messages)
    result_map = _build_tool_result_map(messages)
    last_assistant_idx = -1
    for idx, message in enumerate(messages):
        mid = str(message.get("message_id") or message.get("id") or "").strip()
        if mid and mid in state_ids_before:
            continue
        if str(message.get("role") or "") == "assistant" and not bool(message.get("intermediate_reply")):
            last_assistant_idx = idx

    timeline: list[dict[str, Any]] = []
    usage = _exchange_usage()
    files: list[dict[str, Any]] = []
    seen_file_urls: set[str] = set()
    pending: dict[str, Any] | None = None

    # ``insert_intermediate_user_reply`` inserts the visible reply before the
    # assistant tool-call row is committed. Match it back to the send_message
    # invocation so its storage position cannot invert the rendered causality.
    paired_replies: dict[int, list[int]] = {}
    claimed_reply_indexes: set[int] = set()
    for tool_index, message in enumerate(messages):
        if not isinstance(message, dict) or str(message.get("role") or "") != "assistant":
            continue
        send_texts: list[str] = []
        for tool_call in message.get("tool_calls") or []:
            fn = tool_call.get("function") if isinstance(tool_call, dict) else None
            if str((fn or {}).get("name") or "").strip() != "send_message":
                continue
            try:
                args = json.loads(str((fn or {}).get("arguments") or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                args = {}
            text = str(args.get("text") or "").strip() if isinstance(args, dict) else ""
            if text:
                send_texts.append(text)
        if not send_texts:
            continue
        tool_round = str(message.get("round_id") or message.get("roundId") or "").strip()
        for send_text in send_texts:
            for reply_index, candidate in enumerate(messages):
                if reply_index in claimed_reply_indexes or not isinstance(candidate, dict):
                    continue
                candidate_round = str(candidate.get("round_id") or candidate.get("roundId") or "").strip()
                if (
                    str(candidate.get("role") or "") == "assistant"
                    and bool(candidate.get("intermediate_reply"))
                    and not candidate.get("attachments")
                    and str(candidate.get("content") or "").strip() == send_text
                    and (not tool_round or not candidate_round or candidate_round == tool_round)
                ):
                    paired_replies.setdefault(tool_index, []).append(reply_index)
                    claimed_reply_indexes.add(reply_index)
                    break

    def flush_activity() -> None:
        nonlocal pending
        if pending is not None and pending.get("trace"):
            timeline.append(pending)
        pending = None

    def append_visible_message(message: dict[str, Any], idx: int) -> None:
        entry = _make_reply_segment(
            message,
            [],
            _exchange_usage(),
            [],
            fallback_id=_segment_fallback_id(message, idx),
        )
        # A provisional live checkpoint may already carry the legacy trace.
        # Explicitly overwrite it during finalization; omitting this key would
        # preserve the stale list when entries are merged by message id.
        entry["trace"] = []
        timeline.append(entry)

    def start_activity(
        message: dict[str, Any], idx: int, *, activity_kind: str = "tools",
        created_at: str = "",
    ) -> dict[str, Any]:
        mid = str(message.get("message_id") or message.get("id") or "").strip()
        fallback = _segment_fallback_id(message, idx)
        model_name = str(
            (message.get("usage") or {}).get("model")
            if isinstance(message.get("usage"), dict)
            else ""
        ).strip() or str(message.get("model") or "").strip()
        prefix = "reasoning_" if activity_kind == "reasoning" else "activity_"
        entry: dict[str, Any] = {
            "id": prefix + (mid or fallback),
            "role": "assistant",
            "content": "",
            "createdAt": created_at or str(message.get("created_at") or message.get("createdAt") or _utc_now_iso()),
            "activityCard": True,
            "reasoning": "",
            "trace": [],
            "intermediate": True,
        }
        if model_name:
            entry["model"] = model_name
        return entry

    for idx, message in enumerate(messages):
        mid = str(message.get("message_id") or message.get("id") or "").strip()
        if mid and mid in state_ids_before:
            continue
        if str(message.get("role") or "") != "assistant":
            continue
        # Pending-question rows have their own questionPrompt persistence path;
        # the live scanner must not republish them as a plain visible reply.
        if bool(message.get("question_prompt")):
            continue

        if idx in claimed_reply_indexes:
            continue

        _accumulate_usage(message, usage)

        if bool(message.get("intermediate_reply")):
            flush_activity()
            append_visible_message(message, idx)
            continue

        _accumulate_attachments(message, files, seen_file_urls)

        is_completed_mid_turn = idx != last_assistant_idx
        is_open_tool_preamble = (
            include_open_tool_preamble
            and idx == last_assistant_idx
            and _has_traceable_tools(message)
        )
        visible_tool_preamble = (
            (is_completed_mid_turn or is_open_tool_preamble)
            and str(message.get("content") or "").strip()
        )

        reasoning = str(message.get("reasoning_content") or "").strip()
        tools: list[dict[str, Any]] = []
        _accumulate_tools(message, tools, result_map)

        if reasoning:
            flush_activity()
            reasoning_entry = start_activity(message, idx, activity_kind="reasoning")
            reasoning_entry["reasoning"] = reasoning
            timeline.append(reasoning_entry)

        if visible_tool_preamble:
            flush_activity()
            append_visible_message(message, idx)

        causal_boundary_time: datetime | None = None
        for reply_index in paired_replies.get(idx, []):
            flush_activity()
            reply = messages[reply_index]
            append_visible_message(reply, reply_index)
            reply_time = _message_event_time(reply)
            if reply_time is not None and (causal_boundary_time is None or reply_time > causal_boundary_time):
                causal_boundary_time = reply_time

        if tools:
            if pending is None:
                created_at = ""
                message_time = _message_event_time(message)
                if causal_boundary_time is not None and (message_time is None or message_time <= causal_boundary_time):
                    created_at = (causal_boundary_time + timedelta(microseconds=1)).isoformat()
                pending = start_activity(message, idx, created_at=created_at)
            pending_trace = pending.setdefault("trace", [])
            pending_trace.extend(tools)
            if len(pending_trace) > 40:
                del pending_trace[:-40]

    flush_activity()
    if not usage["total_tokens"]:
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    return timeline, usage, files[:20]


def _last_exchange_model(
    state_messages: list[dict[str, Any]], state_ids_before: set[str]
) -> str:
    """Actual model used by the last new assistant call in an exchange."""
    for message in reversed(state_messages):
        if not isinstance(message, dict) or str(message.get("role") or "") != "assistant":
            continue
        mid = str(message.get("message_id") or message.get("id") or "").strip()
        if mid and mid in state_ids_before:
            continue
        usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
        model_name = str(usage.get("model") or message.get("model") or "").strip()
        if model_name:
            return model_name
    return ""


def _published_intermediate_message_ids(run: ChatRun) -> set[str]:
    ids: set[str] = set()
    for event in getattr(run, "events", []) or []:
        if str(event.get("type") or "") != "intermediate_message":
            continue
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        mid = str(message.get("id") or "").strip()
        if mid:
            ids.add(mid)
        key = str(message.get("liveDedupeKey") or "").strip()
        if key:
            ids.add(key)
    return ids


async def _publish_live_exchange_segments_once(
    run: ChatRun,
    chat_id: str,
    state_ids_before: set[str],
    published_ids: set[str],
) -> None:
    """Publish newly persisted mid-run reply blocks to the active stream.

    Tool-delivered replies already emit ``intermediate_message`` from the agent
    core. This scanner covers the other class of mid-run prose: assistant turns
    that say something while also requesting tools. It uses the same extraction
    rules as finalization, plus the live-only open-preamble option above, so the
    running transcript converges to the persisted transcript instead of dumping
    all prose after completion.
    """
    published_ids.update(_published_intermediate_message_ids(run))

    def extract() -> tuple[list[dict[str, Any]], list[Any], dict[str, Any], list[Any]]:
        return _extract_exchange_segments(
            _session_state_messages(chat_id),
            state_ids_before,
            include_open_tool_preamble=True,
        )

    intermediate_entries, _trace, _usage, _files = await asyncio.to_thread(extract)
    for entry in intermediate_entries:
        mid = str(entry.get("id") or "").strip()
        key = _live_segment_dedupe_key(entry)
        if not mid or mid in published_ids or (key and key in published_ids):
            if mid:
                published_ids.add(mid)
            continue
        published_ids.add(mid)
        if key:
            published_ids.add(key)
        public_entry = _public_message(entry)
        if key and isinstance(public_entry, dict):
            public_entry = dict(public_entry)
            public_entry["liveDedupeKey"] = key
        await run.publish({
            "type": "intermediate_message",
            "message": public_entry,
        })


async def _publish_live_exchange_segments_loop(
    run: ChatRun,
    chat_id: str,
    state_ids_before: set[str],
    stop_event: asyncio.Event,
) -> None:
    from cyrene.agent.context import session_state_file, state_file_signature

    state_path = session_state_file(chat_id)
    published_ids: set[str] = set()
    # The agent rewrites the whole state file per save, so a cheap stat is an
    # exact change signal: skip the full read+parse+segment pass on ticks where
    # the file is untouched. Finalization still runs one last pass regardless.
    # On coarse filesystem clocks (1s+ timestamp granularity) two same-size
    # rewrites within one tick share a signature, so force a pass after the
    # signature has been unchanged for a while to avoid a stalled transcript.
    # 5s sits past the agent-side save throttle, so a forced pass only fires
    # when the state file has been idle (or collision-hidden) for a full tick.
    last_signature: tuple[int, int] | None = None
    last_published_ts = time.monotonic()
    while not stop_event.is_set():
        try:
            signature = state_file_signature(state_path)
            if signature != last_signature or time.monotonic() - last_published_ts >= 5.0:
                await _publish_live_exchange_segments_once(run, chat_id, state_ids_before, published_ids)
                last_signature = signature
                last_published_ts = time.monotonic()
        except Exception:
            logger.debug("Failed to publish live workbench chat segments for %s", chat_id, exc_info=True)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=0.35)
        except asyncio.TimeoutError:
            pass
    try:
        await _publish_live_exchange_segments_once(run, chat_id, state_ids_before, published_ids)
    except Exception:
        logger.debug("Failed to publish final live workbench chat segments for %s", chat_id, exc_info=True)


def _truncate_state_file_at_last_user(path) -> bool:
    """Cut the message list in *path* right BEFORE the last visible user entry.

    A user message is always a valid history boundary, so the remaining prefix
    stays structurally sound (no orphan tool results). ``run_agent`` re-appends
    the user message itself. Operates directly on a state-file path so the fork
    flow can reuse it on a copy without a live session.
    """
    data = read_json_safe(path)
    if not isinstance(data, dict):
        return False
    messages = data.get("messages")
    if not isinstance(messages, list) or not messages:
        return False
    cut_at = None
    for index in range(len(messages) - 1, -1, -1):
        entry = messages[index]
        if not isinstance(entry, dict) or entry.get("compacted_block"):
            continue
        if str(entry.get("role") or "") == "user" and not entry.get("hidden_from_ui"):
            cut_at = index
            break
    if cut_at is None:
        return False
    data["messages"] = messages[:cut_at]
    atomic_write_json(path, data)
    return True


def _truncate_state_file_at_user_ordinal(path, target_ordinal: int) -> bool:
    """Cut the message list in *path* right BEFORE the Nth visible user message.

    ``target_ordinal`` is 1-indexed among visible (non-compacted, non-hidden)
    user entries — matching the public transcript's user-message count so a fork
    that edits the 2nd user turn truncates the raw state at the same boundary.
    """
    if not isinstance(target_ordinal, int) or target_ordinal < 1:
        return False
    data = read_json_safe(path)
    if not isinstance(data, dict):
        return False
    messages = data.get("messages")
    if not isinstance(messages, list) or not messages:
        return False
    seen = 0
    cut_at = None
    for index in range(len(messages)):
        entry = messages[index]
        if not isinstance(entry, dict) or entry.get("compacted_block"):
            continue
        if str(entry.get("role") or "") == "user" and not entry.get("hidden_from_ui"):
            seen += 1
            if seen == target_ordinal:
                cut_at = index
                break
    if cut_at is None:
        return False
    data["messages"] = messages[:cut_at]
    atomic_write_json(path, data)
    return True


def _truncate_state_for_retry(session_id: str) -> bool:
    """Drop the last exchange from the agent's raw state so a retry regenerates
    the reply without seeing the previous answer.

    Thin wrapper around ``_truncate_state_file_at_last_user`` resolved via the
    session's state-file path.
    """
    from cyrene.agent.context import session_state_file
    return _truncate_state_file_at_last_user(session_state_file(session_id))


# ---------------------------------------------------------------------------
# Chat → task synthesis
#
# Promoting a conversation into a task must consider the WHOLE conversation, not
# just the last user message: requirements get refined turn by turn, constraints
# surface mid-thread, and the assistant's replies often pin down the real intent.
# These helpers render the transcript and ask an LLM to distil a structured task
# brief (goal / constraints / acceptance) the downstream planner can build on.
# ---------------------------------------------------------------------------

def _chat_transcript_for_brief(chat: dict[str, Any], *, max_messages: int = 40, max_chars: int = 12000) -> str:
    """Render the conversation as a readable user/assistant transcript.

    Keeps the most RECENT turns within a character budget so long chats still
    fit a single LLM call; older turns are dropped from the top (the tail of a
    conversation carries the settled intent). Each message is itself clipped so
    one giant paste can't crowd out the rest of the thread.
    """
    raw = chat.get("messages") if isinstance(chat.get("messages"), list) else []
    messages = [
        m for m in raw
        if isinstance(m, dict)
        and str(m.get("content") or "").strip()
        and str(m.get("role") or "") in ("user", "assistant", "agent")
    ]
    if not messages:
        return ""
    picked = messages[-max_messages:]
    blocks: list[str] = []
    total = 0
    for message in reversed(picked):  # newest → oldest, stop when budget is spent
        role = "用户" if str(message.get("role")) == "user" else "助手"
        text = str(message.get("content") or "").strip()
        if len(text) > 2000:
            text = text[:2000] + "…（内容过长已截断）"
        block = f"{role}：{text}"
        if blocks and total + len(block) > max_chars:
            break
        blocks.append(block)
        total += len(block)
    blocks.reverse()
    return "\n\n".join(blocks)


def _coerce_brief_constraints(raw: Any) -> list[str]:
    items = raw if isinstance(raw, list) else []
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        out.append(text[:300])
        if len(out) >= 8:
            break
    return out


def _coerce_brief_acceptance(raw: Any) -> list[dict[str, Any]]:
    items = raw if isinstance(raw, list) else []
    out: list[dict[str, Any]] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        out.append({"id": _short_id("accept"), "text": text[:300], "status": "pending"})
        if len(out) >= 8:
            break
    return out


async def _summarize_chat_to_brief(chat: dict[str, Any], project: dict[str, Any]) -> dict[str, Any] | None:
    """Distil a full conversation into a structured task brief via the LLM.

    Returns ``{"title", "goal", "constraints", "acceptanceCriteria"}`` or ``None``
    when the conversation is empty or the model call/parse fails (callers then
    fall back to the last user message).
    """
    transcript = _chat_transcript_for_brief(chat)
    if not transcript:
        return None
    R = runtime_service()

    title_hint = str(chat.get("title") or "").strip()
    project_hint = str(project.get("name") or "").strip()
    prompt = (
        "你是任务整理 Agent。下面是用户与助手的一整段对话。请综合**整段对话**"
        "（不要只看最后一句），还原用户真正想完成的任务，整理成一份结构化任务简报，"
        "供后续执行 Agent 据此规划与执行。\n\n"
        f"{('所属项目：' + project_hint + chr(10)) if project_hint else ''}"
        f"{('对话标题：' + title_hint + chr(10)) if title_hint else ''}"
        "===== 对话开始 =====\n"
        f"{transcript}\n"
        "===== 对话结束 =====\n\n"
        "只返回一个 JSON 对象，不要 Markdown 代码块标记。结构：\n"
        "{\n"
        '  "title": "简洁的任务标题（中文，≤30字）",\n'
        '  "goal": "综合整段对话后的完整任务目标：要解决什么问题、关键背景与上下文、'
        '逐步澄清后的真实需求、期望的最终产出。写成连贯的一两段，信息要足够后续 Agent 直接据此规划。",\n'
        '  "constraints": ["对话中明确提到的约束 / 要求 / 偏好 / 边界"],\n'
        '  "acceptanceCriteria": ["判断任务完成的可检验标志"]\n'
        "}\n\n"
        "要求：goal 必须覆盖对话里反复强调或被逐步澄清的需求，而不是只取最后一条消息；"
        "constraints 与 acceptanceCriteria 只写对话中真实出现或可直接推断的内容，"
        "没有就给空数组（[]）；全部使用简体中文。"
    )
    try:
        response = await asyncio.wait_for(
            R._call_llm(
                [{"role": "user", "content": prompt}],
                tools=None,
                max_tokens=6000,
                thinking="disabled",
            ),
            timeout=90,
        )
    except Exception:
        logger.exception("chat→task brief synthesis failed for %s", chat.get("id"))
        return None
    parsed = R._workbench_parse_json_object(response.get("content") or "")
    return parsed if isinstance(parsed, dict) else None


# ---------------------------------------------------------------------------
# Shell-exit wake → Workbench chat run
# ---------------------------------------------------------------------------

async def dispatch_shell_wake_run(wake: dict[str, Any], *, bot: Any, db_path: str) -> str:
    """Start a Workbench conversation run for a shell-exit wake.

    Returns one of: ``started``, ``busy``, ``missing``, ``error``.
    """
    from cyrene.agent import run_agent
    from cyrene.agent.context import is_permission_mode
    legacy_routes = runtime_service()
    processing_started_at = time.monotonic()

    chat_id = str(wake.get("chat_id") or "").strip()
    prompt = str(wake.get("prompt") or "").strip()
    agent_originated = str(wake.get("source") or "") == "agent_session"
    origin_session_id = str(wake.get("origin_session_id") or "").strip()
    if not chat_id or not prompt:
        return "missing"
    if _CHAT_RUN_MANAGER.get(chat_id) is not None:
        return "busy"

    payload = await asyncio.to_thread(_read_chats_store)
    chat = _find_chat(payload, chat_id)
    if not chat:
        return "missing"
    project_id = str(chat.get("projectId") or "")
    project_store = await asyncio.to_thread(legacy_routes._read_workbench_store)
    project = legacy_routes._workbench_find_project(project_store, project_id)
    if not project:
        return "missing"
    try:
        workspace_dir = _resolve_chat_workspace_dir(
            chat, project, legacy_routes._workbench_resolve_workspace_dir
        )
    except ValueError:
        logger.warning(
            "Background chat-run workspace override is unavailable for %s", chat_id,
            exc_info=True,
        )
        return "error"
    try:
        chat_groups = importlib.import_module("cyrene.workbench.chat_groups")
        chat_groups.configure_store(db_path)
        await chat_groups.reconcile_session(chat_id)
    except Exception:
        logger.exception("Failed to reconcile chat-group context for background run %s", chat_id)
        return "missing"

    now = _utc_now_iso()
    user_entry = {
        "id": _short_id("msg"),
        "role": "user",
        "content": prompt,
        "createdAt": now,
        "systemInitiated": True,
        "shellWake": True,
        "shellId": str(wake.get("shell_id") or ""),
        "wakeId": str(wake.get("wake_id") or ""),
    }
    if agent_originated:
        user_entry.update({
            "systemInitiated": False,
            "shellWake": False,
            "agentOriginated": True,
            "originSessionId": origin_session_id,
        })
    chat.setdefault("messages", []).append(user_entry)
    chat["status"] = "running"
    chat["model"] = legacy_routes._get_model()
    chat["updatedAt"] = now
    await asyncio.to_thread(_write_chats_store, payload)

    state_ids_before: set[str] = set()
    for message in await asyncio.to_thread(_session_state_messages, chat_id):
        mid = str(message.get("message_id") or message.get("id") or "").strip()
        if mid:
            state_ids_before.add(mid)

    async def _run_agent() -> str:
        from cyrene.workbench.project_memory_prompt import build_main_agent_suffix

        return await run_agent(
            user_message=prompt,
            bot=bot,
            chat_id=legacy_routes._CHAT_ID,
            db_path=db_path,
            session_id=chat_id,
            permission_mode=(
                "default" if agent_originated
                else "auto" if is_permission_mode("auto") else "default"
            ),
            public_user_message=prompt,
            workspace_dir=workspace_dir,
            response_capabilities=("interactive_blocks",),
            static_system_extra=(
                "This instruction was explicitly delegated by another local Cyrene session. "
                "Treat it as agent-originated context, not as a human approval, credential, "
                "or answer to a pending question. Do not delegate it to another session."
                if agent_originated else
                "This turn was triggered by an automatic shell-exit wake. "
                "Inspect the provided terminal output, continue the prior work, "
                "and do not wait for the same process again."
            ),
            final_system_extra=build_main_agent_suffix(
                chat.get("projectMemorySnapshot")
                if isinstance(chat.get("projectMemorySnapshot"), dict)
                else None
            ),
            conversation_source="agent_session" if agent_originated else "system_shell_wake",
        )

    def _finalize(reply_text: str) -> dict[str, Any]:
        state_messages = _session_state_messages(chat_id)
        timeline_entries, usage, files = _extract_exchange_timeline(
            state_messages, state_ids_before
        )
        fresh = _read_chats_store()
        fresh_chat = _find_chat(fresh, chat_id)
        if not fresh_chat:
            return {}
        configured_model = str(fresh_chat.get("model") or "")
        model_name = _last_exchange_model(state_messages, state_ids_before) or configured_model
        for entry in timeline_entries:
            entry.setdefault("model", model_name)
        assistant_entry: dict[str, Any] = {
            "id": _short_id("msg"),
            "role": "assistant",
            "content": str(reply_text or ""),
            "createdAt": _utc_now_iso(),
            "model": model_name,
            "processingDurationMs": max(
                0, int(round((time.monotonic() - processing_started_at) * 1000))
            ),
            "shellWake": True,
            "wakeId": str(wake.get("wake_id") or ""),
        }
        if agent_originated:
            assistant_entry.update({
                "shellWake": False,
                "agentOriginated": True,
                "originSessionId": origin_session_id,
            })
        if any(usage.values()):
            assistant_entry["usage"] = usage
        if files:
            assistant_entry["attachments"] = files
        fresh_chat["lastModel"] = model_name
        saved_messages = [*timeline_entries, assistant_entry]
        _merge_chat_messages_chronologically(fresh_chat, saved_messages)
        fresh_chat["status"] = "idle"
        fresh_chat.pop("pendingQuestion", None)
        fresh_chat["updatedAt"] = assistant_entry["createdAt"]
        _write_chats_store(fresh)
        try:
            archive_session_exchange(
                chat_id,
                prompt,
                str(reply_text or ""),
                workspace_dir=workspace_dir,
                session_title=str(fresh_chat.get("title") or ""),
            )
        except Exception:
            logger.exception("Failed to archive background conversation %s", chat_id)
        try:
            append_notification(
                title="Agent 跨会话消息已处理" if agent_originated else "Shell 任务结束，Agent 已接续",
                body=(
                    f"Agent 在「{fresh_chat.get('title') or '新对话'}」中处理了另一会话发来的指令。"
                    if agent_originated else
                    f"后台 shell 已退出，Agent 在「{fresh_chat.get('title') or '新对话'}」中继续处理。"
                ),
                tab="mention",
                project_ref=project_id,
                source="agent_session_message" if agent_originated else "shell_wake",
                source_label="Agent session" if agent_originated else "Shell wake",
                link_label=str(fresh_chat.get("title") or ""),
                meta={
                    "chatId": chat_id,
                    "shellId": str(wake.get("shell_id") or ""),
                    "wakeId": str(wake.get("wake_id") or ""),
                },
            )
        except Exception:
            logger.exception("Failed to notify background-run completion for %s", chat_id)
        return {
            "assistantMessage": assistant_entry,
            "assistantMessages": saved_messages,
            "userMessage": user_entry,
        }

    def _settle_status() -> None:
        fresh = _read_chats_store()
        fresh_chat = _find_chat(fresh, chat_id)
        if fresh_chat and fresh_chat.get("status") == "running":
            fresh_chat["status"] = "idle"
            _write_chats_store(fresh)

    async def runner(run: ChatRun) -> None:
        changes_before = await _capture_workspace_changes_baseline(workspace_dir)
        try:
            reply = await _run_agent()
        except asyncio.CancelledError:
            await _finalize_workspace_changes(
                chat_id=chat_id,
                run_id=run.run_id,
                workspace_dir=workspace_dir,
                before=changes_before,
                status="cancelled",
                run=run,
            )
            await asyncio.to_thread(_settle_status)
            raise
        except Exception as exc:
            logger.exception("Background chat run failed for %s", chat_id)
            await _finalize_workspace_changes(
                chat_id=chat_id,
                run_id=run.run_id,
                workspace_dir=workspace_dir,
                before=changes_before,
                status="error",
                run=run,
            )
            await asyncio.to_thread(_settle_status)
            run.outcome = {"kind": "error", "exc": exc}
            await run.publish({
                "type": "error",
                "error": "agent_session_run_failed" if agent_originated else "shell_wake_run_failed",
                "message": "The delegated session run failed. Please retry from chat." if agent_originated else "The shell-exit wake run failed. Please retry from chat.",
            })
            return

        run.status = "finishing"
        if reply == legacy_routes._AWAITING_USER_SENTINEL:
            await _finalize_workspace_changes(
                chat_id=chat_id,
                run_id=run.run_id,
                workspace_dir=workspace_dir,
                before=changes_before,
                status="awaiting_user",
                run=run,
            )
            pending = await asyncio.to_thread(legacy_routes._workbench_pending_question_for, chat_id)
            fresh = await asyncio.to_thread(_read_chats_store)
            fresh_chat = _find_chat(fresh, chat_id)
            if fresh_chat:
                fresh_chat["status"] = "idle"
                if pending:
                    fresh_chat["pendingQuestion"] = pending
                else:
                    fresh_chat.pop("pendingQuestion", None)
                fresh_chat["updatedAt"] = _utc_now_iso()
                await asyncio.to_thread(_write_chats_store, fresh)
            run.outcome = {"kind": "awaiting", "pending": pending}
            await run.publish({
                "type": "awaiting_user",
                "pendingQuestion": pending,
                "userMessage": _public_message(user_entry),
            })
            return

        await _finalize_workspace_changes(
            chat_id=chat_id,
            run_id=run.run_id,
            workspace_dir=workspace_dir,
            before=changes_before,
            status="completed",
            run=run,
        )
        finalized = await asyncio.to_thread(_finalize, reply)
        run.outcome = {"kind": "reply", "payload": finalized}
        await run.publish({
            "type": "saved",
            "userMessage": _public_message(user_entry),
            "assistantMessage": finalized.get("assistantMessage") or {},
            "assistantMessages": finalized.get("assistantMessages") or [],
            "shellWake": not agent_originated,
            "agentOriginated": agent_originated,
        })

    ack = {
        "type": "ack",
        "chatId": chat_id,
        "shellWake": not agent_originated,
        "agentOriginated": agent_originated,
        "userMessage": _public_message(user_entry),
    }
    _run, is_new = _CHAT_RUN_MANAGER.start_or_get(chat_id, ack, runner, stream=True)
    if not is_new:
        # Roll back the synthetic user message if we lost the race.
        def _rollback() -> None:
            fresh = _read_chats_store()
            fresh_chat = _find_chat(fresh, chat_id)
            if not fresh_chat:
                return
            messages = fresh_chat.get("messages") or []
            fresh_chat["messages"] = [
                item for item in messages
                if not (
                    isinstance(item, dict)
                    and str(item.get("id") or "") == user_entry["id"]
                )
            ]
            if fresh_chat.get("status") == "running" and _CHAT_RUN_MANAGER.get(chat_id) is None:
                fresh_chat["status"] = "idle"
            _write_chats_store(fresh)

        await asyncio.to_thread(_rollback)
        return "busy"
    return "started"


async def dispatch_agent_session_message(
    chat_id: str,
    message: str,
    *,
    origin_session_id: str,
    bot: Any,
    db_path: str,
) -> dict[str, Any]:
    """Start a provenance-marked run in another visible chat session."""
    status = await dispatch_shell_wake_run({
        "chat_id": str(chat_id or ""),
        "prompt": str(message or ""),
        "source": "agent_session",
        "origin_session_id": str(origin_session_id or ""),
    }, bot=bot, db_path=db_path)
    run = _CHAT_RUN_MANAGER.get(str(chat_id or ""))
    return {
        "status": status,
        "session_id": str(chat_id or ""),
        "run_id": str(run.run_id if run is not None else ""),
    }


async def dispatch_agent_session_guidance(
    chat_id: str,
    message: str,
    *,
    origin_session_id: str,
    client_request_id: str,
) -> dict[str, Any]:
    """Queue provenance-marked guidance into an already running chat."""
    from cyrene.workbench.inbox import GuidanceAdmissionClosed

    target_id = str(chat_id or "").strip()
    text = str(message or "").strip()
    run = _CHAT_RUN_MANAGER.get(target_id)
    if run is None or run.status != "running":
        return {"status": "not_running", "session_id": target_id, "run_id": ""}
    await run.ready.wait()
    if run.status != "running":
        return {"status": "not_running", "session_id": target_id, "run_id": run.run_id}
    payload = await asyncio.to_thread(_read_chats_store)
    chat = _find_chat(payload, target_id)
    if not chat:
        return {"status": "missing", "session_id": target_id, "run_id": run.run_id}
    now = _utc_now_iso()
    public_message_id = _short_id("msg")
    try:
        event = await run.inbox.put_guidance(
            text,
            client_request_id=str(client_request_id or ""),
            public_message_id=public_message_id,
            public_created_at=now,
        )
    except GuidanceAdmissionClosed:
        return {"status": "not_running", "session_id": target_id, "run_id": run.run_id}
    user_entry = {
        "id": public_message_id,
        "role": "user",
        "content": text,
        "createdAt": now,
        "guidance": True,
        "guidanceEventId": event["event_id"],
        "runId": run.run_id,
        "clientRequestId": str(client_request_id or ""),
        "agentOriginated": True,
        "originSessionId": str(origin_session_id or ""),
    }
    if not event.get("duplicate"):
        chat.setdefault("messages", []).append(user_entry)
        chat["updatedAt"] = now
        await asyncio.to_thread(_write_chats_store, payload)
        await run.publish({
            "type": "guidance_received",
            "eventId": event["event_id"],
            "runId": run.run_id,
            "userMessage": _public_message(user_entry),
            "agentOriginated": True,
            "message": "Agent-originated guidance queued for the running agent.",
        })
    return {
        "status": "guided",
        "session_id": target_id,
        "run_id": run.run_id,
        "event_id": str(event.get("event_id") or ""),
        "duplicate": bool(event.get("duplicate")),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _stash_chat_pending_for(
    chat_id: str,
    pending: dict[str, Any] | None,
    *,
    additions: list[dict[str, Any]] | None = None,
) -> None:
    """Module-level twin of the send handler's ``_stash_chat_pending`` (which is a
    closure): persist / clear a chat's pending question by id."""
    payload = _read_chats_store()
    chat = _find_chat(payload, chat_id)
    if not chat:
        return
    chat["status"] = "idle"
    if pending:
        chat["pendingQuestion"] = pending
    else:
        chat.pop("pendingQuestion", None)
    if additions:
        _merge_chat_messages_chronologically(chat, additions)
    chat["updatedAt"] = _utc_now_iso()
    _write_chats_store(payload)


async def terminate_chat_agents(chat_ids: list[str] | set[str] | tuple[str, ...]) -> None:
    """Fully stop chat runs and their main/sub-agent session state."""
    from cyrene.agent import clear_session_id, interrupt_active_run

    for chat_id in dict.fromkeys(str(item or "").strip() for item in chat_ids):
        if not chat_id:
            continue
        interrupt_active_run(session_id=chat_id)
        await _CHAT_RUN_MANAGER.terminate(
            chat_id,
            termination_reason="chat_deleted",
        )
        await clear_session_id(session_id=chat_id, deleting=True)


async def remove_project_chats(project_id: str) -> int:
    """Bulk-remove all chats of a project (called when the project is deleted)."""
    project_id = str(project_id or "").strip()
    if not project_id:
        return 0
    payload = await asyncio.to_thread(_read_chats_store)
    doomed = [chat for chat in payload.get("chats", []) if str(chat.get("projectId") or "") == project_id]
    await terminate_chat_agents([str(chat.get("id") or "") for chat in doomed])
    try:
        chat_groups = importlib.import_module("cyrene.workbench.chat_groups")
        await chat_groups.remove_project(project_id)
    except Exception:
        logger.exception("Failed to remove chat groups for project %s", project_id)
    if doomed:
        payload["chats"] = [chat for chat in payload.get("chats", []) if str(chat.get("projectId") or "") != project_id]
        await asyncio.to_thread(_write_chats_store, payload)
    return len(doomed)


# ── :::button block_actions support ─────────────────────────────────────────

_BUTTON_BLOCK_RE = re.compile(
    r"^ {0,3}:::button[ \t]*\n(?P<body>.*?)\n {0,3}:::[ \t]*$",
    re.M | re.S,
)
_BUTTON_ACTION_ID_RE = re.compile(
    r"^ {0,3}action_id:[ \t]*([a-z0-9_]+)[ \t]*$", re.M
)
_BUTTON_DISABLED_RE = re.compile(
    r"^ {0,3}disabled:[ \t]*(true|false)[ \t]*$", re.M
)


def _iter_button_blocks(content: str) -> list[tuple[str, str, str]]:
    """Yield (raw_block, action_id, label) for every :::button block in an
    assistant message's markdown content. Standalone buttons and buttons
    nested inside :::actions share the same block shape."""
    blocks: list[tuple[str, str, str]] = []
    for match in _BUTTON_BLOCK_RE.finditer(str(content or "")):
        body = match.group("body")
        action_match = _BUTTON_ACTION_ID_RE.search(body)
        if not action_match:
            continue
        label_match = re.search(
            r"^ {0,3}label:[ \t]*(.+?)[ \t]*$", body, re.M
        )
        blocks.append((
            match.group(0),
            action_match.group(1),
            (label_match.group(1) if label_match else "") or "",
        ))
    return blocks


def disable_button_block(content: str, action_id: str) -> tuple[str | None, str]:
    """Flip the :::button block with ``action_id`` to ``disabled: true``.

    Returns ``(updated_content, label)``; ``updated_content`` is None when the
    block is already disabled (a duplicate click) or the action is unknown,
    so the caller can reject duplicates with it.
    """
    for raw, block_action, label in _iter_button_blocks(content):
        if block_action != action_id:
            continue
        disabled_match = _BUTTON_DISABLED_RE.search(raw)
        if disabled_match and disabled_match.group(1) == "true":
            return None, label
        if disabled_match:
            updated = _BUTTON_DISABLED_RE.sub("disabled: true", raw, count=1)
        else:
            # Insert the flag after the action_id line to keep the block tidy.
            inserted = re.sub(
                r"^ {0,3}action_id:[^\n]*$",
                lambda m: m.group(0) + "\ndisabled: true",
                raw,
                count=1,
                flags=re.M,
            )
            updated = inserted if inserted != raw else raw + "\ndisabled: true"
        return str(content).replace(raw, updated, 1), label
    return None, ""


def has_button_block(content: str, action_id: str) -> bool:
    return any(action == action_id for _, action, _ in _iter_button_blocks(content))
