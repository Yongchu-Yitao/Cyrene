"""Run, chat, dispatch, and answer adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter

from cyrene.workbench.http import schemas as api_models
from cyrene.workbench.http.workbench.task_session_routes.context import TaskSessionRouteContext
from cyrene.workbench.http.workbench.task_session_routes.responses import service_response


async def _invoke(method: Callable[[str, dict[str, Any]], Awaitable[Any]], session_id: str, body: dict[str, Any]):
    return service_response(await method(session_id, body))


def register_execution_routes(router: APIRouter, context: TaskSessionRouteContext) -> dict[str, Any]:
    async def coordinated(run_type: str, method, session_id: str, body_model, *, bypass: bool = False):
        body = api_models.body_dict(body_model)
        result = await context.run_coordination.execute(
            run_type, session_id, body,
            lambda: _invoke(method, session_id, api_models.body_dict(body_model)),
            bypass_goal_loop_answer=bypass,
        )
        return service_response(result)

    @router.post("/api/task-sessions/{session_id}/runs")
    async def api_workbench_create_run(session_id: str, body_model: api_models.AgentInputBody):
        return await coordinated("execution", context.execution.create_run, session_id, body_model)

    @router.post("/api/task-sessions/{session_id}/chat")
    async def api_workbench_session_chat(session_id: str, body_model: api_models.AgentInputBody):
        """Simple chat mode — returns agent reply without generating plans/steps."""
        return await coordinated("chat", context.execution.chat, session_id, body_model)

    @router.post("/api/task-sessions/{session_id}/dispatch")
    async def api_workbench_dispatch(session_id: str, body_model: api_models.AgentInputBody):
        """Intent-aware entry for the task composer.

        Classifies the input and routes it: a question → a direct answer; a
        one-shot instruction → execute it and report what changed; a complex goal
        → generate a plan; a completion/handoff signal ("done", "可以验收了") →
        summarize the existing deliverables and move to review (no re-planning).
        Only the plan branch enters the planning/approval flow; answer/direct/
        finalize return an agent reply with no plan/steps. ``replyKind`` tells the
        client which card to render.
        """
        return await coordinated("dispatch", context.execution.dispatch, session_id, body_model)

    @router.post("/api/task-sessions/{session_id}/answer")
    async def api_workbench_answer(session_id: str, body_model: api_models.AnswerBody):
        """Continue the Task's durable Agent ContextTree with a user answer."""
        return await coordinated("answer", context.execution.answer, session_id, body_model, bypass=True)

    return {"dispatch_task": api_workbench_dispatch, "create_run": api_workbench_create_run, "answer_task": api_workbench_answer}
