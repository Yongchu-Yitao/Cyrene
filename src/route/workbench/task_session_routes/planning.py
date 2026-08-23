"""Planning, acceptance, reflection, and hint adapters."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.workbench.task_services import TaskMutationError, TaskSessionNotFoundError
from route import schemas as api_models
from route.workbench.task_session_routes.context import TaskSessionRouteContext
from route.workbench.task_session_routes.responses import service_response


def register_planning_routes(router: APIRouter, context: TaskSessionRouteContext) -> None:
    @router.post("/api/task-sessions/{session_id}/plan/generate")
    async def api_workbench_generate_plan(session_id: str, body_model: api_models.PlanGenerateBody):
        """Generate a REAL execution plan for a task session.

        The agent reads the session goal + constraints and explores the project
        workspace, then returns ordered steps (all ``pending`` — nothing is run
        or pre-completed here). Drives the idle → planning transition.
        """
        return service_response(await context.planning_workflow.generate_plan(session_id, api_models.body_dict(body_model)))

    @router.post("/api/task-sessions/{session_id}/acceptance/generate")
    async def api_workbench_generate_acceptance(session_id: str):
        """Generate fresh acceptance criteria from the current task and plan."""
        try:
            return await context.planning.generate_acceptance(session_id)
        except TaskSessionNotFoundError:
            return JSONResponse({"error": "session not found"}, status_code=404)

    @router.post("/api/task-sessions/{session_id}/reflect")
    async def api_workbench_reflect(session_id: str, body_model: api_models.ReflectionBody):
        """Run deep reflection over this task's history and attach the packet."""
        body = api_models.body_dict(body_model)
        try:
            return await context.planning.reflect(session_id, focus=str(body.get("focus") or "").strip(), goal_gap=str(body.get("goalGap") or "").strip())
        except TaskSessionNotFoundError:
            return JSONResponse({"error": "session not found"}, status_code=404)
        except TaskMutationError as exc:
            return JSONResponse({"error": exc.message}, status_code=exc.status_code)

    @router.post("/api/task-sessions/{session_id}/verify")
    async def api_workbench_verify(session_id: str, _body: api_models.EmptyBody | None = None):
        """Independent acceptance agent verifies the criteria against results."""
        try:
            return await context.planning.verify(session_id)
        except TaskSessionNotFoundError:
            return JSONResponse({"error": "session not found"}, status_code=404)
        except TaskMutationError as exc:
            payload = {"error": exc.message}
            if exc.code:
                payload["code"] = exc.code
            if exc.category:
                payload["category"] = exc.category
            return JSONResponse(payload, status_code=exc.status_code)

    @router.post("/api/task-sessions/{session_id}/reflect-and-fork")
    async def api_workbench_reflect_and_fork(session_id: str, _body: api_models.EmptyBody | None = None):
        """Reflect on a (failed) task, then create a fresh sibling session that
        inherits the goal/constraints and carries the reflection packet so its
        plan avoids the dead-ends. Returns the new session (made active)."""
        return service_response(await context.planning_workflow.reflect_and_fork(session_id))


def register_hint_routes(router: APIRouter, context: TaskSessionRouteContext) -> None:
    @router.post("/api/task-sessions/{session_id}/hints/{hint_id}/accept")
    async def api_workbench_accept_hint(session_id: str, hint_id: str):
        """Accept a sibling-reflection hint: merge its packet into THIS session's
        reflection (so its next plan/run benefits) and mark the hint accepted."""
        return service_response(context.planning_workflow.update_hint(session_id, hint_id, accepted=True))

    @router.post("/api/task-sessions/{session_id}/hints/{hint_id}/dismiss")
    async def api_workbench_dismiss_hint(session_id: str, hint_id: str):
        """Dismiss a sibling-reflection hint (no change to this session)."""
        return service_response(context.planning_workflow.update_hint(session_id, hint_id, accepted=False))
