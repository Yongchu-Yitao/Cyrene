"""Project lifecycle HTTP adapters."""

from __future__ import annotations

from fastapi import APIRouter
from cyrene.localization import localized
from cyrene.workbench.projects.project_services import (
    ProjectApplicationService,
    ProjectNotFoundError,
    ProjectOperationError,
)
from cyrene.workbench.http import schemas as api_models
from cyrene.workbench.http.errors import error_response
from cyrene.workbench.http.workspace import WorkspacePathError


def _project_error(exc: Exception):
    if isinstance(exc, ProjectOperationError):
        return error_response(exc.message, exc.status_code, exc.code)
    if isinstance(exc, WorkspacePathError):
        messages = {
            "workspace_path_required": ("Workspace path is required.", "请选择工作区路径。"),
            "invalid_workspace_path": ("Workspace path is invalid.", "工作区路径无效。"),
            "workspace_path_not_allowed": ("Workspace path is outside the allowed locations.", "工作区路径不在允许的位置内。"),
            "workspace_path_not_directory": ("Workspace path must be a directory.", "工作区路径必须是文件夹。"),
            "workspace_path_not_found": ("Workspace path does not exist.", "工作区路径不存在。"),
            "workspace_path_not_writable": ("Workspace path is not writable.", "工作区路径不可写。"),
        }
        en, zh = messages.get(
            exc.code,
            ("Workspace path is invalid.", "工作区路径无效。"),
        )
        return error_response(localized(en, zh), 400, exc.code)
    return error_response(
        localized("Project not found.", "未找到项目。"),
        404,
        "project_not_found",
    )


def register_project_lifecycle_routes(
    router: APIRouter,
    projects: ProjectApplicationService,
) -> None:
    @router.patch("/api/workbench/activate")
    async def api_workbench_activate(body: api_models.WorkbenchActivateBody):
        selection = await projects.activate(body.projectId, body.sessionId)
        return {"ok": True, **selection}

    @router.post("/api/projects")
    async def api_workbench_create_project(body_model: api_models.ProjectCreateBody):
        try:
            return projects.create(api_models.body_dict(body_model))
        except WorkspacePathError as exc:
            return _project_error(exc)

    @router.patch("/api/projects/{project_id}")
    async def api_workbench_update_project(
        project_id: str,
        body_model: api_models.ProjectUpdateBody,
    ):
        try:
            return projects.update(project_id, api_models.body_dict(body_model))
        except (ProjectNotFoundError, WorkspacePathError) as exc:
            return _project_error(exc)

    @router.delete("/api/projects/{project_id}")
    async def api_workbench_delete_project(project_id: str):
        try:
            return await projects.delete(project_id)
        except (ProjectNotFoundError, ProjectOperationError) as exc:
            return _project_error(exc)


__all__ = ["register_project_lifecycle_routes"]
