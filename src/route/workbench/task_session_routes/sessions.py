"""Task session CRUD, workspace, diff, and plan-mutation adapters."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.workbench.task_services import (
    PlanningMutationError,
    TaskMutationError,
    TaskSessionNotFoundError,
)
from route import schemas as api_models
from route.workbench.task_session_routes.context import TaskSessionRouteContext
from route.workbench.task_session_routes.responses import service_response


def register_session_read_routes(router: APIRouter, context: TaskSessionRouteContext):
    @router.get("/api/task-sessions/{session_id}")
    async def api_workbench_get_session(session_id: str):
        try:
            return context.tasks.read(session_id)
        except TaskSessionNotFoundError:
            return JSONResponse({"error": "session not found"}, status_code=404)

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
            return JSONResponse({"error": "session not found"}, status_code=404)
        except ValueError:
            return JSONResponse({"error": "no workspace configured"}, status_code=400)
        return JSONResponse(result, status_code=400) if result.get("error") else result

    return api_workbench_get_session


def register_session_mutation_routes(router: APIRouter, context: TaskSessionRouteContext):
    @router.patch("/api/task-sessions/{session_id}/plan")
    async def api_workbench_mutate_plan(session_id: str, body_model: api_models.PlanMutationBody):
        body = api_models.body_dict(body_model)
        try:
            return context.planning.mutate(session_id, body, base_plan_revision=body_model.basePlanRevision)
        except PlanningMutationError as exc:
            payload = {"error": exc.message}
            if exc.code:
                payload["code"] = exc.code
            return JSONResponse(payload, status_code=exc.status_code)

    @router.patch("/api/task-sessions/{session_id}")
    async def api_workbench_update_session(session_id: str, body_model: api_models.SessionUpdateBody):
        try:
            return context.tasks.update(session_id, api_models.body_dict(body_model))
        except TaskSessionNotFoundError:
            return JSONResponse({"error": "session not found"}, status_code=404)
        except TaskMutationError as exc:
            payload = {"error": exc.message}
            if exc.code:
                payload["code"] = exc.code
            return JSONResponse(payload, status_code=exc.status_code)

    @router.delete("/api/task-sessions/{session_id}")
    async def api_workbench_delete_session(session_id: str):
        from cyrene.workbench import goal_loop

        deps = context.dependencies
        try:
            return await context.tasks.delete(
                session_id, db_path=context.db_path, task_runs=context.task_runs,
                goal_loops=goal_loop, agent_runtime=context.agent_runtime,
                store_lock=deps.store_lock,
            )
        except TaskSessionNotFoundError:
            return JSONResponse({"error": "session not found"}, status_code=404)
        except TaskMutationError as exc:
            payload = {"error": exc.message}
            if exc.code:
                payload["code"] = exc.code
            return JSONResponse(payload, status_code=exc.status_code)

    return api_workbench_update_session


def register_session_routes(router: APIRouter, context: TaskSessionRouteContext) -> dict[str, Any]:
    get_session = register_session_read_routes(router, context)
    update_session = register_session_mutation_routes(router, context)
    return {"get_task": get_session, "update_task": update_session}
