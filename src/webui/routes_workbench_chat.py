"""Workspace-scoped conversation (对话) API for the new Workbench UI.

This module is intentionally INDEPENDENT from the legacy single-session chat
(``/api/chat`` in ``routes.py``), which the old ``--agent`` UI uses. It exposes
a parallel set of endpoints under ``/api/workbench/chats*`` so the two UIs
never share request code, while reusing the same per-session agent runtime
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
import hashlib
import json
import logging
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from cyrene.call_llm import NETWORK_RETRY_LIMIT
from cyrene.config import DATA_DIR
from cyrene.conversations import archive_session_exchange
from cyrene.io_utils import atomic_write_json, read_json_safe
from cyrene.workbench_store import read_document, write_document
from webui import api_models
from webui.workbench_chat_runs import ChatRun, ChatRunManager
from webui.workbench_notifications import append_notification

logger = logging.getLogger(__name__)

_CHATS_STORE = DATA_DIR / "workbench_chats.json"
_STORE_DB_PATH = ""
_CONFIGURED_CHATS_STORE = None
_CHAT_RUN_MANAGER = ChatRunManager()


def _settle_chat_running_status(chat_id: str) -> None:
    """Repair a stale persisted running flag after a run disappears."""
    payload = _read_chats_store()
    chat = _find_chat(payload, str(chat_id))
    if chat and chat.get("status") == "running":
        chat["status"] = "idle"
        chat.pop("pendingQuestion", None)
        chat["updatedAt"] = _utc_now_iso()
        _write_chats_store(payload)

# Internal control tools that say nothing useful in a progress trace.
_TRACE_SKIP_TOOLS = {"use_tools", "quit", "send_message", "update_plan_progress"}
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
    path = Path(raw_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_plan_markdown(plan), encoding="utf-8")


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
        out["markdownPath"] = str(Path(workspace_dir) / "plan" / f"{slug}.md")
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


def _workbench_chat_run_error_message(exc: Exception, lang: str = "") -> str:
    """Return a user-facing message after bounded model-network retries."""
    if isinstance(exc, httpx.TransportError):
        if str(lang or "").lower() == "en":
            return (
                f"The network connection still failed after {NETWORK_RETRY_LIMIT} automatic retries. "
                "Please send this message again."
            )
        return f"网络连接异常，已自动重试 {NETWORK_RETRY_LIMIT} 次仍未成功。请重新发送这条消息。"
    return str(exc).strip() or exc.__class__.__name__


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

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


def configure_store(db_path: str) -> None:
    global _STORE_DB_PATH, _CONFIGURED_CHATS_STORE
    _STORE_DB_PATH = str(db_path or "")
    _CONFIGURED_CHATS_STORE = _CHATS_STORE


def startup_chat_runs() -> None:
    _CHAT_RUN_MANAGER.startup()


async def shutdown_chat_runs() -> None:
    await _CHAT_RUN_MANAGER.shutdown()


def _mark_user_activity(chat: dict[str, Any], timestamp: str) -> None:
    """Record real user activity and restart the proactive lottery window."""
    from cyrene.scheduler import reset_lottery

    chat["lastUserMessageAt"] = timestamp
    chat["updatedAt"] = timestamp
    reset_lottery()


async def append_proactive_message(chat_id: str, text: str) -> dict[str, str] | None:
    """Persist a proactive assistant reply in a Workbench public transcript."""
    from cyrene import debug

    payload = _read_chats_store()
    chat = _find_chat(payload, str(chat_id or ""))
    content = str(text or "").strip()
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


def _new_chat(project_id: str, title: str = "", model: str = "") -> dict[str, Any]:
    now = _utc_now_iso()
    return {
        "id": _short_id("wbchat"),
        "projectId": str(project_id or ""),
        "kind": "chat",
        "title": str(title or "新对话").strip()[:60] or "新对话",
        "status": "idle",
        "model": model,
        "createdAt": now,
        "updatedAt": now,
        "messages": [],
    }


def _find_chat(payload: dict[str, Any], chat_id: str) -> dict[str, Any] | None:
    for chat in payload.get("chats", []):
        if str(chat.get("id") or "") == chat_id:
            return chat
    return None


_FORK_METADATA_FIELDS = ("forkedFromChatId", "forkedAtMessageId", "forkMessage")


def _clear_fork_metadata(chat: dict[str, Any]) -> bool:
    changed = False
    for field in _FORK_METADATA_FIELDS:
        if field in chat:
            chat.pop(field, None)
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


def _public_chat_light(chat: dict[str, Any]) -> dict[str, Any]:
    """Listing payload — transcript omitted to keep the rail cheap."""
    usage = _aggregate_usage(chat.get("messages") or [])
    payload = {
        "id": chat.get("id"),
        "projectId": chat.get("projectId"),
        "kind": "chat",
        "title": chat.get("title"),
        "status": chat.get("status") or "idle",
        "model": chat.get("model") or "",
        "createdAt": chat.get("createdAt"),
        "updatedAt": chat.get("updatedAt"),
        "preview": _chat_preview(chat),
        "messageCount": len(chat.get("messages") or []),
        "usage": usage,
        "pendingQuestion": chat.get("pendingQuestion") or None,
    }
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
    return payload


def _public_message(message: dict[str, Any]) -> dict[str, Any]:
    """Transcript entry without server-private fields (local upload paths)."""
    if isinstance(message, dict) and "agentAttachments" in message:
        return {k: v for k, v in message.items() if k != "agentAttachments"}
    return message


def _merge_chat_messages_chronologically(
    chat: dict[str, Any], additions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Insert new transcript entries at their actual event time.

    Guidance can be persisted while an agent run is still active.  The assistant
    messages that happened before that guidance are discovered only when the run
    is checkpointed/finalized, so blindly appending them groups every user entry
    at the top.  Insert each newly discovered entry before the first later
    timestamp while preserving the existing order for legacy timestamp-less
    records.
    """
    messages = chat.setdefault("messages", [])
    known_ids = {
        str(item.get("id") or ""): index
        for index, item in enumerate(messages)
        if isinstance(item, dict) and str(item.get("id") or "")
    }
    known_intermediate_keys = {
        key: index
        for index, item in enumerate(messages)
        if isinstance(item, dict) and bool(item.get("intermediate"))
        if (key := _live_segment_dedupe_key(item))
    }
    for item in additions:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        if item_id and item_id in known_ids:
            index = known_ids[item_id]
            messages[index] = {**messages[index], **item}
            continue
        intermediate_key = _live_segment_dedupe_key(item) if bool(item.get("intermediate")) else ""
        if intermediate_key and intermediate_key in known_intermediate_keys:
            index = known_intermediate_keys[intermediate_key]
            messages[index] = {**messages[index], **item, "id": messages[index].get("id") or item_id}
            continue
        created_at = str(item.get("createdAt") or item.get("created_at") or "")
        insert_at = len(messages)
        if created_at:
            for index, current in enumerate(messages):
                if not isinstance(current, dict):
                    continue
                current_at = str(current.get("createdAt") or current.get("created_at") or "")
                if current_at and current_at > created_at:
                    insert_at = index
                    break
        messages.insert(insert_at, item)
        if item_id:
            known_ids[item_id] = insert_at
        if intermediate_key:
            known_intermediate_keys[intermediate_key] = insert_at
        # Insertion shifts every later cached index.
        known_ids = {
            str(existing.get("id") or ""): index
            for index, existing in enumerate(messages)
            if isinstance(existing, dict) and str(existing.get("id") or "")
        }
        known_intermediate_keys = {
            key: index
            for index, existing in enumerate(messages)
            if isinstance(existing, dict) and bool(existing.get("intermediate"))
            if (key := _live_segment_dedupe_key(existing))
        }
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
    payload["messages"] = [_public_message(m) for m in (chat.get("messages") or [])]
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

    from cyrene.subagent import _registry, _subagent_tasks  # noqa: WPS437
    live_task_ids = {
        agent_id
        for agent_id, task in _subagent_tasks.items()
        if task is not None and not task.done()
    }
    for agent_id, info in _registry.items():
        if str(info.get("session_id") or "") != chat_id:
            continue
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
        from webui import routes as R

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
    from cyrene.agent.state import _session_state_file
    data = read_json_safe(_session_state_file(session_id))
    if isinstance(data, dict) and isinstance(data.get("messages"), list):
        return data["messages"]
    return []


# Categories surfaced in the Workbench overview's "context breakdown" bar. The
# order is the visual stacking order (oldest/system first, live turns last).
_CONTEXT_SEGMENT_KEYS = ("compacted", "system", "user", "assistant", "tool")


def _context_segment_tokens(messages: list[dict[str, Any]]) -> dict[str, int]:
    """Split the agent's RAW context into per-category token estimates.

    This mirrors ``call_llm._message_token_estimate`` field-by-field so the sum
    equals what the compactor measures against the context window — the gauge and
    the 60% compaction trigger therefore share one honest denominator. Each
    message's tokens are attributed to a UI bucket:

    - ``compacted`` — append-only summary blocks of older history
    - ``system``    — live system messages (non-compacted)
    - ``user`` / ``assistant`` — prose by author (assistant prose only)
    - ``tool``      — assistant tool-call args + tool-result bodies (the bulk)
    """
    from cyrene.call_llm import _approx_token_count

    seg = {key: 0 for key in _CONTEXT_SEGMENT_KEYS}
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        base = 4 + _approx_token_count(role)
        if message.get("compacted_block"):
            seg["compacted"] += base + _approx_token_count(message.get("content") or "")
            continue
        content = message.get("content")
        if isinstance(content, str):
            content_tokens = _approx_token_count(content)
        elif isinstance(content, list):
            content_tokens = sum(
                _approx_token_count(block.get("text") or "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        else:
            content_tokens = _approx_token_count(content or "")
        tool_tokens = 0
        for tool_call in message.get("tool_calls") or []:
            fn = tool_call.get("function", {}) if isinstance(tool_call, dict) else {}
            tool_tokens += _approx_token_count(fn.get("name") or "")
            tool_tokens += _approx_token_count(fn.get("arguments") or "")
        if role == "user":
            seg["user"] += base + content_tokens
        elif role == "assistant":
            seg["assistant"] += base + content_tokens + _approx_token_count(message.get("reasoning_content") or "")
            seg["tool"] += tool_tokens
        elif role == "tool":
            seg["tool"] += base + content_tokens + _approx_token_count(message.get("tool_call_id") or "")
        else:
            seg["system"] += base + content_tokens
    return seg


def _chat_context_payload(state_id: str, model_name: str) -> dict[str, Any]:
    """Live context-window composition for one chat, computed from raw state.

    Per-conversation by construction (state lives at ``sessions/<id>/state.json``)
    and cheap enough to poll while a run streams, so the overview updates in
    real time as the agent appends turns.
    """
    from cyrene.agent.session import _COMPACT_TRIGGER_RATIO
    from cyrene.config_store import ctx_limit_for_model

    messages = _session_state_messages(state_id)
    seg = _context_segment_tokens(messages)
    used = sum(seg.values())
    limit = ctx_limit_for_model(model_name)
    compacted_blocks = sum(
        1 for m in messages if isinstance(m, dict) and m.get("compacted_block")
    )
    distilled = any(
        isinstance(m, dict) and m.get("compacted_block") and m.get("llm_compacted")
        for m in messages
    )
    return {
        "ctxLimit": limit,
        "ctxUsed": used,
        "ratio": (used / limit) if limit > 0 else None,
        "compactTriggerRatio": _COMPACT_TRIGGER_RATIO,
        "messageCount": len(messages),
        "segments": [{"key": key, "tokens": seg[key]} for key in _CONTEXT_SEGMENT_KEYS],
        "compaction": {
            "active": compacted_blocks > 0,
            "blocks": compacted_blocks,
            "tokens": seg["compacted"],
            "distilled": distilled,
        },
    }


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
            segments.append(_make_reply_segment(
                message,
                trace,
                usage,
                files,
                fallback_id=_segment_fallback_id(message, idx),
            ))
            trace, usage, files, seen_file_urls = [], _exchange_usage(), [], set()
            _accumulate_tools(message, trace, result_map)
            continue

        _append_exchange_meta(message, trace, usage, files, seen_file_urls, result_map)

    if not usage["total_tokens"]:
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    return segments, trace[:40], usage, files[:20]


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
    published_ids: set[str] = set()
    while not stop_event.is_set():
        try:
            await _publish_live_exchange_segments_once(run, chat_id, state_ids_before, published_ids)
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
    from cyrene.agent.state import _session_state_file
    return _truncate_state_file_at_last_user(_session_state_file(session_id))


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
    from webui import routes as R

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
                max_tokens=2000,
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
# Routes
# ---------------------------------------------------------------------------

def register_workbench_chat_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    configure_store(db_path)
    _CHAT_RUN_MANAGER.configure(db_path)
    # Heavyweight helpers (store access, attachments, agent entrypoints) live in
    # webui.routes; import lazily at call time to avoid a circular import.

    def _routes():
        from webui import routes as legacy_routes
        return legacy_routes

    def _project_data_key(project_id: str) -> str:
        R = _routes()
        project = R._workbench_find_project_lightweight(project_id)
        return R._workbench_project_data_key(project) if project else project_id

    @router.get("/api/workbench/chats")
    async def api_workbench_list_chats(project: str = ""):
        started = time.monotonic()
        # SQLite busy waits and JSON decoding are synchronous. Keep them off the
        # uvicorn event loop so one contended read cannot freeze every Workbench
        # request (the client otherwise reaches its 30s timeout as a group).
        payload = await asyncio.to_thread(_read_chats_store)
        if _prune_orphaned_fork_metadata(payload):
            await asyncio.to_thread(_write_chats_store, payload)
        data_key = await asyncio.to_thread(_project_data_key, project) if project else ""
        chats = [
            _public_chat_light(chat)
            for chat in payload.get("chats", [])
            if not project or str(chat.get("projectId") or "") == project
        ]
        if project and data_key == "default":
            legacy = await asyncio.to_thread(_legacy_chats, project)
            legacy.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
            chats.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
            chats = chats + legacy
        else:
            chats.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        elapsed_ms = (time.monotonic() - started) * 1000
        if elapsed_ms >= 1000:
            logger.warning("Slow Workbench chat list load [project=%s duration_ms=%.1f]", project, elapsed_ms)
        return {"chats": chats}

    @router.get("/api/workbench/quick-chat/targets")
    async def api_workbench_quick_chat_targets(q: str = "", limit: int = 40):
        """Send targets for the quick-chat window: writable modern chats across
        every project plus the resolved default project (where an unselected
        quick chat starts a new conversation).

        Legacy sessions are read-only and live outside the chats store, so they
        never appear here. ``running`` reflects the authoritative in-flight run
        registry (not the persisted status, which can be stale after a crash).
        """
        R = _routes()
        store = await asyncio.to_thread(R._read_workbench_store)
        projects = store.get("projects", []) or []
        # The default project is identified by its data key, not its name — the
        # name follows the workspace directory and need not be "Cyrene".
        default_project = next(
            (p for p in projects if R._workbench_project_data_key(p) == "default"),
            None,
        )
        if default_project is None and projects:
            default_project = projects[0]
        project_by_id = {str(p.get("id") or ""): p for p in projects}

        query = str(q or "").strip().lower()
        limit = max(1, min(int(limit or 40), 200))

        payload = await asyncio.to_thread(_read_chats_store)
        targets: list[dict[str, Any]] = []
        for chat in payload.get("chats", []):
            chat_id = str(chat.get("id") or "")
            if not chat_id:
                continue
            project_id = str(chat.get("projectId") or "")
            project = project_by_id.get(project_id) or {}
            project_name = str(project.get("name") or "")
            title = str(chat.get("title") or "")
            preview = _chat_preview(chat)
            if query and query not in " ".join([title, project_name, preview]).lower():
                continue
            targets.append(
                {
                    "chatId": chat_id,
                    "title": title,
                    "projectId": project_id,
                    "projectName": project_name,
                    "workspacePath": str(project.get("workspacePath") or ""),
                    "model": str(chat.get("model") or project.get("model") or ""),
                    "preview": preview,
                    "updatedAt": str(chat.get("updatedAt") or ""),
                    "running": _CHAT_RUN_MANAGER.get(chat_id) is not None,
                    "writable": True,
                }
            )
        targets.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        targets = targets[:limit]

        default_payload = None
        if default_project is not None:
            default_payload = {
                "id": str(default_project.get("id") or ""),
                "name": str(default_project.get("name") or ""),
                "dataKey": R._workbench_project_data_key(default_project),
                "workspacePath": str(default_project.get("workspacePath") or ""),
                "model": str(default_project.get("model") or ""),
            }
        return {"defaultProject": default_payload, "targets": targets}

    @router.post("/api/workbench/chats")
    async def api_workbench_create_chat(body_model: api_models.ChatCreateBody):
        started = time.monotonic()
        body = api_models.body_dict(body_model)
        project_id = str(body.get("project") or body.get("projectId") or "").strip()
        if not project_id:
            return JSONResponse({"error": "project is required"}, status_code=400)
        R = _routes()
        project = await asyncio.to_thread(R._workbench_find_project_lightweight, project_id)
        if not project:
            return JSONResponse({"error": "project not found"}, status_code=404)

        def create_and_persist() -> dict[str, Any]:
            payload = _read_chats_store()
            chat = _new_chat(project_id, str(body.get("title") or ""), R._get_model())
            payload.setdefault("chats", []).insert(0, chat)
            _write_chats_store(payload)
            return chat

        chat = await asyncio.to_thread(create_and_persist)
        elapsed_ms = (time.monotonic() - started) * 1000
        if elapsed_ms >= 250:
            logger.warning(
                "Slow Workbench chat creation [project=%s duration_ms=%.1f]",
                project_id,
                elapsed_ms,
            )
        return {"ok": True, "chat": _public_chat_full(chat)}

    @router.get("/api/workbench/chats/{chat_id}")
    async def api_workbench_get_chat(chat_id: str):
        started = time.monotonic()
        if chat_id.startswith("legacy:"):
            _prefix, project_id, _session_id = (chat_id.split(":", 2) + ["", ""])[:3]
            data_key = await asyncio.to_thread(_project_data_key, project_id) if project_id else ""
            if not project_id or data_key != "default":
                return JSONResponse({"error": "chat not found"}, status_code=404)
            legacy = await asyncio.to_thread(_legacy_chats, project_id, full_id=chat_id)
            if not legacy:
                return JSONResponse({"error": "chat not found"}, status_code=404)
            elapsed_ms = (time.monotonic() - started) * 1000
            if elapsed_ms >= 1000:
                logger.warning(
                    "Slow legacy Workbench chat detail load [chat_id=%s duration_ms=%.1f]",
                    chat_id,
                    elapsed_ms,
                )
            return {"chat": legacy[0]}
        payload = await asyncio.to_thread(_read_chats_store)
        if _prune_orphaned_fork_metadata(payload):
            await asyncio.to_thread(_write_chats_store, payload)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        elapsed_ms = (time.monotonic() - started) * 1000
        if elapsed_ms >= 1000:
            logger.warning("Slow Workbench chat detail load [chat_id=%s duration_ms=%.1f]", chat_id, elapsed_ms)
        return {"chat": _public_chat_full(chat)}

    @router.get("/api/workbench/chats/{chat_id}/subagents")
    async def api_workbench_chat_subagents(chat_id: str, round_id: str = ""):
        if chat_id.startswith("legacy:"):
            return {"rounds": [], "activeRoundId": "", "agents": [], "messages": []}
        payload = await asyncio.to_thread(_read_chats_store)
        if not _find_chat(payload, chat_id):
            return JSONResponse({"error": "chat not found"}, status_code=404)
        return await asyncio.to_thread(_workbench_subagent_payload, chat_id, round_id)

    @router.get("/api/workbench/chats/{chat_id}/context")
    async def api_workbench_chat_context(chat_id: str):
        """Live context-window gauge + composition for the overview panel."""
        from cyrene import config

        if chat_id.startswith("legacy:"):
            _prefix, _project_id, session_id = (chat_id.split(":", 2) + ["", ""])[:3]
            if not session_id:
                return JSONResponse({"error": "chat not found"}, status_code=404)
            model_name = str(getattr(config, "OPENAI_MODEL", "") or "")
            return await asyncio.to_thread(_chat_context_payload, session_id, model_name)
        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        model_name = str(chat.get("model") or getattr(config, "OPENAI_MODEL", "") or "")
        return await asyncio.to_thread(_chat_context_payload, chat_id, model_name)

    @router.post("/api/workbench/chats/{chat_id}/compact")
    async def api_workbench_chat_compact(chat_id: str):
        """Let the user explicitly run the normal session compaction flow."""
        from cyrene import config
        from cyrene.agent import compact_session_if_needed
        from cyrene.config_store import ctx_limit_for_model

        if chat_id.startswith("legacy:"):
            return JSONResponse(
                {"error": "legacy chat context is read-only"},
                status_code=403,
            )
        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        model_name = str(
            chat.get("model") or getattr(config, "OPENAI_MODEL", "") or ""
        )
        result = await compact_session_if_needed(
            chat_id,
            # Explicit compaction must always have a usable budget even when an
            # OpenAI-compatible custom model has no family heuristic/configured
            # context size. 128K is the conservative default used by the core
            # chat models and is safer than passing 0 (which disables budgeting).
            ctx_limit=ctx_limit_for_model(model_name) or 128_000,
            force=True,
        )
        return {"ok": True, **result}

    @router.get("/api/workbench/chats/{chat_id}/context-blocks")
    async def api_workbench_chat_context_blocks(chat_id: str):
        """Context block composition using the same token math as the Overview gauge."""
        from cyrene.agent.state import _session_state_file
        from cyrene.call_llm import _approx_token_count

        if chat_id.startswith("legacy:"):
            _, _project_id, session_id = (chat_id.split(":", 2) + ["", ""])[:3]
            if not session_id:
                return JSONResponse({"error": "chat not found"}, status_code=404)
            state_id = session_id
        else:
            state_id = chat_id

        data = read_json_safe(_session_state_file(state_id))
        if not isinstance(data, dict):
            return {"layers": [], "totalTokensEst": 0}

        messages = data.get("messages")
        if not isinstance(messages, list):
            messages = []
        seg = _context_segment_tokens(messages)
        msg_total = sum(seg.values())

        layers: list[dict[str, Any]] = []

        # Layer 1: System Prefix — from separately-saved blocks (not in state.json)
        sys_blocks = data.get("system_context_blocks")
        if isinstance(sys_blocks, list) and sys_blocks:
            sys_tokens = sum(int(b.get("tokens_est", 0) or 0) for b in sys_blocks if isinstance(b, dict))
            layers.append({
                "id": "system_prefix",
                "label": "System Prefix",
                "sublabel": None,
                "blocks": [dict(b) for b in sys_blocks if isinstance(b, dict)],
                "totalTokens": sys_tokens,
            })

        # Layer 2: Ephemeral — from saved text (not in state.json)
        ephemeral = data.get("ephemeral_context")
        if isinstance(ephemeral, str) and ephemeral.strip():
            tokens = _approx_token_count(ephemeral)
            layers.append({
                "id": "ephemeral",
                "label": "Ephemeral Tail",
                "sublabel": None,
                "blocks": [{"id": "ephemeral.run", "type": "ephemeral", "tokens_est": tokens, "chars": len(ephemeral)}],
                "totalTokens": tokens,
            })

        # Layer 3: Messages — same segments as the Overview gauge
        msg_seg_order = [
            ("compacted", "Compacted"),
            ("system", "System"),
            ("user", "User"),
            ("assistant", "Assistant"),
            ("tool", "Tool"),
        ]
        msg_blocks = []
        for key, label in msg_seg_order:
            t = int(seg.get(key, 0) or 0)
            if t > 0:
                msg_blocks.append({"id": "segment." + key, "type": key, "tokens_est": t, "source": "", "reason": ""})
        if msg_blocks:
            layers.append({
                "id": "messages",
                "label": "Conversation Messages",
                "sublabel": None,
                "blocks": msg_blocks,
                "totalTokens": msg_total,
            })

        total = sum(layer["totalTokens"] for layer in layers)
        return {"layers": layers, "totalTokensEst": total, "messageTokens": msg_total}

    @router.get("/api/workbench/chats/{chat_id}/run-stream")
    async def api_workbench_chat_run_stream(chat_id: str):
        """Reconnect to an existing streamed run without submitting a message."""
        run = _CHAT_RUN_MANAGER.get(chat_id)
        if run is None:
            await asyncio.to_thread(_settle_chat_running_status, chat_id)
            return JSONResponse(
                {"error": "chat has no running reply", "code": "chat_run_not_found"},
                status_code=404,
            )
        return StreamingResponse(
            _CHAT_RUN_MANAGER.stream(run),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache"},
        )

    @router.post("/api/workbench/chats/{chat_id}/guidance")
    async def api_workbench_chat_guidance(
        chat_id: str, body_model: api_models.ChatGuidanceBody
    ):
        """Steer the currently running Workbench conversation.

        Guidance is queued in the run-scoped inbox.  A tool waiter consumes it
        immediately; otherwise the agent picks it up at the next model/tool
        boundary.  It never starts a second conversation run.
        """
        body = api_models.body_dict(body_model)
        message = str(body.get("message") or "").strip()
        client_request_id = str(body.get("clientRequestId") or "").strip()
        if not message:
            return JSONResponse(
                {"error": "guidance message is empty", "code": "guidance_empty"},
                status_code=422,
            )
        run = _CHAT_RUN_MANAGER.get(chat_id)
        if run is None or run.status != "running":
            return JSONResponse(
                {"error": "chat has no running reply", "code": "chat_not_running"},
                status_code=409,
            )
        # Durable inbox setup happens off the HTTP event loop. Guidance must
        # wait for it before accepting an event, otherwise a just-started run
        # can race schema initialization.
        await run.ready.wait()
        if run.status != "running":
            return JSONResponse(
                {"error": "chat has no running reply", "code": "chat_not_running"},
                status_code=409,
            )
        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)

        event = await run.inbox.put_guidance(
            message, client_request_id=client_request_id
        )
        if event.get("duplicate"):
            return {
                "queued": True, "duplicate": True, "eventId": event["event_id"],
                "runId": run.run_id,
            }

        now = _utc_now_iso()
        user_entry = {
            "id": _short_id("msg"),
            "role": "user",
            "content": message,
            "createdAt": now,
            "guidance": True,
            "guidanceEventId": event["event_id"],
            "runId": run.run_id,
        }
        if client_request_id:
            user_entry["clientRequestId"] = client_request_id
        chat.setdefault("messages", []).append(user_entry)
        chat["updatedAt"] = now
        await asyncio.to_thread(_write_chats_store, payload)
        await run.publish({
            "type": "guidance_received",
            "eventId": event["event_id"],
            "runId": run.run_id,
            "userMessage": _public_message(user_entry),
            "message": "Guidance queued for the running agent.",
        })
        return {
            "queued": True,
            "eventId": event["event_id"],
            "runId": run.run_id,
            "userMessage": _public_message(user_entry),
        }

    @router.patch("/api/workbench/chats/{chat_id}")
    async def api_workbench_update_chat(
        chat_id: str, body_model: api_models.ChatUpdateBody
    ):
        body = api_models.body_dict(body_model)
        if chat_id.startswith("legacy:"):
            return JSONResponse({"error": "legacy chat metadata is read-only"}, status_code=403)
        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        if "title" in body:
            chat["title"] = str(body.get("title") or "").strip()[:60] or chat.get("title")
        chat["updatedAt"] = _utc_now_iso()
        await asyncio.to_thread(_write_chats_store, payload)
        return {"ok": True, "chat": _public_chat_full(chat)}

    @router.delete("/api/workbench/chats/{chat_id}")
    async def api_workbench_delete_chat(chat_id: str):
        from cyrene.agent import clear_session_id, interrupt_active_run
        if chat_id.startswith("legacy:"):
            _prefix, project_id, session_id = (chat_id.split(":", 2) + ["", ""])[:3]
            if not project_id or not session_id or _project_data_key(project_id) != "default":
                return JSONResponse({"error": "chat not found"}, status_code=404)
            payload, status_code = await _routes()._delete_chat_session(session_id)
            if status_code != 200:
                return JSONResponse(payload, status_code=status_code)
            return {"ok": True}
        payload = await asyncio.to_thread(_read_chats_store)
        chats = payload.get("chats", [])
        next_chats = [chat for chat in chats if str(chat.get("id") or "") != chat_id]
        if len(next_chats) == len(chats):
            return JSONResponse({"error": "chat not found"}, status_code=404)
        for chat in next_chats:
            if str(chat.get("forkedFromChatId") or "") == chat_id:
                _clear_fork_metadata(chat)
        payload["chats"] = next_chats
        await asyncio.to_thread(_write_chats_store, payload)
        try:
            _CHAT_RUN_MANAGER.interrupt(chat_id)
            interrupt_active_run(session_id=chat_id)
            await clear_session_id(session_id=chat_id)
        except Exception:
            logger.exception("Failed to clear agent state for chat %s", chat_id)
        return {"ok": True}

    @router.post("/api/workbench/chats/{chat_id}/messages")
    async def api_workbench_chat_send(
        chat_id: str, body_model: api_models.ChatMessageBody
    ):
        from cyrene.agent import run_agent
        from cyrene.agent.state import PERMISSION_MODES, _attachment_paths_by_name

        body = api_models.body_dict(body_model)
        message = str(body.get("message") or "").strip()
        attachments = body.get("attachments") if isinstance(body.get("attachments"), list) else []
        command = str(body.get("command") or "").strip()
        wants_stream = bool(body.get("stream"))
        retry = bool(body.get("retry"))
        fork_replay = bool(body.get("forkReplay"))
        mode = str(body.get("mode") or "auto").strip().lower()
        if mode not in PERMISSION_MODES:
            mode = "auto"
        lang = str(body.get("lang") or "").strip().lower()
        # Persist the UI language so server-side flows (the proactive scheduler)
        # can reply in the same language even with no HTTP request to read.
        if lang in {"en", "zh"}:
            try:
                from cyrene.settings_store import get as _get_setting, set_ as _set_setting
                if str(_get_setting("app_language", "") or "") != lang:
                    _set_setting("app_language", lang)
            except Exception:
                pass

        R = _routes()
        normalized = R._workbench_normalize_attachments(attachments)
        public_attachments = [R.build_public_attachment_payload(item) for item in normalized]
        if not retry and not message and not normalized:
            return JSONResponse({"error": "message is required"}, status_code=400)

        # ── Budget gate ──
        from webui.routes import _check_budget_gate as _chat_budget_gate
        _bgt = await _chat_budget_gate(chat_id)
        if _bgt:
            return JSONResponse(_bgt, status_code=403)

        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        project_id = str(chat.get("projectId") or "")
        project_store = await asyncio.to_thread(R._read_workbench_store)
        project = R._workbench_find_project(project_store, project_id)
        if not project:
            return JSONResponse({"error": "project not found"}, status_code=404)
        workspace_dir = R._workbench_resolve_workspace_dir(project)

        existing_run = _CHAT_RUN_MANAGER.get(chat_id)
        if existing_run is not None:
            return JSONResponse(
                {"error": "chat already has a running reply", "code": "chat_run_in_progress"},
                status_code=409,
            )

        now = _utc_now_iso()
        messages = chat.setdefault("messages", [])
        user_entry: dict[str, Any]
        truncate_after_id = ""
        retry_replaced_message_ids: set[str] = set()
        retry_state_backup: tuple[Any, bytes | None] | None = None
        if retry:
            # Regenerate the last exchange transactionally. Keep the public
            # transcript intact until the replacement reply has been persisted;
            # otherwise a failed retry permanently deletes the previous answer.
            last_user_index = -1
            for index in range(len(messages) - 1, -1, -1):
                if messages[index].get("role") == "user":
                    last_user_index = index
                    break
            if last_user_index < 0:
                return JSONResponse({"error": "nothing to retry"}, status_code=400)
            user_entry = messages[last_user_index]
            truncate_after_id = str(user_entry.get("id") or "")
            retry_replaced_message_ids = {
                str(item.get("id") or "")
                for item in messages[last_user_index + 1:]
                if isinstance(item, dict) and str(item.get("id") or "")
            }
            message = str(user_entry.get("content") or "").strip()
            command = ""
            normalized = R._workbench_normalize_attachments(user_entry.get("agentAttachments") or [])
            public_attachments = user_entry.get("attachments") if isinstance(user_entry.get("attachments"), list) else []
            # A fork already truncated the raw state at the edit boundary; only
            # a plain retry needs to drop the last exchange from the state here.
            if not fork_replay:
                from cyrene.agent.state import _session_state_file

                state_path = _session_state_file(chat_id)
                previous_state = await asyncio.to_thread(
                    lambda: state_path.read_bytes() if state_path.exists() else None
                )
                retry_state_backup = (state_path, previous_state)
                await asyncio.to_thread(_truncate_state_for_retry, chat_id)
        else:
            user_entry = {
                "id": _short_id("msg"),
                "role": "user",
                "content": message,
                "createdAt": now,
            }
            if public_attachments:
                user_entry["attachments"] = public_attachments
                # Keep the normalized (path-bearing) attachments privately so a
                # later retry can rebuild the agent prompt + read-guard map.
                user_entry["agentAttachments"] = normalized
            is_first_message = not any(m.get("role") == "user" for m in messages)
            messages.append(user_entry)
            if is_first_message and chat.get("title") in ("", "新对话", None) and message:
                chat["title"] = message.replace("\n", " ")[:24]
        chat["status"] = "running"
        chat["model"] = R._get_model()
        _mark_user_activity(chat, now)
        await asyncio.to_thread(_write_chats_store, payload)

        agent_message = message
        if normalized:
            agent_message = (message or "[Attachment upload]") + R._attachment_prompt_block(normalized)
            # Auto-allow uploaded files for tool read guards (same as /api/chat).
            att_map: dict[str, str] = {}
            for item in normalized:
                full_path = str(item.get("path") or "").strip()
                if not full_path:
                    continue
                from pathlib import Path as _Path
                uuid_name = _Path(full_path).name
                att_map[uuid_name] = full_path
                parts = uuid_name.split("_", 1)
                if len(parts) == 2:
                    att_map[parts[1]] = full_path
            _attachment_paths_by_name.set(att_map)

        # Capture IDs of messages already in state before this exchange, so
        # _extract_exchange_segments can identify new messages by ID rather
        # than by positional index (which would break after session compaction).
        state_ids_before: set[str] = set()
        for m in await asyncio.to_thread(_session_state_messages, chat_id):
            mid = str(m.get("message_id") or m.get("id") or "").strip()
            if mid:
                state_ids_before.add(mid)

        async def _run() -> str:
            return await run_agent(
                user_message=agent_message,
                bot=bot,
                chat_id=R._CHAT_ID,
                db_path=db_path,
                session_id=chat_id,
                permission_mode=mode,
                command=command,
                public_user_message=message or None,
                public_attachments=public_attachments or None,
                workspace_dir=workspace_dir,
            )

        def _finalize(reply_text: str) -> dict[str, Any]:
            """Persist mid-run messages plus the final assistant reply in order."""
            intermediate_entries, trace, usage, files = _extract_exchange_segments(
                _session_state_messages(chat_id), state_ids_before
            )
            fresh = _read_chats_store()
            fresh_chat = _find_chat(fresh, chat_id)
            if not fresh_chat:
                return {}
            _commit_retry_cut(fresh_chat)
            model_name = fresh_chat.get("model") or ""
            for entry in intermediate_entries:
                entry["model"] = model_name
            assistant_entry: dict[str, Any] = {
                "id": _short_id("msg"),
                "role": "assistant",
                "content": str(reply_text or ""),
                "createdAt": _utc_now_iso(),
                "model": model_name,
            }
            if trace:
                assistant_entry["trace"] = trace
            if any(usage.values()):
                assistant_entry["usage"] = usage
            if files:
                assistant_entry["attachments"] = files
            saved_messages = [*intermediate_entries, assistant_entry]
            _merge_chat_messages_chronologically(fresh_chat, saved_messages)
            fresh_chat["status"] = "idle"
            fresh_chat.pop("pendingQuestion", None)
            fresh_chat["updatedAt"] = assistant_entry["createdAt"]
            _write_chats_store(fresh)
            # Persist this exchange to the workspace's per-session conversation
            # file so the conversation survives outside the JSON store and the
            # agent can read its own history by id. Best-effort; never block reply.
            try:
                archive_session_exchange(
                    chat_id,
                    message,
                    str(reply_text or ""),
                    workspace_dir=workspace_dir,
                    session_title=str(fresh_chat.get("title") or ""),
                )
            except Exception:
                logger.exception("Failed to archive workbench conversation %s", chat_id)
            if not command and not retry:
                append_notification(
                    title="Agent 回复完成",
                    body=f"Agent 在「{fresh_chat.get('title') or '新对话'}」中回复了你。",
                    tab="mention",
                    project_ref=project_id,
                    source="workbench_chat_reply",
                    source_label="对话",
                    link_label=str(fresh_chat.get("title") or ""),
                    meta={"chatId": chat_id},
                )
            return {
                "assistantMessage": assistant_entry,
                "assistantMessages": saved_messages,
            }

        async def _finalize_async(reply_text: str) -> dict[str, Any]:
            finalized = await asyncio.to_thread(_finalize, reply_text)
            if finalized and not command and not retry:
                # schedule_capture needs the running event loop, unlike the
                # storage/archive work intentionally performed above in a thread.
                R.schedule_capture(project_id, message, str(reply_text or ""))
            return finalized

        def _restore_retry_state() -> None:
            if retry_state_backup is None:
                return
            state_path, previous = retry_state_backup
            try:
                if previous is None:
                    state_path.unlink(missing_ok=True)
                else:
                    state_path.parent.mkdir(parents=True, exist_ok=True)
                    state_path.write_bytes(previous)
            except Exception:
                logger.exception("Failed to restore retry state for %s", chat_id)

        def _commit_retry_cut(target_chat: dict[str, Any]) -> None:
            if not retry or not truncate_after_id:
                return
            # Delete only the stale tail captured when retry began. Guidance or
            # proactive entries added during the new run must survive.
            _remove_retry_replaced_messages(
                target_chat, truncate_after_id, retry_replaced_message_ids
            )

        def _settle_status() -> None:
            fresh = _read_chats_store()
            fresh_chat = _find_chat(fresh, chat_id)
            if fresh_chat and fresh_chat.get("status") == "running":
                fresh_chat["status"] = "idle"
                _write_chats_store(fresh)

        def _stash_chat_pending(pending: dict[str, Any] | None) -> list[dict[str, Any]]:
            """Persist a paused run's pending question on the chat record so the
            transcript shows an answer prompt (not the raw awaiting-user sentinel)."""
            fresh = _read_chats_store()
            fresh_chat = _find_chat(fresh, chat_id)
            if not fresh_chat:
                return []
            saved_messages: list[dict[str, Any]] = []
            fresh_chat["status"] = "idle"
            if pending:
                fresh_chat["pendingQuestion"] = pending
                intermediate_entries, trace, usage, files = _extract_exchange_segments(
                    _session_state_messages(chat_id),
                    state_ids_before,
                    include_open_tool_preamble=True,
                )
                model_name = str(fresh_chat.get("model") or "")
                for entry in intermediate_entries:
                    entry["model"] = model_name
                question_entry = _pending_question_message(
                    pending,
                    trace=trace,
                    usage=usage,
                    files=files,
                    model=model_name,
                )
                saved_messages = [*intermediate_entries, question_entry]
                _merge_chat_messages_chronologically(
                    fresh_chat, saved_messages
                )
            else:
                fresh_chat.pop("pendingQuestion", None)
            fresh_chat["updatedAt"] = _utc_now_iso()
            _write_chats_store(fresh)
            return [_public_message(item) for item in saved_messages]

        async def run_non_streaming(run: ChatRun) -> None:
            try:
                reply = await _run()
            except Exception as exc:
                logger.exception("Workbench chat run failed for %s", chat_id)
                await asyncio.to_thread(_restore_retry_state)
                await asyncio.to_thread(_settle_status)
                run.outcome = {"kind": "error", "exc": exc}
                return
            run.status = "finishing"
            if reply == R._AWAITING_USER_SENTINEL:
                if retry:
                    def commit_retry() -> None:
                        fresh = _read_chats_store()
                        fresh_chat = _find_chat(fresh, chat_id)
                        if fresh_chat:
                            _commit_retry_cut(fresh_chat)
                            _write_chats_store(fresh)
                    await asyncio.to_thread(commit_retry)
                pending = await asyncio.to_thread(R._workbench_pending_question_for, chat_id)
                awaiting_messages = await asyncio.to_thread(_stash_chat_pending, pending)
                run.outcome = {"kind": "awaiting", "pending": pending}
                run.outcome["assistantMessages"] = awaiting_messages
                return
            finalized = await _finalize_async(reply)
            run.outcome = {
                "kind": "reply",
                "payload": finalized,
            }

        if not wants_stream:
            run, is_new = _CHAT_RUN_MANAGER.start_or_get(
                chat_id,
                {"type": "ack", "chatId": chat_id},
                run_non_streaming,
                stream=False,
            )
            if not is_new:
                return JSONResponse(
                    {"error": "chat already has a running reply", "code": "chat_run_in_progress"},
                    status_code=409,
                )
            await run.done.wait()
            outcome = run.outcome or {}
            kind = str(outcome.get("kind") or "")
            if kind == "error":
                exc = outcome.get("exc")
                if not isinstance(exc, Exception):
                    exc = RuntimeError("agent run failed")
                message = _workbench_chat_run_error_message(exc, lang)
                error = message if isinstance(exc, httpx.TransportError) else "agent run failed"
                return JSONResponse({"error": error, "detail": str(exc)}, status_code=502)
            if kind == "awaiting":
                pending = outcome.get("pending")
                return {
                    "ok": True,
                    "awaitingUser": True,
                    "pendingQuestion": pending,
                    "assistantMessages": outcome.get("assistantMessages") or [],
                    "userMessage": _public_message(user_entry),
                    "retry": retry,
                    "retryReplacedMessageIds": sorted(retry_replaced_message_ids),
                }
            finalized = outcome.get("payload")
            if not isinstance(finalized, dict):
                finalized = {}
            return {
                "ok": True,
                "userMessage": _public_message(user_entry),
                "assistantMessage": finalized.get("assistantMessage") or {},
                "assistantMessages": finalized.get("assistantMessages") or [],
                "retry": retry,
            }

        ack: dict[str, Any] = {"type": "ack", "chatId": chat_id}
        if retry:
            ack["retry"] = True
            ack["truncateAfterMessageId"] = truncate_after_id
        else:
            ack["userMessage"] = _public_message(user_entry)

        async def run_streaming(run: ChatRun) -> None:
            live_segments_stop = asyncio.Event()
            live_segments_task = asyncio.create_task(
                _publish_live_exchange_segments_loop(run, chat_id, state_ids_before, live_segments_stop)
            )
            try:
                try:
                    reply = await _run()
                except asyncio.CancelledError:
                    await asyncio.to_thread(_restore_retry_state)
                    raise
                except Exception as exc:
                    logger.exception("Workbench chat streaming run failed for %s", chat_id)
                    await asyncio.to_thread(_restore_retry_state)
                    await asyncio.to_thread(_settle_status)
                    run.outcome = {"kind": "error", "exc": exc}
                    await run.publish({
                        "type": "error",
                        "error": "model_call_failed",
                        "message": _workbench_chat_run_error_message(exc, lang),
                    })
                    return
                # The agent has returned and can no longer absorb new guidance.
                # Keep the run available for stream finalization/replay, but make
                # the guidance endpoint reject this narrow terminal window.
                run.status = "finishing"
                live_segments_stop.set()
                await live_segments_task
                if reply == R._AWAITING_USER_SENTINEL:
                    # Run paused for a permission / clarification answer — surface
                    # the question instead of streaming the sentinel as a reply.
                    if retry:
                        def commit_stream_retry() -> None:
                            fresh = _read_chats_store()
                            fresh_chat = _find_chat(fresh, chat_id)
                            if fresh_chat:
                                _commit_retry_cut(fresh_chat)
                                _write_chats_store(fresh)
                        await asyncio.to_thread(commit_stream_retry)
                    pending = await asyncio.to_thread(R._workbench_pending_question_for, chat_id)
                    awaiting_messages = await asyncio.to_thread(_stash_chat_pending, pending)
                    run.outcome = {"kind": "awaiting", "pending": pending}
                    await run.publish({
                        "type": "awaiting_user",
                        "pending_question": pending,
                        "assistantMessages": awaiting_messages,
                        "retry": retry,
                        "retryReplacedMessageIds": sorted(retry_replaced_message_ids),
                        "truncateAfterMessageId": truncate_after_id,
                    })
                    return
                if not run.saw_reply_events:
                    await run.publish({"type": "reply_start"})
                    for chunk in R._reply_stream_chunks(reply):
                        await run.publish({"type": "reply_delta", "delta": chunk})
                    await run.publish({"type": "reply_done", "response": reply})
                finalized = await _finalize_async(reply)
                saved_event = {
                    "type": "saved",
                    **finalized,
                    "retry": retry,
                    "retryReplacedMessageIds": sorted(retry_replaced_message_ids),
                    "truncateAfterMessageId": truncate_after_id,
                }
                run.outcome = {"kind": "reply", "payload": saved_event}
                await run.publish(saved_event)
            finally:
                if not live_segments_stop.is_set():
                    live_segments_stop.set()
                    try:
                        await live_segments_task
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.debug("Workbench chat live segment publisher failed for %s", chat_id, exc_info=True)
                await asyncio.to_thread(_settle_status)

        run, _is_new = _CHAT_RUN_MANAGER.start_or_get(
            chat_id,
            ack,
            run_streaming,
            stream=True,
        )
        return StreamingResponse(
            _CHAT_RUN_MANAGER.stream(run),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache"},
        )

    @router.post("/api/workbench/chats/{chat_id}/fork")
    async def api_workbench_chat_fork(
        chat_id: str, body_model: api_models.ChatForkBody
    ):
        """Fork a conversation at an edited user message.

        Creates a new chat with the prefix transcript (everything before the
        edited user message) plus a NEW user entry bearing the edited content
        and the original attachments. The source chat is preserved untouched.
        The agent's raw state is copied from the source session and truncated at
        the same user-message boundary so the forked chat can replay the edit
        through a normal send (``{ retry: true, forkReplay: true }``) without
        re-truncating. The agent is NOT run here.
        """
        from cyrene.agent.state import _session_state_file

        body = api_models.body_dict(body_model)
        message_id = str(body.get("messageId") or "").strip()
        new_content = str(body.get("content") or "").strip()
        if not message_id:
            return JSONResponse({"error": "messageId is required"}, status_code=400)
        if not new_content:
            return JSONResponse({"error": "content is required"}, status_code=400)
        if chat_id.startswith("legacy:"):
            return JSONResponse({"error": "legacy chats cannot be forked"}, status_code=403)

        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        messages = chat.get("messages") if isinstance(chat.get("messages"), list) else []
        if not messages:
            return JSONResponse({"error": "chat has no messages"}, status_code=400)

        edit_index = -1
        for index, entry in enumerate(messages):
            if str(entry.get("id") or "") == message_id:
                edit_index = index
                break
        if edit_index < 0:
            return JSONResponse({"error": "message not found"}, status_code=404)
        if str(messages[edit_index].get("role") or "") != "user":
            return JSONResponse({"error": "can only edit user messages"}, status_code=400)

        # User-message ordinal (1-indexed) of the edited turn — this is the
        # boundary at which the raw state will be truncated.
        user_ordinal = sum(
            1 for entry in messages[:edit_index + 1]
            if str(entry.get("role") or "") == "user"
        )

        R = _routes()
        project_id = str(chat.get("projectId") or "")
        now = _utc_now_iso()
        new_chat = _new_chat(project_id, str(chat.get("title") or ""), str(chat.get("model") or R._get_model()))
        new_chat["forkedFromChatId"] = chat_id
        new_chat["forkedAtMessageId"] = message_id
        # Immutable divergence snippet — the edited prompt that started this
        # branch. Captured here so the branch tree never has to diff transcripts.
        new_chat["forkMessage"] = new_content.replace("\n", " ").strip()[:80]

        # Prefix transcript: everything before the edited user message.
        # Strip usage from copied messages so the branch doesn't inherit the
        # parent's accumulated token counts in the overview sidebar.
        prefix = []
        for entry in messages[:edit_index]:
            copied = dict(entry)
            copied.pop("usage", None)
            prefix.append(copied)
        # New user entry bearing the edited text + original attachments.
        orig = messages[edit_index]
        edited_entry: dict[str, Any] = {
            "id": _short_id("msg"),
            "role": "user",
            "content": new_content,
            "createdAt": now,
        }
        if isinstance(orig.get("attachments"), list) and orig["attachments"]:
            edited_entry["attachments"] = orig["attachments"]
            # Preserve the private path-bearing attachments so the replay send
            # can rebuild the agent prompt + read-guard map (same as :1132-1136).
            if orig.get("agentAttachments"):
                edited_entry["agentAttachments"] = orig["agentAttachments"]
        new_chat["messages"] = prefix + [edited_entry]
        new_chat["updatedAt"] = now

        payload.setdefault("chats", []).insert(0, new_chat)
        await asyncio.to_thread(_write_chats_store, payload)

        # Seed the forked session's raw state from the source, truncated at the
        # same user-message boundary so the replay send appends the edited turn.
        new_chat_id = str(new_chat.get("id") or "")
        src_state = _session_state_file(chat_id)
        new_state = _session_state_file(new_chat_id)
        def seed_fork_state() -> None:
            new_state.parent.mkdir(parents=True, exist_ok=True)
            try:
                if src_state.exists():
                    shutil.copyfile(src_state, new_state)
                    truncated = _truncate_state_file_at_user_ordinal(new_state, user_ordinal)
                    if not truncated:
                        logger.warning(
                            "Fork state truncation missed user ordinal %d for %s (source %s) — "
                            "state may have been compacted; replay will use the existing prefix.",
                            user_ordinal, new_chat_id, chat_id,
                        )
                else:
                    atomic_write_json(new_state, {"messages": []})
            except Exception:
                logger.exception("Failed to seed fork state for %s", new_chat_id)

        await asyncio.to_thread(seed_fork_state)

        return {"ok": True, "chat": _public_chat_full(new_chat)}

    @router.post("/api/workbench/chats/{chat_id}/to-task")
    async def api_workbench_chat_to_task(
        chat_id: str, body_model: api_models.ChatToTaskBody
    ):
        """Promote a conversation into a task session of its project (开始执行)."""
        body = api_models.body_dict(body_model)
        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        R = _routes()
        store = await asyncio.to_thread(R._read_workbench_store)
        project = R._workbench_find_project(store, str(chat.get("projectId") or ""))
        if not project:
            return JSONResponse({"error": "project not found"}, status_code=404)
        # Fallback signal when synthesis is unavailable: the last user message.
        last_user = ""
        for message in reversed(chat.get("messages") or []):
            if message.get("role") == "user" and str(message.get("content") or "").strip():
                last_user = str(message["content"]).strip()
                break

        # Synthesize a task brief from the WHOLE conversation unless the caller
        # passed explicit overrides for both title and goal.
        override_title = str(body.get("title") or "").strip()
        override_goal = str(body.get("goal") or "").strip()
        brief: dict[str, Any] = {}
        if not (override_title and override_goal):
            synthesized = await _summarize_chat_to_brief(chat, project)
            if isinstance(synthesized, dict):
                brief = synthesized

        from_synthesis = bool(brief)
        title = (override_title or str(brief.get("title") or "").strip()
                 or str(chat.get("title") or "").strip() or "新任务")[:80] or "新任务"
        goal = (override_goal or str(brief.get("goal") or "").strip() or last_user or title).strip()
        constraints = _coerce_brief_constraints(brief.get("constraints"))
        acceptance = _coerce_brief_acceptance(brief.get("acceptanceCriteria"))

        session = R._workbench_new_session(project.get("id"), title, goal)
        if constraints:
            session["constraints"] = constraints
        if acceptance:
            session["acceptanceCriteria"] = acceptance
        session["sourceChatId"] = chat_id
        session["events"] = [{
            "id": _short_id("event"),
            "type": "CreatedFromChat",
            "createdAt": _utc_now_iso(),
            "body": (
                f"由对话「{chat.get('title') or '新对话'}」综合整理而来（已通读完整对话）。"
                if from_synthesis else
                f"由对话「{chat.get('title') or '新对话'}」创建。"
            ),
            "chatId": chat_id,
        }]
        project.setdefault("sessions", []).insert(0, session)
        project["updatedAt"] = session["createdAt"]
        store["activeProjectId"] = project.get("id")
        store["activeSessionId"] = session["id"]
        await asyncio.to_thread(R._write_workbench_store, store)

        # Keep the original conversation and link it to the task, so it's clearly
        # preserved (never consumed) and reachable from both sides.
        chat["convertedSessionId"] = session["id"]
        chat["convertedTaskTitle"] = title
        chat["convertedAt"] = session["createdAt"]
        await asyncio.to_thread(_write_chats_store, payload)
        await asyncio.to_thread(
            append_notification,
            title="对话已转为任务",
            body=f"对话「{chat.get('title') or '新对话'}」已创建任务「{title}」。",
            tab="comment",
            project_ref=project.get("id"),
            source="chat_to_task",
            source_label="任务",
            link_label=title,
            meta={"chatId": chat_id, "sessionId": session["id"]},
        )
        return {"ok": True, "session": session, **store}

    @router.post("/api/workbench/chats/{chat_id}/answer")
    async def api_workbench_chat_answer(
        chat_id: str, body_model: api_models.AnswerBody
    ):
        """Answer a paused chat run's permission / clarification question and
        resume the SAME round. Returns the continued reply (appended as an
        assistant message) or a follow-up question. Session-scoped to this chat."""
        body = api_models.body_dict(body_model)
        question_id = str(body.get("question_id") or "").strip()
        answer_text = str(body.get("answer") or body.get("selected_option") or "").strip()
        from cyrene.agent.state import PERMISSION_MODES
        mode = str(body.get("mode") or "default").strip().lower()
        if mode not in PERMISSION_MODES:
            mode = "default"
        if not question_id or not answer_text:
            return JSONResponse({"error": "question_id and answer are required"}, status_code=400)
        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        pending = chat.get("pendingQuestion") if isinstance(chat.get("pendingQuestion"), dict) else None
        if not pending or str(pending.get("id") or "") != question_id:
            return JSONResponse({"error": "no matching pending question"}, status_code=409)

        R = _routes()
        project_id = str(chat.get("projectId") or "")
        project_store = await asyncio.to_thread(R._read_workbench_store)
        project = R._workbench_find_project(project_store, project_id)
        if not project:
            return JSONResponse({"error": "project not found"}, status_code=404)
        workspace_dir = R._workbench_resolve_workspace_dir(project)
        now = _utc_now_iso()
        answer_entry: dict[str, Any] = {
            "id": _short_id("msg"),
            "role": "user",
            "content": answer_text,
            "createdAt": now,
            "answerToQuestionId": question_id,
        }
        _merge_chat_messages_chronologically(chat, [answer_entry])
        _mark_user_activity(chat, now)
        await asyncio.to_thread(_write_chats_store, payload)
        state_ids_before_resume: set[str] = set()
        for m in await asyncio.to_thread(_session_state_messages, chat_id):
            mid = str(m.get("message_id") or m.get("id") or "").strip()
            if mid:
                state_ids_before_resume.add(mid)
        try:
            if mode == "default":
                reply = await R._workbench_answer_pending(
                    chat_id, question_id, answer_text, workspace_dir,
                )
            else:
                reply = await R._workbench_answer_pending(
                    chat_id, question_id, answer_text, workspace_dir,
                    permission_mode=mode,
                )
        except Exception as exc:
            logger.exception("Workbench chat answer-resume failed for %s", chat_id)
            return JSONResponse({"error": "answer resume failed", "detail": str(exc)}, status_code=502)

        if reply == R._AWAITING_USER_SENTINEL:
            new_pending = await asyncio.to_thread(R._workbench_pending_question_for, chat_id)

            def extract_pending() -> tuple[list[dict[str, Any]], list[Any], dict[str, Any], list[Any]]:
                return _extract_exchange_segments(
                    _session_state_messages(chat_id),
                    state_ids_before_resume,
                    include_open_tool_preamble=True,
                )

            intermediate_entries, trace, usage, files = await asyncio.to_thread(extract_pending)
            additions = [
                *intermediate_entries,
                *([
                    _pending_question_message(
                        new_pending,
                        trace=trace,
                        usage=usage,
                        files=files,
                        model=str(chat.get("model") or ""),
                    )
                ] if new_pending else []),
            ]
            await asyncio.to_thread(
                _stash_chat_pending_for, chat_id, new_pending, additions=additions
            )
            return {
                "ok": True,
                "awaitingUser": True,
                "pendingQuestion": new_pending,
                "userMessage": _public_message(answer_entry),
            }

        def extract_answer() -> tuple[list[dict[str, Any]], list[Any], dict[str, Any], list[Any]]:
            return _extract_exchange_segments(
                _session_state_messages(chat_id), state_ids_before_resume
            )

        intermediate_entries, trace, usage, files = await asyncio.to_thread(extract_answer)
        fresh = await asyncio.to_thread(_read_chats_store)
        fresh_chat = _find_chat(fresh, chat_id)
        if not fresh_chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        model_name = fresh_chat.get("model") or ""
        for entry in intermediate_entries:
            entry["model"] = model_name
        assistant_entry: dict[str, Any] = {
            "id": _short_id("msg"),
            "role": "assistant",
            "content": str(reply or ""),
            "createdAt": _utc_now_iso(),
            "model": model_name,
        }
        if trace:
            assistant_entry["trace"] = trace
        if any(usage.values()):
            assistant_entry["usage"] = usage
        if files:
            assistant_entry["attachments"] = files
        saved_messages = [*intermediate_entries, assistant_entry]
        _merge_chat_messages_chronologically(fresh_chat, saved_messages)
        fresh_chat["status"] = "idle"
        fresh_chat.pop("pendingQuestion", None)
        fresh_chat["updatedAt"] = assistant_entry["createdAt"]
        await asyncio.to_thread(_write_chats_store, fresh)
        await asyncio.to_thread(complete_chat_plan, chat_id)
        try:
            await asyncio.to_thread(
                archive_session_exchange,
                chat_id,
                answer_text,
                str(reply or ""),
                workspace_dir=workspace_dir,
                session_title=str(fresh_chat.get("title") or ""),
            )
        except Exception:
            logger.exception("Failed to archive workbench conversation %s", chat_id)
        if project_id:
            R.schedule_capture(project_id, answer_text, str(reply or ""))
        return {
            "ok": True,
            "awaitingUser": False,
            "userMessage": _public_message(answer_entry),
            "assistantMessage": _public_message(assistant_entry),
            "assistantMessages": [_public_message(item) for item in saved_messages],
        }


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


async def remove_project_chats(project_id: str) -> int:
    """Bulk-remove all chats of a project (called when the project is deleted)."""
    from cyrene.agent import clear_session_id
    project_id = str(project_id or "").strip()
    if not project_id:
        return 0
    payload = await asyncio.to_thread(_read_chats_store)
    doomed = [chat for chat in payload.get("chats", []) if str(chat.get("projectId") or "") == project_id]
    if doomed:
        payload["chats"] = [chat for chat in payload.get("chats", []) if str(chat.get("projectId") or "") != project_id]
        await asyncio.to_thread(_write_chats_store, payload)
    for chat in doomed:
        try:
            await clear_session_id(session_id=str(chat.get("id") or ""))
        except Exception:
            logger.exception("Failed to clear agent state for chat %s", chat.get("id"))
    return len(doomed)
