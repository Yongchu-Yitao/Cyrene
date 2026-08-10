"""Versioned, desktop-local Control API.

This adapter is deliberately narrower than the Workbench UI API.  It provides
the first stable contract for controlling a Cyrene run while preserving the
existing loopback authentication boundary. Device pairing, grants, E2EE, and
the LAN transport belong to the RemoteGateway layer.
"""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, JSONResponse

from cyrene.runtime.remote_commands import (
    public_remote_event,
    referenced_chat_attachment_target,
)
from cyrene.workbench import runtime as workbench_runtime
from route import schemas as workbench_schemas
from route.control_schemas import (
    ControlApprovalResponse,
    ControlApprovalResponseRequest,
    ControlArtifactListResponse,
    ControlArtifactSummary,
    ControlCapabilitiesResponse,
    ControlChatCreateRequest,
    ControlChatDetail,
    ControlChatListResponse,
    ControlChatMessageRequest,
    ControlChatResponse,
    ControlChatSummary,
    ControlErrorResponse,
    ControlFeature,
    ControlGuidanceRequest,
    ControlGuidanceResponse,
    ControlInterruptResponse,
    ControlMessage,
    ControlProjectListResponse,
    ControlProjectSummary,
    ControlRunAccepted,
    ControlRunEvent,
    ControlRunEventsResponse,
    ControlRunResponse,
    ControlTaskActionResponse,
    ControlTaskApprovalResponse,
    ControlTaskCreateRequest,
    ControlTaskDetail,
    ControlTaskDispatchRequest,
    ControlTaskListResponse,
    ControlTaskPlanApproveRequest,
    ControlTaskResponse,
    ControlTaskStepRunRequest,
    ControlTaskSummary,
)


_CONTROL_OPERATIONS = [
    "capabilities.read",
    "projects.list",
    "chats.list",
    "chats.create",
    "chats.read",
    "chats.send",
    "runs.read",
    "runs.events",
    "runs.guide",
    "runs.interrupt",
    "tasks.list",
    "tasks.create",
    "tasks.read",
    "tasks.dispatch",
    "tasks.approve_plan",
    "tasks.run_step",
    "tasks.pause",
    "tasks.resume",
    "tasks.cancel",
    "approvals.respond",
    "artifacts.list",
    "artifacts.read",
    "attachments.read",
]

def _error_payload(response: JSONResponse) -> JSONResponse:
    """Pass through established Workbench errors without widening the API."""
    return response


def _chat_summary(raw: dict[str, Any], run_manager: Any) -> ControlChatSummary:
    chat_id = str(raw.get("id") or "")
    return ControlChatSummary(
        id=chat_id,
        project_id=str(raw.get("projectId") or ""),
        title=str(raw.get("title") or ""),
        status=str(raw.get("status") or "idle"),
        created_at=str(raw.get("createdAt") or ""),
        updated_at=str(raw.get("updatedAt") or ""),
        message_count=int(raw.get("messageCount") or len(raw.get("messages") or [])),
        running=run_manager.get(chat_id) is not None,
        awaiting_user=isinstance(raw.get("pendingQuestion"), dict),
    )


def _message(raw: dict[str, Any], chat_id: str) -> ControlMessage:
    attachments = raw.get("attachments")
    return ControlMessage(
        id=str(raw.get("id") or ""),
        role=str(raw.get("role") or ""),
        content=str(raw.get("content") or ""),
        created_at=str(raw.get("createdAt") or ""),
        attachments=[
            {
                key: item[key]
                for key in (
                    "id",
                    "name",
                    "type",
                    "mediaType",
                    "content_type",
                    "kind",
                    "size",
                    "width",
                    "height",
                )
                if key in item
            } | {
                "download_url": (
                    f"/v1/control/chats/{chat_id}/attachments/"
                    f"{item.get('id')}"
                )
            }
            for item in attachments or []
            if isinstance(item, dict) and str(item.get("id") or "")
        ],
        question_id=str(raw.get("questionId") or ""),
        question_kind=str(raw.get("questionKind") or ""),
    )


def _chat_detail(raw: dict[str, Any], run_manager: Any) -> ControlChatDetail:
    summary = _chat_summary(raw, run_manager)
    return ControlChatDetail(
        **summary.model_dump(),
        messages=[
            _message(item, str(raw.get("id") or ""))
            for item in raw.get("messages") or []
            if isinstance(item, dict)
        ],
    )


def _run_response(run: Any) -> ControlRunResponse:
    outcome = run.outcome if isinstance(run.outcome, dict) else {}
    return ControlRunResponse(
        run_id=run.run_id,
        chat_id=run.chat_id,
        status=str(run.status or "running"),
        created_at=str(run.created_at or ""),
        completed=run.done.is_set(),
        termination_reason=str(run.termination_reason or ""),
        outcome=str(outcome.get("kind") or ""),
        last_event_cursor=max(
            (int(event.get("_seq") or 0) for event in run.events),
            default=0,
        ),
    )


def _task_summary(raw: dict[str, Any]) -> ControlTaskSummary:
    return ControlTaskSummary(
        id=str(raw.get("id") or ""),
        project_id=str(raw.get("projectId") or ""),
        title=str(raw.get("title") or ""),
        goal=str(raw.get("goal") or ""),
        status=str(raw.get("status") or "idle"),
        priority=str(raw.get("priority") or "medium"),
        created_at=str(raw.get("createdAt") or ""),
        updated_at=str(raw.get("updatedAt") or ""),
        artifact_count=len(raw.get("artifacts") or []),
    )


def _task_detail(raw: dict[str, Any]) -> ControlTaskDetail:
    summary = _task_summary(raw)
    public_task_event_types = {
        "AgentResponseEvent",
        "ExecutionFailed",
        "ExecutionFinished",
        "ExecutionStarted",
        "PlanApproved",
        "PlanGenerated",
        "PlanRevised",
        "UserMessageEvent",
    }
    pending = raw.get("pendingQuestion")
    goal_loop = raw.get("goalLoop")
    return ControlTaskDetail(
        **summary.model_dump(),
        plan=[
            {
                key: item[key]
                for key in ("id", "title", "description", "status", "dependsOn")
                if key in item
            }
            for item in raw.get("plan") or []
            if isinstance(item, dict)
        ],
        pending_question=(
            {
                key: pending[key]
                for key in (
                    "id",
                    "questionId",
                    "kind",
                    "questionKind",
                    "prompt",
                    "question",
                    "title",
                    "options",
                    "choices",
                )
                if key in pending
            }
            if isinstance(pending, dict)
            else None
        ),
        events=[
            {
                key: item[key]
                for key in ("id", "type", "createdAt", "body", "stepId")
                if key in item
            }
            for item in raw.get("events") or []
            if isinstance(item, dict)
            and str(item.get("type") or "") in public_task_event_types
        ],
        artifacts=[
            {
                key: item[key]
                for key in ("id", "name", "type", "status", "createdAt", "size")
                if key in item
            }
            for item in raw.get("artifacts") or []
            if isinstance(item, dict)
        ],
        goal_loop=(
            {
                key: goal_loop[key]
                for key in (
                    "id",
                    "status",
                    "phase",
                    "currentStepId",
                    "stopReason",
                    "activeSeconds",
                    "maxActiveSeconds",
                    "repairRound",
                    "maxRepairRounds",
                    "updatedAt",
                )
                if key in goal_loop
            }
            if isinstance(goal_loop, dict)
            else None
        ),
    )


def _public_event(raw: dict[str, Any]) -> ControlRunEvent | None:
    # The loopback Control API deliberately excludes model reasoning. Paired,
    # encrypted remote clients use the broader Workbench-equivalent event
    # contract, but local API consumers receive only public execution output.
    if str(raw.get("type") or "") in {
        "reasoning_delta",
        "reasoning_done",
        "reasoning_start",
    }:
        return None
    public = public_remote_event(raw)
    if public is None:
        return None
    data = {
        key: value
        for key, value in public.items()
        if key not in {"cursor", "run_id", "type"}
    }
    return ControlRunEvent(
        cursor=int(public["cursor"]),
        run_id=str(public["run_id"]),
        type=str(public["type"]),
        data=data,
    )


def register_control_routes(
    router: APIRouter,
    chat_adapter: dict[str, Any],
    project_adapter: dict[str, Any] | None = None,
    task_adapter: dict[str, Any] | None = None,
    goal_loop_adapter: dict[str, Any] | None = None,
) -> None:
    """Install the first versioned Control API contract."""
    run_manager = chat_adapter["run_manager"]
    project_adapter = project_adapter or {}
    task_adapter = task_adapter or {}
    goal_loop_adapter = goal_loop_adapter or {}

    common_errors = {
        400: {"model": ControlErrorResponse},
        404: {"model": ControlErrorResponse},
        409: {"model": ControlErrorResponse},
    }

    @router.get(
        "/v1/control/capabilities",
        response_model=ControlCapabilitiesResponse,
        tags=["Control"],
        operation_id="control_v1_get_capabilities",
    )
    async def control_capabilities() -> ControlCapabilitiesResponse:
        return ControlCapabilitiesResponse(
            remote_transport_available=True,
            durable_run_events=True,
            operations=list(_CONTROL_OPERATIONS),
            features=[
                ControlFeature(
                    name="chat_runs",
                    available=True,
                    detail="Detached chat runs with cursor-addressable replay.",
                ),
                ControlFeature(
                    name="durable_run_events",
                    available=True,
                    detail="Run metadata and events survive process restarts for seven days.",
                ),
                ControlFeature(
                    name="remote_gateway",
                    available=True,
                    detail="Paired-device E2EE gateway with typed grants.",
                ),
                ControlFeature(
                    name="remote_desktop",
                    available=False,
                    detail="Optional WebRTC takeover is not implemented.",
                ),
            ],
        )

    @router.get(
        "/v1/control/projects",
        response_model=ControlProjectListResponse,
        tags=["Control"],
        operation_id="control_v1_list_projects",
    )
    async def control_list_projects() -> ControlProjectListResponse:
        payload = await asyncio.to_thread(
            workbench_runtime._read_workbench_store_lightweight
        )
        projects = []
        for raw in payload.get("projects") or []:
            if not isinstance(raw, dict):
                continue
            projects.append(
                ControlProjectSummary(
                    id=str(raw.get("id") or ""),
                    name=str(raw.get("name") or ""),
                    status=str(raw.get("status") or "active"),
                    updated_at=str(raw.get("updatedAt") or ""),
                    task_count=len(
                        [
                            item
                            for item in raw.get("sessions") or []
                            if isinstance(item, dict)
                            and str(item.get("kind") or "task") == "task"
                        ]
                    ),
                )
            )
        return ControlProjectListResponse(projects=projects)

    @router.get(
        "/v1/control/chats",
        response_model=ControlChatListResponse,
        responses=common_errors,
        tags=["Control"],
        operation_id="control_v1_list_chats",
    )
    async def control_list_chats(
        project_id: str = Query(default="", max_length=200),
    ) -> ControlChatListResponse | JSONResponse:
        result = await chat_adapter["list_chats"](project=project_id)
        if isinstance(result, JSONResponse):
            return _error_payload(result)
        chats = [
            _chat_summary(raw, run_manager)
            for raw in result.get("chats") or []
            if isinstance(raw, dict) and not str(raw.get("id") or "").startswith("legacy:")
        ]
        return ControlChatListResponse(chats=chats)

    @router.post(
        "/v1/control/chats",
        response_model=ControlChatResponse,
        status_code=201,
        responses=common_errors,
        tags=["Control"],
        operation_id="control_v1_create_chat",
    )
    async def control_create_chat(
        request: ControlChatCreateRequest,
    ) -> ControlChatResponse | JSONResponse:
        result = await chat_adapter["create_chat"](
            workbench_schemas.ChatCreateBody(
                project=request.project_id,
                title=request.title,
            )
        )
        if isinstance(result, JSONResponse):
            return _error_payload(result)
        return ControlChatResponse(
            chat=_chat_detail(dict(result.get("chat") or {}), run_manager)
        )

    @router.get(
        "/v1/control/chats/{chat_id}",
        response_model=ControlChatResponse,
        responses=common_errors,
        tags=["Control"],
        operation_id="control_v1_get_chat",
    )
    async def control_get_chat(chat_id: str) -> ControlChatResponse | JSONResponse:
        result = await chat_adapter["get_chat"](chat_id)
        if isinstance(result, JSONResponse):
            return _error_payload(result)
        return ControlChatResponse(
            chat=_chat_detail(dict(result.get("chat") or {}), run_manager)
        )

    @router.post(
        "/v1/control/chats/{chat_id}/messages",
        response_model=ControlRunAccepted,
        status_code=202,
        responses=common_errors,
        tags=["Control"],
        operation_id="control_v1_send_chat_message",
    )
    async def control_send_chat_message(
        chat_id: str,
        request: ControlChatMessageRequest,
    ):
        return await chat_adapter["send_chat_detached"](
            chat_id,
            {
                "message": request.message,
                "mode": request.permission_mode,
                "lang": request.language,
                "stream": True,
            },
            detached=True,
        )

    @router.get(
        "/v1/control/runs/{run_id}",
        response_model=ControlRunResponse,
        responses=common_errors,
        tags=["Control"],
        operation_id="control_v1_get_run",
    )
    async def control_get_run(run_id: str) -> ControlRunResponse | JSONResponse:
        run = run_manager.get_replayable_by_run_id(run_id)
        if run is None:
            return JSONResponse(
                {"error": "run not found", "code": "control_run_not_found"},
                status_code=404,
            )
        return _run_response(run)

    @router.get(
        "/v1/control/runs/{run_id}/events",
        response_model=ControlRunEventsResponse,
        responses=common_errors,
        tags=["Control"],
        operation_id="control_v1_list_run_events",
    )
    async def control_list_run_events(
        run_id: str,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=500),
    ) -> ControlRunEventsResponse | JSONResponse:
        run = run_manager.get_replayable_by_run_id(run_id)
        if run is None:
            return JSONResponse(
                {"error": "run not found", "code": "control_run_not_found"},
                status_code=404,
            )
        raw_events = list(run.events)
        available_cursors = [
            int(event.get("_seq") or 0)
            for event in raw_events
            if int(event.get("_seq") or 0) > after
        ]
        previous_cursor = after
        truncated = False
        for available_cursor in available_cursors:
            if available_cursor > previous_cursor + 1:
                truncated = True
                break
            previous_cursor = available_cursor
        public_events = []
        next_cursor = after
        for raw in raw_events:
            raw_cursor = int(raw.get("_seq") or 0)
            if raw_cursor <= after:
                continue
            next_cursor = raw_cursor
            event = _public_event(raw)
            if event is not None:
                public_events.append(event)
            if len(public_events) >= limit:
                break
        return ControlRunEventsResponse(
            run_id=run_id,
            events=public_events,
            next_cursor=next_cursor,
            completed=run.done.is_set(),
            truncated=truncated,
        )

    @router.post(
        "/v1/control/runs/{run_id}/guidance",
        response_model=ControlGuidanceResponse,
        responses=common_errors,
        tags=["Control"],
        operation_id="control_v1_guide_run",
    )
    async def control_guide_run(
        run_id: str,
        request: ControlGuidanceRequest,
    ) -> ControlGuidanceResponse | JSONResponse:
        run = run_manager.get_by_run_id(run_id)
        if run is None:
            return JSONResponse(
                {
                    "error": "run is not active",
                    "code": "control_run_not_active",
                },
                status_code=409,
            )
        result = await chat_adapter["guide_chat"](
            run.chat_id,
            workbench_schemas.ChatGuidanceBody(
                message=request.message,
                clientRequestId=request.request_id or None,
            ),
        )
        if isinstance(result, JSONResponse):
            return _error_payload(result)
        return ControlGuidanceResponse(
            queued=bool(result.get("queued")),
            duplicate=bool(result.get("duplicate")),
            event_id=str(result.get("eventId") or ""),
            run_id=str(result.get("runId") or run_id),
        )

    @router.post(
        "/v1/control/runs/{run_id}/interrupt",
        response_model=ControlInterruptResponse,
        responses=common_errors,
        tags=["Control"],
        operation_id="control_v1_interrupt_run",
    )
    async def control_interrupt_run(
        run_id: str,
    ) -> ControlInterruptResponse | JSONResponse:
        run = run_manager.get_by_run_id(run_id)
        if run is None:
            return JSONResponse(
                {
                    "error": "run is not active",
                    "code": "control_run_not_active",
                },
                status_code=409,
            )
        interrupted = run_manager.interrupt(run.chat_id)
        return ControlInterruptResponse(
            interrupted=interrupted,
            run_id=run_id,
            status="cancelled" if interrupted else str(run.status or ""),
        )

    async def _load_task(
        task_id: str,
    ) -> tuple[dict[str, Any] | None, JSONResponse | None]:
        result = await task_adapter["get_task"](task_id)
        if isinstance(result, JSONResponse):
            return None, _error_payload(result)
        return dict(result.get("session") or {}), None

    @router.get(
        "/v1/control/tasks",
        response_model=ControlTaskListResponse,
        responses=common_errors,
        tags=["Control"],
        operation_id="control_v1_list_tasks",
    )
    async def control_list_tasks(
        project_id: str = Query(min_length=1, max_length=200),
    ) -> ControlTaskListResponse | JSONResponse:
        result = await project_adapter["list_tasks"](project_id)
        if isinstance(result, JSONResponse):
            return _error_payload(result)
        return ControlTaskListResponse(
            tasks=[
                _task_summary(item)
                for item in result.get("sessions") or []
                if isinstance(item, dict)
                and str(item.get("kind") or "task") == "task"
            ]
        )

    @router.post(
        "/v1/control/tasks",
        response_model=ControlTaskResponse,
        status_code=201,
        responses=common_errors,
        tags=["Control"],
        operation_id="control_v1_create_task",
    )
    async def control_create_task(
        request: ControlTaskCreateRequest,
    ) -> ControlTaskResponse | JSONResponse:
        result = await project_adapter["create_task"](
            request.project_id,
            workbench_schemas.SessionCreateBody(
                title=request.title,
                goal=request.goal,
                priority=request.priority,
            ),
        )
        if isinstance(result, JSONResponse):
            return _error_payload(result)
        return ControlTaskResponse(
            task=_task_detail(dict(result.get("session") or {}))
        )

    @router.get(
        "/v1/control/tasks/{task_id}",
        response_model=ControlTaskResponse,
        responses=common_errors,
        tags=["Control"],
        operation_id="control_v1_get_task",
    )
    async def control_get_task(
        task_id: str,
    ) -> ControlTaskResponse | JSONResponse:
        task, error = await _load_task(task_id)
        if error is not None:
            return error
        return ControlTaskResponse(task=_task_detail(task or {}))

    @router.post(
        "/v1/control/tasks/{task_id}/dispatch",
        response_model=ControlTaskResponse,
        responses=common_errors,
        tags=["Control"],
        operation_id="control_v1_dispatch_task",
    )
    async def control_dispatch_task(
        task_id: str,
        request: ControlTaskDispatchRequest,
    ) -> ControlTaskResponse | JSONResponse:
        result = await task_adapter["dispatch_task"](
            task_id,
            workbench_schemas.AgentInputBody(
                input=request.message,
                mode=request.permission_mode,
            ),
        )
        if isinstance(result, JSONResponse):
            return _error_payload(result)
        return ControlTaskResponse(
            task=_task_detail(dict(result.get("session") or {}))
        )

    @router.post(
        "/v1/control/tasks/{task_id}/plan/approve",
        response_model=ControlTaskResponse,
        responses=common_errors,
        tags=["Control"],
        operation_id="control_v1_approve_task_plan",
    )
    async def control_approve_task_plan(
        task_id: str,
        request: ControlTaskPlanApproveRequest,
    ) -> ControlTaskResponse | JSONResponse:
        task, error = await _load_task(task_id)
        if error is not None:
            return error
        revision = int((task or {}).get("planDefinitionRevision") or 0)
        if request.plan_definition_revision != revision:
            return JSONResponse(
                {
                    "error": "task plan revision is stale",
                    "code": "stale_plan_revision",
                },
                status_code=409,
            )
        if not (task or {}).get("plan"):
            return JSONResponse(
                {"error": "task plan is empty", "code": "task_plan_empty"},
                status_code=409,
            )
        result = await task_adapter["update_task"](
            task_id,
            workbench_schemas.SessionUpdateBody(
                status="waiting_for_approval",
                approvedPlanDefinitionRevision=revision,
            ),
        )
        if isinstance(result, JSONResponse):
            return _error_payload(result)
        return ControlTaskResponse(
            task=_task_detail(dict(result.get("session") or {}))
        )

    @router.post(
        "/v1/control/tasks/{task_id}/steps/{step_id}/runs",
        response_model=ControlTaskResponse,
        responses=common_errors,
        tags=["Control"],
        operation_id="control_v1_run_task_step",
    )
    async def control_run_task_step(
        task_id: str,
        step_id: str,
        request: ControlTaskStepRunRequest,
    ) -> ControlTaskResponse | JSONResponse:
        task, error = await _load_task(task_id)
        if error is not None:
            return error
        revision = int((task or {}).get("planDefinitionRevision") or 0)
        approved = (task or {}).get("approvedPlanDefinitionRevision")
        if request.plan_definition_revision != revision:
            return JSONResponse(
                {
                    "error": "task plan revision is stale",
                    "code": "stale_plan_revision",
                },
                status_code=409,
            )
        if approved is None or int(approved) != revision:
            return JSONResponse(
                {
                    "error": "task plan has not been approved",
                    "code": "plan_not_approved",
                },
                status_code=409,
            )
        plan = [
            dict(item)
            for item in (task or {}).get("plan") or []
            if isinstance(item, dict)
        ]
        step = next(
            (item for item in plan if str(item.get("id") or "") == step_id),
            None,
        )
        if step is None:
            return JSONResponse(
                {"error": "task step not found", "code": "step_not_found"},
                status_code=404,
            )
        for item in plan:
            if str(item.get("id") or "") == step_id:
                item["status"] = "running"
                item["currentAction"] = "Control API started this step."
        prepared = await task_adapter["update_task"](
            task_id,
            workbench_schemas.SessionUpdateBody(status="running", plan=plan),
        )
        if isinstance(prepared, JSONResponse):
            return _error_payload(prepared)
        result = await task_adapter["create_run"](
            task_id,
            workbench_schemas.AgentInputBody(
                input=request.message,
                mode=request.permission_mode,
                stepId=step_id,
                stepTitle=str(step.get("title") or "")[:1000],
                action="spawn_subagent",
                meta={"scope": "plan_step", "continueAll": False},
                planDefinitionRevision=revision,
            ),
        )
        if isinstance(result, JSONResponse):
            return _error_payload(result)
        updated = dict(result.get("session") or {})
        if str(updated.get("status") or "") == "waiting_for_user":
            return ControlTaskResponse(task=_task_detail(updated))
        returned_plan = [
            dict(item)
            for item in updated.get("plan") or plan
            if isinstance(item, dict)
        ]
        for item in returned_plan:
            if str(item.get("id") or "") == step_id:
                item["status"] = "completed"
                item["currentAction"] = "Control API step completed."
        resolved = {"completed", "done", "skipped"}
        fully_done = bool(returned_plan) and all(
            str(item.get("status") or "") in resolved
            for item in returned_plan
        )
        finalized = await task_adapter["update_task"](
            task_id,
            workbench_schemas.SessionUpdateBody(
                status="review" if fully_done else "paused",
                plan=returned_plan,
            ),
        )
        if isinstance(finalized, JSONResponse):
            return _error_payload(finalized)
        return ControlTaskResponse(
            task=_task_detail(dict(finalized.get("session") or {}))
        )

    async def _control_task_action(
        task_id: str,
        action: str,
    ) -> ControlTaskActionResponse | JSONResponse:
        task, error = await _load_task(task_id)
        if error is not None:
            return error
        if goal_loop_adapter:
            goal_state = await goal_loop_adapter["get"](task_id)
            if isinstance(goal_state, JSONResponse):
                return _error_payload(goal_state)
            goal_loop = goal_state.get("goalLoop")
            if (
                isinstance(goal_loop, dict)
                and str(goal_loop.get("status") or "")
                not in {"completed", "failed", "cancelled"}
            ):
                controlled = await goal_loop_adapter[action](task_id)
                if isinstance(controlled, JSONResponse):
                    return _error_payload(controlled)
                return ControlTaskActionResponse(
                    changed=True,
                    action=action,
                    task=_task_detail(
                        dict(controlled.get("session") or {})
                    ),
                )
        current = str((task or {}).get("status") or "")
        if action == "pause" and current not in {"running", "waiting_for_user"}:
            return JSONResponse(
                {
                    "error": "only an active task can be paused",
                    "code": "invalid_status_transition",
                },
                status_code=409,
            )
        if action == "resume" and current != "paused":
            return JSONResponse(
                {
                    "error": "only a paused task can be resumed",
                    "code": "invalid_status_transition",
                },
                status_code=409,
            )
        status = {
            "pause": "paused",
            "resume": "idle",
            "cancel": "cancelled",
        }[action]
        if action in {"pause", "cancel"}:
            from cyrene.agent import interrupt_active_run

            interrupt_active_run(session_id=task_id)
        result = await task_adapter["update_task"](
            task_id,
            workbench_schemas.SessionUpdateBody(status=status),
        )
        if isinstance(result, JSONResponse):
            return _error_payload(result)
        return ControlTaskActionResponse(
            changed=True,
            action=action,
            task=_task_detail(dict(result.get("session") or {})),
        )

    @router.post(
        "/v1/control/tasks/{task_id}/pause",
        response_model=ControlTaskActionResponse,
        responses=common_errors,
        tags=["Control"],
        operation_id="control_v1_pause_task",
    )
    async def control_pause_task(task_id: str):
        return await _control_task_action(task_id, "pause")

    @router.post(
        "/v1/control/tasks/{task_id}/resume",
        response_model=ControlTaskActionResponse,
        responses=common_errors,
        tags=["Control"],
        operation_id="control_v1_resume_task",
    )
    async def control_resume_task(task_id: str):
        return await _control_task_action(task_id, "resume")

    @router.post(
        "/v1/control/tasks/{task_id}/cancel",
        response_model=ControlTaskActionResponse,
        responses=common_errors,
        tags=["Control"],
        operation_id="control_v1_cancel_task",
    )
    async def control_cancel_task(task_id: str):
        return await _control_task_action(task_id, "cancel")

    @router.post(
        "/v1/control/chats/{chat_id}/approvals/{question_id}/responses",
        response_model=ControlApprovalResponse,
        responses=common_errors,
        tags=["Control"],
        operation_id="control_v1_respond_approval",
    )
    async def control_respond_approval(
        chat_id: str,
        question_id: str,
        request: ControlApprovalResponseRequest,
    ) -> ControlApprovalResponse | JSONResponse:
        result = await chat_adapter["answer_chat"](
            chat_id,
            workbench_schemas.AnswerBody(
                question_id=question_id,
                answer=request.answer,
                mode=request.permission_mode,
            ),
        )
        if isinstance(result, JSONResponse):
            return _error_payload(result)
        return ControlApprovalResponse(
            accepted=True,
            chat_id=chat_id,
            question_id=question_id,
            awaiting_user=bool(result.get("awaitingUser")),
        )

    @router.post(
        "/v1/control/tasks/{task_id}/approvals/{question_id}/responses",
        response_model=ControlTaskApprovalResponse,
        responses=common_errors,
        tags=["Control"],
        operation_id="control_v1_respond_task_approval",
    )
    async def control_respond_task_approval(
        task_id: str,
        question_id: str,
        request: ControlApprovalResponseRequest,
    ) -> ControlTaskApprovalResponse | JSONResponse:
        task, error = await _load_task(task_id)
        if error is not None:
            return error
        pending = (
            (task or {}).get("pendingQuestion")
            if isinstance((task or {}).get("pendingQuestion"), dict)
            else None
        )
        if pending is None or str(pending.get("id") or "") != question_id:
            return JSONResponse(
                {
                    "error": "no matching pending question",
                    "code": "approval_not_pending",
                },
                status_code=409,
            )
        result = await task_adapter["answer_task"](
            task_id,
            workbench_schemas.AnswerBody(
                question_id=question_id,
                answer=request.answer,
                mode=request.permission_mode,
            ),
        )
        if isinstance(result, JSONResponse):
            return _error_payload(result)
        return ControlTaskApprovalResponse(
            accepted=True,
            task_id=task_id,
            question_id=question_id,
            awaiting_user=bool(result.get("awaitingUser")),
        )

    @router.get(
        "/v1/control/tasks/{task_id}/artifacts",
        response_model=ControlArtifactListResponse,
        responses=common_errors,
        tags=["Control"],
        operation_id="control_v1_list_artifacts",
    )
    async def control_list_artifacts(
        task_id: str,
    ) -> ControlArtifactListResponse | JSONResponse:
        result = await task_adapter["task_artifacts"](task_id)
        if isinstance(result, JSONResponse):
            return _error_payload(result)
        artifacts = []
        for item in result.get("artifacts") or []:
            if not isinstance(item, dict):
                continue
            artifact_id = str(item.get("id") or "")
            artifacts.append(
                ControlArtifactSummary(
                    id=artifact_id,
                    task_id=task_id,
                    name=str(item.get("name") or item.get("path") or ""),
                    type=str(item.get("type") or ""),
                    created_at=str(item.get("createdAt") or ""),
                    size=(
                        int(item["size"])
                        if item.get("size") is not None
                        else None
                    ),
                    download_url=(
                        f"/v1/control/tasks/{task_id}/artifacts/{artifact_id}"
                    ),
                )
            )
        return ControlArtifactListResponse(artifacts=artifacts)

    @router.get(
        "/v1/control/tasks/{task_id}/artifacts/{artifact_id}",
        responses={
            **common_errors,
            200: {"content": {"application/octet-stream": {}}},
        },
        tags=["Control"],
        operation_id="control_v1_read_artifact",
    )
    async def control_read_artifact(task_id: str, artifact_id: str):
        payload = await asyncio.to_thread(
            workbench_runtime._read_workbench_store
        )
        project, task = workbench_runtime._workbench_find_session(
            payload,
            task_id,
        )
        if not project or not task:
            return JSONResponse(
                {"error": "task not found", "code": "task_not_found"},
                status_code=404,
            )
        try:
            artifact, target = (
                workbench_runtime._workbench_artifact_download_target(
                    project,
                    task,
                    artifact_id,
                )
            )
        except LookupError as exc:
            return JSONResponse(
                {"error": str(exc), "code": "artifact_not_found"},
                status_code=404,
            )
        except ValueError as exc:
            return JSONResponse(
                {"error": str(exc), "code": "artifact_invalid"},
                status_code=400,
            )
        except FileNotFoundError as exc:
            return JSONResponse(
                {"error": str(exc), "code": "artifact_file_not_found"},
                status_code=404,
            )
        file_path = Path(target)
        filename = Path(
            str(artifact.get("name") or file_path.name)
        ).name or file_path.name
        return FileResponse(
            file_path,
            filename=filename,
            media_type=mimetypes.guess_type(filename)[0]
            or "application/octet-stream",
        )

    @router.get(
        "/v1/control/chats/{chat_id}/attachments/{attachment_id}",
        responses={
            **common_errors,
            200: {"content": {"application/octet-stream": {}}},
        },
        tags=["Control"],
        operation_id="control_v1_read_chat_attachment",
    )
    async def control_read_chat_attachment(chat_id: str, attachment_id: str):
        result = await chat_adapter["get_chat"](chat_id)
        if isinstance(result, JSONResponse):
            return _error_payload(result)
        chat = dict(result.get("chat") or {})
        if not chat:
            return JSONResponse(
                {"error": "chat not found", "code": "chat_not_found"},
                status_code=404,
            )
        try:
            attachment, target = referenced_chat_attachment_target(
                chat,
                attachment_id,
            )
        except LookupError as exc:
            return JSONResponse(
                {"error": str(exc), "code": "attachment_not_found"},
                status_code=404,
            )
        except FileNotFoundError as exc:
            return JSONResponse(
                {"error": str(exc), "code": "attachment_file_not_found"},
                status_code=404,
            )
        filename = Path(
            str(attachment.get("name") or target.name)
        ).name or target.name
        return FileResponse(
            target,
            filename=filename,
            media_type=str(
                attachment.get("content_type")
                or attachment.get("mediaType")
                or mimetypes.guess_type(filename)[0]
                or "application/octet-stream"
            ),
        )


__all__ = ["register_control_routes"]
