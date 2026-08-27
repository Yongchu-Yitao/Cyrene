"""Admit and persist guidance for an active chat run."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable

from cyrene.localization import localized
from cyrene.workbench.inbox import GuidanceAdmissionClosed

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ChatGuidanceDependencies:
    run_manager: Any
    get_chat: Callable[[str], dict[str, Any] | None]
    mutate_chat: Callable[[str, Callable[[dict[str, Any]], Any]], Any]
    public_message: Callable[[dict[str, Any]], dict[str, Any]]
    utc_now_iso: Callable[[], str]
    short_id: Callable[[str], str]


@dataclass(slots=True)
class ChatGuidanceResult:
    payload: dict[str, Any]
    status_code: int = 200


class ChatGuidanceApplicationService:
    def __init__(self, dependencies: ChatGuidanceDependencies) -> None:
        self.dependencies = dependencies

    async def submit(
        self,
        *,
        chat_id: str,
        message: str,
        client_request_id: str,
        agent_originated: bool = False,
        origin_session_id: str = "",
    ) -> ChatGuidanceResult:
        if not message:
            return self._error(
                localized("Guidance message is empty.", "引导消息不能为空。"),
                "guidance_empty",
                422,
            )
        run = self.dependencies.run_manager.get(chat_id)
        if run is None or run.status != "running":
            return self._not_running()
        await run.ready.wait()
        if run.status != "running":
            return self._not_running()
        chat = await asyncio.to_thread(self.dependencies.get_chat, chat_id)
        if not chat:
            return self._error(
                localized("Chat not found.", "未找到对话。"),
                "chat_not_found",
                404,
            )
        now = self.dependencies.utc_now_iso()
        message_id = self.dependencies.short_id("msg")
        try:
            event = await run.inbox.put_guidance(
                message,
                client_request_id=client_request_id,
                public_message_id=message_id,
                public_created_at=now,
            )
        except GuidanceAdmissionClosed:
            await run.done.wait()
            return self._not_running()
        except RuntimeError:
            logger.exception("Failed to persist guidance for chat %s", chat_id)
            return self._error(
                localized(
                    "Guidance could not be saved. Please retry.",
                    "无法保存引导消息，请重试。",
                ),
                "guidance_persistence_failed",
                503,
            )
        if event.get("duplicate"):
            return self._duplicate(chat, run, event, client_request_id)
        entry = self._entry(
            message_id,
            message,
            now,
            str(event["event_id"]),
            run.run_id,
            client_request_id,
            agent_originated=agent_originated,
            origin_session_id=origin_session_id,
        )

        def persist(current: dict[str, Any]) -> None:
            current.setdefault("messages", []).append(entry)
            current["updatedAt"] = now

        await asyncio.to_thread(self.dependencies.mutate_chat, chat_id, persist)
        await run.publish(
            {
                "type": "guidance_received",
                "eventId": event["event_id"],
                "runId": run.run_id,
                "userMessage": self.dependencies.public_message(entry),
                "message": localized(
                    "Guidance queued for the running agent.",
                    "引导消息已加入当前智能体的队列。",
                ),
            }
        )
        return ChatGuidanceResult(
            {
                "queued": True,
                "eventId": event["event_id"],
                "runId": run.run_id,
                "userMessage": self.dependencies.public_message(entry),
            }
        )

    def _duplicate(
        self,
        chat: dict[str, Any],
        run: Any,
        event: dict[str, Any],
        client_request_id: str,
    ) -> ChatGuidanceResult:
        message = next(
            (
                item
                for item in reversed(chat.get("messages") or [])
                if isinstance(item, dict)
                and (
                    str(item.get("guidanceEventId") or "") == str(event.get("event_id") or "")
                    or (
                        client_request_id
                        and str(item.get("clientRequestId") or "") == client_request_id
                    )
                )
            ),
            None,
        )
        payload = {
            "queued": True,
            "duplicate": True,
            "eventId": event["event_id"],
            "runId": run.run_id,
        }
        if message is not None:
            payload["userMessage"] = self.dependencies.public_message(message)
        return ChatGuidanceResult(payload)

    @staticmethod
    def _entry(
        message_id: str,
        message: str,
        now: str,
        event_id: str,
        run_id: str,
        client_request_id: str,
        *,
        agent_originated: bool = False,
        origin_session_id: str = "",
    ) -> dict[str, Any]:
        entry = {
            "id": message_id,
            "role": "user",
            "content": message,
            "createdAt": now,
            "guidance": True,
            "guidanceEventId": event_id,
            "runId": run_id,
        }
        if client_request_id:
            entry["clientRequestId"] = client_request_id
        if agent_originated:
            entry["agentOriginated"] = True
        if origin_session_id:
            entry["originSessionId"] = str(origin_session_id)
        return entry

    @staticmethod
    def _error(message: str, code: str, status_code: int) -> ChatGuidanceResult:
        return ChatGuidanceResult({"error": message, "code": code}, status_code)

    @classmethod
    def _not_running(cls) -> ChatGuidanceResult:
        return cls._error(
            localized("This chat has no active reply.", "此对话当前没有正在生成的回复。"),
            "chat_not_running",
            409,
        )


__all__ = ["ChatGuidanceApplicationService", "ChatGuidanceDependencies", "ChatGuidanceResult"]
