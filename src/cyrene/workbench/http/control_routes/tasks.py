"""Control task, plan, step-run, and task-approval routes."""

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from cyrene.workbench.control.control_services import ControlProjectQueryService, ControlTaskCommandService
from cyrene.workbench.http import schemas as workbench_schemas
from cyrene.workbench.http.control_schemas import (
    ControlApprovalResponseRequest, ControlTaskActionResponse, ControlTaskApprovalResponse,
    ControlTaskCreateRequest, ControlTaskDispatchRequest, ControlTaskListResponse,
    ControlTaskPlanApproveRequest, ControlTaskResponse, ControlTaskStepRunRequest,
)
from cyrene.workbench.http.control_routes.common import COMMON_ERRORS, control_call, task_detail, task_summary


def register_task_routes(
    router: APIRouter,
    queries: ControlProjectQueryService,
    commands: ControlTaskCommandService,
) -> None:
    @router.get("/v1/control/tasks", response_model=ControlTaskListResponse, responses=COMMON_ERRORS, tags=["Control"], operation_id="control_v1_list_tasks")
    async def control_list_tasks(project_id: str = Query(min_length=1, max_length=200)):
        result = await control_call(queries.list_tasks(project_id))
        if isinstance(result, JSONResponse):
            return result
        return ControlTaskListResponse(tasks=[task_summary(item) for item in result if str(item.get("kind") or "task") == "task"])

    @router.post("/v1/control/tasks", response_model=ControlTaskResponse, status_code=201, responses=COMMON_ERRORS, tags=["Control"], operation_id="control_v1_create_task")
    async def control_create_task(request: ControlTaskCreateRequest):
        result = await control_call(queries.create_task(
            request.project_id,
            workbench_schemas.SessionCreateBody(title=request.title, goal=request.goal, priority=request.priority),
        ))
        return result if isinstance(result, JSONResponse) else ControlTaskResponse(task=task_detail(result))

    @router.get("/v1/control/tasks/{task_id}", response_model=ControlTaskResponse, responses=COMMON_ERRORS, tags=["Control"], operation_id="control_v1_get_task")
    async def control_get_task(task_id: str):
        result = await control_call(commands.get(task_id))
        return result if isinstance(result, JSONResponse) else ControlTaskResponse(task=task_detail(result))

    @router.post("/v1/control/tasks/{task_id}/dispatch", response_model=ControlTaskResponse, responses=COMMON_ERRORS, tags=["Control"], operation_id="control_v1_dispatch_task")
    async def control_dispatch_task(task_id: str, request: ControlTaskDispatchRequest):
        result = await control_call(commands.dispatch(task_id, workbench_schemas.AgentInputBody(input=request.message, mode=request.permission_mode)))
        return result if isinstance(result, JSONResponse) else ControlTaskResponse(task=task_detail(result))

    @router.post("/v1/control/tasks/{task_id}/plan/approve", response_model=ControlTaskResponse, responses=COMMON_ERRORS, tags=["Control"], operation_id="control_v1_approve_task_plan")
    async def control_approve_task_plan(task_id: str, request: ControlTaskPlanApproveRequest):
        result = await control_call(commands.approve_plan(task_id, request.plan_definition_revision, workbench_schemas.SessionUpdateBody))
        return result if isinstance(result, JSONResponse) else ControlTaskResponse(task=task_detail(result))

    @router.post("/v1/control/tasks/{task_id}/steps/{step_id}/runs", response_model=ControlTaskResponse, responses=COMMON_ERRORS, tags=["Control"], operation_id="control_v1_run_task_step")
    async def control_run_task_step(task_id: str, step_id: str, request: ControlTaskStepRunRequest):
        result = await control_call(commands.run_step(
            task_id, step_id, revision=request.plan_definition_revision,
            prepare_body=workbench_schemas.SessionUpdateBody,
            run_body=workbench_schemas.AgentInputBody,
            message=request.message, permission_mode=request.permission_mode,
        ))
        return result if isinstance(result, JSONResponse) else ControlTaskResponse(task=task_detail(result))

    register_task_action_routes(router, commands)
    register_task_approval_routes(router, commands)


def register_task_action_routes(router: APIRouter, commands: ControlTaskCommandService) -> None:
    async def apply(task_id: str, action: str):
        result = await control_call(commands.action(task_id, action, workbench_schemas.SessionUpdateBody))
        if isinstance(result, JSONResponse):
            return result
        return ControlTaskActionResponse(changed=True, action=action, task=task_detail(result))

    @router.post("/v1/control/tasks/{task_id}/pause", response_model=ControlTaskActionResponse, responses=COMMON_ERRORS, tags=["Control"], operation_id="control_v1_pause_task")
    async def control_pause_task(task_id: str):
        return await apply(task_id, "pause")

    @router.post("/v1/control/tasks/{task_id}/resume", response_model=ControlTaskActionResponse, responses=COMMON_ERRORS, tags=["Control"], operation_id="control_v1_resume_task")
    async def control_resume_task(task_id: str):
        return await apply(task_id, "resume")

    @router.post("/v1/control/tasks/{task_id}/cancel", response_model=ControlTaskActionResponse, responses=COMMON_ERRORS, tags=["Control"], operation_id="control_v1_cancel_task")
    async def control_cancel_task(task_id: str):
        return await apply(task_id, "cancel")


def register_task_approval_routes(router: APIRouter, commands: ControlTaskCommandService) -> None:
    @router.post("/v1/control/tasks/{task_id}/approvals/{question_id}/responses", response_model=ControlTaskApprovalResponse, responses=COMMON_ERRORS, tags=["Control"], operation_id="control_v1_respond_task_approval")
    async def control_respond_task_approval(task_id: str, question_id: str, request: ControlApprovalResponseRequest):
        result = await control_call(commands.answer(
            task_id, question_id,
            workbench_schemas.AnswerBody(question_id=question_id, answer=request.answer, mode=request.permission_mode),
        ))
        if isinstance(result, JSONResponse):
            return result
        return ControlTaskApprovalResponse(
            accepted=True, task_id=task_id, question_id=question_id,
            awaiting_user=bool(result.get("awaitingUser")),
        )


__all__ = ["register_task_routes"]
