"""Task session CRUD, workspace, diff, and plan-mutation adapters."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.workbench.tasks.task_services import (
    PlanningMutationError,
    TaskMutationError,
    TaskSessionNotFoundError,
)
from cyrene.workbench.http import schemas as api_models
from cyrene.workbench.http.errors import error_response, localized_error_response
from cyrene.workbench.http.workbench.task_session_routes.context import TaskSessionRouteContext
from cyrene.workbench.http.workbench.task_session_routes.responses import service_response


def register_session_read_routes(router: APIRouter, context: TaskSessionRouteContext):
    @router.get("/api/task-sessions/{session_id}")
    async def api_workbench_get_session(session_id: str):
        try:
            return context.tasks.read(session_id)
        except TaskSessionNotFoundError:
            return localized_error_response(
                "Task session not found.",
                "未找到任务会话。",
                404,
                "task_session_not_found",
            )

    @router.get("/api/task-sessions/{session_id}/files/diff")
    async def api_workbench_file_diff(session_id: str, path: str = ""):
        return service_response(await context.workspace.file_diff(session_id, path))

    @router.get("/api/task-sessions/{session_id}/workspace/exists")
    async def api_workbench_workspace_exists(session_id: str, path: str = ""):
        """Validate a context-file path for the per-step '相关文件' editor: confirm
        it resolves INSIDE the project workspace and exists. Returns the workspace-
        relative path so the client stores a normalized reference."""
        try:
            result = context.tasks.workspace_path_status(session_id, path)
        except TaskSessionNotFoundError:
            return localized_error_response(
                "Task session not found.",
                "未找到任务会话。",
                404,
                "task_session_not_found",
            )
        except ValueError:
            return localized_error_response(
                "No workspace is configured.",
                "尚未配置工作区。",
                400,
                "workspace_not_configured",
            )
        if result.get("error"):
            return JSONResponse(
                {**result, "code": "workspace_path_outside_root"},
                status_code=400,
            )
        return result

    return api_workbench_get_session


def register_session_mutation_routes(router: APIRouter, context: TaskSessionRouteContext):
    @router.patch("/api/task-sessions/{session_id}/plan")
    async def api_workbench_mutate_plan(session_id: str, body_model: api_models.PlanMutationBody):
        body = api_models.body_dict(body_model)
        try:
            return context.planning.mutate(session_id, body, base_plan_revision=body_model.basePlanRevision)
        except PlanningMutationError as exc:
            return error_response(
                exc.message,
                exc.status_code,
                exc.code or "plan_mutation_failed",
            )

    @router.patch("/api/task-sessions/{session_id}")
    async def api_workbench_update_session(session_id: str, body_model: api_models.SessionUpdateBody):
        try:
            return context.tasks.update(session_id, api_models.body_dict(body_model))
        except TaskSessionNotFoundError:
            return localized_error_response(
                "Task session not found.",
                "未找到任务会话。",
                404,
                "task_session_not_found",
            )
        except TaskMutationError as exc:
            details = {"category": exc.category} if exc.category else {}
            return error_response(
                exc.message,
                exc.status_code,
                exc.code or "task_mutation_failed",
                **details,
            )

    @router.delete("/api/task-sessions/{session_id}")
    async def api_workbench_delete_session(session_id: str):
        from cyrene.workbench.goals import goal_loop

        deps = context.dependencies
        try:
            return await context.tasks.delete(
                session_id, db_path=context.db_path, task_runs=context.task_runs,
                goal_loops=goal_loop, agent_runtime=context.agent_runtime,
                store_lock=deps.store_lock,
            )
        except TaskSessionNotFoundError:
            return localized_error_response(
                "Task session not found.",
                "未找到任务会话。",
                404,
                "task_session_not_found",
            )
        except TaskMutationError as exc:
            details = {"category": exc.category} if exc.category else {}
            return error_response(
                exc.message,
                exc.status_code,
                exc.code or "task_delete_failed",
                **details,
            )

    return api_workbench_update_session


def register_session_routes(router: APIRouter, context: TaskSessionRouteContext) -> dict[str, Any]:
    get_session = register_session_read_routes(router, context)
    update_session = register_session_mutation_routes(router, context)
    return {"get_task": get_session, "update_task": update_session}
