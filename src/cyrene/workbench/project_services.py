"""Project repositories, lifecycle ports, and application operations."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyrene.localization import app_language, localized

logger = logging.getLogger(__name__)


class ProjectNotFoundError(LookupError):
    pass


class ProjectOperationError(RuntimeError):
    def __init__(self, message: str, status_code: int, code: str) -> None:
        self.message = message
        self.status_code = status_code
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ProjectRouteDependencies:
    """Explicit owner functions used to compose project services."""

    get_model: Callable[[], str]
    persist_selection: Callable[[str | None, str | None], dict[str, Any]]
    read_store: Callable[[], dict[str, Any]]
    read_store_lightweight: Callable[[], dict[str, Any]]
    safe_data_key: Callable[[str], str]
    short_id: Callable[[str], str]
    utc_now: Callable[[], str]
    default_init_form: Callable[[dict[str, Any]], dict[str, Any]]
    default_project: Callable[[], dict[str, Any]]
    find_project: Callable[[dict[str, Any], str], dict[str, Any] | None]
    find_project_lightweight: Callable[[str], dict[str, Any] | None]
    find_session: Callable[
        [dict[str, Any], str],
        tuple[dict[str, Any] | None, dict[str, Any] | None],
    ]
    follow_up_seed: Callable[..., dict[str, Any]]
    generate_init_form: Callable[..., Awaitable[dict[str, Any] | None]]
    lightweight_store: Callable[[dict[str, Any]], dict[str, Any]]
    new_init_session: Callable[..., dict[str, Any]]
    new_session: Callable[..., dict[str, Any]]
    project_data_key: Callable[[dict[str, Any]], str]
    project_resource_key: Callable[[dict[str, Any]], str]
    resolve_workspace: Callable[..., Path]
    resolve_workspace_async: Callable[..., Awaitable[Path]]
    write_store: Callable[..., None]
    append_notification: Callable[..., Any]
    list_notifications: Callable[..., dict[str, Any]]
    mark_notifications_read: Callable[..., dict[str, Any]]

    @classmethod
    def from_modules(
        cls,
        *,
        generate_init_form: Callable[..., Awaitable[dict[str, Any] | None]],
    ) -> ProjectRouteDependencies:
        """Compose projects from their concrete repositories and domain helpers.

        The HTTP composition root uses this constructor so deleting the retired
        ``cyrene.workbench.runtime`` facade cannot change project behavior.
        """

        from cyrene.workbench import (
            notifications,
            planning_runtime,
            project_repository,
            project_runtime,
        )

        return cls(
            get_model=project_runtime._get_model,
            persist_selection=project_repository._persist_workbench_selection,
            read_store=project_repository._read_workbench_store,
            read_store_lightweight=project_repository._read_workbench_store_lightweight,
            safe_data_key=project_runtime._safe_workbench_data_key,
            short_id=project_runtime._short_id,
            utc_now=project_runtime._utc_now_iso,
            default_init_form=project_runtime._workbench_default_init_form,
            default_project=project_runtime._workbench_default_project,
            find_project=project_repository._workbench_find_project,
            find_project_lightweight=(
                project_repository.find_workbench_project_lightweight
            ),
            find_session=project_repository._workbench_find_session,
            follow_up_seed=planning_runtime._workbench_follow_up_seed,
            generate_init_form=generate_init_form,
            lightweight_store=project_repository._workbench_lightweight_store,
            new_init_session=project_runtime._workbench_new_init_session,
            new_session=project_runtime._workbench_new_session,
            project_data_key=project_runtime._workbench_project_data_key,
            project_resource_key=project_runtime._workbench_project_resource_key,
            resolve_workspace=project_repository.resolve_project_workspace_dir,
            resolve_workspace_async=(
                project_repository.resolve_project_workspace_dir_async
            ),
            write_store=project_repository._write_workbench_store,
            append_notification=notifications.append_notification,
            list_notifications=notifications.list_notifications,
            mark_notifications_read=notifications.mark_notifications_read,
        )


@dataclass(frozen=True, slots=True)
class ProjectLifecyclePort:
    workspace_root: Path
    validate_workspace: Callable[..., Path]
    get_model: Callable[[], str]
    safe_data_key: Callable[[str], str]
    short_id: Callable[[str], str]
    utc_now: Callable[[], str]
    default_init_form: Callable[[dict[str, Any]], dict[str, Any]]
    default_project: Callable[[], dict[str, Any]]
    follow_up_seed: Callable[..., dict[str, Any]]
    generate_init_form: Callable[..., Awaitable[dict[str, Any] | None]]
    new_init_session: Callable[..., dict[str, Any]]
    new_session: Callable[..., dict[str, Any]]
    project_data_key: Callable[[dict[str, Any]], str]
    project_resource_key: Callable[[dict[str, Any]], str]
    notify: Callable[..., Any]
    list_notifications: Callable[..., dict[str, Any]]
    mark_notifications_read: Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class AgentRunProjectPort:
    interrupt: Callable[..., Any]
    clear_session: Callable[..., Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class ChatProjectPort:
    list_project_chat_ids: Callable[[str], list[str]]
    remove_project: Callable[[str], Awaitable[int]]


@dataclass(frozen=True, slots=True)
class KnowledgeProjectPort:
    delete_database: Callable[[str], None]


@dataclass(frozen=True, slots=True)
class MemoryProjectPort:
    delete_workspace: Callable[[str], None]
    cancel_jobs: Callable[[str], Awaitable[None]]
    delete_project: Callable[[str, list[str]], None]


@dataclass(frozen=True, slots=True)
class ScheduleProjectPort:
    delete_project_tasks: Callable[[str], Awaitable[int]]


class ProjectRepository:
    """Persistence-facing project operations with no HTTP dependencies."""

    def __init__(
        self,
        *,
        read_store: Callable[[], dict[str, Any]],
        read_store_lightweight: Callable[[], dict[str, Any]],
        write_store: Callable[..., None],
        find_project: Callable[[dict[str, Any], str], dict[str, Any] | None],
        find_session: Callable[
            [dict[str, Any], str],
            tuple[dict[str, Any] | None, dict[str, Any] | None],
        ],
    ) -> None:
        self._read_store = read_store
        self._read_store_lightweight = read_store_lightweight
        self._write_store = write_store
        self._find_project = find_project
        self._find_session = find_session

    def read_store(self) -> dict[str, Any]:
        return self._read_store()

    def read_store_lightweight(self) -> dict[str, Any]:
        return self._read_store_lightweight()

    def write_store(self, payload: dict[str, Any], *, base_value: Any = None) -> None:
        if base_value is None:
            self._write_store(payload)
        else:
            self._write_store(payload, base_value=base_value)

    def get(self, project_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = self.read_store()
        project = self._find_project(payload, project_id)
        if not project:
            raise ProjectNotFoundError(localized("Project not found.", "未找到项目。"))
        return payload, project

    def get_session(
        self, session_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        payload = self.read_store()
        project, session = self._find_session(payload, session_id)
        if not project or not session:
            raise ProjectNotFoundError(localized("Task not found.", "未找到任务。"))
        return payload, project, session


class ProjectApplicationService:
    """Own project, task creation, initialization, and deletion workflows."""

    def __init__(
        self,
        repository: ProjectRepository,
        *,
        lifecycle: ProjectLifecyclePort,
        agent_runs: AgentRunProjectPort,
        chats: ChatProjectPort,
        knowledge: KnowledgeProjectPort,
        memory: MemoryProjectPort,
        schedules: ScheduleProjectPort,
        lightweight_store: Callable[[dict[str, Any]], dict[str, Any]],
        persist_selection: Callable[[str | None, str | None], dict[str, Any]],
    ) -> None:
        self.repository = repository
        self.lifecycle = lifecycle
        self.agent_runs = agent_runs
        self.chats = chats
        self.knowledge = knowledge
        self.memory = memory
        self.schedules = schedules
        self._lightweight_store = lightweight_store
        self._persist_selection = persist_selection

    async def list(self, detail: str = "full") -> dict[str, Any]:
        if str(detail or "").strip().lower() in {"summary", "light", "list"}:
            payload = await asyncio.to_thread(self.repository.read_store_lightweight)
            return self._lightweight_store(payload)
        return self.repository.read_store()

    async def activate(
        self, project_id: str | None, session_id: str | None
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._persist_selection,
            project_id,
            session_id,
        )

    def notifications(self, **filters: Any) -> dict[str, Any]:
        return self.lifecycle.list_notifications(**filters)

    def mark_notifications_read(
        self, ids: list[str], *, mark_all: bool
    ) -> dict[str, Any]:
        result = self.lifecycle.mark_notifications_read(ids, mark_all=mark_all)
        return {**result, **self.lifecycle.list_notifications(limit=80)}

    def sessions(self, project_id: str) -> list[dict[str, Any]]:
        _payload, project = self.repository.get(project_id)
        sessions = project.get("sessions")
        return sessions if isinstance(sessions, list) else []

    def create(self, body: Mapping[str, Any]) -> dict[str, Any]:
        payload = self.repository.read_store()
        now = self.lifecycle.utc_now()
        project_id = self.lifecycle.short_id("project")
        raw_workspace = str(body.get("workspacePath") or "").strip()
        source = "user" if raw_workspace else "generated"
        if not raw_workspace:
            raw_workspace = str(self.lifecycle.workspace_root / project_id)
        workspace_path = str(self.lifecycle.validate_workspace(raw_workspace, create=True))
        project = self._new_project(project_id, workspace_path, source, body, now)
        session = self.lifecycle.new_init_session(project_id, project, now)
        project["sessions"] = [session]
        payload.setdefault("projects", []).insert(0, project)
        payload["activeProjectId"] = project_id
        payload["activeSessionId"] = session["id"]
        self.repository.write_store(payload)
        language = app_language()
        self.lifecycle.notify(
            title=localized(
                "Project created", "项目创建完成", language=language
            ),
            body=localized(
                'Created workspace "{name}".',
                '已创建 workspace「{name}」。',
                language=language,
                name=project['name'],
            ),
            tab="system",
            project_ref=project_id,
            source="project_created",
            source_label=localized("Workspace", "工作区", language=language),
            link_label=project["name"],
            language=language,
        )
        return {"ok": True, "project": project, "session": session, **payload}

    def _new_project(
        self,
        project_id: str,
        workspace_path: str,
        workspace_source: str,
        body: Mapping[str, Any],
        now: str,
    ) -> dict[str, Any]:
        name = str(body.get("name") or Path(workspace_path).name or "New Project").strip()
        description = str(body.get("description") or "").strip()
        return {
            "id": project_id,
            "name": name,
            "dataKey": self.lifecycle.safe_data_key(project_id),
            "description": description,
            "icon": str(body.get("icon") or "spark").strip() or "spark",
            "color": str(body.get("color") or "").strip(),
            "template": str(body.get("template") or "blank").strip() or "blank",
            "workspacePath": workspace_path,
            "workspacePathSource": workspace_source,
            "status": "active",
            "model": self.lifecycle.get_model(),
            "accountTier": str(body.get("accountTier") or "Pro"),
            "context": {
                "summary": str(
                    body.get("summary")
                    or description
                    or f"Workspace at {workspace_path}"
                ),
                "stack": body.get("stack") if isinstance(body.get("stack"), list) else [],
                "decisions": [],
                "knowledgeDocumentIds": [],
            },
            "createdAt": now,
            "updatedAt": now,
            "sessions": [],
            "sharedArtifacts": [],
        }

    def update(self, project_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        payload, project = self.repository.get(project_id)
        values = dict(body)
        if "workspacePath" in values:
            values["workspacePath"] = str(
                self.lifecycle.validate_workspace(
                    str(values.get("workspacePath") or ""),
                    create=True,
                )
            )
            project["workspacePathSource"] = "user"
        fields = (
            "name", "description", "icon", "color", "template",
            "workspacePath", "status", "model", "accountTier",
        )
        for field in fields:
            if field in values:
                project[field] = values[field]
        if isinstance(values.get("context"), dict):
            project["context"] = {**(project.get("context") or {}), **values["context"]}
        project["updatedAt"] = self.lifecycle.utc_now()
        self.repository.write_store(payload)
        return {"ok": True, "project": project, **payload}

    async def delete(self, project_id: str) -> dict[str, Any]:
        payload, project = self.repository.get(project_id)
        projects = payload.get("projects", [])
        data_key = self.lifecycle.project_data_key(project)
        resource_key = self.lifecycle.project_resource_key(project)
        chat_ids = self.chats.list_project_chat_ids(project_id)
        await self._terminate_sessions(project)
        try:
            await self.chats.remove_project(project_id)
        except Exception as exc:
            logger.exception("Failed to remove chats for project %s", project_id)
            raise ProjectOperationError(
                localized(
                    "Project chat agents could not be terminated.",
                    "无法终止项目对话 Agent。",
                ),
                503,
                "project_chat_agents_not_terminated",
            ) from exc
        await self._cleanup_project_data(project_id, data_key, resource_key, chat_ids)
        base_payload = getattr(payload, "_workbench_base", None)
        payload["projects"] = [
            item for item in projects if str(item.get("id") or "") != project_id
        ]
        if not payload["projects"]:
            payload = self.lifecycle.default_project()
        else:
            payload["activeProjectId"] = payload["projects"][0].get("id")
            sessions = payload["projects"][0].get("sessions") or []
            payload["activeSessionId"] = sessions[0].get("id") if sessions else ""
        self.repository.write_store(payload, base_value=base_payload)
        return {"ok": True, **payload}

    async def _terminate_sessions(self, project: dict[str, Any]) -> None:
        for session in project.get("sessions") or []:
            session_id = str(session.get("id") or "").strip()
            if not session_id:
                continue
            try:
                self.agent_runs.interrupt(session_id=session_id)
                await self.agent_runs.clear_session(
                    session_id=session_id,
                )
            except Exception as exc:
                logger.exception("Failed to clear session state for %s", session_id)
                raise ProjectOperationError(
                    localized(
                        "Project agents could not be terminated.",
                        "无法终止项目 Agent。",
                    ),
                    503,
                    "project_agents_not_terminated",
                ) from exc

    async def _cleanup_project_data(
        self,
        project_id: str,
        data_key: str,
        resource_key: str,
        chat_ids: list[str],
    ) -> None:
        try:
            self.knowledge.delete_database(resource_key)
            self.memory.delete_workspace(resource_key)
            await self.memory.cancel_jobs(project_id)
            self.memory.delete_project(project_id, chat_ids)
            await self.schedules.delete_project_tasks(data_key)
        except Exception:
            logger.exception("Failed to remove project-scoped data for %s", project_id)

    def create_task(
        self, project_id: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        payload, project = self.repository.get(project_id)
        language = app_language()
        default_title = localized("New task", "新任务", language=language)
        title = str(body.get("title") or body.get("goal") or default_title).strip() or default_title
        session = self.lifecycle.new_session(
            project_id,
            title,
            str(body.get("goal") or "").strip(),
        )
        priority = str(body.get("priority") or "").strip()
        if priority in {"high", "medium", "low"}:
            session["priority"] = priority
        project.setdefault("sessions", []).insert(0, session)
        project["updatedAt"] = session["createdAt"]
        payload["activeProjectId"] = project_id
        payload["activeSessionId"] = session["id"]
        self.repository.write_store(payload)
        self.lifecycle.notify(
            title=localized(
                "New task created", "新任务已创建", language=language
            ),
            body=localized(
                'Task "{title}" was added to {workspace}.',
                '任务「{title}」已加入 {workspace}。',
                language=language,
                title=title,
                workspace=(
                    project.get('name')
                    or localized("workspace", "工作区", language=language)
                ),
            ),
            tab="comment",
            project_ref=project_id,
            source="task_created",
            source_label=localized("Task", "任务", language=language),
            link_label=title,
            meta={"sessionId": session["id"]},
            language=language,
        )
        return {"ok": True, "session": session, **payload}

    def create_follow_up(
        self, session_id: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        payload, project, source_session = self.repository.get_session(session_id)
        seed = self.lifecycle.follow_up_seed(
            source_session,
            requested_title=str(body.get("title") or "").strip(),
            requested_goal=str(body.get("goal") or "").strip(),
        )
        project_id = str(project.get("id") or "")
        session = self.lifecycle.new_session(project_id, seed["title"], seed["goal"])
        self._apply_follow_up(session, source_session, session_id, seed)
        project.setdefault("sessions", []).insert(0, session)
        project["updatedAt"] = session["createdAt"]
        payload["activeProjectId"] = project_id
        payload["activeSessionId"] = session["id"]
        self.repository.write_store(payload)
        language = app_language()
        source_title = source_session.get('title') or localized(
            "Task", "任务", language=language
        )
        self.lifecycle.notify(
            title=localized(
                "Follow-up task created", "后续任务已创建", language=language
            ),
            body=localized(
                'Created "{title}" from the current state of "{source}".',
                '已根据「{source}」的当前情况创建「{title}」。',
                language=language,
                source=source_title,
                title=session['title'],
            ),
            tab="comment",
            project_ref=project_id,
            source="follow_up_created",
            source_label=localized("Task", "任务", language=language),
            link_label=session["title"],
            meta={"sessionId": session["id"], "sourceSessionId": session_id},
            language=language,
        )
        return {"ok": True, "session": session, "sourceSessionId": session_id, **payload}

    def _apply_follow_up(
        self,
        session: dict[str, Any],
        source: dict[str, Any],
        source_id: str,
        seed: dict[str, Any],
    ) -> None:
        session["parentSessionId"] = source_id
        session["priority"] = seed["priority"]
        session["constraints"] = seed["constraints"]
        session["followUpContext"] = seed["context"]
        language = app_language()
        session["agentReply"] = localized(
            "A follow-up task was created from the source task's current progress. You can assign it to the Agent or add more requirements.",
            "已根据来源任务的当前进度创建后续任务。你可以直接交给 Agent，或继续补充要求。",
            language=language,
        )
        session["events"] = [{
            "id": self.lifecycle.short_id("event"),
            "type": "CreatedAsFollowUp",
            "createdAt": session["createdAt"],
            "body": localized(
                'Created from the current state of task "{title}".',
                '基于任务「{title}」的当前情况创建。',
                language=language,
                title=source.get('title') or localized(
                    "Task", "任务", language=language
                ),
            ),
            "sourceSessionId": source_id,
        }]
        for text in seed["unresolvedAcceptance"]:
            session["acceptanceCriteria"].append({
                "id": self.lifecycle.short_id("accept"),
                "text": text,
                "status": "pending",
            })

    async def generate_init(self, project_id: str, lang: str) -> dict[str, Any]:
        payload, project = self.repository.get(project_id)
        session = next(
            (
                item
                for item in project.get("sessions", [])
                if str(item.get("kind") or "") == "init"
            ),
            None,
        )
        if not session:
            raise ProjectNotFoundError(
                localized("Initialization task not found.", "未找到初始化任务。")
            )
        current = (
            session.get("init")
            if isinstance(session.get("init"), dict)
            else self.lifecycle.default_init_form(project)
        )
        generated = await self.lifecycle.generate_init_form(project, lang=lang)
        if generated:
            generated["answers"] = (
                current.get("answers") if isinstance(current.get("answers"), dict) else {}
            )
            generated["completed"] = bool(current.get("completed"))
            session["init"] = generated
            session["agentReply"] = generated.get("greeting") or session.get("agentReply") or ""
        else:
            current["generated"] = False
            session["init"] = current
        now = self.lifecycle.utc_now()
        session["updatedAt"] = now
        project["updatedAt"] = now
        payload["activeSessionId"] = session.get("id")
        self.repository.write_store(payload)
        return {"ok": True, "project": project, "session": session, **payload}


__all__ = [
    "AgentRunProjectPort",
    "ChatProjectPort",
    "KnowledgeProjectPort",
    "MemoryProjectPort",
    "ProjectApplicationService",
    "ProjectLifecyclePort",
    "ProjectNotFoundError",
    "ProjectOperationError",
    "ProjectRepository",
    "ProjectRouteDependencies",
    "ScheduleProjectPort",
]
