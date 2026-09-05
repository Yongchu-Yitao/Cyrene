"""Direct Workbench application ports shared by Control and Remote APIs."""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from cyrene.workbench.chat.chat_events import publish_chat_changed
from cyrene.workbench.chat.chat_guidance_service import (
    ChatGuidanceApplicationService,
    ChatGuidanceDependencies,
)
from cyrene.workbench.chat.chat_run_lifecycle_service import ChatRunDispatchResult
from cyrene.workbench.control.control_services import ControlServiceError
from cyrene.workbench.projects.project_services import ProjectApplicationService

logger = logging.getLogger(__name__)


def _body(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dict(dump(by_alias=True, exclude_none=True))
    raise ControlServiceError("invalid application command", status_code=400)


def _domain_result(value: Any) -> dict[str, Any]:
    if isinstance(value, ChatRunDispatchResult):
        payload = dict(value.payload or {})
        if value.status_code < 400:
            return payload
        raise ControlServiceError(
            str(payload.get("error") or "chat operation failed"),
            code=str(payload.get("code") or ""),
            status_code=value.status_code,
            payload=payload,
        )
    if isinstance(value, dict):
        return value
    raise ControlServiceError("application operation returned no domain payload", status_code=500)


class WorkbenchChatApplicationPort:
    """Chat commands that bypass FastAPI handlers and response objects."""

    def __init__(
        self,
        *,
        context: Any,
        send: Callable[..., Awaitable[Any]],
        answer: Callable[..., Awaitable[Any]],
    ) -> None:
        self.context = context
        self.service = context.service
        self.run_manager = self.service.run_manager
        self._send = send
        self._answer = answer
        self._guidance = ChatGuidanceApplicationService(ChatGuidanceDependencies(
            run_manager=self.run_manager,
            get_chat=self.service.repository.get,
            mutate_chat=self.service.repository.mutate_one,
            public_message=self.service.public_message,
            utc_now_iso=self.service.utc_now_iso,
            short_id=self.service.short_id,
        ))

    async def list(self, project_id: str) -> dict[str, Any]:
        payload = await asyncio.to_thread(self.service.repository.read_summaries)
        chats = [
            self.service.public_chat_light(item)
            for item in payload.get("chats") or []
            if isinstance(item, dict)
            and str(item.get("kind") or "chat") == "chat"
            and (not project_id or str(item.get("projectId") or "") == project_id)
        ]
        chats.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        return {"chats": chats}

    async def create(self, command: Any) -> dict[str, Any]:
        values = _body(command)
        project_id = str(values.get("project") or values.get("projectId") or "").strip()
        if not project_id:
            raise ControlServiceError("project is required", status_code=400)
        runtime = self.context.runtime()
        project = await asyncio.to_thread(runtime.find_project_lightweight, project_id)
        if not project:
            raise ControlServiceError("project not found", status_code=404)
        snapshot = await self.context.project_memory_snapshot(project_id)

        def persist() -> dict[str, Any]:
            payload = self.service.repository.read()
            chat = self.service.create_chat(
                project_id,
                str(values.get("title") or ""),
                runtime.get_model(),
                project_memory_snapshot=snapshot,
                soul_active=values.get("soulActive"),
                workspace_active=values.get("workspaceActive"),
                reasoning_effort=str(values.get("reasoningEffort") or ""),
            )
            payload.setdefault("chats", []).insert(0, chat)
            self.service.repository.write(payload)
            return chat

        chat = await asyncio.to_thread(persist)
        await publish_chat_changed(str(chat.get("id") or ""), project_id, "created")
        return {"ok": True, "chat": self.service.public_chat_full(chat)}

    async def get(self, chat_id: str) -> dict[str, Any]:
        chat = await asyncio.to_thread(self.service.repository.get, chat_id)
        if not chat:
            raise ControlServiceError("chat not found", status_code=404)
        return {"chat": self.service.public_chat_full(chat)}

    async def update(self, chat_id: str, command: Any) -> dict[str, Any]:
        values = _body(command)
        chat = await asyncio.to_thread(self.service.repository.get_metadata, chat_id)
        if not chat:
            raise ControlServiceError("chat not found", status_code=404)
        base = copy.deepcopy(chat)
        if "title" in values:
            chat["title"] = str(values.get("title") or "").strip()[:60] or chat.get("title")
            chat["titleLocked"] = True
        chat["updatedAt"] = self.service.utc_now_iso()
        chat = await asyncio.to_thread(self.service.repository.write_metadata, chat, base_metadata=base)
        if chat is None:
            raise ControlServiceError("chat not found", status_code=404)
        await publish_chat_changed(chat_id, str(chat.get("projectId") or ""), "updated")
        return {"ok": True, "chat": self.service.public_chat_light(chat)}

    async def delete(self, chat_id: str) -> dict[str, Any]:
        payload = await asyncio.to_thread(self.service.repository.read)
        chats = payload.get("chats") or []
        root = next((item for item in chats if str(item.get("id") or "") == chat_id), None)
        if root is None:
            raise ControlServiceError("chat not found", status_code=404)
        project_id = str(root.get("projectId") or "")
        removed = {chat_id, *[
            str(item.get("id") or "") for item in chats
            if str(item.get("kind") or "") == "side-agent"
            and str(item.get("parentChatId") or "") == chat_id
        ]}
        try:
            await self.service.terminate_chat_agents(removed)
            from cyrene.workbench.chat import chat_groups
            await chat_groups.remove_chat(chat_id, project_id)
        except Exception as exc:
            raise ControlServiceError("chat agents could not be terminated", status_code=503) from exc
        payload["chats"] = [item for item in chats if str(item.get("id") or "") not in removed]
        await asyncio.to_thread(self.service.repository.write, payload)
        await publish_chat_changed(chat_id, project_id, "deleted")
        for removed_chat_id in removed:
            try:
                await self.context.delete_chat_memory(removed_chat_id)
            except Exception:
                # Chat deletion is authoritative; a user-edited optional
                # Plugin must not resurrect the chat by failing its cleanup.
                logger.exception(
                    "Memory Plugin cleanup failed for deleted chat %s",
                    removed_chat_id,
                )
        return {"ok": True}

    async def send(self, chat_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return _domain_result(await self._send(chat_id, body, detached=True))

    async def dispatch_agent_message(
        self,
        chat_id: str,
        message: str,
        *,
        origin_session_id: str = "",
        client_request_id: str = "",
    ) -> dict[str, Any]:
        """Send one cross-session Agent message through the normal Chat kernel."""

        text = str(message or "").strip()
        if not text:
            raise ControlServiceError("message is required", status_code=400)
        request_id = str(client_request_id or "").strip()
        if not request_id:
            from uuid import uuid4

            request_id = f"agent_session_{uuid4().hex}"
        origin = str(origin_session_id or "").strip()

        async def guide_active() -> dict[str, Any]:
            result = await self._guidance.submit(
                chat_id=chat_id,
                message=text,
                client_request_id=request_id,
                agent_originated=True,
                origin_session_id=origin,
            )
            if result.status_code >= 400:
                raise ControlServiceError(
                    str(result.payload.get("error") or "guidance failed"),
                    code=str(result.payload.get("code") or ""),
                    status_code=result.status_code,
                    payload=result.payload,
                )
            return {
                **result.payload,
                "status": "guided",
                "session_id": str(chat_id),
                "run_id": str(result.payload.get("runId") or ""),
            }

        if self.run_manager.get(chat_id) is not None:
            return await guide_active()
        try:
            payload = await self.send(
                chat_id,
                {
                    "message": text,
                    "clientRequestId": request_id,
                    "conversationSource": "agent_session",
                    "agentOriginated": True,
                    "sourceSessionId": origin,
                    "stream": True,
                },
            )
        except ControlServiceError as exc:
            if exc.status_code == 409 or exc.code == "chat_run_in_progress":
                return await guide_active()
            raise
        return {
            **payload,
            "status": "started",
            "session_id": str(chat_id),
            "run_id": str(payload.get("run_id") or payload.get("runId") or ""),
        }

    async def guide(self, chat_id: str, command: Any) -> dict[str, Any]:
        values = _body(command)
        result = await self._guidance.submit(
            chat_id=chat_id,
            message=str(values.get("message") or "").strip(),
            client_request_id=str(values.get("clientRequestId") or "").strip(),
        )
        if result.status_code != 200:
            raise ControlServiceError(
                str(result.payload.get("error") or "guidance failed"),
                code=str(result.payload.get("code") or ""),
                status_code=result.status_code,
                payload=result.payload,
            )
        return result.payload

    async def answer(self, chat_id: str, command: Any) -> dict[str, Any]:
        return _domain_result(await self._answer(chat_id, _body(command)))


class WorkbenchProjectApplicationPort:
    def __init__(self, projects: ProjectApplicationService) -> None:
        self.projects = projects

    async def list_projects(self) -> list[dict[str, Any]]:
        payload = await self.projects.list("summary")
        return [item for item in payload.get("projects") or [] if isinstance(item, dict)]

__all__ = [
    "WorkbenchChatApplicationPort", "WorkbenchProjectApplicationPort",
]
