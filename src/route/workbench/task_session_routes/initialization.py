"""Project initialization task-session adapters."""

from __future__ import annotations

from fastapi import APIRouter

from route import schemas as api_models
from route.workbench.task_session_routes.context import TaskSessionRouteContext
from route.workbench.task_session_routes.responses import service_response


def register_initialization_routes(router: APIRouter, context: TaskSessionRouteContext) -> None:
    @router.post("/api/task-sessions/{session_id}/init/submit")
    async def api_workbench_submit_init(session_id: str, body_model: api_models.InitSubmitBody):
        """Finalize project initialization.

        Persists the onboarding answers, writes a project brief into the project
        context, and asks the initialization agent to draft the major task plan.
        Confirming that plan is a separate step that creates task sessions.
        """
        return service_response(await context.initialization.submit(session_id, api_models.body_dict(body_model)))

    @router.post("/api/task-sessions/{session_id}/init/plan")
    async def api_workbench_revise_init_plan(session_id: str, body_model: api_models.InitPlanBody):
        """Revise the initialization task plan from user feedback."""
        return service_response(await context.initialization.revise(session_id, api_models.body_dict(body_model)))

    @router.post("/api/task-sessions/{session_id}/init/confirm")
    async def api_workbench_confirm_init_plan(session_id: str, body_model: api_models.InitConfirmBody):
        """Create task sessions from the confirmed initialization plan."""
        return service_response(context.initialization.confirm(session_id, api_models.body_dict(body_model)))
