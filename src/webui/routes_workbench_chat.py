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
import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from cyrene.call_llm import NETWORK_RETRY_LIMIT
from cyrene.config import DATA_DIR
from cyrene.conversations import archive_session_exchange
from cyrene.io_utils import atomic_write_json, read_json_safe
from cyrene.workbench_store import read_document, write_document
from webui import api_models
from webui.workbench_notifications import append_notification

logger = logging.getLogger(__name__)

_CHATS_STORE = DATA_DIR / "workbench_chats.json"
_STORE_DB_PATH = ""
_CONFIGURED_CHATS_STORE = None

# Internal control tools that say nothing useful in a progress trace.
_TRACE_SKIP_TOOLS = {"use_tools", "quit", "send_message"}
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


def _ndjson_line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


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


def _chat_preview(chat: dict[str, Any]) -> str:
    for message in reversed(chat.get("messages") or []):
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
    }
    if chat.get("forkedFromChatId"):
        payload["forkedFromChatId"] = chat.get("forkedFromChatId")
    if chat.get("forkedAtMessageId"):
        payload["forkedAtMessageId"] = chat.get("forkedAtMessageId")
    return payload


def _public_message(message: dict[str, Any]) -> dict[str, Any]:
    """Transcript entry without server-private fields (local upload paths)."""
    if isinstance(message, dict) and "agentAttachments" in message:
        return {k: v for k, v in message.items() if k != "agentAttachments"}
    return message


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


def _append_exchange_meta(
    message: dict[str, Any],
    trace: list[dict[str, Any]],
    usage: dict[str, int],
    files: list[dict[str, Any]],
    seen_file_urls: set[str],
) -> None:
    raw_usage = message.get("usage")
    if isinstance(raw_usage, dict):
        for key in _USAGE_KEYS:
            try:
                usage[key] += int(raw_usage.get(key) or 0)
            except (TypeError, ValueError):
                continue
    for tool_call in message.get("tool_calls") or []:
        fn = tool_call.get("function") if isinstance(tool_call, dict) else None
        name = str((fn or {}).get("name") or "").strip()
        if not name or name in _TRACE_SKIP_TOOLS:
            continue
        trace.append({
            "tool": name,
            "preview": _tool_args_preview(str((fn or {}).get("arguments") or "")),
        })
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


def _extract_exchange_segments(
    state_messages: list[dict[str, Any]], start_index: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    """Split one agent exchange at persisted mid-run assistant messages."""
    segments: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    usage = _exchange_usage()
    files: list[dict[str, Any]] = []
    seen_file_urls: set[str] = set()

    for message in state_messages[start_index:]:
        if str(message.get("role") or "") != "assistant":
            continue
        if bool(message.get("intermediate_reply")):
            entry: dict[str, Any] = {
                "id": str(message.get("message_id") or _short_id("msg")),
                "role": "assistant",
                "content": str(message.get("content") or ""),
                "createdAt": str(message.get("created_at") or _utc_now_iso()),
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
            segments.append(entry)
            trace = []
            usage = _exchange_usage()
            files = []
            seen_file_urls = set()
            continue
        _append_exchange_meta(message, trace, usage, files, seen_file_urls)

    if not usage["total_tokens"]:
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    return segments, trace[:40], usage, files[:20]


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
    # Heavyweight helpers (store access, attachments, agent entrypoints) live in
    # webui.routes; import lazily at call time to avoid a circular import.

    def _routes():
        from webui import routes as legacy_routes
        return legacy_routes

    def _project_data_key(project_id: str) -> str:
        R = _routes()
        store = R._read_workbench_store()
        project = R._workbench_find_project(store, project_id)
        return R._workbench_project_data_key(project) if project else project_id

    @router.get("/api/workbench/chats")
    async def api_workbench_list_chats(project: str = ""):
        payload = _read_chats_store()
        data_key = _project_data_key(project) if project else ""
        chats = [
            _public_chat_light(chat)
            for chat in payload.get("chats", [])
            if not project or str(chat.get("projectId") or "") == project
        ]
        if project and data_key == "default":
            legacy = _legacy_chats(project)
            legacy.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
            chats.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
            chats = chats + legacy
        else:
            chats.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        return {"chats": chats}

    @router.post("/api/workbench/chats")
    async def api_workbench_create_chat(body_model: api_models.ChatCreateBody):
        body = api_models.body_dict(body_model)
        project_id = str(body.get("project") or body.get("projectId") or "").strip()
        if not project_id:
            return JSONResponse({"error": "project is required"}, status_code=400)
        R = _routes()
        store = R._read_workbench_store()
        if not R._workbench_find_project(store, project_id):
            return JSONResponse({"error": "project not found"}, status_code=404)
        payload = _read_chats_store()
        chat = _new_chat(project_id, str(body.get("title") or ""), R._get_model())
        payload.setdefault("chats", []).insert(0, chat)
        _write_chats_store(payload)
        return {"ok": True, "chat": _public_chat_full(chat)}

    @router.get("/api/workbench/chats/{chat_id}")
    async def api_workbench_get_chat(chat_id: str):
        if chat_id.startswith("legacy:"):
            _prefix, project_id, _session_id = (chat_id.split(":", 2) + ["", ""])[:3]
            if not project_id or _project_data_key(project_id) != "default":
                return JSONResponse({"error": "chat not found"}, status_code=404)
            legacy = _legacy_chats(project_id, full_id=chat_id)
            if not legacy:
                return JSONResponse({"error": "chat not found"}, status_code=404)
            return {"chat": legacy[0]}
        payload = _read_chats_store()
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        return {"chat": _public_chat_full(chat)}

    @router.get("/api/workbench/chats/{chat_id}/subagents")
    async def api_workbench_chat_subagents(chat_id: str, round_id: str = ""):
        if chat_id.startswith("legacy:"):
            return {"rounds": [], "activeRoundId": "", "agents": [], "messages": []}
        payload = _read_chats_store()
        if not _find_chat(payload, chat_id):
            return JSONResponse({"error": "chat not found"}, status_code=404)
        return _workbench_subagent_payload(chat_id, round_id)

    @router.get("/api/workbench/chats/{chat_id}/context")
    async def api_workbench_chat_context(chat_id: str):
        """Live context-window gauge + composition for the overview panel."""
        from cyrene import config

        if chat_id.startswith("legacy:"):
            _prefix, _project_id, session_id = (chat_id.split(":", 2) + ["", ""])[:3]
            if not session_id:
                return JSONResponse({"error": "chat not found"}, status_code=404)
            model_name = str(getattr(config, "OPENAI_MODEL", "") or "")
            return _chat_context_payload(session_id, model_name)
        payload = _read_chats_store()
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        model_name = str(chat.get("model") or getattr(config, "OPENAI_MODEL", "") or "")
        return _chat_context_payload(chat_id, model_name)

    @router.patch("/api/workbench/chats/{chat_id}")
    async def api_workbench_update_chat(
        chat_id: str, body_model: api_models.ChatUpdateBody
    ):
        body = api_models.body_dict(body_model)
        if chat_id.startswith("legacy:"):
            return JSONResponse({"error": "legacy chat metadata is read-only"}, status_code=403)
        payload = _read_chats_store()
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        if "title" in body:
            chat["title"] = str(body.get("title") or "").strip()[:60] or chat.get("title")
        chat["updatedAt"] = _utc_now_iso()
        _write_chats_store(payload)
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
        payload = _read_chats_store()
        chats = payload.get("chats", [])
        next_chats = [chat for chat in chats if str(chat.get("id") or "") != chat_id]
        if len(next_chats) == len(chats):
            return JSONResponse({"error": "chat not found"}, status_code=404)
        payload["chats"] = next_chats
        _write_chats_store(payload)
        try:
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
        from cyrene.agent.state import PERMISSION_MODES, _attachment_paths_by_name, _reply_stream_writer

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

        payload = _read_chats_store()
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        project_id = str(chat.get("projectId") or "")
        project_store = R._read_workbench_store()
        project = R._workbench_find_project(project_store, project_id)
        if not project:
            return JSONResponse({"error": "project not found"}, status_code=404)
        workspace_dir = R._workbench_resolve_workspace_dir(project)

        now = _utc_now_iso()
        messages = chat.setdefault("messages", [])
        user_entry: dict[str, Any]
        truncate_after_id = ""
        if retry:
            # Regenerate: replay the last user message; drop everything after it
            # from both the public transcript and the agent's raw state.
            last_user_index = -1
            for index in range(len(messages) - 1, -1, -1):
                if messages[index].get("role") == "user":
                    last_user_index = index
                    break
            if last_user_index < 0:
                return JSONResponse({"error": "nothing to retry"}, status_code=400)
            user_entry = messages[last_user_index]
            del messages[last_user_index + 1:]
            truncate_after_id = str(user_entry.get("id") or "")
            message = str(user_entry.get("content") or "").strip()
            command = ""
            normalized = R._workbench_normalize_attachments(user_entry.get("agentAttachments") or [])
            public_attachments = user_entry.get("attachments") if isinstance(user_entry.get("attachments"), list) else []
            # A fork already truncated the raw state at the edit boundary; only
            # a plain retry needs to drop the last exchange from the state here.
            if not fork_replay:
                _truncate_state_for_retry(chat_id)
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
        _write_chats_store(payload)

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

        state_len_before = len(_session_state_messages(chat_id))

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
                _session_state_messages(chat_id), state_len_before
            )
            fresh = _read_chats_store()
            fresh_chat = _find_chat(fresh, chat_id)
            if not fresh_chat:
                return {}
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
            existing_ids = {
                str(item.get("id") or "")
                for item in fresh_chat.setdefault("messages", [])
                if isinstance(item, dict)
            }
            fresh_chat["messages"].extend([
                item for item in saved_messages
                if str(item.get("id") or "") not in existing_ids
            ])
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
                R.schedule_capture(_project_data_key(project_id), message, str(reply_text or ""))
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

        def _settle_status() -> None:
            fresh = _read_chats_store()
            fresh_chat = _find_chat(fresh, chat_id)
            if fresh_chat and fresh_chat.get("status") == "running":
                fresh_chat["status"] = "idle"
                _write_chats_store(fresh)

        def _stash_chat_pending(pending: dict[str, Any] | None) -> None:
            """Persist a paused run's pending question on the chat record so the
            transcript shows an answer prompt (not the raw awaiting-user sentinel)."""
            fresh = _read_chats_store()
            fresh_chat = _find_chat(fresh, chat_id)
            if not fresh_chat:
                return
            fresh_chat["status"] = "idle"
            if pending:
                fresh_chat["pendingQuestion"] = pending
            else:
                fresh_chat.pop("pendingQuestion", None)
            fresh_chat["updatedAt"] = _utc_now_iso()
            _write_chats_store(fresh)

        if not wants_stream:
            try:
                reply = await _run()
            except Exception as exc:
                logger.exception("Workbench chat run failed for %s", chat_id)
                _settle_status()
                message = _workbench_chat_run_error_message(exc, lang)
                error = message if isinstance(exc, httpx.TransportError) else "agent run failed"
                return JSONResponse({"error": error, "detail": str(exc)}, status_code=502)
            if reply == R._AWAITING_USER_SENTINEL:
                pending = R._workbench_pending_question_for(chat_id)
                _stash_chat_pending(pending)
                return {"ok": True, "awaitingUser": True, "pendingQuestion": pending, "userMessage": _public_message(user_entry), "retry": retry}
            finalized = _finalize(reply)
            return {
                "ok": True,
                "userMessage": _public_message(user_entry),
                "assistantMessage": finalized.get("assistantMessage") or {},
                "assistantMessages": finalized.get("assistantMessages") or [],
                "retry": retry,
            }

        async def event_stream():
            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            saw_reply_events = False

            async def publish(event: dict[str, Any]) -> None:
                await queue.put(dict(event))

            token = _reply_stream_writer.set(publish)
            task = asyncio.create_task(_run())
            _reply_stream_writer.reset(token)

            ack: dict[str, Any] = {"type": "ack", "chatId": chat_id}
            if retry:
                ack["retry"] = True
                ack["truncateAfterMessageId"] = truncate_after_id
            else:
                ack["userMessage"] = _public_message(user_entry)
            yield _ndjson_line(ack)
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
                    reply = await task
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("Workbench chat streaming run failed for %s", chat_id)
                    _settle_status()
                    yield _ndjson_line({
                        "type": "error",
                        "error": "model_call_failed",
                        "message": _workbench_chat_run_error_message(exc, lang),
                    })
                    return
                if reply == R._AWAITING_USER_SENTINEL:
                    # Run paused for a permission / clarification answer — surface
                    # the question instead of streaming the sentinel as a reply.
                    pending = R._workbench_pending_question_for(chat_id)
                    _stash_chat_pending(pending)
                    yield _ndjson_line({"type": "awaiting_user", "pending_question": pending})
                    return
                if not saw_reply_events:
                    yield _ndjson_line({"type": "reply_start"})
                    for chunk in R._reply_stream_chunks(reply):
                        yield _ndjson_line({"type": "reply_delta", "delta": chunk})
                    yield _ndjson_line({"type": "reply_done", "response": reply})
                finalized = _finalize(reply)
                yield _ndjson_line({"type": "saved", **finalized})
            finally:
                if not task.done():
                    task.cancel()
                _settle_status()

        return StreamingResponse(
            event_stream(),
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

        payload = _read_chats_store()
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

        # Prefix transcript: everything before the edited user message.
        prefix = [dict(entry) for entry in messages[:edit_index]]
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
        _write_chats_store(payload)

        # Seed the forked session's raw state from the source, truncated at the
        # same user-message boundary so the replay send appends the edited turn.
        new_chat_id = str(new_chat.get("id") or "")
        src_state = _session_state_file(chat_id)
        new_state = _session_state_file(new_chat_id)
        new_state.parent.mkdir(parents=True, exist_ok=True)
        try:
            if src_state.exists():
                shutil.copyfile(src_state, new_state)
                _truncate_state_file_at_user_ordinal(new_state, user_ordinal)
            else:
                atomic_write_json(new_state, {"messages": []})
        except Exception:
            logger.exception("Failed to seed fork state for %s", new_chat_id)

        return {"ok": True, "chat": _public_chat_full(new_chat)}

    @router.post("/api/workbench/chats/{chat_id}/to-task")
    async def api_workbench_chat_to_task(
        chat_id: str, body_model: api_models.ChatToTaskBody
    ):
        """Promote a conversation into a task session of its project (开始执行)."""
        body = api_models.body_dict(body_model)
        payload = _read_chats_store()
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        R = _routes()
        store = R._read_workbench_store()
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
        R._write_workbench_store(store)

        # Keep the original conversation and link it to the task, so it's clearly
        # preserved (never consumed) and reachable from both sides.
        chat["convertedSessionId"] = session["id"]
        chat["convertedTaskTitle"] = title
        chat["convertedAt"] = session["createdAt"]
        _write_chats_store(payload)
        append_notification(
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
        if not question_id or not answer_text:
            return JSONResponse({"error": "question_id and answer are required"}, status_code=400)
        payload = _read_chats_store()
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        pending = chat.get("pendingQuestion") if isinstance(chat.get("pendingQuestion"), dict) else None
        if not pending or str(pending.get("id") or "") != question_id:
            return JSONResponse({"error": "no matching pending question"}, status_code=409)

        R = _routes()
        project_id = str(chat.get("projectId") or "")
        project_store = R._read_workbench_store()
        project = R._workbench_find_project(project_store, project_id)
        if not project:
            return JSONResponse({"error": "project not found"}, status_code=404)
        workspace_dir = R._workbench_resolve_workspace_dir(project)
        now = _utc_now_iso()
        _mark_user_activity(chat, now)
        _write_chats_store(payload)
        state_len_before = len(_session_state_messages(chat_id))
        try:
            reply = await R._workbench_answer_pending(
                chat_id, question_id, answer_text, workspace_dir,
            )
        except Exception as exc:
            logger.exception("Workbench chat answer-resume failed for %s", chat_id)
            return JSONResponse({"error": "answer resume failed", "detail": str(exc)}, status_code=502)

        if reply == R._AWAITING_USER_SENTINEL:
            new_pending = R._workbench_pending_question_for(chat_id)
            _stash_chat_pending_for(chat_id, new_pending)
            return {"ok": True, "awaitingUser": True, "pendingQuestion": new_pending}

        intermediate_entries, trace, usage, files = _extract_exchange_segments(
            _session_state_messages(chat_id), state_len_before
        )
        fresh = _read_chats_store()
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
        fresh_chat.setdefault("messages", []).extend(saved_messages)
        fresh_chat["status"] = "idle"
        fresh_chat.pop("pendingQuestion", None)
        fresh_chat["updatedAt"] = assistant_entry["createdAt"]
        _write_chats_store(fresh)
        try:
            archive_session_exchange(
                chat_id,
                answer_text,
                str(reply or ""),
                workspace_dir=workspace_dir,
                session_title=str(fresh_chat.get("title") or ""),
            )
        except Exception:
            logger.exception("Failed to archive workbench conversation %s", chat_id)
        if project_id:
            R.schedule_capture(_project_data_key(project_id), answer_text, str(reply or ""))
        return {
            "ok": True,
            "awaitingUser": False,
            "assistantMessage": _public_message(assistant_entry),
            "assistantMessages": [_public_message(item) for item in saved_messages],
        }


def _stash_chat_pending_for(chat_id: str, pending: dict[str, Any] | None) -> None:
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
    chat["updatedAt"] = _utc_now_iso()
    _write_chats_store(payload)


async def remove_project_chats(project_id: str) -> int:
    """Bulk-remove all chats of a project (called when the project is deleted)."""
    from cyrene.agent import clear_session_id
    project_id = str(project_id or "").strip()
    if not project_id:
        return 0
    payload = _read_chats_store()
    doomed = [chat for chat in payload.get("chats", []) if str(chat.get("projectId") or "") == project_id]
    if doomed:
        payload["chats"] = [chat for chat in payload.get("chats", []) if str(chat.get("projectId") or "") != project_id]
        _write_chats_store(payload)
    for chat in doomed:
        try:
            await clear_session_id(session_id=str(chat.get("id") or ""))
        except Exception:
            logger.exception("Failed to clear agent state for chat %s", chat.get("id"))
    return len(doomed)
