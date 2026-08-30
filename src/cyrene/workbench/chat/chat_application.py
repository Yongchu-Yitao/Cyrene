"""Agent-independent Workbench Chat domain and projection helpers.

This module deliberately owns only persisted chat documents, public DTO
projection, ContextTree transcript reads, and workspace-change bookkeeping.
It must stay importable without loading the retired ``cyrene.workbench.chat``
composition module or the legacy Agent runtime.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import mimetypes
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from cyrene.localization import app_language, localized
from cyrene.model.constants import NETWORK_RETRY_LIMIT
from cyrene.workbench.chat.chat_repository import ChatRepository
from cyrene.workbench.workspaces.workspace_changes import (
    WorkspaceSnapshot,
    build_change_set,
    capture_workspace_snapshot,
    list_chat_change_sets,
    save_change_set,
)

logger = logging.getLogger(__name__)


def _composer_context_service():
    from cyrene.core.plugin import application_plugin_service

    service = application_plugin_service("composer_context")
    if service is None:
        raise RuntimeError(
            "Required Plugin application service is unavailable: composer_context"
        )
    return service

_USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
)
_VISIBLE_PLAN_STATUSES = {"proposed", "active", "paused"}
_FORK_METADATA_FIELDS = ("forkedFromChatId", "forkedAtMessageId", "forkMessage")
_TRACE_FIELDS = (
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


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def short_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _builtin_agent_fields(model: str = "") -> dict[str, Any]:
    """Return the stable built-in binding without loading legacy Agent code."""

    capabilities = {
        "session": {"load": "supported", "fork": "supported", "close": "supported"},
        "input": {
            "text": "supported",
            "image": "supported",
            "file": "supported",
            "audio": "supported",
        },
        "output": {
            "streaming": "supported",
            "reasoning": "supported",
            "toolLifecycle": "supported",
            "artifacts": "supported",
            "diff": "supported",
        },
        "interaction": {
            "permission": "supported",
            "elicitation": "supported",
            "steer": "supported",
            "cancel": "supported",
        },
        "model": {
            "agentManaged": "unsupported",
            "cyreneManaged": [],
            "switchDuringSession": "supported",
            "reasoningEffort": "supported",
        },
    }
    return {
        "agent": {
            "installationId": "agent_cyrene_builtin",
            "agentId": "cyrene",
            "displayName": "Cyrene",
            "version": "1.0.0",
            "driver": "cyrene_builtin",
            "protocolVersion": 1,
            "externalSessionId": "",
            "bindingLocked": False,
        },
        "modelAccess": {
            "mode": "cyrene_managed",
            "profileId": "",
            "protocol": "",
            "model": str(model or ""),
        },
        "capabilities": capabilities,
        "capabilitiesRevision": 1,
    }


def agent_fields(chat: Mapping[str, Any]) -> dict[str, Any]:
    fields = _builtin_agent_fields(str(chat.get("model") or ""))
    for key in ("agent", "modelAccess", "capabilities"):
        raw = chat.get(key)
        if isinstance(raw, Mapping):
            fields[key] = copy.deepcopy(dict(raw))
    revision = chat.get("capabilitiesRevision")
    if isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0:
        fields["capabilitiesRevision"] = revision
    return fields


def new_chat(
    project_id: str,
    title: str = "",
    model: str = "",
    *,
    project_memory_snapshot: Mapping[str, Any] | None = None,
    agent: Mapping[str, Any] | None = None,
    model_access: Mapping[str, Any] | None = None,
    capabilities: Mapping[str, Any] | None = None,
    soul_active: bool | None = None,
    workspace_active: bool | None = None,
    reasoning_effort: str = "",
) -> dict[str, Any]:
    now = utc_now_iso()
    supplied_title = str(title or "").strip()
    if soul_active is None or workspace_active is None:
        defaults = _composer_context_service().default_input_context()

        if soul_active is None:
            soul_active = bool(defaults["soulActive"])
        if workspace_active is None:
            workspace_active = bool(defaults["workspaceActive"])
    chat: dict[str, Any] = {
        "id": short_id("wbchat"),
        "projectId": str(project_id or ""),
        "kind": "chat",
        "title": supplied_title[:60] or localized("New chat", "新对话"),
        "titleLocked": bool(supplied_title),
        "status": "idle",
        "model": str(model or ""),
        "permissionMode": "auto",
        "createdAt": now,
        "updatedAt": now,
        "messages": [],
        "completedTurnCount": 0,
        "soulActive": bool(soul_active),
        "workspaceActive": bool(workspace_active),
    }
    fields = _builtin_agent_fields(model)
    if isinstance(agent, Mapping):
        fields["agent"] = copy.deepcopy(dict(agent))
    if isinstance(model_access, Mapping):
        fields["modelAccess"] = copy.deepcopy(dict(model_access))
    if isinstance(capabilities, Mapping):
        fields["capabilities"] = copy.deepcopy(dict(capabilities))
    chat.update(fields)
    if reasoning_effort:
        chat["reasoningEffort"] = str(reasoning_effort)
    if isinstance(project_memory_snapshot, Mapping):
        frozen_snapshot = {
            "prompt": str(project_memory_snapshot.get("prompt") or ""),
            "modifiedAt": str(project_memory_snapshot.get("modifiedAt") or ""),
            "hash": str(project_memory_snapshot.get("hash") or ""),
        }
        for key in (
            "shortTermContext",
            "structuredContext",
            "memoryContextHash",
        ):
            if key in project_memory_snapshot:
                frozen_snapshot[key] = str(project_memory_snapshot.get(key) or "")
        chat["projectMemorySnapshot"] = frozen_snapshot
    return chat


def chat_soul_active(
    chat: Mapping[str, Any],
    *,
    defaults: Mapping[str, Any] | None = None,
) -> bool:
    if isinstance(chat.get("soulActive"), bool):
        return bool(chat["soulActive"])
    resolved = defaults or _composer_context_service().default_input_context()
    return bool(resolved["soulActive"])


def chat_workspace_active(
    chat: Mapping[str, Any],
    *,
    defaults: Mapping[str, Any] | None = None,
) -> bool:
    if isinstance(chat.get("workspaceActive"), bool):
        return bool(chat["workspaceActive"])
    resolved = defaults or _composer_context_service().default_input_context()
    return bool(resolved["workspaceActive"])


def resolve_composer_input_context(
    chat: Mapping[str, Any],
    workspace_dir: str | Path,
    *,
    strict: bool = True,
) -> dict[str, Any]:
    """Delegate every composer-owned context field to its required Plugin."""

    return _composer_context_service().resolve_input_context(
        soul_active=chat_soul_active(chat),
        workspace_active=chat_workspace_active(chat),
        workspace_dir=str(workspace_dir or ""),
        remote_device_ids=chat.get("remoteDeviceIds") or (),
        context_activations=chat.get("contextActivations"),
        strict=bool(strict),
    )


def normalize_workspace_override(path: Any) -> str:
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


def resolve_chat_workspace_dir(
    chat: Mapping[str, Any],
    project: Mapping[str, Any],
    project_workspace_resolver: Callable[[Mapping[str, Any] | None], str],
) -> str:
    override = str(chat.get("workspaceOverride") or "").strip()
    if not override:
        return project_workspace_resolver(project)
    return project_workspace_resolver(
        {"workspacePath": normalize_workspace_override(override)}
    )


def completed_turn_count(chat: Mapping[str, Any]) -> int:
    stored = chat.get("completedTurnCount")
    if isinstance(stored, int) and not isinstance(stored, bool) and stored >= 0:
        return stored
    projection = chat.get("_messageProjection")
    projected = (
        projection.get("completedTurnCount")
        if isinstance(projection, Mapping)
        else None
    )
    if isinstance(projected, int) and not isinstance(projected, bool) and projected >= 0:
        return projected
    return sum(
        1
        for message in chat.get("messages") or ()
        if isinstance(message, Mapping)
        and str(message.get("role") or "") == "assistant"
        and "processingDurationMs" in message
        and not bool(message.get("systemInitiated"))
    )


def next_completed_turn_count(
    chat: Mapping[str, Any],
    *,
    retry: bool = False,
    command: str = "",
    is_side_agent: bool = False,
) -> int:
    count = completed_turn_count(chat)
    if not retry and not command and not is_side_agent:
        count += 1
    return count


def is_hidden_protocol_record(message: Any) -> bool:
    return bool(
        isinstance(message, Mapping)
        and (
            message.get("hidden_from_ui")
            or str(message.get("record_kind") or "")
            in {"execution_handoff", "execution_outcome"}
        )
    )


def message_event_time(message: Mapping[str, Any]) -> datetime | None:
    raw = str(message.get("createdAt") or message.get("created_at") or "").strip()
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def messages_in_chronological_order(messages: list[Any]) -> list[Any]:
    if not messages:
        return list(messages)
    times: list[datetime] = []
    for message in messages:
        if not isinstance(message, Mapping):
            return list(messages)
        event_time = message_event_time(message)
        if event_time is None:
            return list(messages)
        times.append(event_time)
    return [
        message
        for index, message in sorted(
            enumerate(messages),
            key=lambda pair: (times[pair[0]], pair[0]),
        )
    ]


def merge_chat_messages_chronologically(
    chat: dict[str, Any],
    additions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    messages = chat.setdefault("messages", [])
    messages[:] = messages_in_chronological_order(messages)
    causal_floor = 0
    for item in additions:
        if not isinstance(item, dict):
            continue
        identity = str(item.get("id") or "")
        existing_index = next(
            (
                index
                for index, existing in enumerate(messages)
                if isinstance(existing, Mapping)
                and identity
                and str(existing.get("id") or "") == identity
            ),
            -1,
        )
        if existing_index >= 0:
            merged = {**messages[existing_index], **item}
            messages[existing_index] = merged
            causal_floor = max(causal_floor, existing_index + 1)
            continue
        item_time = message_event_time(item)
        insert_at = len(messages)
        if item_time is not None:
            for index, current in enumerate(messages):
                if not isinstance(current, Mapping):
                    continue
                current_time = message_event_time(current)
                if current_time is not None and current_time > item_time:
                    insert_at = index
                    break
        insert_at = max(causal_floor, insert_at)
        messages.insert(insert_at, item)
        causal_floor = insert_at + 1
    return messages


def chat_preview(chat: Mapping[str, Any]) -> str:
    projection = chat.get("_messageProjection")
    if isinstance(projection, Mapping) and "preview" in projection:
        return str(projection.get("preview") or "")
    for message in reversed(chat.get("messages") or ()):
        if not isinstance(message, Mapping) or is_hidden_protocol_record(message):
            continue
        text = str(message.get("content") or "").strip()
        if text:
            return text.replace("\n", " ")[:80]
    return ""


def chat_first_message(chat: Mapping[str, Any]) -> str:
    projection = chat.get("_messageProjection")
    if isinstance(projection, Mapping) and "firstMessage" in projection:
        return str(projection.get("firstMessage") or "")
    visible = [
        item
        for item in chat.get("messages") or ()
        if isinstance(item, Mapping) and not is_hidden_protocol_record(item)
    ]
    for preferred_role in ("user", ""):
        for message in visible:
            if preferred_role and str(message.get("role") or "") != preferred_role:
                continue
            text = str(message.get("content") or "").strip()
            if text:
                return text.replace("\n", " ")[:80]
    return ""


def side_agent_parent_transcript(chat: Mapping[str, Any] | None) -> str:
    if not isinstance(chat, Mapping):
        return ""
    sections: list[str] = []
    for message in chat.get("messages") or ():
        if not isinstance(message, Mapping) or is_hidden_protocol_record(message):
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
            for item in message.get("attachments") or ()
            if isinstance(item, Mapping)
            and str(item.get("name") or item.get("title") or "").strip()
        ]
        if attachment_names:
            content = (
                f"{content}\nAttachments: {', '.join(attachment_names)}"
            ).strip()
        if content:
            sections.append(f"[{len(sections) + 1}. {role}]\n{content}")
    return "\n\n".join(sections)


def aggregate_usage(messages: list[Any]) -> dict[str, int]:
    totals = {key: 0 for key in _USAGE_KEYS}
    for message in messages:
        usage = message.get("usage") if isinstance(message, Mapping) else None
        if not isinstance(usage, Mapping):
            continue
        for key in _USAGE_KEYS:
            try:
                totals[key] += max(0, int(usage.get(key) or 0))
            except (TypeError, ValueError, OverflowError):
                pass
    if not totals["total_tokens"]:
        totals["total_tokens"] = (
            totals["prompt_tokens"] + totals["completion_tokens"]
        )
    return totals


def latest_request_usage(messages: list[Any]) -> dict[str, int]:
    """Return the newest model request usage without changing lifetime totals."""

    for message in reversed(messages):
        if not isinstance(message, Mapping):
            continue
        usage = message.get("latestRequestUsage")
        if not isinstance(usage, Mapping):
            if isinstance(message.get("usage"), Mapping):
                return {key: 0 for key in _USAGE_KEYS}
            continue
        latest = {key: 0 for key in _USAGE_KEYS}
        for key in _USAGE_KEYS:
            try:
                latest[key] = max(0, int(usage.get(key) or 0))
            except (TypeError, ValueError, OverflowError):
                pass
        if not latest["total_tokens"]:
            latest["total_tokens"] = (
                latest["prompt_tokens"] + latest["completion_tokens"]
            )
        return latest
    return {key: 0 for key in _USAGE_KEYS}


def _public_usage(
    chat: Mapping[str, Any],
    projected_usage: Any,
) -> tuple[dict[str, int], dict[str, int]]:
    messages = list(chat.get("messages") or ())
    totals = (
        {key: int(projected_usage.get(key) or 0) for key in _USAGE_KEYS}
        if isinstance(projected_usage, Mapping)
        else aggregate_usage(messages)
    )
    return totals, latest_request_usage(messages)


def public_message(message: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(message, dict):
        return message
    payload = {key: value for key, value in message.items() if key != "agentAttachments"}
    if (
        str(payload.get("role") or "") == "assistant"
        and not str(payload.get("content") or "").strip()
        and (str(payload.get("reasoning") or "").strip() or payload.get("trace"))
    ):
        payload.setdefault("activityCard", True)
    return payload


def public_chat_light(
    chat: Mapping[str, Any],
    *,
    active_run: Any = None,
    composer_context: Any = None,
    default_input_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    composer = composer_context or _composer_context_service()
    defaults = default_input_context
    if defaults is None and (
        not isinstance(chat.get("soulActive"), bool)
        or not isinstance(chat.get("workspaceActive"), bool)
    ):
        defaults = composer.default_input_context()
    projection = chat.get("_messageProjection")
    projected_usage = (
        projection.get("usage") if isinstance(projection, Mapping) else None
    )
    usage, latest_usage = _public_usage(chat, projected_usage)
    persisted_status = str(chat.get("status") or "idle")
    last_run = chat.get("lastRun") if isinstance(chat.get("lastRun"), Mapping) else {}
    if active_run is not None:
        run_status = str(getattr(active_run, "status", "") or "running")
    else:
        last_status = str(last_run.get("status") or "").lower()
        last_outcome = str(last_run.get("outcome") or "").lower()
        reason = str(last_run.get("terminationReason") or "").lower()
        if last_status == "error" or last_outcome == "error":
            run_status = "failed"
        elif last_status in {"cancelled", "interrupted"} or reason in {
            "cancelled",
            "user_interrupted",
            "shutdown_timeout",
        }:
            run_status = "cancelled"
        elif (
            last_outcome == "awaiting" or reason == "awaiting_user"
        ) and isinstance(chat.get("pendingQuestion"), Mapping):
            run_status = "awaiting_user"
        elif last_status in {"done", "completed", "success"} or last_outcome == "reply":
            run_status = "completed"
        else:
            run_status = "idle" if persisted_status == "running" else persisted_status
    payload: dict[str, Any] = {
        "id": chat.get("id"),
        "projectId": chat.get("projectId"),
        "kind": str(chat.get("kind") or "chat"),
        "title": chat.get("title"),
        "status": persisted_status,
        "runStatus": run_status,
        "lastRun": dict(last_run) if last_run else None,
        "model": chat.get("model") or "",
        "lastModel": chat.get("lastModel") or "",
        "modelSelectionId": chat.get("modelSelectionId") or "",
        "reasoningEffort": chat.get("reasoningEffort") or "",
        "completedTurnCount": completed_turn_count(chat),
        "projectMemoryEnabled": isinstance(chat.get("projectMemorySnapshot"), Mapping),
        "projectMemoryModifiedAt": str(
            (chat.get("projectMemorySnapshot") or {}).get("modifiedAt") or ""
        ),
        "projectMemoryHash": str(
            (chat.get("projectMemorySnapshot") or {}).get("hash") or ""
        ),
        "permissionMode": chat.get("permissionMode") or "default",
        "workspaceOverride": str(chat.get("workspaceOverride") or ""),
        "soulActive": chat_soul_active(chat, defaults=defaults),
        "workspaceActive": chat_workspace_active(chat, defaults=defaults),
        "contextActivations": composer.normalize(
            chat.get("contextActivations")
        ),
        "remoteDeviceIds": [
            str(item)
            for item in chat.get("remoteDeviceIds") or ()
            if str(item or "").strip()
        ],
        "createdAt": chat.get("createdAt"),
        "updatedAt": chat.get("updatedAt"),
        "preview": chat_preview(chat),
        "messageCount": (
            int(projection.get("messageCount") or 0)
            if isinstance(projection, Mapping) and "messageCount" in projection
            else len(chat.get("messages") or ())
        ),
        "usage": usage,
        "latestUsage": latest_usage,
        "pendingQuestion": chat.get("pendingQuestion") or None,
        "firstMessage": chat_first_message(chat),
    }
    for key in (
        "parentChatId",
        "sourceQuote",
        "forkedFromChatId",
        "forkedAtMessageId",
        "forkMessage",
    ):
        if chat.get(key):
            payload[key] = chat.get(key)
    plan = chat.get("activePlan")
    if isinstance(plan, Mapping) and str(plan.get("status") or "") in _VISIBLE_PLAN_STATUSES:
        payload["activePlan"] = copy.deepcopy(dict(plan))
    goal = chat.get("activeGoal")
    if isinstance(goal, Mapping):
        payload["activeGoal"] = copy.deepcopy(dict(goal))
    fields = agent_fields(chat)
    payload.update(fields)
    payload["agentConfigOptions"] = chat.get("agentConfigOptions") or []
    payload["agentConfigValues"] = chat.get("agentConfigValues") or {}
    payload["agentCommands"] = chat.get("agentCommands") or []
    if chat.get("agentMode") is not None:
        payload["agentMode"] = chat.get("agentMode")
    return payload


def public_chats_light(
    chats: list[Mapping[str, Any]],
    *,
    active_runs: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Project a chat collection with one Composer catalog resolution.

    Historical rows may predate the explicit Composer booleans. Their fallback
    is request-scoped: the catalog is resolved once and the resulting defaults
    are shared by every row in this projection.
    """

    composer = _composer_context_service()
    defaults: Mapping[str, Any] = {}
    if any(
        not isinstance(chat.get("soulActive"), bool)
        or not isinstance(chat.get("workspaceActive"), bool)
        for chat in chats
    ):
        defaults = composer.default_input_context()
    runs = active_runs or {}
    return [
        public_chat_light(
            chat,
            active_run=runs.get(str(chat.get("id") or "")),
            composer_context=composer,
            default_input_context=defaults,
        )
        for chat in chats
    ]


def public_chat_full(
    chat: Mapping[str, Any],
    *,
    active_run: Any = None,
) -> dict[str, Any]:
    payload = public_chat_light(chat, active_run=active_run)
    payload["messages"] = [
        public_message(dict(item))
        for item in messages_in_chronological_order(list(chat.get("messages") or ()))
        if isinstance(item, Mapping) and not is_hidden_protocol_record(item)
    ]
    payload["files"] = [
        dict(item)
        for item in chat.get("generatedFiles") or ()
        if isinstance(item, Mapping) and str(item.get("path") or "").strip()
    ]
    return payload


def clear_fork_metadata(chat: dict[str, Any]) -> bool:
    changed = False
    for key in _FORK_METADATA_FIELDS:
        if key in chat:
            chat.pop(key, None)
            changed = True
    return changed


def prune_orphaned_fork_metadata(payload: dict[str, Any]) -> bool:
    chats = payload.get("chats") if isinstance(payload.get("chats"), list) else []
    ids = {
        str(chat.get("id") or "")
        for chat in chats
        if isinstance(chat, Mapping) and str(chat.get("id") or "")
    }
    changed = False
    for chat in chats:
        if not isinstance(chat, dict):
            continue
        parent_id = str(chat.get("forkedFromChatId") or "").strip()
        if parent_id and parent_id not in ids:
            changed = clear_fork_metadata(chat) or changed
    return changed


def sanitize_durable_traces(traces: list[Any]) -> list[list[dict[str, Any]]]:
    sanitized: list[list[dict[str, Any]]] = []
    if not isinstance(traces, list):
        return sanitized
    for raw_trace in traces[:100]:
        entries: list[dict[str, Any]] = []
        for raw_entry in raw_trace[:40] if isinstance(raw_trace, list) else ():
            if not isinstance(raw_entry, Mapping):
                continue
            entry: dict[str, Any] = {}
            for key in _TRACE_FIELDS:
                value = raw_entry.get(key)
                if value is None:
                    continue
                if isinstance(value, bool):
                    entry[key] = value
                elif isinstance(value, str):
                    entry[key] = value[:400]
                elif isinstance(value, (int, float)):
                    entry[key] = value
                elif isinstance(value, (dict, list)):
                    try:
                        entry[key] = json.dumps(value, ensure_ascii=False)[:2000]
                    except (TypeError, ValueError):
                        continue
            if entry:
                entries.append(entry)
        sanitized.append(entries)
    return sanitized


def pending_question_message(
    pending: Mapping[str, Any],
    *,
    trace: list[dict[str, Any]] | None = None,
    usage: Mapping[str, int] | None = None,
    files: list[dict[str, Any]] | None = None,
    model: str = "",
) -> dict[str, Any]:
    question_id = str(pending.get("id") or "")
    entry: dict[str, Any] = {
        "id": f"msg_question_{question_id}" if question_id else short_id("msg"),
        "role": "assistant",
        "content": str(pending.get("text") or ""),
        "createdAt": utc_now_iso(),
        "model": model,
        "questionPrompt": True,
        "questionId": question_id,
        "questionKind": str(pending.get("kind") or ""),
    }
    if trace:
        entry["trace"] = trace
    if usage and any(usage.values()):
        entry["usage"] = dict(usage)
    if files:
        entry["attachments"] = files
    return entry


def remove_retry_replaced_messages(
    chat: dict[str, Any],
    after_id: str,
    replaced_ids: set[str],
) -> None:
    messages = chat.setdefault("messages", [])
    cut = next(
        (
            index
            for index, item in enumerate(messages)
            if isinstance(item, Mapping)
            and str(item.get("id") or "") == str(after_id or "")
        ),
        -1,
    )
    if cut >= 0:
        messages[cut + 1 :] = [
            item
            for item in messages[cut + 1 :]
            if not isinstance(item, Mapping)
            or str(item.get("id") or "") not in replaced_ids
        ]


def mark_user_activity(chat: dict[str, Any], timestamp: str) -> None:
    chat["lastUserMessageAt"] = timestamp
    chat["updatedAt"] = timestamp
    try:
        from cyrene.core.plugin import application_plugin_service

        proactive = application_plugin_service("proactive")
        reset = getattr(proactive, "reset_lottery", None)
        if callable(reset):
            reset()
    except Exception:
        logger.debug("Could not reset proactive lottery", exc_info=True)


def coerce_brief_constraints(raw: Any) -> list[str]:
    result: list[str] = []
    for item in raw if isinstance(raw, list) else ():
        text = str(item).strip()
        if text:
            result.append(text[:300])
        if len(result) >= 8:
            break
    return result


def coerce_brief_acceptance(raw: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else ():
        text = str(item).strip()
        if text:
            result.append(
                {"id": short_id("accept"), "text": text[:300], "status": "pending"}
            )
        if len(result) >= 8:
            break
    return result


def chat_transcript_for_brief(
    chat: Mapping[str, Any],
    *,
    max_messages: int = 80,
    max_chars: int = 50_000,
) -> str:
    messages = [
        item
        for item in chat.get("messages") or ()
        if isinstance(item, Mapping)
        and not is_hidden_protocol_record(item)
        and str(item.get("role") or "") in {"user", "assistant"}
        and str(item.get("content") or "").strip()
    ]
    blocks: list[str] = []
    total = 0
    language = app_language()
    for message in reversed(messages[-max_messages:]):
        role = (
            localized("User", "用户", language=language)
            if str(message.get("role")) == "user"
            else localized("Assistant", "助手", language=language)
        )
        text = str(message.get("content") or "").strip()
        if len(text) > 2000:
            text = text[:2000] + localized(
                "… (truncated)", "…（内容过长已截断）", language=language
            )
        block = localized(
            "{role}: {text}", "{role}：{text}",
            language=language, role=role, text=text,
        )
        if blocks and total + len(block) > max_chars:
            break
        blocks.append(block)
        total += len(block)
    return "\n\n".join(reversed(blocks))


def parse_json_object(raw: Any) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match is None:
            return None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return dict(value) if isinstance(value, Mapping) else None


def chat_run_error_message(exc: Exception, lang: str = "") -> str:
    language = app_language(lang)
    http_error = _http_status_error(exc)
    if http_error is not None and int(http_error.response.status_code) in (401, 403):
        return localized(
            "The model service could not be authenticated. Check its API key or sign-in, then try again.",
            "无法访问模型服务：鉴权失败。请检查 API Key 或登录状态后重试。",
            language=language,
        )
    if isinstance(exc, httpx.TransportError):
        return localized(
            "The network connection still failed after {count} automatic retries. Please send this message again.",
            "网络连接异常，已自动重试 {count} 次仍未成功。请重新发送这条消息。",
            language=language,
            count=NETWORK_RETRY_LIMIT,
        )
    return localized(
        "The Agent run failed. Please try again.",
        "Agent 运行失败，请重试。",
        language=language,
    )


def _http_status_error(exc: Exception) -> httpx.HTTPStatusError | None:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, httpx.HTTPStatusError):
            return current
        current = current.__cause__ or current.__context__
    return None


_ERROR_KEYS = {
    "quota_exhausted": "workbenchChat.error.quotaExhausted",
    "authentication_expired": "workbenchChat.error.authenticationExpired",
    "model_unavailable": "workbenchChat.error.modelUnavailable",
    "model_not_configured": "workbenchChat.error.modelNotConfigured",
    "model_authentication_failed": "workbenchChat.error.modelAuthenticationFailed",
}


def chat_error_metadata(exc: Exception) -> dict[str, str]:
    kind = str(getattr(exc, "kind", "") or getattr(exc, "code", "") or "").strip()
    if kind:
        key = _ERROR_KEYS.get(kind)
        return {"code": kind, **({"detail_key": key} if key else {"failureKind": kind})}
    http_error = _http_status_error(exc)
    if http_error is not None and int(http_error.response.status_code) in (401, 403):
        return {
            "code": "model_authentication_failed",
            "detail_key": _ERROR_KEYS["model_authentication_failed"],
        }
    return {}


class ContextTreeTranscript:
    """Read the active durable conversation branch without opening an Agent."""

    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path or "")

    def messages(self, session_id: str) -> list[dict[str, Any]]:
        from cyrene.core.context import ContextStoreRouter, TreeNotFoundError
        from cyrene.workbench.core_adapter.chat_runtime import workbench_agent_data_directory

        router = ContextStoreRouter(
            workbench_agent_data_directory(self.db_path) / "context"
        )
        try:
            try:
                tree = router.get_tree(str(session_id or ""))
            except TreeNotFoundError:
                return []
            nodes = router.get_subtree(tree.id, tree.root_id)
            dialogue = [
                node
                for node in nodes
                if isinstance(node.value, Mapping)
                and node.value.get("role")
                in {"system", "user", "context", "context_compaction", "context_reflection", "assistant", "tool_results"}
            ]
            if not dialogue:
                return []
            leaf = max(dialogue, key=lambda item: (item.created_at, item.id))
            path = router.get_path(tree.id, leaf.id)
            result: list[dict[str, Any]] = []
            for node in path:
                value = node.value if isinstance(node.value, Mapping) else {}
                if value.get("role") == "context_reflection":
                    public_nodes = value.get("public_nodes")
                    for raw in public_nodes if isinstance(public_nodes, list) else ():
                        if not isinstance(raw, Mapping):
                            continue
                        public_value = raw.get("value")
                        if not isinstance(public_value, Mapping) or public_value.get(
                            "role"
                        ) not in {"user", "assistant", "tool_results"}:
                            continue
                        public_id = str(raw.get("id") or "")
                        result.append(
                            {
                                "message_id": public_id,
                                "id": public_id,
                                "created_at": str(raw.get("created_at") or ""),
                                **copy.deepcopy(dict(public_value)),
                            }
                        )
                    continue
                if value.get("role") not in {"user", "assistant", "tool_results"}:
                    continue
                result.append(
                    {
                        "message_id": node.id,
                        "id": node.id,
                        "created_at": node.created_at.isoformat(),
                        **copy.deepcopy(dict(value)),
                    }
                )
            return result
        finally:
            router.close()


def _usage_from_message(message: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = message.get("usage")
    return raw if isinstance(raw, Mapping) else {}


def _collect_attachments(value: Any, output: list[dict[str, Any]]) -> None:
    if isinstance(value, Mapping):
        for key in ("attachments", "files", "artifacts"):
            nested = value.get(key)
            for item in nested if isinstance(nested, list) else ():
                if isinstance(item, Mapping):
                    output.append(dict(item))
        nested_value = value.get("value")
        if isinstance(nested_value, (Mapping, list)):
            _collect_attachments(nested_value, output)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (Mapping, list)):
                _collect_attachments(item, output)


def extract_exchange_timeline(
    state_messages: list[dict[str, Any]],
    state_ids_before: set[str],
) -> tuple[list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    fresh = [
        message
        for message in state_messages
        if str(message.get("message_id") or message.get("id") or "")
        not in state_ids_before
    ]
    usage = aggregate_usage(fresh)
    timeline: list[dict[str, Any]] = []
    attachments: list[dict[str, Any]] = []
    for index, message in enumerate(fresh):
        if is_hidden_protocol_record(message):
            continue
        role = str(message.get("role") or "")
        if role == "tool_results":
            _collect_attachments(message.get("results"), attachments)
            continue
        if role != "assistant" or not bool(
            message.get("intermediate_reply") or message.get("intermediate")
        ):
            continue
        entry = {
            "id": str(message.get("message_id") or message.get("id") or short_id("msg")),
            "role": "assistant",
            "content": str(message.get("content") or ""),
            "createdAt": str(message.get("created_at") or message.get("createdAt") or utc_now_iso()),
            "intermediate": True,
        }
        model = str(message.get("model") or _usage_from_message(message).get("model") or "")
        if model:
            entry["model"] = model
        timeline.append(entry)
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in attachments:
        key = str(item.get("id") or item.get("url") or item.get("path") or "")
        if key and key not in seen:
            seen.add(key)
            deduped.append(item)
    return timeline, usage, deduped[:20]


def last_exchange_model(
    state_messages: list[dict[str, Any]],
    state_ids_before: set[str],
) -> str:
    for message in reversed(state_messages):
        if str(message.get("role") or "") != "assistant":
            continue
        identity = str(message.get("message_id") or message.get("id") or "")
        if identity and identity in state_ids_before:
            continue
        usage = _usage_from_message(message)
        model = str(usage.get("model") or message.get("model") or "").strip()
        if model:
            return model
    return ""


@dataclass(slots=True)
class WorkspaceChangesBaseline:
    snapshot: WorkspaceSnapshot | None
    workspace_key: str = ""
    run_id: str = ""
    overlapping_run_ids: set[str] = field(default_factory=set)
    released: bool = False


class WorkspaceChangeService:
    """Small run-scoped workspace snapshot service used by Chat routes."""

    def __init__(self, db_path: str, repository: ChatRepository) -> None:
        self.db_path = str(db_path or "")
        self.repository = repository
        self._lock = asyncio.Lock()
        self._active: dict[str, dict[str, WorkspaceChangesBaseline]] = {}
        self._prewarmed: dict[str, WorkspaceSnapshot] = {}
        self._prewarm_tasks: set[asyncio.Task[Any]] = set()

    def prewarm(self, workspace_dir: str | Path | None) -> None:
        if not workspace_dir:
            return
        try:
            key = str(Path(workspace_dir).expanduser().resolve())
            asyncio.get_running_loop()
        except (OSError, RuntimeError):
            return
        if key in self._prewarmed:
            return
        task = asyncio.create_task(self._prewarm(key))
        self._prewarm_tasks.add(task)
        task.add_done_callback(self._prewarm_tasks.discard)

    async def _prewarm(self, workspace_key: str) -> None:
        try:
            snapshot = await asyncio.to_thread(
                capture_workspace_snapshot,
                workspace_key,
            )
            if snapshot is not None:
                self._prewarmed[workspace_key] = snapshot
        except Exception:
            logger.debug("Workspace snapshot prewarm failed", exc_info=True)

    async def capture(
        self,
        workspace_dir: str | Path | None,
        run_id: str = "",
    ) -> WorkspaceChangesBaseline:
        if not workspace_dir:
            return WorkspaceChangesBaseline(None)
        try:
            key = str(Path(workspace_dir).expanduser().resolve())
        except OSError:
            return WorkspaceChangesBaseline(None)
        normalized_run_id = str(run_id or f"snapshot_{uuid.uuid4().hex}")
        snapshot = await asyncio.to_thread(
            capture_workspace_snapshot,
            key,
            previous=self._prewarmed.get(key),
        )
        baseline = WorkspaceChangesBaseline(snapshot, key, normalized_run_id)
        if snapshot is None:
            return baseline
        async with self._lock:
            active = self._active.setdefault(key, {})
            for other_id, other in active.items():
                baseline.overlapping_run_ids.add(other_id)
                other.overlapping_run_ids.add(normalized_run_id)
            active[normalized_run_id] = baseline
        return baseline

    async def finalize(
        self,
        *,
        chat_id: str,
        run_id: str,
        workspace_dir: str | Path | None,
        before: WorkspaceChangesBaseline | None,
        status: str,
        run: Any = None,
    ) -> dict[str, Any] | None:
        if before is None or before.snapshot is None or before.released:
            return None
        try:
            after = await asyncio.to_thread(
                capture_workspace_snapshot,
                workspace_dir,
                previous=before.snapshot,
            )
            if after is not None:
                self._prewarmed[before.workspace_key] = after
            overlaps = sorted(before.overlapping_run_ids)
            change_set = await asyncio.to_thread(
                build_change_set,
                chat_id=chat_id,
                run_id=run_id,
                before=before.snapshot,
                after=after,
                status=status,
                attribution="overlapping" if overlaps else "exclusive",
                overlapping_run_ids=overlaps,
            )
            if change_set.get("fileCount"):
                await asyncio.to_thread(save_change_set, self.db_path, change_set)
                await asyncio.to_thread(self.sync_generated_files, chat_id, change_set)
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
                "overlappingRunIds": list(change_set.get("overlappingRunIds") or []),
            }
            if run is not None:
                await run.publish(event)
            try:
                from cyrene.observability import debug

                await debug.publish_event(event, session_id=chat_id)
            except Exception:
                logger.debug("Workspace event publication failed", exc_info=True)
            return change_set
        except Exception:
            logger.exception("Failed to finalize workspace changes for chat %s", chat_id)
            return None
        finally:
            before.released = True
            async with self._lock:
                active = self._active.get(before.workspace_key)
                if active is not None:
                    active.pop(before.run_id, None)
                    if not active:
                        self._active.pop(before.workspace_key, None)

    def sync_generated_files(
        self,
        chat_id: str,
        change_set: dict[str, Any] | None = None,
    ) -> None:
        historical: list[dict[str, Any]] = []
        current = self.repository.get(chat_id)
        if current is not None and "generatedFiles" not in current:
            historical = list(
                reversed(list_chat_change_sets(self.db_path, chat_id))
            )

        def update(chat: dict[str, Any]) -> None:
            existing = {
                str(item.get("path") or ""): dict(item)
                for item in chat.get("generatedFiles") or ()
                if isinstance(item, Mapping) and str(item.get("path") or "")
            }
            changes = [*historical]
            if change_set is not None:
                changes.append(change_set)
            order: list[str] = []
            for item in changes:
                for change in item.get("files") or ():
                    if not isinstance(change, Mapping):
                        continue
                    path = str(change.get("path") or "").strip().replace("\\", "/")
                    if not path:
                        continue
                    if path in order:
                        order.remove(path)
                    order.append(path)
                    if str(change.get("changeType") or "") == "deleted":
                        existing.pop(path, None)
                        continue
                    name = Path(path).name or path
                    existing[path] = {
                        "id": str(change.get("id") or f"workspace_{uuid.uuid4().hex[:12]}"),
                        "name": name,
                        "path": path,
                        "content_type": mimetypes.guess_type(name)[0]
                        or "application/octet-stream",
                        "size": int(change.get("afterSize") or 0),
                        "kind": "file",
                        "source": "agent",
                    }
            ordered = [existing[path] for path in order if path in existing]
            ordered.extend(
                item for path, item in existing.items() if path not in order
            )
            chat["generatedFiles"] = ordered[:200]

        self.repository.mutate_one(chat_id, update)

    async def shutdown(self) -> None:
        tasks = list(self._prewarm_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._prewarm_tasks.clear()


_BUTTON_BLOCK_RE = re.compile(
    r"^ {0,3}:::button[ \t]*\n(?P<body>.*?)\n {0,3}:::[ \t]*$",
    re.M | re.S,
)
_BUTTON_ACTION_ID_RE = re.compile(
    r"^ {0,3}action_id:[ \t]*([a-z0-9_]+)[ \t]*$",
    re.M,
)
_BUTTON_DISABLED_RE = re.compile(
    r"^ {0,3}disabled:[ \t]*(true|false)[ \t]*$",
    re.M,
)


def _button_blocks(content: str) -> list[tuple[str, str, str]]:
    blocks: list[tuple[str, str, str]] = []
    for match in _BUTTON_BLOCK_RE.finditer(str(content or "")):
        body = match.group("body")
        action = _BUTTON_ACTION_ID_RE.search(body)
        if action is None:
            continue
        label = re.search(r"^ {0,3}label:[ \t]*(.+?)[ \t]*$", body, re.M)
        blocks.append((match.group(0), action.group(1), label.group(1) if label else ""))
    return blocks


def disable_button_block(content: str, action_id: str) -> tuple[str | None, str]:
    for raw, action, label in _button_blocks(content):
        if action != action_id:
            continue
        disabled = _BUTTON_DISABLED_RE.search(raw)
        if disabled and disabled.group(1) == "true":
            return None, label
        if disabled:
            updated = _BUTTON_DISABLED_RE.sub("disabled: true", raw, count=1)
        else:
            updated = re.sub(
                r"^ {0,3}action_id:[^\n]*$",
                lambda match: match.group(0) + "\ndisabled: true",
                raw,
                count=1,
                flags=re.M,
            )
            if updated == raw:
                updated = raw + "\ndisabled: true"
        return str(content).replace(raw, updated, 1), label
    return None, ""


def has_button_block(content: str, action_id: str) -> bool:
    return any(action == action_id for _raw, action, _label in _button_blocks(content))


__all__ = [
    "ContextTreeTranscript",
    "WorkspaceChangeService",
    "WorkspaceChangesBaseline",
    "agent_fields",
    "chat_error_metadata",
    "chat_preview",
    "chat_run_error_message",
    "chat_soul_active",
    "chat_transcript_for_brief",
    "chat_workspace_active",
    "clear_fork_metadata",
    "coerce_brief_acceptance",
    "coerce_brief_constraints",
    "completed_turn_count",
    "disable_button_block",
    "extract_exchange_timeline",
    "has_button_block",
    "last_exchange_model",
    "mark_user_activity",
    "merge_chat_messages_chronologically",
    "new_chat",
    "next_completed_turn_count",
    "normalize_workspace_override",
    "parse_json_object",
    "pending_question_message",
    "prune_orphaned_fork_metadata",
    "public_chat_full",
    "public_chat_light",
    "public_chats_light",
    "public_message",
    "remove_retry_replaced_messages",
    "resolve_chat_workspace_dir",
    "resolve_composer_input_context",
    "sanitize_durable_traces",
    "short_id",
    "side_agent_parent_transcript",
    "utc_now_iso",
]
