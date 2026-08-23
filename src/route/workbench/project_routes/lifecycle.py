"""Project lifecycle HTTP adapters."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.workbench.project_services import (
    ProjectApplicationService,
    ProjectNotFoundError,
    ProjectOperationError,
)
from route import schemas as api_models
from route.errors import error_response
from route.workspace import WorkspacePathError


def _project_error(exc: Exception):
    if isinstance(exc, ProjectOperationError):
        return error_response(exc.message, exc.status_code, exc.code)
    if isinstance(exc, WorkspacePathError):
        return error_response(str(exc), 400, exc.code)
    return JSONResponse({"error": str(exc)}, status_code=404)


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
