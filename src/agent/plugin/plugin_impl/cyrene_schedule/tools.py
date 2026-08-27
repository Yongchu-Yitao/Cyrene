"""Tool handlers and schemas for the editable schedule pack."""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any

from agent.plugin import Plugin, PluginContext

from .schedule_spec import next_run, occurrence_window, task_events

SERVICE_NAME = "schedules"
SCHEDULE_FIELDS = frozenset(
    {"schedule_type", "schedule_value", "schedule_timezone"}
)


def _service(context: PluginContext) -> Any:
    service = context.services.get(SERVICE_NAME)
    if service is None:
        raise RuntimeError("PluginContext.services['schedules'] is unavailable")
    return service


async def _ready(context: PluginContext) -> tuple[Any, Any]:
    service = _service(context)
    await service.ensure_ready()
    return service, service.scope(context)


def _task_id(arguments: dict[str, Any]) -> str:
    value = str(arguments.get("task_id") or "").strip()
    if not value:
        raise ValueError("task_id is required")
    return value


def _task_payload(task: Any) -> dict[str, Any]:
    return task.to_dict() if hasattr(task, "to_dict") else dict(task)


def _permission(arguments: dict[str, Any], source: str) -> str:
    if source == "workbench":
        return "workspace_only"
    value = str(arguments.get("permission_mode") or "workspace_only").strip()
    if value not in {"workspace_only", "full_access"}:
        raise ValueError("permission_mode must be workspace_only or full_access")
    return value


async def create_schedule(
    arguments: dict[str, Any], context: PluginContext
) -> dict[str, Any]:
    service, scope = await _ready(context)
    prompt = str(arguments.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt is required")
    kind = str(arguments.get("schedule_type") or "").strip().lower()
    value = str(arguments.get("schedule_value") or "").strip()
    timezone_name = str(arguments.get("schedule_timezone") or "UTC").strip() or "UTC"
    fire_at = next_run(kind, value, timezone_name=timezone_name)
    if kind == "once":
        value = fire_at
    action = str(arguments.get("action_type") or "agent_task").strip().lower()
    if action not in {"message", "agent_task"}:
        raise ValueError("action_type must be message or agent_task")
    task_id = await service.repository.create(
        chat_id=scope.chat_id,
        prompt=prompt,
        schedule_type=kind,
        schedule_value=value,
        next_run=fire_at,
        permission_mode=_permission(arguments, scope.source),
        project_id=scope.project_id,
        schedule_timezone=timezone_name,
        origin_session_id=scope.session_id,
        action_type=action,
    )
    task = await service.repository.get(task_id, scope.project_id)
    return {
        "ok": True,
        "id": task_id,
        "task": _task_payload(task),
        "message": f"Scheduled task {task_id}. Next run: {fire_at}",
    }


async def list_schedules(
    _arguments: dict[str, Any], context: PluginContext
) -> dict[str, Any]:
    service, scope = await _ready(context)
    tasks = [
        _task_payload(task) for task in await service.repository.list(scope.project_id)
    ]
    return {"ok": True, "count": len(tasks), "tasks": tasks}


async def list_occurrences(
    arguments: dict[str, Any], context: PluginContext
) -> dict[str, Any]:
    service, scope = await _ready(context)
    start_at, end_at = occurrence_window(
        str(arguments.get("start") or ""),
        str(arguments.get("end") or ""),
    )
    events: list[dict[str, Any]] = []
    for task in await service.repository.list(scope.project_id):
        events.extend(task_events(_task_payload(task), start_at, end_at))
    events.sort(key=lambda event: str(event.get("start") or ""))
    return {
        "ok": True,
        "events": events,
        "start": start_at.isoformat(),
        "end": end_at.isoformat(),
    }


async def edit_schedule(
    arguments: dict[str, Any], context: PluginContext
) -> dict[str, Any]:
    service, scope = await _ready(context)
    task_id = _task_id(arguments)
    task = await service.repository.get(task_id, scope.project_id)
    if task is None:
        raise LookupError(f"scheduled task not found: {task_id}")

    updates: dict[str, Any] = {}
    if "prompt" in arguments:
        prompt = str(arguments["prompt"] or "").strip()
        if not prompt:
            raise ValueError("prompt cannot be empty")
        updates["prompt"] = prompt
    if "action_type" in arguments:
        action = str(arguments["action_type"] or "").strip().lower()
        if action not in {"message", "agent_task"}:
            raise ValueError("action_type must be message or agent_task")
        updates["action_type"] = action
    if "status" in arguments:
        status = str(arguments["status"] or "").strip().lower()
        if status not in {"active", "paused"}:
            raise ValueError("status must be active or paused")
        updates["status"] = status

    schedule_changed = any(field in arguments for field in SCHEDULE_FIELDS)
    if schedule_changed:
        kind = str(arguments.get("schedule_type", task.schedule_type)).strip().lower()
        value = str(arguments.get("schedule_value", task.schedule_value)).strip()
        zone = str(
            arguments.get("schedule_timezone", task.schedule_timezone or "UTC")
        ).strip() or "UTC"
        fire_at = next_run(kind, value, timezone_name=zone)
        if kind == "once":
            value = fire_at
        updates.update(
            {
                "schedule_type": kind,
                "schedule_value": value,
                "schedule_timezone": zone,
                "next_run": fire_at,
            }
        )
    elif updates.get("status") == "active" and task.status != "active":
        updates["next_run"] = next_run(
            task.schedule_type,
            task.schedule_value,
            timezone_name=task.schedule_timezone,
        )

    sensitive_edit = "prompt" in updates or "action_type" in updates
    if "permission_mode" in arguments:
        updates["permission_mode"] = _permission(arguments, scope.source)
    elif scope.source == "workbench" and (sensitive_edit or task.permission_mode == "full_access"):
        updates["permission_mode"] = "workspace_only"
    elif sensitive_edit and task.permission_mode == "full_access":
        # A changed action is a new grant. Preserve full access only when the
        # caller explicitly supplies it, causing the exact new call to pass
        # through PreToolUse review.
        updates["permission_mode"] = "workspace_only"

    if not updates:
        raise ValueError("no editable fields were provided")
    if updates.get("status") == "paused":
        await service.cancel_active(task_id, "schedule paused")
    changed = await service.repository.edit(task_id, updates, scope.project_id)
    if not changed:
        raise LookupError(f"scheduled task not found: {task_id}")
    updated = await service.repository.get(task_id, scope.project_id)
    return {"ok": True, "task": _task_payload(updated), "changed": sorted(updates)}


async def pause_schedule(
    arguments: dict[str, Any], context: PluginContext
) -> dict[str, Any]:
    service, scope = await _ready(context)
    task_id = _task_id(arguments)
    await service.cancel_active(task_id, "schedule paused")
    if not await service.repository.update_status(
        task_id, "paused", project_id=scope.project_id
    ):
        raise LookupError(f"scheduled task not found: {task_id}")
    return {"ok": True, "id": task_id, "status": "paused"}


async def resume_schedule(
    arguments: dict[str, Any], context: PluginContext
) -> dict[str, Any]:
    service, scope = await _ready(context)
    task_id = _task_id(arguments)
    task = await service.repository.get(task_id, scope.project_id)
    if task is None:
        raise LookupError(f"scheduled task not found: {task_id}")
    fire_at = next_run(
        task.schedule_type,
        task.schedule_value,
        timezone_name=task.schedule_timezone,
    )
    if not await service.repository.update_status(
        task_id,
        "active",
        project_id=scope.project_id,
        next_run=fire_at,
    ):
        raise LookupError(f"scheduled task not found: {task_id}")
    return {"ok": True, "id": task_id, "status": "active", "next_run": fire_at}


async def cancel_schedule(
    arguments: dict[str, Any], context: PluginContext
) -> dict[str, Any]:
    service, scope = await _ready(context)
    task_id = _task_id(arguments)
    await service.cancel_active(task_id, "schedule cancelled")
    if not await service.repository.delete(task_id, scope.project_id):
        raise LookupError(f"scheduled task not found: {task_id}")
    return {"ok": True, "id": task_id, "cancelled": True}


async def list_runs(
    arguments: dict[str, Any], context: PluginContext
) -> dict[str, Any]:
    service, scope = await _ready(context)
    task_id = _task_id(arguments)
    if await service.repository.get(task_id, scope.project_id) is None:
        raise LookupError(f"scheduled task not found: {task_id}")
    runs = await service.repository.list_runs(
        task_id,
        project_id=scope.project_id,
        limit=int(arguments.get("limit") or 20),
    )
    return {"ok": True, "task_id": task_id, "runs": runs}


async def _lease_heartbeat(service: Any, claim: Any) -> None:
    while True:
        await asyncio.sleep(30)
        if not await service.repository.renew_lease(claim, lease_seconds=120):
            return


async def _execute_claim(service: Any, claim: Any) -> dict[str, Any]:
    started = time.monotonic()
    heartbeat = asyncio.create_task(_lease_heartbeat(service, claim))
    task = claim.task
    try:
        if task.action_type == "message":
            result = task.prompt
        else:
            result = await service.run_agent(task, claim.run_id)
        if not await service.repository.renew_lease(claim, lease_seconds=120):
            raise asyncio.CancelledError("schedule is no longer active")
        await service.deliver(task, result, run_id=claim.run_id, error=False)
        if task.schedule_type == "once":
            next_fire, task_status = None, "completed"
        else:
            next_fire = next_run(
                task.schedule_type,
                task.schedule_value,
                timezone_name=task.schedule_timezone,
                now=datetime.now(timezone.utc),
            )
            task_status = "active"
        finalized = await service.repository.finalize_claim(
            claim,
            run_status="success",
            result=str(result),
            error=None,
            duration_ms=int((time.monotonic() - started) * 1000),
            next_run=next_fire,
            task_status=task_status,
        )
        return {"task_id": task.id, "run_id": claim.run_id, "ok": finalized}
    except asyncio.CancelledError:
        await asyncio.shield(
            service.repository.release_claim(claim, reason="execution cancelled")
        )
        raise
    except Exception as exc:
        error = str(exc) or type(exc).__name__
        try:
            await service.deliver(task, error, run_id=claim.run_id, error=True)
        except Exception:
            pass
        next_fire: str | None = None
        task_status = "failed" if task.schedule_type == "once" else "active"
        if task.schedule_type != "once":
            try:
                next_fire = next_run(
                    task.schedule_type,
                    task.schedule_value,
                    timezone_name=task.schedule_timezone,
                )
            except Exception:
                task_status = "failed"
        await service.repository.finalize_claim(
            claim,
            run_status="error",
            result=None,
            error=error,
            duration_ms=int((time.monotonic() - started) * 1000),
            next_run=next_fire,
            task_status=task_status,
        )
        return {"task_id": task.id, "run_id": claim.run_id, "ok": False, "error": error}
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)


async def schedule_tick(
    arguments: dict[str, Any], context: PluginContext
) -> dict[str, Any]:
    service = _service(context)
    await service.ensure_ready()
    claims = await service.repository.claim_due(
        limit=int(arguments.get("limit") or 10), lease_seconds=120
    )
    if not claims:
        return {"ok": True, "claimed": 0, "runs": []}
    runs = await asyncio.gather(
        *(asyncio.create_task(_execute_claim(service, claim)) for claim in claims),
        return_exceptions=True,
    )
    return {
        "ok": True,
        "claimed": len(claims),
        "runs": [
            item if isinstance(item, dict) else {"ok": False, "error": str(item)}
            for item in runs
        ],
    }


def _schema(
    properties: dict[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required or ()),
        "additionalProperties": False,
    }


TASK_ID = {"type": "string", "minLength": 1}
SCHEDULE_PROPERTIES = {
    "schedule_type": {"type": "string", "enum": ["once", "cron", "interval"]},
    "schedule_value": {"type": "string"},
    "schedule_timezone": {"type": "string", "minLength": 1},
}


def _plugin(
    name: str,
    description: str,
    schema: dict[str, Any],
    handler: Any,
    *,
    allow_parallel: bool = False,
    timeout: float = 30.0,
    metadata: dict[str, Any] | None = None,
) -> Plugin:
    return Plugin(
        name=name,
        description=description,
        input_schema=schema,
        handler=handler,
        allow_parallel=allow_parallel,
        timeout_seconds=timeout,
        metadata=metadata or {},
    )


plugins = (
    _plugin(
        "schedule.create",
        "Create an exact-message or Agent scheduled task in the current project.",
        _schema(
            {
                "prompt": {"type": "string", "minLength": 1},
                **SCHEDULE_PROPERTIES,
                "action_type": {"type": "string", "enum": ["message", "agent_task"]},
                "permission_mode": {
                    "type": "string",
                    "enum": ["workspace_only", "full_access"],
                },
            },
            ["prompt", "schedule_type", "schedule_value"],
        ),
        create_schedule,
    ),
    _plugin(
        "schedule.list",
        "List scheduled tasks in the current project.",
        _schema({}),
        list_schedules,
        allow_parallel=True,
    ),
    _plugin(
        "schedule.occurrences",
        "Expand scheduled tasks into calendar events for a requested time window.",
        _schema(
            {
                "start": {"type": "string"},
                "end": {"type": "string"},
            }
        ),
        list_occurrences,
        allow_parallel=True,
        metadata={"model_visible": False},
    ),
    _plugin(
        "schedule.edit",
        "Partially edit one scheduled task; changed grants are reviewed again.",
        _schema(
            {
                "task_id": TASK_ID,
                "prompt": {"type": "string", "minLength": 1},
                **SCHEDULE_PROPERTIES,
                "action_type": {"type": "string", "enum": ["message", "agent_task"]},
                "permission_mode": {
                    "type": "string",
                    "enum": ["workspace_only", "full_access"],
                },
                "status": {"type": "string", "enum": ["active", "paused"]},
            },
            ["task_id"],
        ),
        edit_schedule,
    ),
    _plugin(
        "schedule.pause",
        "Pause a scheduled task and cancel its currently running occurrence.",
        _schema({"task_id": TASK_ID}, ["task_id"]),
        pause_schedule,
    ),
    _plugin(
        "schedule.resume",
        "Resume a paused scheduled task and calculate its next occurrence.",
        _schema({"task_id": TASK_ID}, ["task_id"]),
        resume_schedule,
    ),
    _plugin(
        "schedule.cancel",
        "Cancel a running occurrence and permanently delete its scheduled task.",
        _schema({"task_id": TASK_ID}, ["task_id"]),
        cancel_schedule,
    ),
    _plugin(
        "schedule.runs",
        "List durable execution history for one scheduled task.",
        _schema(
            {
                "task_id": TASK_ID,
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            ["task_id"],
        ),
        list_runs,
        allow_parallel=True,
    ),
    _plugin(
        "schedule.tick",
        "Claim and execute due schedules. This host-triggered tool is hidden from models.",
        _schema({"limit": {"type": "integer", "minimum": 1, "maximum": 100}}),
        schedule_tick,
        timeout=86400.0,
        metadata={
            "model_visible": False,
            "background_job": {
                "id": "scheduled_tasks",
                "interval_seconds": max(
                    1, int(os.environ.get("SCHEDULER_INTERVAL", "60") or "60")
                ),
                "coalesce": True,
                "max_instances": 1,
                "run_on_start": True,
            },
        },
    ),
)


__all__ = ["plugins"]
