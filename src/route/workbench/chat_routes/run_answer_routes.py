from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from cyrene.runtime.memory.conversations import archive_session_exchange
from cyrene.workbench.chat_answer_stream_service import (
    ChatAnswerStreamApplicationService,
    ChatAnswerStreamDependencies,
)
from cyrene.workbench.chat_events import publish_chat_changed
from route import schemas as api_models
from route.workbench.chat_routes.context import ChatRouteContext
from route.workbench.chat_routes.shared import (
    _DETACHED_ANSWER_TASKS,
    finish_detached_answer_task,
    schedule_structured_memory_capture,
)

logger = logging.getLogger(__name__)


def _select_answer_permission_mode(
    chat: dict[str, Any],
    requested_mode: str,
    allowed_modes,
) -> tuple[str, bool]:
    is_side_agent = str(chat.get("kind") or "") == "side-agent"
    stored_mode = str(chat.get("permissionMode") or "").strip().lower()
    selected = requested_mode or stored_mode
    mode = selected if selected in allowed_modes else "default"
    chat["permissionMode"] = mode
    return mode, is_side_agent


def _answer_message(
    answer_text: str,
    question_id: str,
    now: str,
    short_id,
) -> dict[str, Any]:
    return {
        "id": short_id("msg"),
        "role": "user",
        "content": answer_text,
        "createdAt": now,
        "answerToQuestionId": question_id,
    }


class ChatAnswerController:
    def __init__(self, context: ChatRouteContext):
        self.context = context

        def track_task(task: asyncio.Task[Any]) -> None:
            _DETACHED_ANSWER_TASKS.add(task)
            task.add_done_callback(finish_detached_answer_task)

        self.stream_service = ChatAnswerStreamApplicationService(ChatAnswerStreamDependencies(track_task=track_task))

    async def answer(self, chat_id: str, body: dict[str, Any]):
        if bool(body.get("stream")):
            return self._stream_response(chat_id, body)
        return await _AnswerOperation(self.context, chat_id, body).execute()

    def _stream_response(self, chat_id: str, body: dict[str, Any]):
        async def event_stream():
            next_body = api_models.AnswerBody(**{**body, "stream": False})

            async def answer_once():
                return await self.answer(
                    chat_id,
                    api_models.body_dict(next_body),
                )

            async for event in self.stream_service.stream(
                chat_id=chat_id,
                answer_once=answer_once,
            ):
                yield json.dumps(event, ensure_ascii=False) + "\n"

        return StreamingResponse(
            event_stream(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache"},
        )


class _AnswerOperation:
    def __init__(
        self,
        context: ChatRouteContext,
        chat_id: str,
        body: dict[str, Any],
    ):
        self.context = context
        self.service = context.service
        self.chat_id = chat_id
        self.body = body
        self.question_id = str(body.get("question_id") or "").strip()
        self.answer_text = str(body.get("answer") or body.get("selected_option") or "").strip()
        self.ui_instance_id = str(body.get("uiInstanceId") or "").strip()
        self.processing_started_at = time.monotonic()

    async def execute(self):
        from cyrene.agent.state import PERMISSION_MODES

        self.requested_mode = str(self.body.get("mode") or "").strip().lower()
        error = await self._prepare(PERMISSION_MODES)
        if error is not None:
            return error
        error = await self._persist_answer()
        if error is not None:
            return error
        reply = await self._resume_agent()
        if isinstance(reply, (dict, JSONResponse)):
            return reply
        await self.service.finalize_workspace_changes(
            chat_id=self.chat_id,
            run_id=self.resume_run_id,
            workspace_dir=self.workspace_dir,
            before=self.changes_before,
            status=("awaiting_user" if reply == self.routes.awaiting_user_sentinel else "completed"),
        )
        if reply == self.routes.awaiting_user_sentinel:
            return await self._handle_awaiting_user()
        return await self._handle_reply(reply)

    async def _prepare(self, permission_modes):
        if not self.question_id or not self.answer_text:
            return JSONResponse(
                {"error": "question_id and answer are required"},
                status_code=400,
            )
        self.chat = await asyncio.to_thread(
            self.service.repository.get,
            self.chat_id,
        )
        if not self.chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        self.base_chat = copy.deepcopy(self.chat)
        self.mode, self.is_side_agent = _select_answer_permission_mode(
            self.chat,
            self.requested_mode,
            permission_modes,
        )
        pending = self.chat.get("pendingQuestion") if isinstance(self.chat.get("pendingQuestion"), dict) else None
        if not pending or str(pending.get("id") or "") != self.question_id:
            return JSONResponse(
                {"error": "no matching pending question"},
                status_code=409,
            )
        self.routes = self.context.runtime()
        self.project_id = str(self.chat.get("projectId") or "")
        store = await asyncio.to_thread(self.routes.read_store)
        self.project = self.routes.find_project(store, self.project_id)
        if not self.project:
            return JSONResponse({"error": "project not found"}, status_code=404)
        try:
            self.workspace_dir = self.service.resolve_chat_workspace_dir(
                self.chat,
                self.project,
                self.routes.resolve_workspace_dir,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return None

    async def _persist_answer(self):
        self.now = self.service.utc_now_iso()
        self.answer_entry = _answer_message(
            self.answer_text,
            self.question_id,
            self.now,
            self.service.short_id,
        )
        self.service.merge_chat_messages_chronologically(
            self.chat,
            [self.answer_entry],
        )
        self.service.mark_user_activity(self.chat, self.now)
        await asyncio.to_thread(
            self.service.repository.write_one,
            self.chat,
            base_chat=self.base_chat,
        )
        await publish_chat_changed(
            self.chat_id,
            self.project_id,
            "answer_submitted",
            run_status="running",
            chatSummary=self.service.public_chat_light(self.chat),
            userMessage=self.service.public_message(self.answer_entry),
        )
        self.state_ids_before_resume: set[str] = set()
        messages = await asyncio.to_thread(
            self.service.session_state_messages,
            self.chat_id,
        )
        for message in messages:
            message_id = str(message.get("message_id") or message.get("id") or "").strip()
            if message_id:
                self.state_ids_before_resume.add(message_id)
        self.resume_run_id = f"resume_{uuid.uuid4().hex}"
        self.changes_before = await self.service.capture_workspace_changes_baseline(
            self.workspace_dir,
            self.resume_run_id,
        )
        from cyrene.runtime.host_bridge import resolve_conversation_source

        self.conversation_source = await resolve_conversation_source(self.ui_instance_id)
        return None

    async def _resume_agent(self):
        kwargs = {
            "ui_instance_id": self.ui_instance_id,
            "conversation_source": self.conversation_source,
        }
        if self.mode != "default":
            kwargs["permission_mode"] = self.mode
        try:
            return await self.routes.answer_pending(
                self.chat_id,
                self.question_id,
                self.answer_text,
                self.workspace_dir,
                **kwargs,
            )
        except asyncio.CancelledError:
            return await self._handle_cancelled()
        except Exception as exc:
            return await self._handle_error(exc)

    async def _handle_cancelled(self) -> dict[str, Any]:
        await self.service.finalize_workspace_changes(
            chat_id=self.chat_id,
            run_id=self.resume_run_id,
            workspace_dir=self.workspace_dir,
            before=self.changes_before,
            status="cancelled",
        )
        await asyncio.to_thread(
            self.service.stash_chat_pending_for,
            self.chat_id,
            None,
        )
        await asyncio.to_thread(
            self.service.record_chat_run_outcome,
            self.chat_id,
            run_id=self.resume_run_id,
            status="cancelled",
            termination_reason="user_interrupted",
            outcome_kind="interrupted",
            created_at=self.now,
        )
        summary = await asyncio.to_thread(self._load_chat_summary)
        await publish_chat_changed(
            self.chat_id,
            self.project_id,
            "settled",
            run_id=self.resume_run_id,
            run_status="cancelled",
            chatSummary=summary,
        )
        return {
            "ok": True,
            "interrupted": True,
            "awaitingUser": False,
            "runId": self.resume_run_id,
            "userMessage": self.service.public_message(self.answer_entry),
        }

    async def _handle_error(self, exc: Exception):
        await self.service.finalize_workspace_changes(
            chat_id=self.chat_id,
            run_id=self.resume_run_id,
            workspace_dir=self.workspace_dir,
            before=self.changes_before,
            status="error",
        )
        logger.exception(
            "Workbench chat answer-resume failed for %s",
            self.chat_id,
        )
        await asyncio.to_thread(
            self.service.record_chat_run_outcome,
            self.chat_id,
            run_id=self.resume_run_id,
            status="error",
            termination_reason="agent_error",
            outcome_kind="error",
            created_at=self.now,
        )
        summary = await asyncio.to_thread(self._load_chat_summary)
        await publish_chat_changed(
            self.chat_id,
            self.project_id,
            "settled",
            run_id=self.resume_run_id,
            run_status="failed",
            chatSummary=summary,
        )
        return JSONResponse(
            {
                "error": "answer resume failed",
                "detail": str(exc),
                **self.service.chat_error_metadata(exc),
            },
            status_code=502,
        )

    def _load_chat_summary(self) -> dict[str, Any]:
        chat = self.service.repository.get(self.chat_id)
        return self.service.public_chat_light(chat) if chat else {}

    async def _handle_awaiting_user(self) -> dict[str, Any]:
        pending = await asyncio.to_thread(
            self.routes.pending_question_for,
            self.chat_id,
        )
        state_messages = await asyncio.to_thread(
            self.service.session_state_messages,
            self.chat_id,
        )
        timeline, usage, files = await asyncio.to_thread(
            self.service.extract_exchange_timeline,
            state_messages,
            self.state_ids_before_resume,
            include_open_tool_preamble=True,
        )
        model = self.service.last_exchange_model(
            state_messages,
            self.state_ids_before_resume,
        ) or str(self.chat.get("model") or "")
        for entry in timeline:
            entry.setdefault("model", model)
        additions = [*timeline]
        if pending:
            additions.append(
                self.service.pending_question_message(
                    pending,
                    usage=usage,
                    files=files,
                    model=model,
                )
            )
        await asyncio.to_thread(
            self.service.stash_chat_pending_for,
            self.chat_id,
            pending,
            additions=additions,
        )
        await asyncio.to_thread(
            self.service.record_chat_run_outcome,
            self.chat_id,
            run_id=self.resume_run_id,
            status="done",
            termination_reason="awaiting_user",
            outcome_kind="awaiting",
            created_at=self.now,
        )
        summary = await asyncio.to_thread(self._load_chat_summary)
        await publish_chat_changed(
            self.chat_id,
            self.project_id,
            "settled",
            run_id=self.resume_run_id,
            run_status="awaiting_user",
            chatSummary=summary,
            assistantMessages=[self.service.public_message(item) for item in additions],
        )
        return {
            "ok": True,
            "awaitingUser": True,
            "runId": self.resume_run_id,
            "pendingQuestion": pending,
            "userMessage": self.service.public_message(self.answer_entry),
        }

    async def _handle_reply(self, reply: Any) -> dict[str, Any]:
        state_messages = await asyncio.to_thread(
            self.service.session_state_messages,
            self.chat_id,
        )
        timeline, usage, files = await asyncio.to_thread(
            self.service.extract_exchange_timeline,
            state_messages,
            self.state_ids_before_resume,
        )
        fresh_chat = await asyncio.to_thread(
            self.service.repository.get,
            self.chat_id,
        )
        if not fresh_chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        model = self.service.last_exchange_model(
            state_messages,
            self.state_ids_before_resume,
        ) or str(fresh_chat.get("model") or "")
        for entry in timeline:
            entry.setdefault("model", model)
        assistant = self._assistant_message(reply, model, usage, files)
        saved_messages = [*timeline, assistant]
        completed_turn_count = await self._persist_completed_reply(
            fresh_chat,
            saved_messages,
            assistant,
        )
        summary = await self._publish_completed(saved_messages)
        await self._archive_reply(fresh_chat, reply)
        if self.project_id and not self.is_side_agent:
            await self._schedule_memory(
                fresh_chat,
                reply,
                state_messages,
                completed_turn_count,
            )
        return {
            "ok": True,
            "awaitingUser": False,
            "runId": self.resume_run_id,
            "userMessage": self.service.public_message(self.answer_entry),
            "assistantMessage": self.service.public_message(assistant),
            "assistantMessages": [self.service.public_message(item) for item in saved_messages],
            "chatSummary": summary,
        }

    def _assistant_message(
        self,
        reply: Any,
        model: str,
        usage: dict[str, Any],
        files: list[Any],
    ) -> dict[str, Any]:
        message: dict[str, Any] = {
            "id": self.service.short_id("msg"),
            "role": "assistant",
            "content": str(reply or ""),
            "createdAt": self.service.utc_now_iso(),
            "model": model,
            "processingDurationMs": max(
                0,
                int(round((time.monotonic() - self.processing_started_at) * 1000)),
            ),
        }
        if any(usage.values()):
            message["usage"] = usage
        if files:
            message["attachments"] = files
        return message

    async def _persist_completed_reply(
        self,
        chat: dict[str, Any],
        saved_messages: list[dict[str, Any]],
        assistant: dict[str, Any],
    ) -> int:
        base_chat = copy.deepcopy(chat)
        self.service.merge_chat_messages_chronologically(chat, saved_messages)
        completed = self.service.next_completed_turn_count(
            chat,
            is_side_agent=self.is_side_agent,
        )
        chat["completedTurnCount"] = completed
        chat["lastModel"] = assistant["model"]
        chat["status"] = "idle"
        chat.pop("pendingQuestion", None)
        chat["updatedAt"] = assistant["createdAt"]
        await asyncio.to_thread(
            self.service.repository.write_one,
            chat,
            base_chat=base_chat,
        )
        from cyrene.runtime.host_actions import finalize_origin

        asyncio.create_task(finalize_origin(self.chat_id, ""))
        await asyncio.to_thread(self.service.complete_chat_plan, self.chat_id)
        return completed

    async def _publish_completed(
        self,
        saved_messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        await asyncio.to_thread(
            self.service.record_chat_run_outcome,
            self.chat_id,
            run_id=self.resume_run_id,
            status="done",
            termination_reason="completed",
            outcome_kind="reply",
            created_at=self.now,
        )
        summary = await asyncio.to_thread(self._load_chat_summary)
        await publish_chat_changed(
            self.chat_id,
            self.project_id,
            "settled",
            run_id=self.resume_run_id,
            run_status="completed",
            chatSummary=summary,
            assistantMessages=[self.service.public_message(item) for item in saved_messages],
        )
        return summary

    async def _archive_reply(self, chat: dict[str, Any], reply: Any) -> None:
        try:
            await asyncio.to_thread(
                archive_session_exchange,
                self.chat_id,
                self.answer_text,
                str(reply or ""),
                workspace_dir=self.workspace_dir,
                session_title=str(chat.get("title") or ""),
            )
        except Exception:
            logger.exception(
                "Failed to archive workbench conversation %s",
                self.chat_id,
            )

    async def _schedule_memory(
        self,
        chat: dict[str, Any],
        reply: Any,
        state_messages: list[dict[str, Any]],
        completed_turn_count: int,
    ) -> None:
        schedule_structured_memory_capture(
            self.routes,
            project_id=self.project_id,
            user_text=self.answer_text,
            agent_text=str(reply or ""),
            state_messages=state_messages,
            prior_message_ids=self.state_ids_before_resume,
            session_id=self.chat_id,
        )
        from cyrene.workbench.project_memory_prompt import (
            completed_context_snapshot,
            context_auto_trigger_threshold,
            schedule_learning,
        )

        snapshot = await asyncio.to_thread(
            completed_context_snapshot,
            self.chat_id,
            self.project_id,
            completed_turn_count=completed_turn_count,
            final_assistant_text=str(reply or ""),
        )
        threshold = (
            context_auto_trigger_threshold(
                self.project_id,
                self.chat_id,
                snapshot.get("messages") or [],
            )
            if snapshot
            else None
        )
        if snapshot and threshold is not None:
            snapshot["contextThresholdPercent"] = threshold
            schedule_learning(
                self.project_id,
                snapshot,
                source="conversation_auto",
                reason=f"context_{threshold}_percent",
            )


def register_run_answer_routes(
    router: APIRouter,
    context: ChatRouteContext,
) -> dict[str, Any]:
    controller = ChatAnswerController(context)

    @router.post("/api/workbench/chats/{chat_id}/answer")
    async def api_workbench_chat_answer(
        chat_id: str,
        body_model: api_models.AnswerBody,
    ):
        return await controller.answer(chat_id, api_models.body_dict(body_model))

    return {"answer_chat": api_workbench_chat_answer}
