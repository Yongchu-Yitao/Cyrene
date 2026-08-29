from __future__ import annotations

import asyncio
import copy
import logging
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from cyrene.localization import app_language, localized
from cyrene.workbench.chat.chat_events import publish_chat_changed
from cyrene.workbench.chat.chat_runs import ChatRun
from cyrene.workbench.http import schemas as api_models
from cyrene.workbench.http.errors import localized_error_response
from cyrene.workbench.http.workbench.chat_routes.context import ChatRouteContext

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
        self.language = app_language()

    async def execute(self):
        from cyrene.core.permission import PERMISSION_MODES

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
            return localized_error_response(
                "This chat already has a reply in progress.",
                "此对话已有回复正在生成。",
                409,
                "chat_run_in_progress",
                language=self.language,
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
            return localized_error_response(
                "A question ID and answer are required.",
                "缺少问题 ID 或回答。",
                400,
                "answer_required",
                language=self.language,
            )
        self.chat = await asyncio.to_thread(
            self.service.repository.get,
            self.chat_id,
        )
        if not self.chat:
            return localized_error_response(
                "Chat not found.",
                "未找到对话。",
                404,
                "chat_not_found",
                language=self.language,
            )
        self.base_chat = copy.deepcopy(self.chat)
        self.mode, self.is_side_agent = _select_answer_permission_mode(
            self.chat,
            self.requested_mode,
            permission_modes,
        )
        checkpoint = await asyncio.to_thread(
            self.service.run_manager.conversation_runtime.context_checkpoint,
            self.chat_id,
        )
        pending_value = (
            checkpoint.get("pending_question")
            if isinstance(checkpoint, dict)
            and checkpoint.get("status") == "awaiting_user"
            else None
        )
        pending = (
            pending_value.as_dict()
            if hasattr(pending_value, "as_dict")
            else None
        )
        if not pending or str(pending.get("id") or "") != self.question_id:
            return localized_error_response(
                "No matching pending question was found.",
                "未找到匹配的待回答问题。",
                409,
                "pending_question_not_found",
                language=self.language,
            )
        self.pending = pending
        self.agent_run_id = str((checkpoint or {}).get("run_id") or "")
        self.routes = self.context.runtime()
        self.project_id = str(self.chat.get("projectId") or "")
        store = await asyncio.to_thread(self.routes.read_store)
        self.project = self.routes.find_project(store, self.project_id)
        if not self.project:
            return localized_error_response(
                "Project not found.",
                "未找到项目。",
                404,
                "project_not_found",
                language=self.language,
            )
        try:
            self.workspace_dir = self.service.resolve_chat_workspace_dir(
                self.chat,
                self.project,
                self.routes.resolve_workspace_dir,
            )
        except ValueError:
            logger.warning(
                "Invalid workspace configuration for chat %s",
                self.chat_id,
                exc_info=True,
            )
            return localized_error_response(
                "The workspace configuration is invalid.",
                "工作区配置无效。",
                400,
                "invalid_workspace",
                language=self.language,
            )
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
        self.changes_before = await self.service.capture_workspace_changes_baseline(
            self.workspace_dir,
            self.agent_run_id or run.run_id,
        )
        from cyrene.runtime.host_bridge import resolve_conversation_source

        self.conversation_source = await resolve_conversation_source(self.ui_instance_id)

    async def _run(self, run: ChatRun) -> None:
        self.changes_before = None
        self.answer_persisted = False
        try:
            await self._persist_answer(run)
            result = await self._resume_agent(run)
            run.status = "finishing"
            status = str(result.status or "completed")
            await self.service.finalize_workspace_changes(
                chat_id=self.chat_id,
                run_id=result.run_id,
                workspace_dir=self.workspace_dir,
                before=self.changes_before,
                status=status,
                run=run,
            )
            if status == "awaiting_user":
                payload = await self._handle_awaiting_user(result)
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

            payload = await self._handle_reply(result)
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

    def _attachment_path_map(self) -> dict[str, str]:
        from pathlib import Path

        paths: dict[str, str] = {}
        for message in self.chat.get("messages") or ():
            if not isinstance(message, dict):
                continue
            attachments = message.get("agentAttachments")
            for item in attachments if isinstance(attachments, list) else ():
                if not isinstance(item, dict):
                    continue
                full_path = str(item.get("path") or "").strip()
                if not full_path:
                    continue
                name = Path(full_path).name
                paths[name] = full_path
                parts = name.split("_", 1)
                if len(parts) == 2:
                    paths[parts[1]] = full_path
                attachment_id = str(item.get("id") or "").strip()
                if attachment_id:
                    paths[attachment_id] = full_path
        return paths

    async def _resume_agent(self, run: ChatRun):
        from cyrene.workbench.core_adapter.conversation_runtime import ConversationConfig

        original_request = next(
            (
                str(item.get("content") or "")
                for item in reversed(self.chat.get("messages") or [])
                if isinstance(item, dict)
                and item.get("role") == "user"
                and not item.get("answerToQuestionId")
            ),
            "",
        )
        memory_snapshot = self.service.ensure_chat_memory_snapshot(self.chat)
        input_context = self.service.resolve_composer_input_context(
            self.chat,
            self.workspace_dir,
            strict=True,
        )
        config = ConversationConfig(
            session_id=self.chat_id,
            workspace_dir=self.workspace_dir,
            db_path=self.context.db_path,
            bot=self.context.bot,
            host_chat_id=self.routes.chat_id,
            client_request_id=str(self.pending.get("clientRequestId") or ""),
            permission_mode=self.mode,
            public_user_message=original_request,
            attachment_paths=self._attachment_path_map(),
            remote_device_ids=tuple(input_context["remoteDeviceIds"]),
            soul_enabled=bool(input_context["soulActive"]),
            workspace_enabled=bool(input_context["workspaceActive"]),
            context_activations=dict(input_context["contextActivations"]),
            resolved_context_activations=dict(
                input_context["resolvedContextActivations"]
            ),
            project_id=self.project_id,
            project_memory_snapshot=memory_snapshot,
            session_title=str(self.chat.get("title") or ""),
            memory_write_enabled=not self.is_side_agent,
            memory_trigger_enabled=not self.is_side_agent,
            memory_archive_enabled=True,
            completed_turn_count=int(self.chat.get("completedTurnCount") or 0) + 1,
            response_capabilities=("interactive_blocks",),
            ui_instance_id=self.ui_instance_id,
            conversation_source=self.conversation_source,
        )
        result = await self.service.run_manager.conversation_runtime.answer(
            config,
            self.question_id,
            self.answer_text,
            publish=run.publish,
        )
        self._project_plan(run, result)
        return result

    def _project_plan(self, run: ChatRun, result: Any) -> None:
        plan = result.active_plan
        for event in run.events:
            if str(event.get("type") or "") not in {"plan", "plan_progress"}:
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
            candidate = payload.get("plan") if isinstance(payload, dict) else None
            if isinstance(candidate, dict):
                plan = candidate
        self.active_plan = copy.deepcopy(plan) if isinstance(plan, dict) else None

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
            await asyncio.to_thread(self._clear_answered_pending)

    def _clear_answered_pending(self) -> None:
        chat = self.service.repository.get(self.chat_id)
        if not chat:
            return
        base_chat = copy.deepcopy(chat)
        chat.pop("pendingQuestion", None)
        chat["status"] = "idle"
        chat["updatedAt"] = self.service.utc_now_iso()
        self.service.repository.write_one(chat, base_chat=base_chat)

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
        if self.answer_persisted:
            await asyncio.to_thread(self._clear_answered_pending)
        if self.wants_stream:
            await run.publish(
                {
                    "type": "error",
                    "error": "answer_resume_failed",
                    "message": localized(
                        "The Agent could not continue this answer.",
                        "Agent 无法继续处理此回答。",
                        language=self.language,
                    ),
                    **self.service.chat_error_metadata(exc),
                }
            )

    def _load_chat_summary(self) -> dict[str, Any]:
        chat = self.service.repository.get(self.chat_id)
        return self.service.public_chat_light(chat) if chat else {}

    def _runtime_activities(self, result: Any, model: str) -> list[dict[str, Any]]:
        now = self.service.utc_now_iso()
        activities = [
            copy.deepcopy(dict(item))
            for item in (result.activity_messages or ())
            if isinstance(item, dict)
        ]
        for item in activities:
            item.setdefault("model", model)
            item.setdefault("createdAt", now)
        return activities

    def _runtime_message_fields(self, result: Any) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        usage = dict(result.usage or {})
        if any(usage.values()):
            fields["usage"] = usage
        identity = dict(result.model_identity or {})
        if identity:
            fields["modelIdentity"] = identity
        if result.generation_duration_ms is not None and result.generation_duration_ms > 0:
            fields["modelGenerationDurationMs"] = round(
                float(result.generation_duration_ms),
                3,
            )
        if result.output_tokens_per_second is not None and result.output_tokens_per_second > 0:
            fields["outputTokensPerSecond"] = round(
                float(result.output_tokens_per_second),
                3,
            )
        return fields

    async def _handle_awaiting_user(self, result: Any) -> dict[str, Any]:
        pending_value = result.pending_question
        if pending_value is None:
            raise RuntimeError("Agent paused without a pending question")
        pending = pending_value.as_dict()
        model = str(result.model or self.chat.get("model") or "")
        additions = self._runtime_activities(result, model)
        additions.append(
            self.service.pending_question_message(
                pending,
                usage=dict(result.usage or {}),
                model=model,
            )
        )
        fresh_chat = await asyncio.to_thread(self.service.repository.get, self.chat_id)
        if not fresh_chat:
            raise RuntimeError("chat disappeared while saving pending question")
        base_chat = copy.deepcopy(fresh_chat)
        self.service.merge_chat_messages_chronologically(fresh_chat, additions)
        fresh_chat["pendingQuestion"] = pending
        fresh_chat["status"] = "idle"
        fresh_chat["lastModel"] = model
        if isinstance(self.active_plan, dict):
            fresh_chat["activePlan"] = copy.deepcopy(self.active_plan)
        fresh_chat["updatedAt"] = self.service.utc_now_iso()
        await asyncio.to_thread(
            self.service.repository.write_one,
            fresh_chat,
            base_chat=base_chat,
        )
        public_additions = [self.service.public_message(item) for item in additions]
        return {
            "ok": True,
            "awaitingUser": True,
            "runId": result.run_id,
            "pendingQuestion": pending,
            "userMessage": self.service.public_message(self.answer_entry),
            "assistantMessages": public_additions,
            "chatSummary": self.service.public_chat_light(fresh_chat),
        }

    async def _handle_reply(self, result: Any) -> dict[str, Any]:
        fresh_chat = await asyncio.to_thread(
            self.service.repository.get,
            self.chat_id,
        )
        if not fresh_chat:
            raise RuntimeError("chat disappeared while resuming its answer")
        model = str(result.model or fresh_chat.get("model") or "")
        timeline = self._runtime_activities(result, model)
        assistant = self._assistant_message(result, model)
        saved_messages = [*timeline, assistant]
        await self._persist_completed_reply(
            fresh_chat,
            saved_messages,
            assistant,
        )
        summary = await asyncio.to_thread(self._load_chat_summary)
        # ChatRunManager records lastRun immediately after the runner returns.
        # Keep the terminal stream projection accurate during that tiny gap.
        summary["runStatus"] = "completed"
        return {
            "ok": True,
            "awaitingUser": False,
            "runId": result.run_id,
            "userMessage": self.service.public_message(self.answer_entry),
            "assistantMessage": self.service.public_message(assistant),
            "assistantMessages": [self.service.public_message(item) for item in saved_messages],
            "chatSummary": summary,
        }

    def _assistant_message(
        self,
        result: Any,
        model: str,
    ) -> dict[str, Any]:
        message: dict[str, Any] = {
            "id": self.service.short_id("msg"),
            "role": "assistant",
            "content": str(result.text or ""),
            "createdAt": self.service.utc_now_iso(),
            "model": model,
            "processingDurationMs": max(
                0,
                int(round((time.monotonic() - self.processing_started_at) * 1000)),
            ),
        }
        message.update(self._runtime_message_fields(result))
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
        if isinstance(self.active_plan, dict):
            chat["activePlan"] = copy.deepcopy(self.active_plan)
        chat["updatedAt"] = assistant["createdAt"]
        await asyncio.to_thread(
            self.service.repository.write_one,
            chat,
            base_chat=base_chat,
        )
        from cyrene.runtime.host_actions import finalize_origin

        asyncio.create_task(finalize_origin(self.chat_id, ""))
        return completed

    def _response(self, run: ChatRun):
        outcome = run.outcome or {}
        kind = str(outcome.get("kind") or "")
        if kind == "error":
            exc = outcome.get("exc")
            if not isinstance(exc, Exception):
                exc = RuntimeError("answer resume failed")
            metadata = dict(self.service.chat_error_metadata(exc))
            code = str(metadata.pop("code", "") or "answer_resume_failed")
            metadata.pop("detail", None)
            metadata.pop("message", None)
            metadata.pop("error", None)
            return localized_error_response(
                "The Agent could not continue this answer.",
                "Agent 无法继续处理此回答。",
                502,
                code,
                language=self.language,
                **metadata,
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
        return localized_error_response(
            "The answer ended without a result.",
            "回答流程结束，但未产生结果。",
            500,
            "answer_outcome_missing",
            language=self.language,
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
            "run_id": (
                str(payload.get("runId") or run.run_id)
                if isinstance(payload, dict)
                else run.run_id
            ),
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
