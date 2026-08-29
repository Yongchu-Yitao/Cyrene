"""Typed application services for the versioned local Control API."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from cyrene.workbench.projects.project_services import ProjectApplicationService
from cyrene.workbench.tasks.task_services import (
    ArtifactApplicationService,
    ArtifactDownload,
    TaskSessionNotFoundError,
)


class ControlServiceError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "",
        status_code: int,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.payload = payload or ({"error": message, "code": code} if code else {"error": message})


class ControlChatPort(Protocol):
    run_manager: Any

    async def list(self, project_id: str) -> dict[str, Any]: ...
    async def create(self, body: Any) -> dict[str, Any]: ...
    async def get(self, chat_id: str) -> dict[str, Any]: ...
    async def send(self, chat_id: str, body: dict[str, Any]) -> Any: ...
    async def guide(self, chat_id: str, body: Any) -> dict[str, Any]: ...
    async def answer(self, chat_id: str, body: Any) -> dict[str, Any]: ...


class ControlProjectPort(Protocol):
    async def list_tasks(self, project_id: str) -> dict[str, Any]: ...
    async def create_task(self, project_id: str, body: Any) -> dict[str, Any]: ...


class ControlTaskPort(Protocol):
    async def get(self, task_id: str) -> dict[str, Any]: ...
    async def dispatch(self, task_id: str, body: Any) -> dict[str, Any]: ...
    async def update(self, task_id: str, body: Any) -> dict[str, Any]: ...
    async def create_run(self, task_id: str, body: Any) -> dict[str, Any]: ...
    async def answer(self, task_id: str, body: Any) -> dict[str, Any]: ...


class ControlGoalLoopPort(Protocol):
    async def get(self, task_id: str) -> dict[str, Any]: ...
    async def control(self, action: str, task_id: str) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ControlRunEventPage:
    run_id: str
    events: list[dict[str, Any]]
    next_cursor: int
    completed: bool
    truncated: bool


@dataclass(frozen=True, slots=True)
class ControlInterruptResult:
    interrupted: bool
    run_id: str
    status: str


@dataclass(frozen=True, slots=True)
class ControlAttachmentDownload:
    path: Path
    filename: str
    media_type: str


class ControlProjectQueryService:
    def __init__(
        self,
        *,
        projects: ProjectApplicationService,
        chat: ControlChatPort,
        project_port: ControlProjectPort,
    ) -> None:
        self._projects = projects
        self._chat = chat
        self._project_port = project_port

    async def list_projects(self) -> list[dict[str, Any]]:
        payload = await self._projects.list("summary")
        return [item for item in payload.get("projects") or [] if isinstance(item, dict)]

    async def list_chats(self, project_id: str) -> list[dict[str, Any]]:
        payload = await self._chat.list(project_id)
        return [item for item in payload.get("chats") or [] if isinstance(item, dict)]

    async def create_chat(self, body: Any) -> dict[str, Any]:
        return dict((await self._chat.create(body)).get("chat") or {})

    async def get_chat(self, chat_id: str) -> dict[str, Any]:
        return dict((await self._chat.get(chat_id)).get("chat") or {})

    async def send_chat(self, chat_id: str, body: dict[str, Any]) -> Any:
        return await self._chat.send(chat_id, body)

    async def answer_chat(self, chat_id: str, body: Any) -> dict[str, Any]:
        return await self._chat.answer(chat_id, body)

    async def list_tasks(self, project_id: str) -> list[dict[str, Any]]:
        payload = await self._project_port.list_tasks(project_id)
        return [item for item in payload.get("sessions") or [] if isinstance(item, dict)]

    async def create_task(self, project_id: str, body: Any) -> dict[str, Any]:
        payload = await self._project_port.create_task(project_id, body)
        return dict(payload.get("session") or {})


class ControlRunService:
    def __init__(
        self,
        *,
        chat: ControlChatPort,
        public_event: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> None:
        self._chat = chat
        self._manager = chat.run_manager
        self._public_event = public_event

    def replayable(self, run_id: str) -> Any:
        run = self._manager.get_replayable_by_run_id(run_id)
        if run is None:
            raise ControlServiceError("run not found", code="control_run_not_found", status_code=404)
        return run

    def active(self, run_id: str) -> Any:
        run = self._manager.get_by_run_id(run_id)
        if run is None:
            raise ControlServiceError("run is not active", code="control_run_not_active", status_code=409)
        return run

    def events(self, run_id: str, *, after: int, limit: int) -> ControlRunEventPage:
        run = self.replayable(run_id)
        raw_events = list(run.events)
        available = [int(item.get("_seq") or 0) for item in raw_events if int(item.get("_seq") or 0) > after]
        truncated = any(cursor > previous + 1 for previous, cursor in zip([after, *available], available))
        events: list[dict[str, Any]] = []
        next_cursor = after
        for raw in raw_events:
            cursor = int(raw.get("_seq") or 0)
            if cursor <= after:
                continue
            next_cursor = cursor
            public = self._public_event(raw)
            if public is not None:
                events.append(public)
            if len(events) >= limit:
                break
        return ControlRunEventPage(run_id, events, next_cursor, run.done.is_set(), truncated)

    async def guide(self, run_id: str, body: Any) -> dict[str, Any]:
        run = self.active(run_id)
        result = await self._chat.guide(run.chat_id, body)
        return {
            "queued": bool(result.get("queued")),
            "duplicate": bool(result.get("duplicate")),
            "event_id": str(result.get("eventId") or ""),
            "run_id": str(result.get("runId") or run_id),
        }

    async def interrupt(self, run_id: str) -> ControlInterruptResult:
        run = self.active(run_id)
        interrupted = self._manager.interrupt(run.chat_id)
        if interrupted and run.task is not None and not run.done.is_set():
            try:
                await asyncio.wait_for(asyncio.shield(run.done.wait()), timeout=8.0)
            except asyncio.TimeoutError as exc:
                raise ControlServiceError(
                    "run interruption is still settling",
                    code="control_interrupt_timeout",
                    status_code=504,
                ) from exc
        return ControlInterruptResult(
            interrupted=interrupted,
            run_id=run_id,
            status="cancelled" if interrupted else str(run.status or ""),
        )


class ControlTaskCommandService:
    def __init__(
        self,
        *,
        task: ControlTaskPort,
        goals: ControlGoalLoopPort,
        interrupt_task: Callable[..., bool],
    ) -> None:
        self._task = task
        self._goals = goals
        self._interrupt_task = interrupt_task

    async def get(self, task_id: str) -> dict[str, Any]:
        return dict((await self._task.get(task_id)).get("session") or {})

    async def dispatch(self, task_id: str, body: Any) -> dict[str, Any]:
        return dict((await self._task.dispatch(task_id, body)).get("session") or {})

    async def approve_plan(self, task_id: str, revision: int, body_factory: Callable[..., Any]) -> dict[str, Any]:
        task = await self.get(task_id)
        current = int(task.get("planDefinitionRevision") or 0)
        if revision != current:
            raise ControlServiceError("task plan revision is stale", code="stale_plan_revision", status_code=409)
        if not task.get("plan"):
            raise ControlServiceError("task plan is empty", code="task_plan_empty", status_code=409)
        result = await self._task.update(
            task_id,
            body_factory(status="waiting_for_approval", approvedPlanDefinitionRevision=current),
        )
        return dict(result.get("session") or {})

    async def run_step(
        self,
        task_id: str,
        step_id: str,
        *,
        revision: int,
        prepare_body: Callable[..., Any],
        run_body: Callable[..., Any],
        message: str,
        permission_mode: str,
    ) -> dict[str, Any]:
        task, plan, step = await self._validate_step(task_id, step_id, revision)
        self._mark_step(plan, step_id, "running", "Control API started this step.")
        await self._task.update(task_id, prepare_body(status="running", plan=plan))
        result = await self._task.create_run(
            task_id,
            run_body(
                input=message, mode=permission_mode, stepId=step_id,
                stepTitle=str(step.get("title") or "")[:1000], action="spawn_subagent",
                meta={"scope": "plan_step", "continueAll": False},
                planDefinitionRevision=int(task.get("planDefinitionRevision") or 0),
            ),
        )
        updated = dict(result.get("session") or {})
        if str(updated.get("status") or "") == "waiting_for_user":
            return updated
        return await self._finalize_step(task_id, step_id, updated, plan, prepare_body)

    async def _validate_step(
        self, task_id: str, step_id: str, revision: int
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        task = await self.get(task_id)
        current = int(task.get("planDefinitionRevision") or 0)
        if revision != current:
            raise ControlServiceError("task plan revision is stale", code="stale_plan_revision", status_code=409)
        approved = task.get("approvedPlanDefinitionRevision")
        if approved is None or int(approved) != current:
            raise ControlServiceError("task plan has not been approved", code="plan_not_approved", status_code=409)
        plan = [dict(item) for item in task.get("plan") or [] if isinstance(item, dict)]
        step = next((item for item in plan if str(item.get("id") or "") == step_id), None)
        if step is None:
            raise ControlServiceError("task step not found", code="step_not_found", status_code=404)
        return task, plan, step

    async def _finalize_step(
        self, task_id: str, step_id: str, updated: dict[str, Any], plan: list[dict[str, Any]], body_factory: Callable[..., Any]
    ) -> dict[str, Any]:
        returned = [dict(item) for item in updated.get("plan") or plan if isinstance(item, dict)]
        self._mark_step(returned, step_id, "completed", "Control API step completed.")
        resolved = {"completed", "done", "skipped"}
        fully_done = bool(returned) and all(str(item.get("status") or "") in resolved for item in returned)
        result = await self._task.update(
            task_id,
            body_factory(status="review" if fully_done else "paused", plan=returned),
        )
        return dict(result.get("session") or {})

    @staticmethod
    def _mark_step(plan: list[dict[str, Any]], step_id: str, status: str, action: str) -> None:
        for item in plan:
            if str(item.get("id") or "") == step_id:
                item["status"] = status
                item["currentAction"] = action

    async def action(self, task_id: str, action: str, body_factory: Callable[..., Any]) -> dict[str, Any]:
        task = await self.get(task_id)
        goal_state = await self._goals.get(task_id)
        goal_loop = goal_state.get("goalLoop")
        if isinstance(goal_loop, dict) and str(goal_loop.get("status") or "") not in {"completed", "failed", "cancelled"}:
            controlled = await self._goals.control(action, task_id)
            return dict(controlled.get("session") or {})
        current = str(task.get("status") or "")
        self._validate_action(action, current)
        status = {"pause": "paused", "resume": "idle", "cancel": "cancelled"}[action]
        if action in {"pause", "cancel"}:
            self._interrupt_task(session_id=task_id)
        result = await self._task.update(task_id, body_factory(status=status))
        return dict(result.get("session") or {})

    @staticmethod
    def _validate_action(action: str, current: str) -> None:
        if action == "pause" and current not in {"running", "waiting_for_user"}:
            raise ControlServiceError("only an active task can be paused", code="invalid_status_transition", status_code=409)
        if action == "resume" and current != "paused":
            raise ControlServiceError("only a paused task can be resumed", code="invalid_status_transition", status_code=409)

    async def answer(self, task_id: str, question_id: str, body: Any) -> dict[str, Any]:
        task = await self.get(task_id)
        pending = task.get("pendingQuestion") if isinstance(task.get("pendingQuestion"), dict) else None
        if pending is None or str(pending.get("id") or "") != question_id:
            raise ControlServiceError("no matching pending question", code="approval_not_pending", status_code=409)
        return await self._task.answer(task_id, body)


class ControlArtifactQueryService:
    def __init__(
        self,
        *,
        artifacts: ArtifactApplicationService,
        chat: ControlChatPort,
        resolve_attachment: Callable[[dict[str, Any], str], tuple[dict[str, Any], Path]],
    ) -> None:
        self._artifacts = artifacts
        self._chat = chat
        self._resolve_attachment = resolve_attachment

    async def list(self, task_id: str) -> list[dict[str, Any]]:
        try:
            return await self._artifacts.list(task_id)
        except TaskSessionNotFoundError as exc:
            raise ControlServiceError(str(exc), status_code=404) from exc

    def download(self, task_id: str, artifact_id: str) -> ArtifactDownload:
        try:
            return self._artifacts.download(task_id, artifact_id)
        except TaskSessionNotFoundError as exc:
            raise ControlServiceError("task not found", code="task_not_found", status_code=404) from exc
        except LookupError as exc:
            raise ControlServiceError(str(exc), code="artifact_not_found", status_code=404) from exc
        except ValueError as exc:
            raise ControlServiceError(str(exc), code="artifact_invalid", status_code=400) from exc
        except FileNotFoundError as exc:
            raise ControlServiceError(str(exc), code="artifact_file_not_found", status_code=404) from exc

    async def chat_attachment(self, chat_id: str, attachment_id: str) -> ControlAttachmentDownload:
        chat = dict((await self._chat.get(chat_id)).get("chat") or {})
        if not chat:
            raise ControlServiceError("chat not found", code="chat_not_found", status_code=404)
        try:
            attachment, target = self._resolve_attachment(chat, attachment_id)
        except LookupError as exc:
            raise ControlServiceError(str(exc), code="attachment_not_found", status_code=404) from exc
        except FileNotFoundError as exc:
            raise ControlServiceError(str(exc), code="attachment_file_not_found", status_code=404) from exc
        filename = Path(str(attachment.get("name") or target.name)).name or target.name
        media_type = str(attachment.get("content_type") or attachment.get("mediaType") or "")
        return ControlAttachmentDownload(target, filename, media_type)


__all__ = [
    "ControlArtifactQueryService",
    "ControlAttachmentDownload",
    "ControlChatPort",
    "ControlGoalLoopPort",
    "ControlInterruptResult",
    "ControlProjectPort",
    "ControlProjectQueryService",
    "ControlRunEventPage",
    "ControlRunService",
    "ControlServiceError",
    "ControlTaskCommandService",
    "ControlTaskPort",
]
