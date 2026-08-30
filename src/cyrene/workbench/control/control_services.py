"""Typed application services for the versioned local Control API."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from cyrene.workbench.projects.project_services import ProjectApplicationService


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
    ) -> None:
        self._projects = projects
        self._chat = chat

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


class ControlArtifactQueryService:
    def __init__(
        self,
        *,
        chat: ControlChatPort,
        resolve_attachment: Callable[[dict[str, Any], str], tuple[dict[str, Any], Path]],
    ) -> None:
        self._chat = chat
        self._resolve_attachment = resolve_attachment

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
    "ControlInterruptResult",
    "ControlProjectQueryService",
    "ControlRunEventPage",
    "ControlRunService",
    "ControlServiceError",
]
