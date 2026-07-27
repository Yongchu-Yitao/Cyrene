"""Typed remote-command application adapter.

The encrypted gateway calls this adapter after trust, capability, project
scope, replay, and idempotency checks.  It deliberately maps a fixed command
enum to existing Workbench application services; it never exposes arbitrary
HTTP routes, native tools, Python calls, or shell execution.
"""

from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any

from fastapi.responses import JSONResponse

from cyrene.runtime.remote_control import (
    REMOTE_CAPABILITIES,
    RemoteControlStore,
    RemoteGateway,
    register_remote_gateway,
    unregister_remote_gateway,
)
from cyrene.runtime.remote_pairing import DirectPairingServer
from cyrene.workbench import runtime as workbench_runtime
from route import schemas as api_models

_MAX_ARTIFACT_BYTES = 10 * 1024 * 1024
_REMOTE_PUBLIC_EVENT_TYPES = {
    "ack",
    "awaiting_user",
    "error",
    "guidance_received",
    "intermediate_message",
    "interrupted",
    "reply_delta",
    "reply_done",
    "reply_start",
    "run_finalizing",
    "saved",
}


def _public_pending_question(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result = {
        key: value[key]
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
        if key in value
    }
    return result or None


def _public_intermediate_message(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result = {
        key: value[key]
        for key in (
            "id",
            "role",
            "content",
            "text",
            "kind",
            "status",
            "createdAt",
        )
        if key in value
    }
    return result or None


def public_remote_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Return the fixed public event DTO; unknown/internal events are omitted."""
    event_type = str(event.get("type") or "")
    if event_type not in _REMOTE_PUBLIC_EVENT_TYPES:
        return None
    result: dict[str, Any] = {
        "type": event_type,
        "cursor": int(event.get("_seq") or 0),
        "run_id": str(event.get("runId") or ""),
    }
    for key in ("chatId", "status", "code", "delta", "response", "message"):
        value = event.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            if key in event:
                result[key] = value
    if event_type == "awaiting_user":
        question = _public_pending_question(event.get("pending_question"))
        if question is not None:
            result["pending_question"] = question
    if event_type == "intermediate_message":
        message = _public_intermediate_message(event.get("message"))
        if message is not None:
            result["message"] = message
    return result


def _json_response_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, JSONResponse):
        return dict(value or {}) if isinstance(value, dict) else {"data": value}
    try:
        payload = json.loads(bytes(value.body).decode("utf-8"))
    except Exception:
        payload = {"error": "remote command failed"}
    if not isinstance(payload, dict):
        payload = {"error": str(payload)}
    return {
        "ok": False,
        "status_code": int(value.status_code),
        **payload,
    }


def _require_text(
    payload: dict[str, Any],
    field: str,
    *,
    max_length: int = 200_000,
) -> str:
    value = str(payload.get(field) or "").strip()
    if not value:
        raise ValueError(f"{field} is required")
    if len(value) > max_length:
        raise ValueError(f"{field} is too long")
    return value


def _permission_mode(
    payload: dict[str, Any],
    *,
    allowed: frozenset[str],
    default: str = "default",
) -> str:
    value = str(payload.get("permission_mode") or default)
    if value not in allowed:
        expected = ", ".join(sorted(allowed))
        raise ValueError(f"permission_mode must be one of: {expected}")
    return value


def _task_summary(task: dict[str, Any]) -> dict[str, Any]:
    plan = [
        {
            key: item[key]
            for key in ("id", "title", "description", "status")
            if key in item
        }
        for item in task.get("plan") or []
        if isinstance(item, dict)
    ]
    return {
        "id": str(task.get("id") or ""),
        "project_id": str(task.get("projectId") or ""),
        "title": str(task.get("title") or ""),
        "goal": str(task.get("goal") or ""),
        "status": str(task.get("status") or "idle"),
        "priority": str(task.get("priority") or "medium"),
        "created_at": str(task.get("createdAt") or ""),
        "updated_at": str(task.get("updatedAt") or ""),
        "plan": plan,
        "pending_question": _public_pending_question(
            task.get("pendingQuestion")
        ),
        "artifact_count": len(task.get("artifacts") or []),
    }


def _attachment_summary(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    result = {
        key: item[key]
        for key in ("id", "name", "type", "mediaType", "size")
        if key in item
    }
    return result or None


def _chat_summary(chat: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(chat.get("id") or ""),
        "project_id": str(chat.get("projectId") or ""),
        "title": str(chat.get("title") or ""),
        "status": str(chat.get("status") or "idle"),
        "created_at": str(chat.get("createdAt") or ""),
        "updated_at": str(chat.get("updatedAt") or ""),
        "message_count": int(
            chat.get("messageCount") or len(chat.get("messages") or [])
        ),
        "awaiting_user": isinstance(chat.get("pendingQuestion"), dict),
    }


def _chat_detail(chat: dict[str, Any]) -> dict[str, Any]:
    messages = []
    for raw in chat.get("messages") or []:
        if not isinstance(raw, dict):
            continue
        attachments = [
            summary
            for item in raw.get("attachments") or []
            if (summary := _attachment_summary(item)) is not None
        ]
        messages.append(
            {
                "id": str(raw.get("id") or ""),
                "role": str(raw.get("role") or ""),
                "content": str(raw.get("content") or ""),
                "created_at": str(raw.get("createdAt") or ""),
                "question_id": str(raw.get("questionId") or ""),
                "question_kind": str(raw.get("questionKind") or ""),
                "attachments": attachments,
            }
        )
    return {**_chat_summary(chat), "messages": messages}


def _artifact_summary(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    artifact_id = str(item.get("id") or "")
    if not artifact_id:
        return None
    return {
        "id": artifact_id,
        "name": str(item.get("name") or ""),
        "type": str(item.get("type") or ""),
        "status": str(item.get("status") or ""),
        "created_at": str(item.get("createdAt") or ""),
        "size": int(item["size"]) if item.get("size") is not None else None,
    }


def _task_detail(task: dict[str, Any]) -> dict[str, Any]:
    plan = [
        {
            key: item[key]
            for key in ("id", "title", "description", "status")
            if key in item
        }
        for item in task.get("plan") or []
        if isinstance(item, dict)
    ]
    artifacts = [
        summary
        for item in task.get("artifacts") or []
        if (summary := _artifact_summary(item)) is not None
    ]
    goal_loop = task.get("goalLoop")
    return {
        **_task_summary(task),
        "plan": plan,
        "pending_question": _public_pending_question(
            task.get("pendingQuestion")
        ),
        "artifacts": artifacts,
        "goal_loop": (
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
    }


class RemoteCommandExecutor:
    """Execute the protocol's fixed command set against local Workbench state."""

    def __init__(
        self,
        *,
        store: RemoteControlStore,
        chat_adapter: dict[str, Any],
        project_adapter: dict[str, Any],
        task_adapter: dict[str, Any],
        goal_loop_adapter: dict[str, Any] | None = None,
    ) -> None:
        self.store = store
        self.chat = chat_adapter
        self.project = project_adapter
        self.task = task_adapter
        self.goal_loop = goal_loop_adapter or {}

    async def __call__(
        self,
        peer_device_id: str,
        command: str,
        payload: dict[str, Any],
        project_id: str,
    ) -> dict[str, Any]:
        command = str(command or "")
        payload = dict(payload or {})

        if command == "capabilities.read":
            return {
                "ok": True,
                "protocol_version": 1,
                "capabilities": sorted(REMOTE_CAPABILITIES),
            }
        if command == "projects.list":
            return await self._projects_list(peer_device_id)
        if command == "chats.list":
            return await self._chats_list(project_id)
        if command == "chats.create":
            return await self._chats_create(project_id, payload)
        if command == "chats.read":
            return await self._chats_read(project_id, payload)
        if command == "chats.send":
            return await self._chats_send(project_id, payload)
        if command == "runs.read":
            return await self._runs_read(project_id, payload)
        if command == "runs.events":
            return await self._runs_events(project_id, payload)
        if command == "runs.guide":
            return await self._runs_guide(project_id, payload)
        if command == "runs.interrupt":
            return await self._runs_interrupt(project_id, payload)
        if command == "tasks.list":
            return await self._tasks_list(project_id)
        if command == "tasks.create":
            return await self._tasks_create(project_id, payload)
        if command == "tasks.read":
            return await self._tasks_read(project_id, payload)
        if command == "tasks.dispatch":
            return await self._tasks_dispatch(project_id, payload)
        if command == "tasks.approve_plan":
            return await self._tasks_approve_plan(project_id, payload)
        if command == "tasks.run_step":
            return await self._tasks_run_step(project_id, payload)
        if command in {"tasks.pause", "tasks.resume", "tasks.cancel"}:
            return await self._tasks_control(command, project_id, payload)
        if command == "approvals.respond":
            return await self._approvals_respond(project_id, payload)
        if command == "artifacts.list":
            return await self._artifacts_list(project_id, payload)
        if command == "artifacts.read":
            return await self._artifacts_read(project_id, payload)
        return {
            "ok": False,
            "code": "remote_command_unsupported",
            "error": f"unsupported remote command: {command}",
        }

    async def _projects_list(self, peer_device_id: str) -> dict[str, Any]:
        store = workbench_runtime._read_workbench_store_lightweight()
        peer = self.store.get_peer(peer_device_id)
        shared_project_ids = set(
            peer.get("granted_project_scopes") or [] if peer else []
        )
        return {
            "ok": True,
            "projects": [
                {
                    "id": str(project.get("id") or ""),
                    "name": str(project.get("name") or ""),
                    "status": str(project.get("status") or "active"),
                    "updated_at": str(project.get("updatedAt") or ""),
                }
                for project in store.get("projects") or []
                if isinstance(project, dict)
                and str(project.get("id") or "") in shared_project_ids
            ],
        }

    async def _chats_list(self, project_id: str) -> dict[str, Any]:
        result = _json_response_payload(
            await self.chat["list_chats"](project=project_id)
        )
        if result.get("ok") is False:
            return result
        return {
            "ok": True,
            "chats": [
                _chat_summary(item)
                for item in result.get("chats") or []
                if isinstance(item, dict)
            ],
        }

    async def _chats_create(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        result = _json_response_payload(
            await self.chat["create_chat"](
                api_models.ChatCreateBody(
                    project=project_id,
                    title=str(payload.get("title") or "")[:160],
                )
            )
        )
        return {"ok": True, **result} if result.get("ok") is not False else result

    async def _chat_for_project(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> tuple[str, dict[str, Any] | None]:
        chat_id = _require_text(payload, "chat_id", max_length=200)
        result = _json_response_payload(await self.chat["get_chat"](chat_id))
        if result.get("ok") is False:
            return chat_id, result
        chat = dict(result.get("chat") or {})
        if str(chat.get("projectId") or "") != project_id:
            return chat_id, {
                "ok": False,
                "code": "remote_project_mismatch",
                "error": "chat does not belong to the authorized project",
            }
        return chat_id, chat

    async def _chats_read(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        _chat_id, chat = await self._chat_for_project(project_id, payload)
        if chat is None or chat.get("ok") is False:
            return dict(chat or {"ok": False, "error": "chat not found"})
        return {"ok": True, "chat": _chat_detail(chat)}

    async def _chats_send(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        chat_id, chat = await self._chat_for_project(project_id, payload)
        if chat is None or chat.get("ok") is False:
            return dict(chat or {"ok": False, "error": "chat not found"})
        result = _json_response_payload(
            await self.chat["send_chat_detached"](
                chat_id,
                {
                    "message": _require_text(payload, "message"),
                    "mode": _permission_mode(
                        payload,
                        allowed=frozenset({"default", "plan"}),
                    ),
                    "lang": str(payload.get("language") or ""),
                    "stream": True,
                },
                detached=True,
            )
        )
        return {"ok": True, **result} if result.get("ok") is not False else result

    async def _run_for_project(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> tuple[Any, dict[str, Any] | None]:
        run_id = _require_text(payload, "run_id", max_length=200)
        run = self.chat["run_manager"].get_replayable_by_run_id(run_id)
        if run is None:
            return None, {
                "ok": False,
                "code": "run_not_found",
                "error": "run not found",
            }
        result = _json_response_payload(await self.chat["get_chat"](run.chat_id))
        if result.get("ok") is False:
            return None, result
        chat = dict(result.get("chat") or {})
        if str(chat.get("projectId") or "") != project_id:
            return None, {
                "ok": False,
                "code": "remote_project_mismatch",
                "error": "run does not belong to the authorized project",
            }
        return run, None

    async def _runs_read(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        run, error = await self._run_for_project(project_id, payload)
        if error:
            return error
        return {
            "ok": True,
            "run": {
                "run_id": run.run_id,
                "chat_id": run.chat_id,
                "status": run.status,
                "created_at": run.created_at,
                "completed": run.done.is_set(),
                "termination_reason": run.termination_reason,
            },
        }

    async def _runs_events(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        run, error = await self._run_for_project(project_id, payload)
        if error:
            return error
        cursor = max(0, int(payload.get("cursor") or 0))
        limit = max(1, min(int(payload.get("limit") or 200), 1000))
        raw_events = [
            event
            for event in run.events
            if int(event.get("_seq") or 0) > cursor
        ][:limit]
        events = [
            public
            for event in raw_events
            if (public := public_remote_event(event)) is not None
        ]
        return {
            "ok": True,
            "events": events,
            "next_cursor": max(
                [cursor, *[int(event.get("_seq") or 0) for event in raw_events]]
            ),
            "completed": run.done.is_set(),
        }

    async def _runs_guide(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        chat_id, chat = await self._chat_for_project(project_id, payload)
        if chat is None or chat.get("ok") is False:
            return dict(chat or {"ok": False, "error": "chat not found"})
        result = _json_response_payload(
            await self.chat["guide_chat"](
                chat_id,
                api_models.ChatGuidanceBody(
                    message=_require_text(payload, "message"),
                    clientRequestId=str(payload.get("request_id") or ""),
                ),
            )
        )
        return {"ok": True, **result} if result.get("ok") is not False else result

    async def _runs_interrupt(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        chat_id, chat = await self._chat_for_project(project_id, payload)
        if chat is None or chat.get("ok") is False:
            return dict(chat or {"ok": False, "error": "chat not found"})
        interrupted = self.chat["run_manager"].interrupt(chat_id)
        return {
            "ok": interrupted,
            "interrupted": interrupted,
            "code": "" if interrupted else "chat_not_running",
        }

    async def _tasks_list(self, project_id: str) -> dict[str, Any]:
        result = _json_response_payload(
            await self.project["list_tasks"](project_id)
        )
        if result.get("ok") is False:
            return result
        return {
            "ok": True,
            "tasks": [
                _task_summary(item)
                for item in result.get("sessions") or []
                if isinstance(item, dict) and str(item.get("kind") or "task") == "task"
            ],
        }

    async def _tasks_create(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        result = _json_response_payload(
            await self.project["create_task"](
                project_id,
                api_models.SessionCreateBody(
                    title=str(payload.get("title") or "")[:160],
                    goal=_require_text(payload, "goal", max_length=50_000),
                    priority=str(payload.get("priority") or "medium"),
                ),
            )
        )
        if result.get("ok") is False:
            return result
        return {"ok": True, "task": _task_summary(dict(result.get("session") or {}))}

    async def _task_for_project(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
        task_id = _require_text(payload, "task_id", max_length=200)
        result = _json_response_payload(await self.task["get_task"](task_id))
        if result.get("ok") is False:
            return task_id, None, result
        task = dict(result.get("session") or {})
        if str(task.get("projectId") or result.get("projectId") or "") != project_id:
            return task_id, None, {
                "ok": False,
                "code": "remote_project_mismatch",
                "error": "task does not belong to the authorized project",
            }
        return task_id, task, None

    async def _tasks_read(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        _task_id, task, error = await self._task_for_project(project_id, payload)
        return error or {"ok": True, "task": _task_detail(task or {})}

    async def _tasks_dispatch(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        task_id, _task, error = await self._task_for_project(project_id, payload)
        if error:
            return error
        result = _json_response_payload(
            await self.task["dispatch_task"](
                task_id,
                api_models.AgentInputBody(
                    input=_require_text(payload, "message"),
                    mode=_permission_mode(
                        payload,
                        allowed=frozenset({"default"}),
                    ),
                    command=str(payload.get("command") or ""),
                ),
            )
        )
        if result.get("ok") is False:
            return result
        return {
            "ok": True,
            "task": _task_summary(dict(result.get("session") or {})),
            "reply_kind": str(result.get("replyKind") or ""),
        }

    async def _tasks_approve_plan(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        task_id, task, error = await self._task_for_project(project_id, payload)
        if error:
            return error
        revision = int((task or {}).get("planDefinitionRevision") or 0)
        if not (task or {}).get("plan"):
            return {
                "ok": False,
                "code": "task_plan_empty",
                "error": "task plan is empty",
            }
        result = _json_response_payload(
            await self.task["update_task"](
                task_id,
                api_models.SessionUpdateBody(
                    status="waiting_for_approval",
                    approvedPlanDefinitionRevision=revision,
                ),
            )
        )
        if result.get("ok") is False:
            return result
        return {
            "ok": True,
            "task": _task_detail(dict(result.get("session") or {})),
            "approved_plan_definition_revision": revision,
        }

    async def _tasks_run_step(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        task_id, task, error = await self._task_for_project(project_id, payload)
        if error:
            return error
        step_id = _require_text(payload, "step_id", max_length=200)
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
            return {
                "ok": False,
                "code": "step_not_found",
                "error": "task step not found",
            }
        revision = int((task or {}).get("planDefinitionRevision") or 0)
        approved_revision = (task or {}).get("approvedPlanDefinitionRevision")
        if (
            approved_revision is None
            or int(approved_revision) != revision
        ):
            return {
                "ok": False,
                "code": "plan_not_approved",
                "error": "current task plan has not been approved",
            }
        for item in plan:
            if str(item.get("id") or "") == step_id:
                item["status"] = "running"
                item["currentAction"] = "Remote controller started this step."
        prepared = _json_response_payload(
            await self.task["update_task"](
                task_id,
                api_models.SessionUpdateBody(status="running", plan=plan),
            )
        )
        if prepared.get("ok") is False:
            return prepared
        result = _json_response_payload(
            await self.task["create_run"](
                task_id,
                api_models.AgentInputBody(
                    input=_require_text(payload, "message"),
                    mode=_permission_mode(
                        payload,
                        allowed=frozenset({"default"}),
                    ),
                    stepId=step_id,
                    stepTitle=str(step.get("title") or "")[:1000],
                    action="spawn_subagent",
                    meta={"scope": "plan_step", "continueAll": False},
                    planDefinitionRevision=revision,
                ),
            )
        )
        if result.get("ok") is False:
            return result
        updated = dict(result.get("session") or {})
        if str(updated.get("status") or "") == "waiting_for_user":
            return {"ok": True, "task": _task_detail(updated)}
        returned_plan = [
            dict(item)
            for item in updated.get("plan") or plan
            if isinstance(item, dict)
        ]
        for item in returned_plan:
            if str(item.get("id") or "") == step_id:
                item["status"] = "completed"
                item["currentAction"] = "Remote-controlled step completed."
        resolved = {"completed", "done", "skipped"}
        fully_done = bool(returned_plan) and all(
            str(item.get("status") or "") in resolved
            for item in returned_plan
        )
        finalized = _json_response_payload(
            await self.task["update_task"](
                task_id,
                api_models.SessionUpdateBody(
                    status="review" if fully_done else "paused",
                    plan=returned_plan,
                ),
            )
        )
        if finalized.get("ok") is False:
            return finalized
        return {
            "ok": True,
            "task": _task_detail(dict(finalized.get("session") or {})),
            "step_id": step_id,
            "fully_done": fully_done,
        }

    async def _tasks_control(
        self,
        command: str,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        task_id, task, error = await self._task_for_project(project_id, payload)
        if error:
            return error
        if self.goal_loop:
            goal_state = _json_response_payload(
                await self.goal_loop["get"](task_id)
            )
            goal_loop = goal_state.get("goalLoop")
            if (
                isinstance(goal_loop, dict)
                and str(goal_loop.get("status") or "")
                not in {"completed", "failed", "cancelled"}
            ):
                action = command.removeprefix("tasks.")
                controlled = _json_response_payload(
                    await self.goal_loop[action](task_id)
                )
                if controlled.get("ok") is False:
                    return controlled
                return {
                    "ok": True,
                    "task": _task_detail(
                        dict(controlled.get("session") or {})
                    ),
                    "goal_loop": controlled.get("goalLoop"),
                }
        current = str((task or {}).get("status") or "")
        if command == "tasks.pause" and current not in {
            "running",
            "waiting_for_user",
        }:
            return {
                "ok": False,
                "code": "invalid_status_transition",
                "error": "only an active task can be paused",
            }
        if command == "tasks.resume" and current != "paused":
            return {
                "ok": False,
                "code": "invalid_status_transition",
                "error": "only a paused task can be resumed",
            }
        next_status = {
            "tasks.pause": "paused",
            "tasks.resume": "idle",
            "tasks.cancel": "cancelled",
        }[command]
        if command in {"tasks.pause", "tasks.cancel"}:
            from cyrene.agent import interrupt_active_run

            interrupt_active_run(session_id=task_id)
        result = _json_response_payload(
            await self.task["update_task"](
                task_id,
                api_models.SessionUpdateBody(status=next_status),
            )
        )
        if result.get("ok") is False:
            return result
        return {"ok": True, "task": _task_summary(dict(result.get("session") or {}))}

    async def _approvals_respond(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if str(payload.get("task_id") or "").strip():
            task_id, _task, error = await self._task_for_project(
                project_id,
                payload,
            )
            if error:
                return error
            result = _json_response_payload(
                await self.task["answer_task"](
                    task_id,
                    api_models.AnswerBody(
                        question_id=_require_text(
                            payload,
                            "question_id",
                            max_length=500,
                        ),
                        answer=_require_text(payload, "answer"),
                        mode=_permission_mode(
                            payload,
                            allowed=frozenset({"default"}),
                        ),
                    ),
                )
            )
            if result.get("ok") is False:
                return result
            return {
                "ok": True,
                "task": _task_detail(dict(result.get("session") or {})),
                "awaiting_user": bool(result.get("awaitingUser")),
            }
        chat_id, chat = await self._chat_for_project(project_id, payload)
        if chat is None or chat.get("ok") is False:
            return dict(chat or {"ok": False, "error": "chat not found"})
        result = _json_response_payload(
            await self.chat["answer_chat"](
                chat_id,
                api_models.AnswerBody(
                    question_id=_require_text(
                        payload,
                        "question_id",
                        max_length=500,
                    ),
                    answer=_require_text(payload, "answer"),
                    mode=_permission_mode(
                        payload,
                        allowed=frozenset({"default"}),
                    ),
                ),
            )
        )
        return {"ok": True, **result} if result.get("ok") is not False else result

    async def _artifacts_list(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        task_id, _task, error = await self._task_for_project(project_id, payload)
        if error:
            return error
        result = _json_response_payload(
            await self.task["task_artifacts"](task_id)
        )
        if result.get("ok") is False:
            return result
        return {
            "ok": True,
            "artifacts": [
                summary
                for item in result.get("artifacts") or []
                if (summary := _artifact_summary(item)) is not None
            ],
        }

    async def _artifacts_read(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        _task_id, task, error = await self._task_for_project(project_id, payload)
        if error:
            return error
        artifact_id = _require_text(payload, "artifact_id", max_length=200)
        artifact = next(
            (
                item
                for item in (task or {}).get("artifacts") or []
                if isinstance(item, dict)
                and str(item.get("id") or "") == artifact_id
            ),
            None,
        )
        if artifact is None:
            return {
                "ok": False,
                "code": "artifact_not_found",
                "error": "artifact not found",
            }
        store = workbench_runtime._read_workbench_store()
        project, full_task = workbench_runtime._workbench_find_session(
            store,
            str((task or {}).get("id") or ""),
        )
        try:
            _artifact, path = workbench_runtime._workbench_artifact_download_target(
                project,
                full_task,
                artifact_id,
            )
        except (LookupError, ValueError, FileNotFoundError) as exc:
            return {"ok": False, "code": "artifact_unavailable", "error": str(exc)}
        file_path = Path(path)
        size = file_path.stat().st_size
        if size > _MAX_ARTIFACT_BYTES:
            return {
                "ok": False,
                "code": "artifact_too_large",
                "error": "artifact exceeds the 10 MiB remote transfer limit",
                "size": size,
            }
        return {
            "ok": True,
            "artifact": _artifact_summary(artifact),
            "filename": file_path.name,
            "media_type": mimetypes.guess_type(file_path.name)[0]
            or "application/octet-stream",
            "size": size,
            "content_base64": base64.b64encode(file_path.read_bytes()).decode(
                "ascii"
            ),
        }


class RemoteControlRuntime:
    """Own the LAN listener and encrypted gateway lifecycle."""

    def __init__(
        self,
        *,
        db_path: str,
        store: RemoteControlStore,
        executor: RemoteCommandExecutor,
        lan_host: str = "0.0.0.0",
        lan_port: int = 37841,
    ) -> None:
        self.db_path = str(db_path)
        self.store = store
        self.executor = executor
        self.lan_host = str(lan_host)
        self.lan_port = int(lan_port)
        self.gateway: RemoteGateway | None = None
        self.pairing_server: DirectPairingServer | None = None
        self._running = False
        self._lock: Any = None
        self.last_error = ""

    async def start(self) -> None:
        import asyncio

        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            self._running = True
            await self._apply_locked()

    async def reload(self) -> None:
        if not self._running:
            return
        if self._lock is None:
            await self.start()
            return
        async with self._lock:
            await self._apply_locked()

    async def stop(self) -> None:
        if self._lock is None:
            self._running = False
            return
        async with self._lock:
            self._running = False
            await self._stop_gateway_locked()
            await self._stop_pairing_server_locked()

    async def _apply_locked(self) -> None:
        await self._stop_gateway_locked()
        await self._stop_pairing_server_locked()
        settings = self.store.get_settings()
        if not settings["enabled"]:
            self.last_error = ""
            return
        try:
            pairing_server = DirectPairingServer(
                self.store, host=self.lan_host, port=self.lan_port
            )
            await pairing_server.start()
            self.pairing_server = pairing_server
        except Exception as exc:
            self.last_error = f"LAN control listener failed: {exc}"
            self.store.audit(
                "direct_pairing_listener_failed",
                outcome="error",
                detail={"error": str(exc)},
            )
            return
        try:
            gateway = RemoteGateway(self.store, pairing_server, self.executor)
            await gateway.start()
        except Exception as exc:
            self.last_error = str(exc)
            self.store.audit(
                "remote_gateway_start_failed",
                outcome="error",
                detail={"error": self.last_error},
            )
            return
        self.gateway = gateway
        self.last_error = ""
        register_remote_gateway(self.db_path, gateway)

    async def _stop_gateway_locked(self) -> None:
        gateway, self.gateway = self.gateway, None
        if gateway is None:
            return
        unregister_remote_gateway(self.db_path, gateway)
        await gateway.stop()

    async def _stop_pairing_server_locked(self) -> None:
        server, self.pairing_server = self.pairing_server, None
        if server is not None:
            await server.stop()

    def status(self) -> dict[str, Any]:
        settings = self.store.get_settings()
        gateway = self.gateway
        if not settings["enabled"]:
            state = "disabled"
            detail = "Remote access is disabled."
        elif self.last_error:
            state = "error"
            detail = self.last_error
        elif gateway is not None and gateway.connected:
            state = "connected"
            detail = "LAN E2EE control is ready."
        elif gateway is not None and gateway.started:
            state = "connecting"
            detail = "Starting the LAN E2EE control listener."
        else:
            state = "configured"
            detail = "LAN control will start with the Cyrene runtime."
        return {
            "status": state,
            "connected": bool(gateway and gateway.connected),
            "detail": detail,
            "direct_pairing": bool(self.pairing_server and self.pairing_server.running),
            "lan_port": self.lan_port,
        }


__all__ = [
    "RemoteCommandExecutor",
    "RemoteControlRuntime",
    "public_remote_event",
]
