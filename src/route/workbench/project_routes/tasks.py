"""Project task creation and initialization HTTP adapters."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.workbench.project_services import (
    ProjectApplicationService,
    ProjectNotFoundError,
)
from route import schemas as api_models


def _not_found(exc: ProjectNotFoundError) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=404)


def register_project_task_routes(
    router: APIRouter,
    projects: ProjectApplicationService,
) -> dict[str, Any]:
    @router.get("/api/projects/{project_id}/sessions")
    async def api_workbench_project_sessions(project_id: str):
        try:
            return {"sessions": projects.sessions(project_id)}
        except ProjectNotFoundError as exc:
            return _not_found(exc)

    @router.post("/api/projects/{project_id}/sessions")
    async def api_workbench_create_session(
        project_id: str,
        body_model: api_models.SessionCreateBody,
    ):
        try:
            return projects.create_task(project_id, api_models.body_dict(body_model))
        except ProjectNotFoundError as exc:
            return _not_found(exc)

    @router.post("/api/task-sessions/{session_id}/follow-up")
    async def api_workbench_create_follow_up(
        session_id: str,
        body_model: api_models.FollowUpBody,
    ):
        try:
            return projects.create_follow_up(
                session_id,
                api_models.body_dict(body_model),
            )
        except ProjectNotFoundError as exc:
            return _not_found(exc)

    @router.post("/api/projects/{project_id}/init/generate")
    async def api_workbench_generate_init(
        project_id: str,
        body_model: api_models.InitGenerateBody,
    ):
        """(Re)generate the onboarding questions for a project's init session.

        Runs the agent against the project's metadata and workspace files; on
        any failure it keeps the deterministic fallback form. Either way the
        form is marked as ``generated`` so the client only requests this once.
        """
        try:
            return await projects.generate_init(
                project_id,
                str(body_model.lang or "").strip(),
            )
        except ProjectNotFoundError as exc:
            return _not_found(exc)

    return {
        "list_tasks": api_workbench_project_sessions,
        "create_task": api_workbench_create_session,
    }


__all__ = ["register_project_task_routes"]
