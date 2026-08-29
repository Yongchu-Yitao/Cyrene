"""Durable admission and audit lifecycle for bounded Workbench task runs.

Normal task requests remain bounded operations, but their ownership and audit
record must exist before the Agent can produce tool side effects.  This module
provides the common control-plane contract used by ``/runs``, ``/dispatch`` and
the direct ``/chat`` endpoint:

* one explicit in-process owner per task session;
* a durable ``running`` record before model/tool work starts;
* terminal reconciliation for success, rejection, cancellation and crashes;
* a ContextVar that lets route branches update the same run instead of creating
  a late, unrelated audit record.
"""

from __future__ import annotations

import asyncio
import contextvars
import copy
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from fastapi.responses import JSONResponse

from cyrene.localization import localized
from cyrene.runtime.run_coordinator import RunCoordinator, RunLease, run_coordinator_for
from cyrene.workbench.projects import project_repository, project_runtime


logger = logging.getLogger(__name__)
ResumeTaskRun = Callable[[str, str, dict[str, Any]], Awaitable[Any]]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_CURRENT_TASK_RUN_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "workbench_task_run_id", default=""
)


def coordinator_for(db_path: str) -> RunCoordinator:
    return run_coordinator_for(str(db_path or ""))


def current_task_run_id() -> str:
    return str(_CURRENT_TASK_RUN_ID.get() or "")


def bind_task_run_id(run_id: str) -> contextvars.Token[str]:
    return _CURRENT_TASK_RUN_ID.set(str(run_id or ""))


def reset_task_run_id(token: contextvars.Token[str]) -> None:
    _CURRENT_TASK_RUN_ID.reset(token)


def is_task_run_active(db_path: str, session_id: str) -> bool:
    return coordinator_for(db_path).get("task", session_id) is not None


def interrupt_task_run(db_path: str, session_id: str) -> bool:
    return coordinator_for(db_path).interrupt("task", session_id)


def _find_run(session: dict[str, Any], run_id: str) -> dict[str, Any] | None:
    return next(
        (
            run
            for run in session.get("runs") or []
            if isinstance(run, dict) and str(run.get("id") or "") == run_id
        ),
        None,
    )


def upsert_task_run(session: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    """Replace a provisional run while preserving its already-durable events."""
    runs = session.setdefault("runs", [])
    run_id = str(run.get("id") or "")
    for index, existing in enumerate(runs):
        if not isinstance(existing, dict) or str(existing.get("id") or "") != run_id:
            continue
        existing_events = [
            copy.deepcopy(item)
            for item in existing.get("events") or []
            if isinstance(item, dict)
        ]
        known = {str(item.get("id") or "") for item in existing_events}
        merged_events = existing_events + [
            copy.deepcopy(item)
            for item in run.get("events") or []
            if isinstance(item, dict) and str(item.get("id") or "") not in known
        ]
        merged = {**existing, **copy.deepcopy(run), "events": merged_events}
        runs[index] = merged
        return merged
    runs.append(copy.deepcopy(run))
    return runs[-1]


def begin_task_run(
    session_id: str,
    run_id: str,
    *,
    request_id: str,
    run_type: str,
    body: dict[str, Any],
) -> bool:
    """Persist admission before any task model or tool work can start."""
    payload = project_repository._read_workbench_store()
    project, session = project_repository._workbench_find_session(
        payload, str(session_id or "")
    )
    if not session or not project:
        return False
    now = _utc_now_iso()
    user_input = str(
        body.get("input")
        or body.get("message")
        or body.get("answer")
        or body.get("selected_option")
        or ""
    ).strip()
    attachments = body.get("attachments") if isinstance(body.get("attachments"), list) else []
    accepted_event = {
        "id": project_runtime._short_id("event"),
        "type": "RunAcceptedEvent",
        "runId": str(run_id),
        "createdAt": now,
        "body": user_input or (localized("[Attachment]", "[附件]") if attachments else ""),
        "runType": str(run_type or "task"),
    }
    run: dict[str, Any] = {
        "id": str(run_id),
        "taskId": str(session_id),
        "clientRequestId": str(request_id or ""),
        "runType": str(run_type or "task"),
        "userInput": user_input,
        "status": "running",
        "startedAt": now,
        "endedAt": None,
        "events": [accepted_event],
        "fileChanges": [],
        "toolCalls": [],
        "artifacts": [],
        "attachments": [
            {
                key: item.get(key)
                for key in ("id", "name", "url", "kind", "content_type", "size")
                if isinstance(item, dict) and item.get(key) is not None
            }
            for item in attachments
            if isinstance(item, dict)
        ],
        "mode": str(body.get("mode") or "auto"),
        "binding": {
            key: body.get(key)
            for key in (
                "stepId",
                "action",
                "planDefinitionRevision",
                "basePlanRevision",
            )
            if body.get(key) is not None
        },
        "previousTaskStatus": str(session.get("status") or "idle"),
        # ``body`` is already an API DTO projection and therefore JSON-safe.
        # Keeping it intact is what lets a new process replay the route-domain
        # work around the same durable Agent run instead of guessing from UI text.
        "resumeBody": copy.deepcopy(body),
        "error": None,
    }
    upsert_task_run(session, run)
    session.setdefault("events", []).append(accepted_event)
    session["activeRunId"] = str(run_id)
    session["updatedAt"] = now
    project["updatedAt"] = now
    project_repository._write_workbench_store(payload)
    return True


def _response_error(result: Any) -> tuple[int, str, str]:
    if isinstance(result, JSONResponse):
        try:
            payload = json.loads(bytes(result.body).decode("utf-8"))
        except Exception:
            payload = {}
        return (
            int(result.status_code),
            str(payload.get("error") or ""),
            str(payload.get("code") or ""),
        )
    if isinstance(result, dict):
        return 200, str(result.get("error") or ""), str(result.get("code") or "")
    status_code = getattr(result, "status_code", None)
    payload = getattr(result, "payload", None)
    if isinstance(status_code, int) and isinstance(payload, dict):
        return (
            status_code,
            str(payload.get("error") or ""),
            str(payload.get("code") or ""),
        )
    return 200, "", ""


def finish_task_run_if_open(
    session_id: str,
    run_id: str,
    *,
    result: Any = None,
    status: str = "",
    error: str = "",
    termination_reason: str = "",
) -> bool:
    """Settle a provisional audit unless the domain route already finalized it."""
    payload = project_repository._read_workbench_store()
    project, session = project_repository._workbench_find_session(
        payload, str(session_id or "")
    )
    if not session or not project:
        return False
    run = _find_run(session, str(run_id or ""))
    if run is None:
        if str(session.get("activeRunId") or "") == str(run_id or ""):
            session.pop("activeRunId", None)
            project_repository._write_workbench_store(payload)
        return False
    if str(run.get("status") or "") != "running":
        if str(session.get("activeRunId") or "") != str(run_id or ""):
            return False
        now = _utc_now_iso()
        route_status = str(run.get("status") or "completed")
        terminal_type = {
            "completed": "RunCompletedEvent",
            "awaiting_user": "RunAwaitingUserEvent",
            "cancelled": "RunCancelledEvent",
            "interrupted": "RunInterruptedEvent",
        }.get(route_status, "RunFailedEvent")
        if not any(
            isinstance(event, dict)
            and str(event.get("type") or "") == terminal_type
            for event in run.get("events") or []
        ):
            terminal_event = {
                "id": project_runtime._short_id("event"),
                "type": terminal_type,
                "runId": str(run_id),
                "createdAt": now,
                "body": str(run.get("error") or route_status),
            }
            run.setdefault("events", []).append(terminal_event)
            session.setdefault("events", []).append(terminal_event)
        session.pop("activeRunId", None)
        session["updatedAt"] = now
        project["updatedAt"] = now
        project_repository._write_workbench_store(payload)
        return True

    response_status, response_error, response_code = _response_error(result)
    terminal_status = str(status or "").strip()
    if not terminal_status:
        terminal_status = "completed" if response_status < 400 else "failed"
    now = _utc_now_iso()
    run["status"] = terminal_status
    run["endedAt"] = now
    run["error"] = str(error or response_error or "") or None
    if termination_reason:
        run["terminationReason"] = str(termination_reason)
    if response_code:
        run["errorCode"] = response_code
    if isinstance(result, dict) and result.get("replyKind"):
        run["replyKind"] = str(result.get("replyKind") or "")
    terminal_event = {
        "id": project_runtime._short_id("event"),
        "type": {
            "completed": "RunCompletedEvent",
            "cancelled": "RunCancelledEvent",
            "interrupted": "RunInterruptedEvent",
        }.get(terminal_status, "RunFailedEvent"),
        "runId": str(run_id),
        "createdAt": now,
        "body": str(run.get("error") or terminal_status),
    }
    run.setdefault("events", []).append(terminal_event)
    session.setdefault("events", []).append(terminal_event)
    if str(session.get("activeRunId") or "") == str(run_id or ""):
        session.pop("activeRunId", None)
    session["updatedAt"] = now
    project["updatedAt"] = now
    project_repository._write_workbench_store(payload)
    return True


def _resume_body(run: dict[str, Any]) -> dict[str, Any]:
    stored = run.get("resumeBody")
    if isinstance(stored, dict):
        return copy.deepcopy(stored)
    raise ValueError("Task run is missing its durable resumeBody request")


async def _resume_one_task_run(
    *,
    coordinator: RunCoordinator,
    lease: RunLease,
    session_id: str,
    run_id: str,
    run_type: str,
    body: dict[str, Any],
    resume_run: ResumeTaskRun,
) -> None:
    # Let the caller attach this task to the lease before cancellation can race
    # with the first await inside the route-domain handler.
    await asyncio.sleep(0)
    token = bind_task_run_id(run_id)
    try:
        result = await resume_run(session_id, run_type, body)
        finish_task_run_if_open(session_id, run_id, result=result)
    except asyncio.CancelledError:
        if str(lease.termination_reason or "") != "server_shutdown":
            finish_task_run_if_open(
                session_id,
                run_id,
                status="cancelled",
                error=localized(
                    "The task run was interrupted.",
                    "任务运行已被中断。",
                ),
                termination_reason=str(
                    lease.termination_reason or "user_interrupted"
                ),
            )
        raise
    except Exception:
        logger.exception(
            "Failed to resume Workbench Task run [session=%s run=%s]",
            session_id,
            run_id,
        )
        finish_task_run_if_open(
            session_id,
            run_id,
            status="failed",
            error=localized(
                "The interrupted task run could not be resumed.",
                "无法恢复被中断的任务运行。",
            ),
            termination_reason="process_resume_failed",
        )
    finally:
        reset_task_run_id(token)
        coordinator.release(lease)


async def recover_interrupted_task_runs(
    db_path: str,
    resume_run: ResumeTaskRun,
) -> int:
    """Rebind durable runs to their original ids and resume their ContextTrees."""

    project_repository._configure_workbench_store(str(db_path or ""))
    payload = project_repository._read_workbench_store()
    coordinator = coordinator_for(db_path)
    scheduled = 0
    for project in payload.get("projects") or []:
        if not isinstance(project, dict):
            continue
        for session in project.get("sessions") or []:
            if not isinstance(session, dict):
                continue
            session_id = str(session.get("id") or "")
            if not session_id:
                continue
            for run in session.get("runs") or []:
                if (
                    not isinstance(run, dict)
                    or str(run.get("status") or "") != "running"
                    or str(run.get("runType") or "") == "goal_loop"
                ):
                    continue
                run_id = str(run.get("id") or "")
                run_type = str(run.get("runType") or "execution")
                if not run_id:
                    continue
                try:
                    resume_body = _resume_body(run)
                except ValueError:
                    logger.warning(
                        "Interrupted Task run has no resumable request "
                        "[session=%s run=%s]",
                        session_id,
                        run_id,
                        exc_info=True,
                    )
                    finish_task_run_if_open(
                        session_id,
                        run_id,
                        status="failed",
                        error=localized(
                            "The interrupted task run cannot be resumed because its request is unavailable.",
                            "被中断任务的请求数据不可用，无法恢复运行。",
                        ),
                        termination_reason="process_resume_request_missing",
                    )
                    continue
                lease = coordinator.try_acquire(
                    "task",
                    session_id,
                    run_id,
                    request_id=str(run.get("clientRequestId") or ""),
                    run_type=run_type,
                    bind_current_task=False,
                    payload=copy.deepcopy(run),
                    metadata={"recovered": True},
                )
                if lease is None:
                    continue
                task = asyncio.create_task(
                    _resume_one_task_run(
                        coordinator=coordinator,
                        lease=lease,
                        session_id=session_id,
                        run_id=run_id,
                        run_type=run_type,
                        body=resume_body,
                        resume_run=resume_run,
                    ),
                    name=f"workbench-task-resume:{session_id}:{run_id}",
                )
                if not coordinator.attach_task(lease, task):
                    task.cancel()
                    coordinator.release(lease)
                    continue
                scheduled += 1
    return scheduled


async def shutdown_task_runs(db_path: str) -> None:
    """Cancel request tasks still owned during application shutdown."""
    coordinator = coordinator_for(db_path)
    leases = coordinator.active_leases(owner_type="task")
    current_task = asyncio.current_task()
    current_loop = asyncio.get_running_loop()
    local_tasks: list[asyncio.Task[Any]] = []
    for lease in leases:
        task = lease.task
        if task is None or task.done() or task is current_task:
            continue
        coordinator.interrupt(
            "task",
            lease.owner_id,
            reason="server_shutdown",
        )
        if task.get_loop() is current_loop:
            local_tasks.append(task)
    if local_tasks:
        await asyncio.gather(*local_tasks, return_exceptions=True)
