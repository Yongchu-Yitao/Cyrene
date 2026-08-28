"""Typed local application services used by Cyrene self-management tools.

These functions operate on the same repositories as Workbench without going
through HTTP routes.  Route migration can call these functions incrementally.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from cyrene.config import WORKSPACE_DIR, cyrene_dir
from cyrene.localization import localized
from cyrene.workbench import project_repository, project_runtime
from cyrene.workbench.chat_repository import ChatRepository

_BACKGROUND_SESSION_TASKS: set[asyncio.Task[Any]] = set()
logger = logging.getLogger(__name__)


def _track_background_session_task(task: asyncio.Task[Any]) -> None:
    _BACKGROUND_SESSION_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_SESSION_TASKS.discard)


def _plugin_host_values() -> tuple[Any, str]:
    """Resolve process services from the active Plugin invocation/host."""

    from agent.plugin import active_plugin_application_host
    from agent.plugin.execution import current_plugin_execution

    execution = current_plugin_execution()
    host = active_plugin_application_host()
    data = execution.context.data if execution is not None else {}
    bot = data.get("bot") if isinstance(data, Mapping) else None
    db_path = str(data.get("db_path") or "") if isinstance(data, Mapping) else ""
    if host is not None:
        bot = bot if bot is not None else host.bot
        db_path = db_path or str(host.db_path or "")
    if not db_path:
        raise RuntimeError(localized(
            "The Cyrene Plugin application host is not configured.",
            "尚未配置 Cyrene 插件应用宿主。",
        ))
    return bot, db_path


def _active_workspace_dir() -> Path:
    from agent.plugin.execution import current_plugin_execution

    execution = current_plugin_execution()
    if execution is None or execution.context.workspace is None:
        raise RuntimeError(localized(
            "Cyrene project tools require an active Plugin workspace.",
            "Cyrene 项目工具需要活动的插件工作区。",
        ))
    return Path(execution.context.workspace).expanduser().resolve()


def _chat_repository() -> ChatRepository:
    _bot, db_path = _plugin_host_values()
    return ChatRepository(db_path)


def _chat_application_port() -> Any:
    """Return the Workbench Chat port published by route composition.

    Cyrene application Plugins must share the exact same run manager and
    ConversationRuntime as the Workbench UI.  Falling back to the retired
    module singleton would create a second execution path and make deletion or
    cross-session dispatch race the authoritative route-owned run.
    """

    from agent.plugin import active_plugin_application_host

    host = active_plugin_application_host()
    port = host.service("workbench_chat") if host is not None else None
    if port is None:
        raise RuntimeError(localized(
            "The Workbench Chat application service is not configured.",
            "尚未配置工作台对话应用服务。",
        ))
    return port


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_id(prefix: str) -> str:
    return f"{str(prefix or 'id')}_{uuid4().hex[:10]}"


def _new_chat_record(project_id: str, title: str, model: str) -> dict[str, Any]:
    from agent.plugin import active_plugin_service

    composer = active_plugin_service("composer_context")
    defaults = getattr(composer, "default_input_context", None)
    if not callable(defaults):
        raise RuntimeError(localized(
            "The required composer_context Plugin is unavailable.",
            "所需的 composer_context 插件不可用。",
        ))
    input_context = defaults()

    memory = active_plugin_service("memory")
    snapshot_loader = getattr(memory, "current_snapshot", None)
    memory_snapshot = None
    if callable(snapshot_loader):
        loaded = snapshot_loader(str(project_id or ""))
        if isinstance(loaded, Mapping):
            memory_snapshot = deepcopy(dict(loaded))

    now = _utc_now_iso()
    normalized_title = str(title or "").strip()
    chat = {
        "id": _short_id("wbchat"),
        "projectId": str(project_id or ""),
        "kind": "chat",
        "title": normalized_title[:60] or localized("New chat", "新对话"),
        "titleLocked": bool(normalized_title),
        "status": "idle",
        "model": str(model or ""),
        "permissionMode": "auto",
        "createdAt": now,
        "updatedAt": now,
        "messages": [],
        "completedTurnCount": 0,
        "soulActive": bool(input_context["soulActive"]),
        "workspaceActive": bool(input_context["workspaceActive"]),
        "remoteDeviceIds": list(input_context["remoteDeviceIds"]),
        "contextActivations": dict(input_context["contextActivations"]),
    }
    if memory_snapshot is not None:
        chat["projectMemorySnapshot"] = memory_snapshot
    return chat


def _public_message(message: Any) -> Any:
    if not isinstance(message, Mapping):
        return message
    if bool(message.get("hidden_from_ui")) or str(
        message.get("record_kind") or ""
    ).strip() in {"execution_handoff", "runtime_checkpoint", "context_compaction"}:
        return None
    payload = {
        key: deepcopy(value)
        for key, value in message.items()
        if key != "agentAttachments"
    }
    if (
        str(payload.get("role") or "") == "assistant"
        and not str(payload.get("content") or "").strip()
        and (str(payload.get("reasoning") or "").strip() or payload.get("trace"))
    ):
        payload.setdefault("activityCard", True)
    return payload


def _public_chat_full(chat: Mapping[str, Any]) -> dict[str, Any]:
    payload = {
        key: deepcopy(value)
        for key, value in chat.items()
        if key not in {"_messageProjection", "messages", "projectMemorySnapshot"}
    }
    snapshot = chat.get("projectMemorySnapshot")
    if isinstance(snapshot, Mapping):
        payload.update({
            "projectMemoryEnabled": True,
            "projectMemoryModifiedAt": str(snapshot.get("modifiedAt") or ""),
            "projectMemoryHash": str(snapshot.get("hash") or ""),
        })
    else:
        payload["projectMemoryEnabled"] = False
    public_messages = [
        public
        for item in chat.get("messages") or ()
        if (public := _public_message(item)) is not None
    ]
    public_messages.sort(key=lambda item: str(item.get("createdAt") or ""))
    payload["messages"] = public_messages
    payload["messageCount"] = len(public_messages)
    payload["files"] = [
        deepcopy(item)
        for item in chat.get("generatedFiles") or ()
        if isinstance(item, Mapping) and str(item.get("path") or "").strip()
    ]
    return payload


def list_projects() -> list[dict[str, Any]]:
    payload = project_repository._read_workbench_store()
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
    project = project_repository._workbench_find_project(
        project_repository._read_workbench_store(), project_id
    )
    if not project:
        raise LookupError(localized("Project not found.", "未找到项目。"))
    return deepcopy(project)


def create_project(name: str, *, description: str = "", workspace_path: str = "") -> dict[str, Any]:
    payload = project_repository._read_workbench_store()
    now = project_runtime._utc_now_iso()
    project_id = project_runtime._short_id("project")
    root = _active_workspace_dir()
    target = Path(workspace_path).expanduser() if workspace_path else cyrene_dir(root) / "projects" / project_id
    if not target.is_absolute():
        target = root / target
    target = target.resolve(strict=False)
    if not target.is_relative_to(root):
        raise ValueError(localized(
            "Agent-created project workspaces must stay inside the active workspace.",
            "智能体创建的项目工作区必须位于当前活动工作区内。",
        ))
    target.mkdir(parents=True, exist_ok=True)
    project_name = str(name or target.name or localized(
        "New project", "新项目"
    )).strip()[:120]
    if not project_name:
        raise ValueError(localized("Project name is required.", "必须提供项目名称。"))
    project = {
        "id": project_id,
        "name": project_name,
        "dataKey": project_runtime._safe_workbench_data_key(project_id),
        "description": str(description or "").strip()[:4000],
        "icon": "spark",
        "color": "",
        "template": "blank",
        "workspacePath": str(target),
        "workspacePathSource": "generated" if not workspace_path else "agent_scoped",
        "status": "active",
        "model": project_runtime._get_model(),
        "accountTier": "Pro",
        "context": {
            "summary": str(description or localized(
                "Workspace at {path}",
                "工作区位于 {path}",
                path=target,
            )).strip(),
            "stack": [],
            "decisions": [],
            "knowledgeDocumentIds": [],
        },
        "createdAt": now,
        "updatedAt": now,
        "sessions": [],
        "sharedArtifacts": [],
    }
    session = project_runtime._workbench_new_init_session(project_id, project, now)
    project["sessions"] = [session]
    payload.setdefault("projects", []).insert(0, project)
    payload["activeProjectId"] = project_id
    payload["activeSessionId"] = session["id"]
    project_repository._write_workbench_store(payload)
    return {"project": deepcopy(project), "session": deepcopy(session)}


def update_project(project_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    payload = project_repository._read_workbench_store()
    project = project_repository._workbench_find_project(payload, project_id)
    if not project:
        raise LookupError(localized("Project not found.", "未找到项目。"))
    allowed = {"name", "description", "icon", "color", "status", "model"}
    unknown = sorted(set(changes) - allowed)
    if unknown:
        raise ValueError(localized(
            "The request contains unsupported project fields: {fields}",
            "请求包含不支持的项目字段：{fields}",
            fields=", ".join(unknown),
        ))
    for key, value in changes.items():
        if key in {"name", "description"}:
            value = str(value or "").strip()
        project[key] = value
    project["updatedAt"] = project_runtime._utc_now_iso()
    project_repository._write_workbench_store(payload)
    return deepcopy(project)


def activate_project(project_id: str, session_id: str = "") -> dict[str, Any]:
    return project_repository._persist_workbench_selection(project_id, session_id)


async def delete_project(project_id: str) -> dict[str, Any]:
    bot, db_path = _plugin_host_values()
    payload = project_repository._read_workbench_store()
    project = project_repository._workbench_find_project(payload, project_id)
    if not project:
        raise LookupError(localized("Project not found.", "未找到项目。"))
    session_ids = [str(item.get("id") or "") for item in project.get("sessions", []) if item.get("id")]
    chat_repository = ChatRepository(db_path)
    chat_payload = chat_repository.read()
    project_chats = [
        item
        for item in chat_payload.get("chats", [])
        if isinstance(item, dict)
        and str(item.get("projectId") or "") == project_id
    ]
    removed_chat_ids = [
        str(item.get("id") or "") for item in project_chats if item.get("id")
    ]

    # Stop every execution owner before making either project or chat deletion
    # durable. A failed termination leaves the records intact and recoverable.
    chat_port = _chat_application_port()
    project_chat_id_set = set(removed_chat_ids)
    root_chat_ids = [
        str(item.get("id") or "")
        for item in project_chats
        if str(item.get("kind") or "chat") != "side-agent"
        or str(item.get("parentChatId") or "") not in project_chat_id_set
    ]
    for chat_id in root_chat_ids:
        await chat_port.delete(chat_id)
    from agent.workbench.task_runtime import TaskAgentRuntime
    from cyrene.workbench import task_runs

    agent_runtime = TaskAgentRuntime(bot=bot, db_path=db_path)
    for session_id in session_ids:
        coordinator = task_runs.coordinator_for(db_path)
        lease = coordinator.get("task", session_id)
        task_runs.interrupt_task_run(db_path, session_id)
        if (
            lease is not None
            and lease.task is not None
            and lease.task is not asyncio.current_task()
        ):
            await asyncio.gather(lease.task, return_exceptions=True)
        await agent_runtime.clear_session(session_id)

    # Cancellation finalizers may have updated other projects while we waited.
    # Remove from a fresh snapshot instead of writing the pre-cancel view back.
    payload = project_repository._read_workbench_store()
    payload["projects"] = [item for item in payload.get("projects", []) if str(item.get("id") or "") != project_id]
    if not payload["projects"]:
        payload = project_runtime._workbench_default_project()
    else:
        payload["activeProjectId"] = payload["projects"][0]["id"]
        sessions = payload["projects"][0].get("sessions") or []
        payload["activeSessionId"] = str(sessions[0].get("id") or "") if sessions else ""
    project_repository._write_workbench_store(payload)
    # A malformed historical store may contain an orphan side-agent. Remove
    # those records and their ContextTrees without routing through a missing
    # parent chat.
    from agent.workbench.conversation_runtime import ConversationRuntime

    conversation_runtime = ConversationRuntime(db_path)
    for chat_id in removed_chat_ids:
        await asyncio.to_thread(conversation_runtime.delete_context, chat_id)
    chat_payload = chat_repository.read()
    chat_payload["chats"] = [
        item
        for item in chat_payload.get("chats", [])
        if str(item.get("projectId") or "") != project_id
    ]
    chat_repository.write(chat_payload)
    return {"deletedProjectId": project_id, "deletedSessionIds": session_ids, "deletedChatIds": removed_chat_ids}


def list_chats(project_id: str = "") -> list[dict[str, Any]]:
    from cyrene.workbench.session_presentation import WorkbenchSessionPresentation

    _bot, db_path = _plugin_host_values()
    return [
        item
        for item in WorkbenchSessionPresentation(db_path).list()
        if not project_id or str(item.get("projectId") or "") == project_id
    ]


def read_chat(chat_id: str) -> dict[str, Any]:
    chat = _chat_repository().get(chat_id)
    if not chat:
        raise LookupError(localized("Conversation not found.", "未找到对话。"))
    return _public_chat_full(chat)


def create_chat(project_id: str, title: str = "") -> dict[str, Any]:
    if not project_repository._workbench_find_project(
        project_repository._read_workbench_store(), project_id
    ):
        raise LookupError(localized("Project not found.", "未找到项目。"))
    repository = _chat_repository()
    payload = repository.read()
    chat = _new_chat_record(
        project_id,
        str(title or "").strip()[:80],
        project_runtime._get_model(),
    )
    payload.setdefault("chats", []).insert(0, chat)
    repository.write(payload)
    return _public_chat_full(chat)


def rename_chat(chat_id: str, title: str) -> dict[str, Any]:
    normalized = str(title or "").strip()[:60]
    if not normalized:
        raise ValueError(localized("Chat title is required.", "必须提供对话标题。"))

    def rename(chat: dict[str, Any]) -> None:
        chat["title"] = normalized
        chat["titleLocked"] = True
        chat["updatedAt"] = _utc_now_iso()

    chat = _chat_repository().mutate_one(chat_id, rename)
    if not chat:
        raise LookupError(localized("Conversation not found.", "未找到对话。"))
    return _public_chat_full(chat)


async def compact_chat(chat_id: str) -> dict[str, Any]:
    from agent.workbench.conversation_runtime import ConversationConfig, ConversationRuntime

    chat = read_chat(chat_id)
    model = str(chat.get("model") or project_runtime._get_model() or "")
    project_id = str(chat.get("projectId") or "")
    project = project_repository.find_workbench_project_lightweight(project_id)
    bot, db_path = _plugin_host_values()
    workspace_dir = str(
        chat.get("workspaceOverride")
        or (project or {}).get("workspacePath")
        or WORKSPACE_DIR
    )
    compact_config = ConversationConfig(
        session_id=str(chat_id),
        workspace_dir=workspace_dir,
        db_path=db_path,
        bot=bot,
        project_id=project_id,
        session_title=str(chat.get("title") or ""),
        remote_device_ids=tuple(
            str(item or "").strip()
            for item in (chat.get("remoteDeviceIds") or ())
            if str(item or "").strip()
        ),
        completed_turn_count=max(0, int(chat.get("completedTurnCount") or 0)),
    )
    if _chat_application_port().run_manager.get(chat_id) is not None:
        raise ValueError(localized(
            "The conversation is currently running.",
            "对话当前正在运行。",
        ))
    return await ConversationRuntime(db_path).compact(
        compact_config,
        context_limit=project_runtime._ctx_limit_for_model(model) or 128_000,
    )


async def delete_chat(chat_id: str) -> dict[str, Any]:
    repository = _chat_repository()
    payload = repository.read()
    chat = repository.find(payload, chat_id)
    if not chat:
        raise LookupError(localized("Conversation not found.", "未找到对话。"))
    removed = {chat_id, *[
        str(item.get("id") or "") for item in payload.get("chats", [])
        if str(item.get("kind") or "") == "side-agent" and str(item.get("parentChatId") or "") == chat_id
    ]}
    await _chat_application_port().delete(chat_id)
    return {"deletedChatIds": sorted(removed)}


async def fork_chat(chat_id: str, message_id: str, content: str) -> dict[str, Any]:
    from agent.workbench.conversation_runtime import ConversationRuntime

    _bot, db_path = _plugin_host_values()
    repository = ChatRepository(db_path)
    payload = repository.read()
    source = repository.find(payload, chat_id)
    if not source:
        raise LookupError(localized("Conversation not found.", "未找到对话。"))
    messages = source.get("messages") if isinstance(source.get("messages"), list) else []
    index = next((i for i, item in enumerate(messages) if str(item.get("id") or "") == message_id), -1)
    if index < 0 or str(messages[index].get("role") or "") != "user":
        raise ValueError(localized(
            "Forking requires an existing user message.",
            "分叉对话需要选择一条已有的用户消息。",
        ))
    text = str(content or "").strip()
    if not text:
        raise ValueError(localized("Fork content is required.", "必须提供分叉内容。"))
    forked = _new_chat_record(
        str(source.get("projectId") or ""),
        str(source.get("title") or ""),
        str(source.get("model") or ""),
    )
    if isinstance(source.get("projectMemorySnapshot"), Mapping):
        forked["projectMemorySnapshot"] = deepcopy(
            dict(source["projectMemorySnapshot"])
        )
    forked["forkedFromChatId"] = chat_id
    forked["forkedAtMessageId"] = message_id
    forked["forkMessage"] = text.replace("\n", " ")[:80]
    entry = {"id": _short_id("msg"), "role": "user", "content": text, "createdAt": _utc_now_iso()}
    if isinstance(messages[index].get("attachments"), list):
        entry["attachments"] = deepcopy(messages[index]["attachments"])
    forked["messages"] = [deepcopy(item) for item in messages[:index]] + [entry]
    payload.setdefault("chats", []).insert(0, forked)
    repository.write(payload)
    user_ordinal = sum(
        1
        for item in messages[: index + 1]
        if isinstance(item, Mapping) and str(item.get("role") or "") == "user"
    )
    try:
        await asyncio.to_thread(
            ConversationRuntime(db_path).fork_context,
            chat_id,
            str(forked["id"]),
            user_ordinal=user_ordinal,
        )
    except Exception:
        # Public metadata and ContextTree form one logical branch.  If the
        # durable tree cannot be copied, remove the half-created chat.
        fresh = repository.read()
        fresh["chats"] = [
            item
            for item in fresh.get("chats", [])
            if str(item.get("id") or "") != str(forked["id"])
        ]
        repository.write(fresh)
        raise
    return _public_chat_full(forked)


def list_chat_groups(project_id: str) -> dict[str, Any]:
    if not project_repository._workbench_find_project(
        project_repository._read_workbench_store(), project_id
    ):
        raise LookupError(localized("Project not found.", "未找到项目。"))
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
        raise ValueError(localized(
            "The chat group contains an unknown conversation.",
            "对话组中包含未知对话。",
        ))

    if normalized_operation == "group":
        if len(requested_ids) < 2:
            raise ValueError(localized(
                "A group requires at least two conversations.",
                "对话组至少需要包含两个对话。",
            ))
        for group in current:
            group["chatIds"] = [item for item in group.get("chatIds", []) if str(item) not in requested_ids]
        current = [group for group in current if len(group.get("chatIds", [])) >= 2]
        current.append({
            "id": target_group_id,
            "title": str(title or localized(
                "New chat group", "新对话组"
            )).strip()[:60] or localized("New chat group", "新对话组"),
            "titleLocked": bool(str(title or "").strip()),
            "chatIds": requested_ids,
        })
    elif normalized_operation == "ungroup":
        if not target_group_id:
            raise ValueError(localized("group_id is required.", "必须提供 group_id。"))
        if not any(str(group.get("id") or "") == target_group_id for group in current):
            raise LookupError(localized("Chat group not found.", "未找到对话组。"))
        current = [group for group in current if str(group.get("id") or "") != target_group_id]
    elif normalized_operation == "rename_group":
        normalized_title = str(title or "").strip()[:60]
        if not target_group_id or not normalized_title:
            raise ValueError(localized(
                "group_id and title are required.",
                "必须提供 group_id 和标题。",
            ))
        target = next((group for group in current if str(group.get("id") or "") == target_group_id), None)
        if target is None:
            raise LookupError(localized("Chat group not found.", "未找到对话组。"))
        target["title"] = normalized_title
        target["titleLocked"] = True
    else:
        raise ValueError(localized(
            "Unsupported chat group operation.",
            "不支持此对话组操作。",
        ))

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
        raise ValueError(localized(
            "The current composer is not a supported session.",
            "当前输入区不属于受支持的会话。",
        ))
    if not target_id or not text:
        raise ValueError(localized(
            "Target session and message are required.",
            "必须提供目标会话和消息。",
        ))
    if len(text) > 20_000:
        raise ValueError(localized("The session message is too long.", "会话消息过长。"))
    if target_id == origin_id:
        raise ValueError(localized(
            "An agent cannot dispatch a second run into its own active session.",
            "智能体不能向自身的活动会话再次分派运行。",
        ))
    bot, db_path = _plugin_host_values()
    if kind == "chat":
        outcome = await _chat_application_port().dispatch_agent_message(
            target_id,
            text,
            origin_session_id=origin_id,
        )
        if outcome.get("status") not in {"started", "guided"}:
            raise ValueError(localized(
                "The target conversation is unavailable.",
                "目标对话不可用。",
            ))
        return outcome

    from agent.workbench.task_runtime import TaskAgentRuntime
    from cyrene.workbench import task_runs

    coordinator = task_runs.coordinator_for(db_path)
    if task_runs.is_task_run_active(db_path, target_id):
        raise ValueError(localized(
            "The target task already has a running agent.",
            "目标任务已有智能体正在运行。",
        ))
    payload = project_repository._read_workbench_store()
    project, session = project_repository._workbench_find_session(payload, target_id)
    if not project or not session:
        raise LookupError(localized("Task session not found.", "未找到任务会话。"))
    if session.get("pendingQuestion"):
        raise ValueError(localized(
            "The target task is waiting for a user or delegated answer.",
            "目标任务正在等待用户或委派回复。",
        ))
    run_id = project_runtime._short_id("run")
    request_id = (
        f"agent-session:{origin_id}:{target_id}:"
        f"{hashlib.sha256(text.encode('utf-8')).hexdigest()[:20]}"
    )
    lease = coordinator.try_acquire(
        "task",
        target_id,
        run_id,
        request_id=request_id,
        run_type="agent_session",
        bind_current_task=False,
        metadata={"origin_session_id": origin_id},
    )
    if lease is None:
        raise ValueError(localized(
            "The target task already has a running agent.",
            "目标任务已有智能体正在运行。",
        ))
    if not task_runs.begin_task_run(
        target_id,
        run_id,
        request_id=request_id,
        run_type="agent_session",
        body={
            "message": text,
            "mode": "default",
            "clientRequestId": request_id,
            "agentOriginated": True,
            "originSessionId": origin_id,
        },
    ):
        coordinator.release(lease)
        raise LookupError(localized("Task session not found.", "未找到任务会话。"))

    payload = project_repository._read_workbench_store()
    project, session = project_repository._workbench_find_session(payload, target_id)
    if not project or not session:
        coordinator.release(lease)
        raise LookupError(localized("Task session not found.", "未找到任务会话。"))
    started_at = project_runtime._utc_now_iso()
    instruction_event = {
        "id": project_runtime._short_id("event"),
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
    run = next(
        (
            item
            for item in session.get("runs") or []
            if isinstance(item, dict) and str(item.get("id") or "") == run_id
        ),
        None,
    )
    if run is not None:
        run.setdefault("events", []).append(instruction_event)
    project_repository._write_workbench_store(payload)

    agent_runtime = TaskAgentRuntime(bot=bot, db_path=db_path)

    async def _run_task_message() -> None:
        token = task_runs.bind_task_run_id(run_id)
        terminal_status = "failed"
        try:
            fresh_payload = project_repository._read_workbench_store()
            fresh_project, fresh_session = project_repository._workbench_find_session(
                fresh_payload, target_id,
            )
            if not fresh_project or not fresh_session:
                return
            agent_result = await agent_runtime.run_turn(
                project=fresh_project,
                session=fresh_session,
                text=text,
                run_id=run_id,
                permission_mode="default",
                client_request_id=request_id,
                purpose="agent_session",
                instruction=(
                    "This instruction was explicitly delegated by another local Cyrene "
                    "session. It is agent-originated context, not human approval and not "
                    "an answer to a pending question. Do not delegate it to another session."
                ),
                metadata={
                    "agent_originated": True,
                    "origin_session_id": origin_id,
                    "conversation_source": "agent_session",
                },
            )
            # The delegated run may have used typed tools that persisted task
            # metadata while the model was running. Re-read before committing
            # the response so this background delivery never overwrites those
            # newer authoritative changes with its pre-run snapshot.
            result_payload = project_repository._read_workbench_store()
            result_project, result_session = project_repository._workbench_find_session(
                result_payload, target_id,
            )
            if not result_project or not result_session:
                return
            reply = str(agent_result.text or "")
            awaiting_user = bool(agent_result.awaiting_user)
            if agent_result.pending_question is not None:
                result_session["pendingQuestion"] = dict(agent_result.pending_question)
            else:
                result_session.pop("pendingQuestion", None)
            finished_at = project_runtime._utc_now_iso()
            response_event = {
                "id": project_runtime._short_id("event"),
                "type": "AgentResponseEvent",
                "runId": run_id,
                "createdAt": finished_at,
                "body": reply,
                "agentOriginated": True,
                "originSessionId": origin_id,
            }
            run = next(
                (
                    item
                    for item in result_session.get("runs") or []
                    if isinstance(item, dict) and str(item.get("id") or "") == run_id
                ),
                None,
            )
            if run is None:
                raise RuntimeError("delegated task run record disappeared")
            known_event_ids = {
                str(item.get("id") or "")
                for item in run.get("events") or []
                if isinstance(item, dict)
            }
            projected_events = [
                dict(item)
                for item in agent_result.tool_events
                if isinstance(item, dict)
                and str(item.get("id") or "") not in known_event_ids
            ]
            run.update({
                "agentResponse": reply,
                "status": "awaiting_user" if awaiting_user else "completed",
                "endedAt": finished_at,
                "agentOriginated": True,
                "originSessionId": origin_id,
                "error": None,
                "usage": dict(agent_result.usage),
                "model": str(agent_result.model or ""),
                "modelIdentity": dict(agent_result.model_identity),
                "toolCalls": [
                    {
                        "tool": event.get("tool"),
                        "argsPreview": event.get("argsPreview", ""),
                    }
                    for event in projected_events
                    if event.get("type") == "ToolCallEvent"
                ],
            })
            run.setdefault("events", []).extend([*projected_events, response_event])
            result_session["agentReply"] = reply
            result_session["status"] = "waiting_for_user" if awaiting_user else "acted"
            result_session["updatedAt"] = finished_at
            result_session.setdefault("events", []).extend([*projected_events, response_event])
            result_project["updatedAt"] = finished_at
            project_repository._write_workbench_store(result_payload)
            terminal_status = "awaiting_user" if awaiting_user else "completed"
            task_runs.finish_task_run_if_open(
                target_id,
                run_id,
                status=terminal_status,
            )
        except asyncio.CancelledError:
            terminal_status = "cancelled"
            task_runs.finish_task_run_if_open(
                target_id,
                run_id,
                status="cancelled",
                error=localized(
                    "The task instruction was cancelled.",
                    "任务指令已取消。",
                ),
                termination_reason=str(lease.termination_reason or "user_interrupted"),
            )
            raise
        except Exception:
            logger.exception(
                "Delegated task instruction failed [session=%s run=%s]",
                target_id,
                run_id,
            )
            safe_error = localized(
                "The delegated task instruction failed.",
                "委派的任务指令执行失败。",
            )
            task_runs.finish_task_run_if_open(
                target_id,
                run_id,
                status="failed",
                error=safe_error,
            )
            fresh_payload = project_repository._read_workbench_store()
            fresh_project, fresh_session = project_repository._workbench_find_session(
                fresh_payload, target_id,
            )
            if fresh_project and fresh_session:
                fresh_session["status"] = "idle"
                fresh_session["updatedAt"] = project_runtime._utc_now_iso()
                fresh_session.setdefault("events", []).append({
                    "id": project_runtime._short_id("event"),
                    "type": "AgentResponseErrorEvent",
                    "runId": run_id,
                    "createdAt": fresh_session["updatedAt"],
                    "body": safe_error,
                    "code": "delegated_task_run_failed",
                    "agentOriginated": True,
                })
                fresh_project["updatedAt"] = fresh_session["updatedAt"]
                project_repository._write_workbench_store(fresh_payload)
        finally:
            task_runs.reset_task_run_id(token)
            coordinator.finish(
                lease,
                status=terminal_status,
                termination_reason=str(lease.termination_reason or ""),
            )

    task = asyncio.create_task(_run_task_message())
    if not coordinator.attach_task(lease, task):
        task.cancel()
        coordinator.release(lease)
        raise ValueError(localized(
            "The target task run could not be started.",
            "无法启动目标任务运行。",
        ))
    _track_background_session_task(task)
    return {"status": "started", "session_id": target_id, "run_id": run_id}


__all__ = [
    "activate_project", "compact_chat", "create_chat", "create_project", "delete_chat",
    "delete_project", "fork_chat", "list_chat_groups", "list_chats", "list_projects",
    "dispatch_session_message", "manage_chat_group", "read_chat", "read_project",
    "rename_chat", "update_project",
]
