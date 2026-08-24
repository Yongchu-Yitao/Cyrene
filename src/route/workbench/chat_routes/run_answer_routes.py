from __future__ import annotations

import asyncio
import copy
import logging
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from cyrene.runtime.memory.conversations import archive_session_exchange
from cyrene.workbench.chat_events import publish_chat_changed
from cyrene.workbench.chat_runs import ChatRun
from route import schemas as api_models
from route.workbench.chat_routes.context import ChatRouteContext
from route.workbench.chat_routes.shared import (
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

    async def answer(self, chat_id: str, body: dict[str, Any]):
        return await _AnswerOperation(self.context, chat_id, body).execute()


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
        self.wants_stream = bool(body.get("stream"))
        self.processing_started_at = time.monotonic()

    async def execute(self):
        from cyrene.agent.state import PERMISSION_MODES

        self.requested_mode = str(self.body.get("mode") or "").strip().lower()
        error = await self._prepare(PERMISSION_MODES)
        if error is not None:
            return error

        async def runner(run: ChatRun) -> None:
            await self._run(run)

        run, is_new = self.service.run_manager.start_or_get(
            self.chat_id,
            {"type": "ack", "chatId": self.chat_id},
            runner,
            stream=self.wants_stream,
            settler=self._publish_settled,
        )
        if not is_new:
            return JSONResponse(
                {
                    "error": "chat already has a running reply",
                    "code": "chat_run_in_progress",
                },
                status_code=409,
            )
        self.run = run
        if self.wants_stream:
            return StreamingResponse(
                self.service.run_manager.stream(run),
                media_type="application/x-ndjson",
                headers={"Cache-Control": "no-cache"},
            )
        await run.done.wait()
        return self._response(run)

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

    async def _persist_answer(self, run: ChatRun) -> None:
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
        self.chat["status"] = "running"
        self.service.mark_user_activity(self.chat, self.now)
        await asyncio.to_thread(
            self.service.repository.write_one,
            self.chat,
            base_chat=self.base_chat,
        )
        # From this point on cancellation must consume the answered question.
        # Set the marker before any more await points so cleanup reflects the
        # durable write rather than how far the preparation happened to get.
        self.answer_persisted = True
        running_summary = self.service.public_chat_light(self.chat)
        # The durable pending question stays in the store until the resumed run
        # settles so an agent failure can still restore the prompt.  The live
        # projection must hide it immediately, though; otherwise this event
        # overwrites the frontend's optimistic clear and mounts the answered
        # question card again while the agent is running.
        running_summary["pendingQuestion"] = None
        await publish_chat_changed(
            self.chat_id,
            self.project_id,
            "answer_submitted",
            run_status="running",
            chatSummary=running_summary,
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
        self.changes_before = await self.service.capture_workspace_changes_baseline(
            self.workspace_dir,
            run.run_id,
        )
        from cyrene.runtime.host_bridge import resolve_conversation_source

        self.conversation_source = await resolve_conversation_source(self.ui_instance_id)

    async def _run(self, run: ChatRun) -> None:
        self.changes_before = None
        self.answer_persisted = False
        try:
            await self._persist_answer(run)
            reply = await self._resume_agent()
            run.status = "finishing"
            status = (
                "awaiting_user"
                if reply == self.routes.awaiting_user_sentinel
                else "completed"
            )
            await self.service.finalize_workspace_changes(
                chat_id=self.chat_id,
                run_id=run.run_id,
                workspace_dir=self.workspace_dir,
                before=self.changes_before,
                status=status,
                run=run,
            )
            if reply == self.routes.awaiting_user_sentinel:
                payload = await self._handle_awaiting_user(run)
                run.outcome = {
                    "kind": "awaiting",
                    "pending": payload.get("pendingQuestion"),
                    "assistantMessages": payload.get("assistantMessages") or [],
                    "payload": payload,
                }
                if self.wants_stream:
                    await run.publish(
                        {
                            "type": "awaiting_user",
                            "pending_question": payload.get("pendingQuestion"),
                            "assistantMessages": payload.get("assistantMessages") or [],
                        }
                    )
                return

            payload = await self._handle_reply(run, reply)
            run.outcome = {"kind": "reply", "payload": payload}
            if self.wants_stream:
                event_types = {str(event.get("type") or "") for event in run.events}
                if "reply_start" not in event_types:
                    await run.publish({"type": "reply_start"})
                if "reply_done" not in event_types:
                    text = str((payload.get("assistantMessage") or {}).get("content") or "")
                    if text:
                        await run.publish({"type": "reply_delta", "delta": text})
                    await run.publish({"type": "reply_done", "response": text})
                await run.publish(
                    {
                        "type": "saved",
                        "assistantMessage": payload.get("assistantMessage") or {},
                        "assistantMessages": payload.get("assistantMessages") or [],
                        "chatSummary": payload.get("chatSummary") or {},
                    }
                )
        except asyncio.CancelledError:
            if not run.outcome:
                run.outcome = {"kind": "interrupted"}
            if not run.termination_reason:
                run.termination_reason = "user_interrupted"
            await self._handle_cancelled(run)
            raise
        except Exception as exc:
            await self._handle_error(run, exc)

    async def _resume_agent(self):
        kwargs = {
            "ui_instance_id": self.ui_instance_id,
            "conversation_source": self.conversation_source,
        }
        if self.mode != "default":
            kwargs["permission_mode"] = self.mode
        return await self.routes.answer_pending(
            self.chat_id,
            self.question_id,
            self.answer_text,
            self.workspace_dir,
            **kwargs,
        )

    async def _handle_cancelled(self, run: ChatRun) -> None:
        await self.service.finalize_workspace_changes(
            chat_id=self.chat_id,
            run_id=run.run_id,
            workspace_dir=self.workspace_dir,
            before=self.changes_before,
            status="cancelled",
            run=run,
        )
        if self.answer_persisted:
            await asyncio.to_thread(
                self.service.stash_chat_pending_for,
                self.chat_id,
                None,
            )

    async def _handle_error(self, run: ChatRun, exc: Exception) -> None:
        await self.service.finalize_workspace_changes(
            chat_id=self.chat_id,
            run_id=run.run_id,
            workspace_dir=self.workspace_dir,
            before=self.changes_before,
            status="error",
            run=run,
        )
        logger.exception(
            "Workbench chat answer-resume failed for %s",
            self.chat_id,
        )
        run.outcome = {"kind": "error", "exc": exc}
        if self.wants_stream:
            await run.publish(
                {
                    "type": "error",
                    "error": "answer_resume_failed",
                    "message": str(exc),
                    **self.service.chat_error_metadata(exc),
                }
            )

    def _load_chat_summary(self) -> dict[str, Any]:
        chat = self.service.repository.get(self.chat_id)
        return self.service.public_chat_light(chat) if chat else {}

    async def _handle_awaiting_user(self, run: ChatRun) -> dict[str, Any]:
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
        public_additions = [self.service.public_message(item) for item in additions]
        return {
            "ok": True,
            "awaitingUser": True,
            "runId": run.run_id,
            "pendingQuestion": pending,
            "userMessage": self.service.public_message(self.answer_entry),
            "assistantMessages": public_additions,
        }

    async def _handle_reply(self, run: ChatRun, reply: Any) -> dict[str, Any]:
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
            raise RuntimeError("chat disappeared while resuming its answer")
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
        summary = await asyncio.to_thread(self._load_chat_summary)
        # ChatRunManager records lastRun immediately after the runner returns.
        # Keep the terminal stream projection accurate during that tiny gap.
        summary["runStatus"] = "completed"
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
            "runId": run.run_id,
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

    def _response(self, run: ChatRun):
        outcome = run.outcome or {}
        kind = str(outcome.get("kind") or "")
        if kind == "error":
            exc = outcome.get("exc")
            if not isinstance(exc, Exception):
                exc = RuntimeError("answer resume failed")
            return JSONResponse(
                {
                    "error": "answer resume failed",
                    "detail": str(exc),
                    **self.service.chat_error_metadata(exc),
                },
                status_code=502,
            )
        if kind == "interrupted" or run.status == "cancelled":
            payload: dict[str, Any] = {
                "ok": True,
                "interrupted": True,
                "awaitingUser": False,
                "runId": run.run_id,
            }
            if self.answer_persisted:
                payload["userMessage"] = self.service.public_message(self.answer_entry)
            return payload
        payload = outcome.get("payload")
        if isinstance(payload, dict):
            return payload
        return JSONResponse(
            {"error": "answer resume ended without an outcome"},
            status_code=500,
        )

    async def _publish_settled(self, run: ChatRun) -> None:
        outcome = run.outcome or {}
        kind = str(outcome.get("kind") or "")
        run_status = {
            "reply": "completed",
            "awaiting": "awaiting_user",
            "error": "failed",
            "interrupted": "cancelled",
        }.get(kind, "cancelled" if run.status == "cancelled" else run.status)
        payload = outcome.get("payload")
        details: dict[str, Any] = {
            "run_id": run.run_id,
            "run_status": run_status,
            "chatSummary": await asyncio.to_thread(self._load_chat_summary),
        }
        if isinstance(payload, dict):
            details["assistantMessages"] = payload.get("assistantMessages") or []
        await publish_chat_changed(
            self.chat_id,
            self.project_id,
            "settled",
            **details,
        )

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
