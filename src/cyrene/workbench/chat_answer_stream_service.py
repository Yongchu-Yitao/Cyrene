"""Stream one answer continuation without coupling it to a FastAPI route."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from cyrene.agent.context import bind_run_context


@dataclass(slots=True)
class ChatAnswerStreamDependencies:
    track_task: Callable[[asyncio.Task[Any]], None]


class ChatAnswerStreamApplicationService:
    def __init__(self, dependencies: ChatAnswerStreamDependencies) -> None:
        self.dependencies = dependencies

    async def stream(
        self,
        *,
        chat_id: str,
        answer_once: Callable[[], Awaitable[Any]],
    ) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        saw_reply_events = False
        subscriber_active = True

        async def publish(event: dict[str, Any]) -> None:
            if subscriber_active:
                await queue.put(dict(event))

        binding = bind_run_context(reply_stream_writer=publish, runtime_event_writer=publish)
        try:
            task = asyncio.create_task(answer_once())
            self.dependencies.track_task(task)
        finally:
            binding.reset()

        try:
            while True:
                if task.done() and queue.empty():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
                if str(event.get("type") or "").startswith("reply_"):
                    saw_reply_events = True
                yield event
            try:
                response = await task
            except asyncio.CancelledError:
                yield {"type": "interrupted", "chatId": chat_id}
                return
            for event in self._terminal_events(response, saw_reply_events, chat_id):
                yield event
        finally:
            subscriber_active = False

    @staticmethod
    def _terminal_events(
        response: Any,
        saw_reply_events: bool,
        chat_id: str,
    ) -> list[dict[str, Any]]:
        if hasattr(response, "body"):
            try:
                payload = json.loads(bytes(response.body).decode("utf-8"))
            except Exception:
                payload = {}
            return [
                {
                    "type": "error",
                    "error": str(payload.get("error") or "answer_failed"),
                    "message": str(
                        payload.get("detail")
                        or payload.get("error")
                        or "Failed to resume the conversation."
                    ),
                }
            ]
        if not isinstance(response, dict):
            return [
                {
                    "type": "error",
                    "error": "invalid_answer_response",
                    "message": "Invalid answer response from the daemon.",
                }
            ]
        if bool(response.get("interrupted")):
            return [{"type": "interrupted", "chatId": chat_id}]
        if bool(response.get("awaitingUser")):
            return [
                {
                    "type": "awaiting_user",
                    "pending_question": response.get("pendingQuestion"),
                }
            ]
        assistant = response.get("assistantMessage")
        reply = str(assistant.get("content") or "") if isinstance(assistant, dict) else ""
        events: list[dict[str, Any]] = []
        if not saw_reply_events:
            events.append({"type": "reply_start"})
            if reply:
                events.append({"type": "reply_delta", "delta": reply})
        events.extend(
            [
                {"type": "reply_done", "response": reply},
                {
                    "type": "saved",
                    "assistantMessage": assistant or {},
                    "assistantMessages": response.get("assistantMessages") or [],
                    "chatSummary": response.get("chatSummary") or {},
                },
            ]
        )
        return events


__all__ = ["ChatAnswerStreamApplicationService", "ChatAnswerStreamDependencies"]
