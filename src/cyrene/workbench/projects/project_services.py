"""Project repositories, lifecycle ports, and application operations."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyrene.localization import app_language, localized
from cyrene.workbench.projects.project_execution import normalize_execution_actions

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
    persist_selection: Callable[[str | None], dict[str, Any]]
    read_store: Callable[[], dict[str, Any]]
    read_store_lightweight: Callable[[], dict[str, Any]]
    safe_data_key: Callable[[str], str]
    short_id: Callable[[str], str]
    utc_now: Callable[[], str]
    default_project: Callable[[], dict[str, Any]]
    find_project: Callable[[dict[str, Any], str], dict[str, Any] | None]
    find_project_lightweight: Callable[[str], dict[str, Any] | None]
    lightweight_store: Callable[[dict[str, Any]], dict[str, Any]]
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
    ) -> ProjectRouteDependencies:
        """Compose projects from their concrete repositories and domain helpers.

        The HTTP composition root uses this constructor so deleting the retired
        ``cyrene.workbench.runtime`` facade cannot change project behavior.
        """

        from cyrene.workbench.application import notifications
        from cyrene.workbench.projects import project_repository, project_runtime

        return cls(
            get_model=project_runtime._get_model,
            persist_selection=project_repository._persist_workbench_selection,
            read_store=project_repository._read_workbench_store,
            read_store_lightweight=project_repository._read_workbench_store_lightweight,
            safe_data_key=project_runtime._safe_workbench_data_key,
            short_id=project_runtime._short_id,
            utc_now=project_runtime._utc_now_iso,
            default_project=project_runtime._workbench_default_project,
            find_project=project_repository._workbench_find_project,
            find_project_lightweight=(
                project_repository.find_workbench_project_lightweight
            ),
            lightweight_store=project_repository._workbench_lightweight_store,
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
    default_project: Callable[[], dict[str, Any]]
    project_data_key: Callable[[dict[str, Any]], str]
    project_resource_key: Callable[[dict[str, Any]], str]
    notify: Callable[..., Any]
    list_notifications: Callable[..., dict[str, Any]]
    mark_notifications_read: Callable[..., dict[str, Any]]


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
    ) -> None:
        self._read_store = read_store
        self._read_store_lightweight = read_store_lightweight
        self._write_store = write_store
        self._find_project = find_project

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

class ProjectApplicationService:
    """Own Conversation-native project lifecycle workflows."""

    def __init__(
        self,
        repository: ProjectRepository,
        *,
        lifecycle: ProjectLifecyclePort,
        chats: ChatProjectPort,
        knowledge: KnowledgeProjectPort,
        memory: MemoryProjectPort,
        schedules: ScheduleProjectPort,
        lightweight_store: Callable[[dict[str, Any]], dict[str, Any]],
        persist_selection: Callable[[str | None], dict[str, Any]],
    ) -> None:
        self.repository = repository
        self.lifecycle = lifecycle
        self.chats = chats
        self.knowledge = knowledge
        self.memory = memory
        self.schedules = schedules
        self._lightweight_store = lightweight_store
        self._persist_selection = persist_selection

    async def list(self, detail: str = "full") -> dict[str, Any]:
        del detail
        payload = await asyncio.to_thread(self.repository.read_store_lightweight)
        return self._lightweight_store(payload)

    async def activate(self, project_id: str | None) -> dict[str, Any]:
        payload = await asyncio.to_thread(
            self._persist_selection,
            project_id,
        )
        return self._lightweight_store(payload)

    def notifications(self, **filters: Any) -> dict[str, Any]:
        return self.lifecycle.list_notifications(**filters)

    def mark_notifications_read(
        self, ids: list[str], *, mark_all: bool
    ) -> dict[str, Any]:
        result = self.lifecycle.mark_notifications_read(ids, mark_all=mark_all)
        return {**result, **self.lifecycle.list_notifications(limit=80)}

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
        payload.setdefault("projects", []).insert(0, project)
        payload["activeProjectId"] = project_id
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
        projected = self._lightweight_store(payload)
        projected_project = next(
            item for item in projected["projects"] if item.get("id") == project_id
        )
        return {"ok": True, "project": projected_project, **projected}

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
        execution_scope = str(body.get("executionScope") or ".").strip().replace("\\", "/") or "."
        execution_scope_path = Path(execution_scope)
        if execution_scope_path.is_absolute() or ".." in execution_scope_path.parts:
            raise ValueError("executionScope must stay inside the project workspace")
        return {
            "id": project_id,
            "name": name,
            "dataKey": self.lifecycle.safe_data_key(project_id),
            "description": description,
            "icon": str(body.get("icon") or "spark").strip() or "spark",
            "color": str(body.get("color") or "").strip(),
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
            "sharedArtifacts": [],
            "executionActions": normalize_execution_actions(body.get("executionActions")),
            "executionScope": execution_scope_path.as_posix() or ".",
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
            "name", "description", "icon", "color",
            "workspacePath", "status", "model", "accountTier",
        )
        for field in fields:
            if field in values:
                project[field] = values[field]
        if isinstance(values.get("context"), dict):
            project["context"] = {**(project.get("context") or {}), **values["context"]}
        if "executionActions" in values:
            project["executionActions"] = normalize_execution_actions(
                values.get("executionActions")
            )
        if "executionScope" in values:
            scope = str(values.get("executionScope") or ".").strip().replace("\\", "/") or "."
            scope_path = Path(scope)
            if scope_path.is_absolute() or ".." in scope_path.parts:
                raise ValueError("executionScope must stay inside the project workspace")
            project["executionScope"] = scope_path.as_posix() or "."
        project["updatedAt"] = self.lifecycle.utc_now()
        self.repository.write_store(payload)
        projected = self._lightweight_store(payload)
        projected_project = next(
            item for item in projected["projects"] if item.get("id") == project_id
        )
        return {"ok": True, "project": projected_project, **projected}

    async def delete(self, project_id: str) -> dict[str, Any]:
        payload, project = self.repository.get(project_id)
        projects = payload.get("projects", [])
        data_key = self.lifecycle.project_data_key(project)
        resource_key = self.lifecycle.project_resource_key(project)
        chat_ids = self.chats.list_project_chat_ids(project_id)
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
        self.repository.write_store(payload, base_value=base_payload)
        return {"ok": True, **self._lightweight_store(payload)}

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

__all__ = [
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
