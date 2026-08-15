"""Typed local application services used by Cyrene self-management tools.

These functions operate on the same repositories as Workbench without going
through HTTP routes.  Route migration can call these functions incrementally.
"""

from __future__ import annotations

import asyncio
import hashlib
from copy import deepcopy
from pathlib import Path
from typing import Any

from cyrene.agent.context import active_workspace_dir

_BACKGROUND_SESSION_TASKS: set[asyncio.Task[Any]] = set()


def _track_background_session_task(task: asyncio.Task[Any]) -> None:
    _BACKGROUND_SESSION_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_SESSION_TASKS.discard)


def _runtime():
    from cyrene.workbench import runtime
    return runtime


def list_projects() -> list[dict[str, Any]]:
    payload = _runtime()._read_workbench_store()
    return [
        {
            key: deepcopy(value)
            for key, value in project.items()
            if key not in {"sessions", "sharedArtifacts"}
        }
        | {"sessionCount": len(project.get("sessions") or [])}
        for project in payload.get("projects", [])
        if isinstance(project, dict)
    ]


def read_project(project_id: str) -> dict[str, Any]:
    runtime = _runtime()
    project = runtime._workbench_find_project(runtime._read_workbench_store(), project_id)
    if not project:
        raise LookupError("project not found")
    return deepcopy(project)


def create_project(name: str, *, description: str = "", workspace_path: str = "") -> dict[str, Any]:
    runtime = _runtime()
    payload = runtime._read_workbench_store()
    now = runtime._utc_now_iso()
    project_id = runtime._short_id("project")
    root = active_workspace_dir().resolve()
    target = Path(workspace_path).expanduser() if workspace_path else root / "projects" / project_id
    if not target.is_absolute():
        target = root / target
    target = target.resolve(strict=False)
    if not target.is_relative_to(root):
        raise ValueError("agent-created project workspaces must stay inside the active workspace")
    target.mkdir(parents=True, exist_ok=True)
    project_name = str(name or target.name or "New Project").strip()[:120]
    if not project_name:
        raise ValueError("project name is required")
    project = {
        "id": project_id,
        "name": project_name,
        "dataKey": runtime._safe_workbench_data_key(project_id),
        "description": str(description or "").strip()[:4000],
        "icon": "spark",
        "color": "",
        "template": "blank",
        "workspacePath": str(target),
        "workspacePathSource": "generated" if not workspace_path else "agent_scoped",
        "status": "active",
        "model": runtime._get_model(),
        "accountTier": "Pro",
        "context": {"summary": str(description or f"Workspace at {target}").strip(), "stack": [], "decisions": [], "knowledgeDocumentIds": []},
        "createdAt": now,
        "updatedAt": now,
        "sessions": [],
        "sharedArtifacts": [],
    }
    session = runtime._workbench_new_init_session(project_id, project, now)
    project["sessions"] = [session]
    payload.setdefault("projects", []).insert(0, project)
    payload["activeProjectId"] = project_id
    payload["activeSessionId"] = session["id"]
    runtime._write_workbench_store(payload)
    return {"project": deepcopy(project), "session": deepcopy(session)}


def update_project(project_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    payload = runtime._read_workbench_store()
    project = runtime._workbench_find_project(payload, project_id)
    if not project:
        raise LookupError("project not found")
    allowed = {"name", "description", "icon", "color", "status", "model"}
    unknown = sorted(set(changes) - allowed)
    if unknown:
        raise ValueError("unsupported project field(s): " + ", ".join(unknown))
    for key, value in changes.items():
        if key in {"name", "description"}:
            value = str(value or "").strip()
        project[key] = value
    project["updatedAt"] = runtime._utc_now_iso()
    runtime._write_workbench_store(payload)
    return deepcopy(project)


def activate_project(project_id: str, session_id: str = "") -> dict[str, Any]:
    return _runtime()._persist_workbench_selection(project_id, session_id)


async def delete_project(project_id: str) -> dict[str, Any]:
    runtime = _runtime()
    payload = runtime._read_workbench_store()
    project = runtime._workbench_find_project(payload, project_id)
    if not project:
        raise LookupError("project not found")
    if runtime._workbench_project_data_key(project) == runtime._WORKBENCH_LEGACY_DATA_KEY:
        raise ValueError("the default project cannot be deleted")
    session_ids = [str(item.get("id") or "") for item in project.get("sessions", []) if item.get("id")]
    from cyrene.workbench import chat as chat_store
    chat_payload = chat_store._read_chats_store()
    removed_chat_ids = [str(item.get("id") or "") for item in chat_payload.get("chats", []) if str(item.get("projectId") or "") == project_id]

    # Stop every execution owner before making either project or chat deletion
    # durable. A failed termination leaves the records intact and recoverable.
    await chat_store.terminate_chat_agents(removed_chat_ids)
    from cyrene.agent import clear_session_id, interrupt_active_run
    for session_id in session_ids:
        interrupt_active_run(session_id=session_id)
        await clear_session_id(session_id=session_id, deleting=True)

    payload["projects"] = [item for item in payload.get("projects", []) if str(item.get("id") or "") != project_id]
    if not payload["projects"]:
        payload = runtime._workbench_default_project()
    else:
        payload["activeProjectId"] = payload["projects"][0]["id"]
        sessions = payload["projects"][0].get("sessions") or []
        payload["activeSessionId"] = str(sessions[0].get("id") or "") if sessions else ""
    runtime._write_workbench_store(payload)
    chat_payload["chats"] = [item for item in chat_payload.get("chats", []) if str(item.get("projectId") or "") != project_id]
    chat_store._write_chats_store(chat_payload)
    return {"deletedProjectId": project_id, "deletedSessionIds": session_ids, "deletedChatIds": removed_chat_ids}


def list_chats(project_id: str = "") -> list[dict[str, Any]]:
    from cyrene.workbench import chat as chat_store
    chats = [
        chat_store._public_chat_light(item)
        for item in chat_store._read_chats_store().get("chats", [])
        if str(item.get("kind") or "chat") == "chat"
        and (not project_id or str(item.get("projectId") or "") == project_id)
    ]
    chats.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
    return chats


def read_chat(chat_id: str) -> dict[str, Any]:
    from cyrene.workbench import chat as chat_store
    chat = chat_store._find_chat(chat_store._read_chats_store(), chat_id)
    if not chat:
        raise LookupError("chat not found")
    return chat_store._public_chat_full(chat)


def create_chat(project_id: str, title: str = "") -> dict[str, Any]:
    runtime = _runtime()
    if not runtime._workbench_find_project(runtime._read_workbench_store(), project_id):
        raise LookupError("project not found")
    from cyrene.workbench import chat as chat_store
    payload = chat_store._read_chats_store()
    chat = chat_store._new_chat(project_id, str(title or "").strip()[:80], runtime._get_model())
    payload.setdefault("chats", []).insert(0, chat)
    chat_store._write_chats_store(payload)
    return chat_store._public_chat_full(chat)


def rename_chat(chat_id: str, title: str) -> dict[str, Any]:
    from cyrene.workbench import chat as chat_store
    payload = chat_store._read_chats_store()
    chat = chat_store._find_chat(payload, chat_id)
    if not chat:
        raise LookupError("chat not found")
    normalized = str(title or "").strip()[:60]
    if not normalized:
        raise ValueError("chat title is required")
    chat["title"] = normalized
    chat["titleLocked"] = True
    chat["updatedAt"] = chat_store._utc_now_iso()
    chat_store._write_chats_store(payload)
    return chat_store._public_chat_full(chat)


async def compact_chat(chat_id: str) -> dict[str, Any]:
    from cyrene import config
    from cyrene.agent import compact_session_if_needed
    from cyrene.runtime.config_store import effective_ctx_limit_for_model
    chat = read_chat(chat_id)
    model = str(chat.get("model") or config.OPENAI_MODEL or "")
    return await compact_session_if_needed(chat_id, ctx_limit=effective_ctx_limit_for_model(model) or 128_000, force=True)


async def delete_chat(chat_id: str) -> dict[str, Any]:
    from cyrene.workbench import chat as chat_store
    payload = chat_store._read_chats_store()
    chat = chat_store._find_chat(payload, chat_id)
    if not chat:
        raise LookupError("chat not found")
    removed = {chat_id, *[
        str(item.get("id") or "") for item in payload.get("chats", [])
        if str(item.get("kind") or "") == "side-agent" and str(item.get("parentChatId") or "") == chat_id
    ]}
    await chat_store.terminate_chat_agents(removed)
    payload["chats"] = [item for item in payload.get("chats", []) if str(item.get("id") or "") not in removed]
    chat_store._write_chats_store(payload)
    return {"deletedChatIds": sorted(removed)}


async def fork_chat(chat_id: str, message_id: str, content: str) -> dict[str, Any]:
    from cyrene.workbench import chat as chat_store
    payload = chat_store._read_chats_store()
    source = chat_store._find_chat(payload, chat_id)
    if not source:
        raise LookupError("chat not found")
    messages = source.get("messages") if isinstance(source.get("messages"), list) else []
    index = next((i for i, item in enumerate(messages) if str(item.get("id") or "") == message_id), -1)
    if index < 0 or str(messages[index].get("role") or "") != "user":
        raise ValueError("fork requires an existing user message")
    text = str(content or "").strip()
    if not text:
        raise ValueError("fork content is required")
    forked = chat_store._new_chat(str(source.get("projectId") or ""), str(source.get("title") or ""), str(source.get("model") or ""))
    forked["forkedFromChatId"] = chat_id
    forked["forkedAtMessageId"] = message_id
    forked["forkMessage"] = text.replace("\n", " ")[:80]
    entry = {"id": chat_store._short_id("msg"), "role": "user", "content": text, "createdAt": chat_store._utc_now_iso()}
    if isinstance(messages[index].get("attachments"), list):
        entry["attachments"] = deepcopy(messages[index]["attachments"])
    forked["messages"] = [deepcopy(item) for item in messages[:index]] + [entry]
    payload.setdefault("chats", []).insert(0, forked)
    chat_store._write_chats_store(payload)
    return chat_store._public_chat_full(forked)


def list_chat_groups(project_id: str) -> dict[str, Any]:
    runtime = _runtime()
    if not runtime._workbench_find_project(runtime._read_workbench_store(), project_id):
        raise LookupError("project not found")
    from cyrene.workbench import chat_groups
    return chat_groups.get_project_groups(project_id)


async def manage_chat_group(
    project_id: str,
    operation: str,
    *,
    group_id: str = "",
    chat_ids: list[str] | None = None,
    title: str = "",
) -> dict[str, Any]:
    """Apply one semantic group mutation through the authoritative group store."""
    current_payload = list_chat_groups(project_id)
    current = [deepcopy(item) for item in current_payload.get("groups", []) if isinstance(item, dict)]
    base = deepcopy(current)
    normalized_operation = str(operation or "").strip()
    target_group_id = str(group_id or "").strip()
    requested_ids = list(dict.fromkeys(
        str(item or "").strip() for item in (chat_ids or []) if str(item or "").strip()
    ))
    valid_chat_ids = {
        str(item.get("id") or "") for item in list_chats(project_id)
    }
    if any(item not in valid_chat_ids for item in requested_ids):
        raise ValueError("chat group contains an unknown chat")

    if normalized_operation == "group":
        if len(requested_ids) < 2:
            raise ValueError("group requires at least two chats")
        for group in current:
            group["chatIds"] = [item for item in group.get("chatIds", []) if str(item) not in requested_ids]
        current = [group for group in current if len(group.get("chatIds", [])) >= 2]
        current.append({
            "id": target_group_id,
            "title": str(title or "New chat group").strip()[:60] or "New chat group",
            "titleLocked": bool(str(title or "").strip()),
            "chatIds": requested_ids,
        })
    elif normalized_operation == "ungroup":
        if not target_group_id:
            raise ValueError("group_id is required")
        if not any(str(group.get("id") or "") == target_group_id for group in current):
            raise LookupError("chat group not found")
        current = [group for group in current if str(group.get("id") or "") != target_group_id]
    elif normalized_operation == "rename_group":
        normalized_title = str(title or "").strip()[:60]
        if not target_group_id or not normalized_title:
            raise ValueError("group_id and title are required")
        target = next((group for group in current if str(group.get("id") or "") == target_group_id), None)
        if target is None:
            raise LookupError("chat group not found")
        target["title"] = normalized_title
        target["titleLocked"] = True
    else:
        raise ValueError("unsupported chat group operation")

    from cyrene.workbench import chat_groups
    return await chat_groups.replace_project_groups(
        project_id,
        current,
        base_groups=base,
        mutation_intent={
            "type": normalized_operation,
            "groupId": target_group_id,
            "chatIds": requested_ids,
            "title": str(title or "").strip()[:60],
        },
        mark_migrated=True,
    )


async def dispatch_session_message(
    session_kind: str,
    session_id: str,
    message: str,
    *,
    origin_session_id: str,
) -> dict[str, Any]:
    """Deliver a provenance-marked instruction to another local session.

    The target is resolved by the current-surface tree before this service is
    called. The public record is explicitly agent-originated and the target run
    receives ``conversation_source=agent_session``, so it cannot turn the
    forwarded text into human approval or recursively delegate again.
    """
    kind = str(session_kind or "").strip()
    target_id = str(session_id or "").strip()
    origin_id = str(origin_session_id or "").strip()
    text = str(message or "").strip()
    if kind not in {"chat", "task"}:
        raise ValueError("current composer is not a supported session")
    if not target_id or not text:
        raise ValueError("target session and message are required")
    if len(text) > 20_000:
        raise ValueError("session message is too long")
    if target_id == origin_id:
        raise ValueError("an agent cannot dispatch a second run into its own active session")
    if kind == "chat":
        from cyrene.workbench.chat import (
            dispatch_agent_session_guidance,
            dispatch_agent_session_message,
        )

        outcome = await dispatch_agent_session_message(
            target_id,
            text,
            origin_session_id=origin_id,
            bot=_runtime()._bot,
            db_path=_runtime()._db_path,
        )
        if outcome.get("status") == "busy":
            outcome = await dispatch_agent_session_guidance(
                target_id,
                text,
                origin_session_id=origin_id,
                client_request_id=(
                    f"agent-session:{origin_id}:{target_id}:"
                    f"{hashlib.sha256(text.encode('utf-8')).hexdigest()[:20]}"
                ),
            )
        if outcome.get("status") not in {"started", "guided"}:
            raise ValueError(f"target chat is {outcome.get('status') or 'unavailable'}")
        return outcome

    from cyrene.agent import is_session_running

    runtime = _runtime()
    if is_session_running(target_id):
        raise ValueError("target task already has a running agent")
    payload = runtime._read_workbench_store()
    project, session = runtime._workbench_find_session(payload, target_id)
    if not project or not session:
        raise LookupError("task session not found")
    if session.get("pendingQuestion"):
        raise ValueError("target task is waiting for a user or delegated answer")
    run_id = runtime._short_id("run")
    started_at = runtime._utc_now_iso()
    instruction_event = {
        "id": runtime._short_id("event"),
        "type": "AgentInstructionEvent",
        "runId": run_id,
        "createdAt": started_at,
        "body": text,
        "agentOriginated": True,
        "originSessionId": origin_id,
    }
    session["status"] = "running"
    session["updatedAt"] = started_at
    session.setdefault("events", []).append(instruction_event)
    project["updatedAt"] = started_at
    runtime._write_workbench_store(payload)

    async def _run_task_message() -> None:
        try:
            fresh_payload = runtime._read_workbench_store()
            fresh_project, fresh_session = runtime._workbench_find_session(
                fresh_payload, target_id,
            )
            if not fresh_project or not fresh_session:
                return
            workspace_root = runtime._workbench_workspace_root(fresh_project)
            memory_pair = runtime._workbench_compose_memory_ephemeral(
                fresh_project, fresh_session,
            )
            ephemeral = runtime._workbench_compose_ephemeral_system(
                fresh_project, fresh_session, workspace_root=workspace_root,
                memory_pair=memory_pair,
            )
            static = runtime._workbench_compose_static_system(
                fresh_project, fresh_session,
            )
            static = (
                static
                + "\n\nThis instruction was explicitly delegated by another local Cyrene session. "
                "It is agent-originated context, not a human approval or an answer to a pending "
                "question. Do not delegate it to another session."
            ).strip()
            reply = await runtime._workbench_agent_reply(
                text,
                fresh_session,
                [],
                permission_mode="default",
                project_workspace=str(fresh_project.get("workspacePath") or ""),
                ephemeral_system=ephemeral,
                volatile_ephemeral_system=runtime._workbench_compose_volatile_ephemeral_system(
                    fresh_project, fresh_session,
                    memory_pair=memory_pair,
                ),
                static_system_extra=static,
                conversation_source="agent_session",
            )
            # The delegated run may have used typed tools that persisted task
            # metadata while the model was running. Re-read before committing
            # the response so this background delivery never overwrites those
            # newer authoritative changes with its pre-run snapshot.
            result_payload = runtime._read_workbench_store()
            result_project, result_session = runtime._workbench_find_session(
                result_payload, target_id,
            )
            if not result_project or not result_session:
                return
            reply, awaiting_user = runtime._workbench_apply_pending(
                result_session, target_id, reply,
            )
            finished_at = runtime._utc_now_iso()
            response_event = {
                "id": runtime._short_id("event"),
                "type": "AgentResponseEvent",
                "runId": run_id,
                "createdAt": finished_at,
                "body": reply,
                "agentOriginated": True,
                "originSessionId": origin_id,
            }
            run = {
                "id": run_id,
                "taskId": target_id,
                "userInput": text,
                "agentResponse": reply,
                "status": "waiting_for_user" if awaiting_user else "completed",
                "startedAt": started_at,
                "endedAt": finished_at,
                "events": [instruction_event, response_event],
                "fileChanges": [],
                "toolCalls": [],
                "artifacts": [],
                "attachments": [],
                "mode": "default",
                "agentOriginated": True,
                "originSessionId": origin_id,
                "error": None,
            }
            result_session["agentReply"] = reply
            result_session["status"] = "waiting_for_user" if awaiting_user else "acted"
            result_session["updatedAt"] = finished_at
            result_session.setdefault("runs", []).append(run)
            result_session.setdefault("events", []).append(response_event)
            result_project["updatedAt"] = finished_at
            runtime._write_workbench_store(result_payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            fresh_payload = runtime._read_workbench_store()
            fresh_project, fresh_session = runtime._workbench_find_session(
                fresh_payload, target_id,
            )
            if fresh_project and fresh_session:
                fresh_session["status"] = "idle"
                fresh_session["updatedAt"] = runtime._utc_now_iso()
                fresh_session.setdefault("events", []).append({
                    "id": runtime._short_id("event"),
                    "type": "AgentResponseErrorEvent",
                    "runId": run_id,
                    "createdAt": fresh_session["updatedAt"],
                    "body": str(exc)[:500],
                    "agentOriginated": True,
                })
                fresh_project["updatedAt"] = fresh_session["updatedAt"]
                runtime._write_workbench_store(fresh_payload)

    task = asyncio.create_task(_run_task_message())
    _track_background_session_task(task)
    return {"status": "started", "session_id": target_id, "run_id": run_id}


__all__ = [
    "activate_project", "compact_chat", "create_chat", "create_project", "delete_chat",
    "delete_project", "fork_chat", "list_chat_groups", "list_chats", "list_projects",
    "dispatch_session_message", "manage_chat_group", "read_chat", "read_project",
    "rename_chat", "update_project",
]
