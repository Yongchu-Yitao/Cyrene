from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import time
import uuid
from typing import Any

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from cyrene.runtime.memory.conversations import archive_session_exchange
from cyrene.workbench import chat_groups
from cyrene.workbench.chat_runs import ChatRun
from cyrene.workbench.inbox import GuidanceAdmissionClosed
from cyrene.workbench.notifications import append_notification
from route import schemas as api_models
from route.workbench.chat_routes.context import ChatRouteContext
from route.workbench.chat_routes.shared import (
    _DETACHED_ANSWER_TASKS,
    finish_detached_answer_task,
    schedule_reply_bookkeeping,
    schedule_structured_memory_capture,
    schedule_workspace_changes_finalize,
    track_session_title_task,
)

logger = logging.getLogger(__name__)


def register_run_routes(router: APIRouter, context: ChatRouteContext) -> dict[str, Any]:
    service = context.service
    runtime = context.workbench_runtime
    bot = context.bot
    db_path = context.db_path
    _routes = context.runtime
    _project_data_key = context.project_data_key
    _resolve_library_file_payload = context.resolve_library_file_payload
    _public_pinned_resource = context.public_pinned_resource
    _CHATS_STORE_JSON_LOCK = service.repository.lock
    _CHAT_RUN_MANAGER = service.run_manager
    _capture_workspace_changes_baseline = service.capture_workspace_changes_baseline
    _chat_soul_active = service.chat_soul_active
    _chat_workspace_active = service.chat_workspace_active
    _completed_turn_count = service.completed_turn_count
    _extract_exchange_timeline = service.extract_exchange_timeline
    _finalize_workspace_changes = service.finalize_workspace_changes
    _find_chat = service.repository.find
    _get_workbench_chat = service.repository.get
    _last_exchange_model = service.last_exchange_model
    _mark_user_activity = service.mark_user_activity
    _merge_chat_messages_chronologically = service.merge_chat_messages_chronologically
    _next_completed_turn_count = service.next_completed_turn_count
    _normalize_workspace_override = service.normalize_workspace_override
    _pending_question_message = service.pending_question_message
    _public_message = service.public_message
    _publish_live_exchange_segments_loop = service.publish_live_exchange_segments_loop
    _read_chats_store = service.repository.read
    _mutate_chat_store = service.repository.mutate_one
    _record_chat_run_outcome = service.record_chat_run_outcome
    _remove_retry_replaced_messages = service.remove_retry_replaced_messages
    _resolve_chat_workspace_dir = service.resolve_chat_workspace_dir
    _session_state_messages = service.session_state_messages
    _settle_chat_running_status = service.settle_chat_running_status
    _short_id = service.short_id
    _side_agent_parent_transcript = service.side_agent_parent_transcript
    _stash_chat_pending_for = service.stash_chat_pending_for
    _truncate_state_for_retry = service.truncate_state_for_retry
    _utc_now_iso = service.utc_now_iso
    _workbench_chat_run_error_message = service.chat_run_error_message
    _write_chats_store = service.repository.write
    _write_chat_store = service.repository.write_one
    complete_chat_plan = service.complete_chat_plan
    disable_button_block = service.disable_button_block
    has_button_block = service.has_button_block
    _finish_detached_answer_task = finish_detached_answer_task

    def _schedule_post_reply_bookkeeping(**kwargs: Any) -> None:
        schedule_reply_bookkeeping(service, **kwargs)

    def _schedule_workspace_changes_finalize(**kwargs: Any) -> None:
        schedule_workspace_changes_finalize(service, **kwargs)

    _schedule_structured_memory_capture = schedule_structured_memory_capture
    _track_session_title_task = track_session_title_task

    @router.get("/api/workbench/chats/{chat_id}/run-stream")
    async def api_workbench_chat_run_stream(chat_id: str, cursor: int = 0):
        """Reconnect to an existing streamed run without submitting a message."""
        replay_lookup = getattr(_CHAT_RUN_MANAGER, "get_replayable", _CHAT_RUN_MANAGER.get)
        run = replay_lookup(chat_id)
        if run is None:
            await asyncio.to_thread(_settle_chat_running_status, chat_id)
            return JSONResponse(
                {"error": "chat has no running reply", "code": "chat_run_not_found"},
                status_code=404,
            )
        return StreamingResponse(
            _CHAT_RUN_MANAGER.stream(run, cursor=max(0, int(cursor or 0))),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache"},
        )

    @router.post("/api/workbench/chats/{chat_id}/guidance")
    async def api_workbench_chat_guidance(chat_id: str, body_model: api_models.ChatGuidanceBody):
        """Steer the currently running Workbench conversation.

        Guidance is queued in the run-scoped inbox.  A tool waiter consumes it
        immediately; otherwise the agent picks it up at the next model/tool
        boundary.  It never starts a second conversation run.
        """
        body = api_models.body_dict(body_model)
        message = str(body.get("message") or "").strip()
        client_request_id = str(body.get("clientRequestId") or "").strip()
        if not message:
            return JSONResponse(
                {"error": "guidance message is empty", "code": "guidance_empty"},
                status_code=422,
            )
        run = _CHAT_RUN_MANAGER.get(chat_id)
        if run is None or run.status != "running":
            return JSONResponse(
                {"error": "chat has no running reply", "code": "chat_not_running"},
                status_code=409,
            )
        # Durable inbox setup happens off the HTTP event loop. Guidance must
        # wait for it before accepting an event, otherwise a just-started run
        # can race schema initialization.
        await run.ready.wait()
        if run.status != "running":
            return JSONResponse(
                {"error": "chat has no running reply", "code": "chat_not_running"},
                status_code=409,
            )
        chat = await asyncio.to_thread(_get_workbench_chat, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)

        now = _utc_now_iso()
        public_message_id = _short_id("msg")
        try:
            event = await run.inbox.put_guidance(
                message,
                client_request_id=client_request_id,
                public_message_id=public_message_id,
                public_created_at=now,
            )
        except GuidanceAdmissionClosed:
            # The UI promotes this text to a normal follow-up. Do not release
            # that retry while the sealed run is still finalizing, otherwise it
            # can immediately bounce with ``chat_run_in_progress``.
            await run.done.wait()
            return JSONResponse(
                {"error": "chat has no running reply", "code": "chat_not_running"},
                status_code=409,
            )
        except RuntimeError:
            logger.exception("Failed to persist guidance for chat %s", chat_id)
            return JSONResponse(
                {
                    "error": "guidance could not be saved; please retry",
                    "code": "guidance_persistence_failed",
                },
                status_code=503,
            )
        if event.get("duplicate"):
            duplicate_message = next(
                (
                    item
                    for item in reversed(chat.get("messages") or [])
                    if isinstance(item, dict)
                    and (
                        str(item.get("guidanceEventId") or "") == str(event.get("event_id") or "")
                        or (client_request_id and str(item.get("clientRequestId") or "") == client_request_id)
                    )
                ),
                None,
            )
            response = {
                "queued": True,
                "duplicate": True,
                "eventId": event["event_id"],
                "runId": run.run_id,
            }
            if duplicate_message is not None:
                response["userMessage"] = _public_message(duplicate_message)
            return response

        user_entry = {
            "id": public_message_id,
            "role": "user",
            "content": message,
            "createdAt": now,
            "guidance": True,
            "guidanceEventId": event["event_id"],
            "runId": run.run_id,
        }
        if client_request_id:
            user_entry["clientRequestId"] = client_request_id

        def persist_guidance(current: dict[str, Any]) -> None:
            current.setdefault("messages", []).append(user_entry)
            current["updatedAt"] = now

        await asyncio.to_thread(_mutate_chat_store, chat_id, persist_guidance)
        await run.publish(
            {
                "type": "guidance_received",
                "eventId": event["event_id"],
                "runId": run.run_id,
                "userMessage": _public_message(user_entry),
                "message": "Guidance queued for the running agent.",
            }
        )
        return {
            "queued": True,
            "eventId": event["event_id"],
            "runId": run.run_id,
            "userMessage": _public_message(user_entry),
        }

    async def _workbench_chat_send_impl(
        chat_id: str,
        body: dict[str, Any],
        *,
        detached: bool = False,
    ):
        processing_started_at = time.monotonic()
        from cyrene.agent import run_agent
        from cyrene.agent.context import set_attachment_paths
        from cyrene.agent.state import PERMISSION_MODES

        message = str(body.get("message") or "").strip()
        client_request_id = str(body.get("clientRequestId") or "").strip()
        ui_instance_id = str(body.get("uiInstanceId") or "").strip()
        attachments = body.get("attachments") if isinstance(body.get("attachments"), list) else []
        if attachments:
            attachments = [await _resolve_library_file_payload(item) if isinstance(item, dict) else item for item in attachments]
        command = str(body.get("command") or "").strip()
        wants_stream = bool(body.get("stream"))
        retry = bool(body.get("retry"))
        fork_replay = bool(body.get("forkReplay"))
        requested_mode = str(body.get("mode") or "").strip().lower()
        requested_model = str(body.get("model") or "").strip()
        requested_effort = str(body.get("reasoningEffort") or "").strip().lower()
        lang = str(body.get("lang") or "").strip().lower()
        voice_command = body.get("voiceCommand") is True
        # Persist the UI language so server-side flows (the proactive scheduler)
        # can reply in the same language even with no HTTP request to read.
        if lang in {"en", "zh"}:
            try:
                from cyrene.runtime.settings_store import get as _get_setting, set_ as _set_setting

                if str(_get_setting("app_language", "") or "") != lang:
                    _set_setting("app_language", lang)
            except Exception:
                pass

        R = _routes()

        def notify_voice_command_attention(pending: Any) -> None:
            if not voice_command:
                return
            question = pending if isinstance(pending, dict) else {}
            prompt = next(
                (str(question.get(key) or "").strip() for key in ("text", "prompt", "question", "title") if str(question.get(key) or "").strip()),
                "Agent 正在等待你的回答。",
            )
            append_notification(
                title="语音命令需要你的回答",
                body=prompt,
                tab="mention",
                project_ref=project_id,
                source="voice_command_attention",
                source_label="语音命令",
                link_label=str(chat.get("title") or "新对话"),
                meta={"chatId": chat_id, "voiceCommand": True},
            )

        normalized = R.normalize_attachments(attachments)
        public_attachments = [R.build_public_attachment_payload(item) for item in normalized]
        if not retry and not message and not normalized:
            return JSONResponse({"error": "message is required"}, status_code=400)

        # ── Budget gate ──
        _bgt = await runtime.check_budget_gate(chat_id)
        if _bgt:
            return JSONResponse(_bgt, status_code=403)

        chat = await asyncio.to_thread(_get_workbench_chat, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        base_chat = copy.deepcopy(chat)
        from cyrene.agent_runtime.builtin import normalize_agent_binding

        agent_binding = normalize_agent_binding(chat.get("agent") if isinstance(chat.get("agent"), dict) else None)
        is_external_agent = not agent_binding.is_builtin
        requested_agent = body.get("agent") if isinstance(body.get("agent"), dict) else None
        requested_installation_id = str((requested_agent or {}).get("installationId") or "").strip()
        if requested_installation_id and requested_installation_id != agent_binding.installation_id:
            return JSONResponse(
                {
                    "error": "Agent binding cannot be changed from the message endpoint",
                    "code": "agent_binding_locked",
                },
                status_code=409,
            )
        is_side_agent = str(chat.get("kind") or "") == "side-agent"
        completed_turn_count_before = _completed_turn_count(chat)
        parent_chat = (
            await asyncio.to_thread(
                _get_workbench_chat,
                str(chat.get("parentChatId") or ""),
            )
            if is_side_agent
            else None
        )
        parent_transcript = _side_agent_parent_transcript(parent_chat)
        stored_mode = str(chat.get("permissionMode") or "").strip().lower()
        if requested_mode:
            mode = requested_mode if requested_mode in PERMISSION_MODES else "default"
        else:
            mode = stored_mode if stored_mode in PERMISSION_MODES else "default"
        chat["permissionMode"] = mode
        if "soulActive" in body:
            chat["soulActive"] = bool(body.get("soulActive"))
        if "workspaceActive" in body:
            chat["workspaceActive"] = bool(body.get("workspaceActive"))
        project_id = str(chat.get("projectId") or "")
        project_store = await asyncio.to_thread(R.read_store)
        project = R.find_project(project_store, project_id)
        if not project:
            return JSONResponse({"error": "project not found"}, status_code=404)
        if "workspaceOverride" in body:
            try:
                requested_workspace = _normalize_workspace_override(body.get("workspaceOverride"))
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            if requested_workspace:
                chat["workspaceOverride"] = requested_workspace
            else:
                chat.pop("workspaceOverride", None)
        try:
            workspace_dir = _resolve_chat_workspace_dir(chat, project, R.resolve_workspace_dir)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        selected_candidate = None
        recovered_stale_selection = False
        agent_owns_models = is_external_agent and str((chat.get("modelAccess") or {}).get("mode") or "") == "agent_managed"
        selected_key = "" if agent_owns_models else requested_model or str(chat.get("modelSelectionId") or "").strip()
        if selected_key:
            from cyrene.runtime.model_configuration import selectable_model_candidates
            from cyrene.runtime.settings_store import get_models

            configured_models = selectable_model_candidates(legacy_candidates=get_models() or [])
            selected_candidate = next(
                (
                    candidate
                    for candidate in configured_models
                    if selected_key
                    in {
                        str(candidate.get("id") or "").strip(),
                        str(candidate.get("model") or "").strip(),
                        str(candidate.get("name") or "").strip(),
                    }
                ),
                None,
            )
            if selected_candidate is None:
                if requested_model:
                    return JSONResponse({"error": "configured model not found"}, status_code=400)
                # The active model source can change while a conversation is
                # idle (for example, Codex quota is exhausted and the user
                # switches back to a custom DeepSeek model).  In that case the
                # chat still carries the old source-specific selection id,
                # which is no longer present in the active candidate list.
                # A retry has no explicit model field, so recover by selecting
                # the current configured primary instead of rejecting the run
                # with a misleading "configured model not found" response.
                primary_models = get_models() or []
                selected_candidate = primary_models[0] if primary_models else None
                if selected_candidate is not None:
                    recovered_stale_selection = True
                    selected_key = str(selected_candidate.get("id") or selected_candidate.get("model") or selected_candidate.get("name") or "").strip()
        if selected_candidate is not None:
            from cyrene.model_runtime.client import set_session_model_preference

            selected_model_name = str(selected_candidate.get("model") or selected_candidate.get("name") or selected_key).strip()
            selected_model_id = str(selected_candidate.get("id") or selected_key).strip()
            selected_effort = (
                requested_effort
                or str(
                    (selected_candidate.get("reasoning_effort") if recovered_stale_selection else chat.get("reasoningEffort")) or selected_candidate.get("reasoning_effort") or ""
                )
                .strip()
                .lower()
            )
            set_session_model_preference(
                chat_id,
                selected_candidate,
                selected_effort,
            )
            chat["modelSelectionId"] = selected_model_id
            chat["model"] = selected_model_name
            chat["reasoningEffort"] = selected_effort

        existing_run = _CHAT_RUN_MANAGER.get(chat_id)
        if existing_run is not None:
            return JSONResponse(
                {"error": "chat already has a running reply", "code": "chat_run_in_progress"},
                status_code=409,
            )

        now = _utc_now_iso()
        messages = chat.setdefault("messages", [])
        should_generate_title = False
        user_entry: dict[str, Any]
        truncate_after_id = ""
        retry_replaced_message_ids: set[str] = set()
        retry_state_backup: tuple[Any, bytes | None] | None = None
        if retry:
            # Regenerate the last exchange transactionally. Keep the public
            # transcript intact until the replacement reply has been persisted;
            # otherwise a failed retry permanently deletes the previous answer.
            last_user_index = -1
            for index in range(len(messages) - 1, -1, -1):
                if messages[index].get("role") == "user":
                    last_user_index = index
                    break
            if last_user_index < 0:
                return JSONResponse({"error": "nothing to retry"}, status_code=400)
            user_entry = messages[last_user_index]
            truncate_after_id = str(user_entry.get("id") or "")
            retry_replaced_message_ids = {str(item.get("id") or "") for item in messages[last_user_index + 1 :] if isinstance(item, dict) and str(item.get("id") or "")}
            message = str(user_entry.get("content") or "").strip()
            command = ""
            normalized = R.normalize_attachments(user_entry.get("agentAttachments") or [])
            public_attachments = user_entry.get("attachments") if isinstance(user_entry.get("attachments"), list) else []
            # A fork already truncated the raw state at the edit boundary; only
            # a plain retry needs to drop the last exchange from the state here.
            if not fork_replay:
                state_path = runtime.session_state_file(chat_id)
                previous_state = await asyncio.to_thread(lambda: state_path.read_bytes() if state_path.exists() else None)
                retry_state_backup = (state_path, previous_state)
                await asyncio.to_thread(_truncate_state_for_retry, chat_id)
        else:
            user_entry = {
                "id": _short_id("msg"),
                "role": "user",
                "content": message,
                "createdAt": now,
            }
            if client_request_id:
                user_entry["clientRequestId"] = client_request_id
            if public_attachments:
                user_entry["attachments"] = public_attachments
                # Keep the normalized (path-bearing) attachments privately so a
                # later retry can rebuild the agent prompt + read-guard map.
                user_entry["agentAttachments"] = normalized
            is_first_message = not any(m.get("role") == "user" for m in messages)
            messages.append(user_entry)
            if is_first_message:
                locked_agent = dict(chat.get("agent") or {})
                locked_agent["bindingLocked"] = True
                chat["agent"] = locked_agent
            if is_first_message and chat.get("title") in ("", "新对话", None) and message:
                chat["title"] = message.replace("\n", " ")[:24]
            if is_first_message and bool(message) and not bool(chat.get("titleLocked")) and not chat.get("titleNamingStatus"):
                should_generate_title = True
                chat["titleNamingStatus"] = "pending"
                chat["titleNamingStartedAt"] = now
        if not is_side_agent:
            try:
                # Retry truncation can remove a membership event that followed
                # the regenerated exchange, so reconcile only after that cut.
                await chat_groups.reconcile_session(chat_id)
            except Exception:
                logger.exception("Failed to reconcile chat-group context for %s", chat_id)
                if retry_state_backup is not None:
                    state_path, previous_state = retry_state_backup
                    if previous_state is None:
                        await asyncio.to_thread(state_path.unlink, missing_ok=True)
                    else:
                        await asyncio.to_thread(state_path.write_bytes, previous_state)
                return JSONResponse(
                    {"error": "chat group context could not be prepared"},
                    status_code=503,
                )
        chat["status"] = "running"
        if selected_candidate is None and not agent_owns_models:
            chat["model"] = R.get_model()
        _mark_user_activity(chat, now)
        await asyncio.to_thread(
            _write_chat_store,
            chat,
            base_chat=base_chat,
        )

        # Register sent attachments into the session's project knowledge base
        # (idempotent by content hash; failures never block the message).
        if normalized and not retry:
            await R.register_attachments_kb(chat_id, normalized)

        async def _name_session_once() -> None:
            if not should_generate_title:
                return
            from cyrene.workbench.session_naming import generate_session_title
            from cyrene.model_runtime.client import resolve_session_model_candidate

            naming_candidate = resolve_session_model_candidate(chat_id)
            candidate_id = str((naming_candidate or {}).get("id") or "")
            candidate_model = str((naming_candidate or {}).get("model") or "")
            logger.info(
                "Workbench session naming started [chat=%s project=%s candidate=%s model=%s input_chars=%d]",
                chat_id,
                project_id,
                candidate_id or "unresolved",
                candidate_model or "unresolved",
                len(message),
            )

            try:
                if naming_candidate is None:
                    raise RuntimeError("no configured model candidate for conversation")
                generated_title = await generate_session_title(
                    message,
                    limit=60,
                    candidate=naming_candidate,
                )
            except Exception as exc:
                logger.exception(
                    "Workbench session naming failed [chat=%s project=%s candidate=%s model=%s error_type=%s]",
                    chat_id,
                    project_id,
                    candidate_id or "unresolved",
                    candidate_model or "unresolved",
                    type(exc).__name__,
                )
                generated_title = ""

            def persist_title() -> bool:
                changed = False

                def update(fresh_chat: dict[str, Any]) -> bool:
                    nonlocal changed
                    if fresh_chat.get("titleNamingStatus") != "pending":
                        return False
                    if generated_title and not bool(fresh_chat.get("titleLocked")):
                        fresh_chat["title"] = generated_title
                        fresh_chat["titleNamingStatus"] = "generated"
                        fresh_chat["titleGeneratedAt"] = _utc_now_iso()
                        changed = True
                    else:
                        fresh_chat["titleNamingStatus"] = (
                            "locked" if bool(fresh_chat.get("titleLocked")) else "failed"
                        )
                    return True

                _mutate_chat_store(chat_id, update)
                return changed

            changed = await asyncio.to_thread(persist_title)
            logger.info(
                "Workbench session naming finished [chat=%s project=%s candidate=%s model=%s status=%s output_chars=%d]",
                chat_id,
                project_id,
                candidate_id or "unresolved",
                candidate_model or "unresolved",
                "generated" if changed else "failed_or_locked",
                len(generated_title),
            )
            if changed:
                from cyrene.observability import debug

                await debug.publish_event(
                    {
                        "type": "workbench_chat_changed",
                        "change": "renamed",
                        "session_id": chat_id,
                        "chat_id": chat_id,
                        "project_id": project_id,
                    },
                    session_id=chat_id,
                )

        if should_generate_title:
            _track_session_title_task(asyncio.create_task(_name_session_once()))

        agent_message = message
        if is_external_agent and command:
            agent_message = "/" + command + ((" " + message) if message else "")
        if is_side_agent:
            source_quote = str(chat.get("sourceQuote") or "").strip()
            agent_message = (
                "你是主对话旁的独立 Side Agent。以下 main_conversation 是提问"
                "发生时主对话的完整公开内容；结合全部对话理解问题，并把"
                " selected_quote 作为用户当前关注的重点。不要假装上下文中未提供"
                "的事实。\n\n<main_conversation>\n"
                + (parent_transcript or "(empty)")
                + "\n</main_conversation>\n\n<selected_quote>\n"
                + (source_quote or "(none)")
                + "\n</selected_quote>\n\n用户问题：\n"
                + message
            )
        if normalized:
            agent_message = (agent_message or "[Attachment upload]") + R.attachment_prompt_block(normalized)
            # Auto-allow uploaded files for tool read guards (same as /api/chat).
            att_map: dict[str, str] = {}
            for item in normalized:
                full_path = str(item.get("path") or "").strip()
                if not full_path:
                    continue
                from pathlib import Path as _Path

                uuid_name = _Path(full_path).name
                att_map[uuid_name] = full_path
                parts = uuid_name.split("_", 1)
                if len(parts) == 2:
                    att_map[parts[1]] = full_path
            set_attachment_paths(att_map)

        # Capture IDs of messages already in state before this exchange, so
        # _extract_exchange_segments can identify new messages by ID rather
        # than by positional index (which would break after session compaction).
        state_ids_before: set[str] = set()
        for m in await asyncio.to_thread(_session_state_messages, chat_id):
            mid = str(m.get("message_id") or m.get("id") or "").strip()
            if mid:
                state_ids_before.add(mid)

        # External Agent usage is collected by the nested runtime callback and
        # consumed later by the sibling finalizer. Keep it in their shared
        # enclosing scope; defining it inside _run makes successful streamed
        # replies crash during persistence after they have already rendered.
        external_usage: dict[str, int] = {}
        external_context_report: dict[str, Any] = {}
        external_artifacts: list[dict[str, Any]] = []
        external_commands: list[Any] | None = None
        external_plan: dict[str, Any] | None = None
        external_agent_mode: Any = None
        external_config_options: dict[str, dict[str, Any]] = {}
        external_trace: list[dict[str, Any]] = []
        external_reasoning_parts: list[str] = []
        external_notifications: list[dict[str, Any]] = []
        external_notification_keys: set[str] = set()

        async def _run(run: ChatRun) -> str:
            logger.info("Workbench chat _run entered [chat=%s run=%s]", chat_id, run.run_id)
            if is_external_agent:
                from cyrene.agent_runtime import run_external_agent_turn
                from cyrene.agent_runtime.events import event_envelope
                from cyrene.agent_runtime.notices import LeadingOperationalNoticeFilter

                reply_parts: list[str] = []
                completed_reply = ""
                external_session_id = ""
                notice_filter = LeadingOperationalNoticeFilter()

                async def publish_notice(notice: dict[str, Any], source_event: dict[str, Any]) -> None:
                    key = "\n".join(
                        (
                            str(notice.get("category") or "transport_warning"),
                            str(notice.get("message") or "").strip(),
                        )
                    )
                    if not key.strip() or key in external_notification_keys:
                        return
                    await publish_external(
                        event_envelope(
                            type="notification.created",
                            payload=notice,
                            timestamp=str(source_event.get("timestamp") or ""),
                            agent_id=str(source_event.get("agentId") or ""),
                            installation_id=str(source_event.get("installationId") or ""),
                            chat_id=str(source_event.get("chatId") or chat_id),
                            run_id=str(source_event.get("runId") or run.run_id),
                            session_id=str(source_event.get("sessionId") or ""),
                            actor_id=str(source_event.get("actorId") or "primary"),
                            parent_run_id=source_event.get("parentRunId"),
                            extensions={
                                "originEventId": str(source_event.get("eventId") or ""),
                                "normalizedFrom": "message_text",
                            },
                        )
                    )

                async def publish_external(event: dict[str, Any]) -> None:
                    nonlocal completed_reply, external_usage, external_context_report, external_session_id, external_commands, external_plan, external_agent_mode
                    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                    event_type = str(event.get("type") or "")
                    if event_type == "message.delta":
                        delta = str(payload.get("delta") or payload.get("text") or "")
                        notices, visible_delta = notice_filter.feed(delta)
                        for notice in notices:
                            await publish_notice(notice, event)
                        if not visible_delta:
                            return
                        if visible_delta != delta:
                            payload = {**payload, "delta": visible_delta}
                            if "text" in payload:
                                payload["text"] = visible_delta
                            event = {**event, "payload": payload}
                        reply_parts.append(visible_delta)
                    elif event_type == "message.completed":
                        raw_completed_reply = str(payload.get("response") or payload.get("text") or payload.get("content") or "")
                        if raw_completed_reply:
                            notices, completed_reply = notice_filter.complete(raw_completed_reply)
                        else:
                            notices, visible_tail = notice_filter.finish()
                            if visible_tail:
                                reply_parts.append(visible_tail)
                                await run.publish(
                                    event_envelope(
                                        type="message.delta",
                                        payload={"delta": visible_tail},
                                        timestamp=str(event.get("timestamp") or ""),
                                        agent_id=str(event.get("agentId") or ""),
                                        installation_id=str(event.get("installationId") or ""),
                                        chat_id=str(event.get("chatId") or chat_id),
                                        run_id=str(event.get("runId") or run.run_id),
                                        session_id=str(event.get("sessionId") or ""),
                                        actor_id=str(event.get("actorId") or "primary"),
                                        parent_run_id=event.get("parentRunId"),
                                    )
                                )
                        for notice in notices:
                            await publish_notice(notice, event)
                        if raw_completed_reply and completed_reply != raw_completed_reply:
                            payload = {**payload}
                            for key in ("response", "text", "content"):
                                if key in payload:
                                    payload[key] = completed_reply
                            event = {**event, "payload": payload}
                    elif event_type in {"run.completed", "run.failed", "run.cancelled"}:
                        notices, visible_tail = notice_filter.finish()
                        for notice in notices:
                            await publish_notice(notice, event)
                        if visible_tail:
                            await publish_external(
                                event_envelope(
                                    type="message.delta",
                                    payload={"delta": visible_tail},
                                    timestamp=str(event.get("timestamp") or ""),
                                    agent_id=str(event.get("agentId") or ""),
                                    installation_id=str(event.get("installationId") or ""),
                                    chat_id=str(event.get("chatId") or chat_id),
                                    run_id=str(event.get("runId") or run.run_id),
                                    session_id=str(event.get("sessionId") or ""),
                                    actor_id=str(event.get("actorId") or "primary"),
                                    parent_run_id=event.get("parentRunId"),
                                )
                            )
                    elif event_type == "notification.created":
                        notice_message = str(payload.get("message") or payload.get("detail") or "").strip()
                        notice_category = str(payload.get("category") or "transport_warning")
                        notice_key = "\n".join((notice_category, notice_message))
                        if notice_message and notice_key not in external_notification_keys:
                            external_notification_keys.add(notice_key)
                            external_notifications.append(
                                {
                                    "eventId": str(event.get("eventId") or ""),
                                    "createdAt": str(event.get("timestamp") or _utc_now_iso()),
                                    "severity": str(payload.get("severity") or "warning"),
                                    "category": notice_category,
                                    "message": notice_message,
                                    "source": str(payload.get("source") or "agent_runtime"),
                                    "terminal": bool(payload.get("terminal")),
                                }
                            )
                    elif event_type == "reasoning.delta":
                        reasoning_delta = str(payload.get("delta") or payload.get("text") or "")
                        if reasoning_delta:
                            external_reasoning_parts.append(reasoning_delta)
                    elif event_type == "reasoning.completed":
                        reasoning_text = str(payload.get("response") or payload.get("text") or payload.get("content") or "")
                        if reasoning_text:
                            external_reasoning_parts[:] = [reasoning_text]
                    elif event_type in {"tool.started", "tool.updated", "tool.completed"}:
                        tool_call_id = str(payload.get("toolCallId") or payload.get("tool_call_id") or "")
                        tool_status = str(payload.get("status") or ("completed" if event_type == "tool.completed" else "running")).strip().lower()
                        tool_entry: dict[str, Any] = {
                            "kind": "tool",
                            "toolCallId": tool_call_id,
                            "tool": str(payload.get("name") or payload.get("tool") or payload.get("title") or "tool"),
                            "status": tool_status,
                            "failed": bool(payload.get("failed")) or tool_status in {"failed", "error", "failure", "expired", "cancelled"},
                        }
                        if payload.get("inputSummary") is not None:
                            tool_entry["input"] = payload.get("inputSummary")
                        if payload.get("outputSummary") is not None:
                            tool_entry["output"] = payload.get("outputSummary")
                        # Prefer invocation parameters in the compact trace;
                        # the output remains available as structured detail.
                        visible_summary = payload.get("inputSummary")
                        if visible_summary is None:
                            visible_summary = payload.get("outputSummary")
                        if isinstance(visible_summary, (str, int, float, bool)):
                            tool_entry["preview"] = str(visible_summary)
                        elif visible_summary is not None:
                            try:
                                tool_entry["preview"] = json.dumps(
                                    visible_summary,
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                )[:600]
                            except (TypeError, ValueError):
                                tool_entry["preview"] = str(visible_summary)[:600]
                        if isinstance(payload.get("presentation"), dict):
                            tool_entry["presentation"] = payload.get("presentation")
                        matching_tool_indices = [index for index, item in enumerate(external_trace) if tool_call_id and str(item.get("toolCallId") or "") == tool_call_id]
                        terminal_statuses = {"completed", "failed", "error", "failure", "expired", "cancelled"}
                        open_tool_indices = [index for index in matching_tool_indices if str(external_trace[index].get("status") or "").strip().lower() not in terminal_statuses]
                        existing_tool_index = (
                            open_tool_indices[-1] if open_tool_indices else (matching_tool_indices[-1] if matching_tool_indices and tool_status in terminal_statuses else -1)
                        )
                        if existing_tool_index >= 0:
                            # Keep the invocation parameters captured by the
                            # started/updated event. A completion summary is
                            # output, not a replacement for those parameters.
                            existing_preview = external_trace[existing_tool_index].get("preview")
                            if existing_preview not in (None, "") and event_type == "tool.completed":
                                tool_entry["preview"] = existing_preview
                            external_trace[existing_tool_index] = {
                                **external_trace[existing_tool_index],
                                **tool_entry,
                            }
                        else:
                            # Anchor the call at the amount of reasoning that
                            # had arrived when the invocation was first seen.
                            # The frontend uses this to interleave tools with
                            # the surrounding thought segments.
                            tool_entry["reasoningOffset"] = len("".join(external_reasoning_parts))
                            tool_entry["startedAt"] = str(event.get("timestamp") or _utc_now_iso())
                            external_trace.append(tool_entry)
                        if len(external_trace) > 40:
                            del external_trace[:-40]
                    elif event_type == "usage.updated":
                        for source, target in (
                            ("inputTokens", "prompt_tokens"),
                            ("outputTokens", "completion_tokens"),
                            ("totalTokens", "total_tokens"),
                            ("used", "total_tokens"),
                        ):
                            try:
                                value = int(payload.get(source) or 0)
                            except (TypeError, ValueError):
                                value = 0
                            if value > 0:
                                external_usage[target] = value
                        context_candidate = next((payload.get(key) for key in ("contextComposition", "context", "contextWindow") if isinstance(payload.get(key), dict)), {})
                        if isinstance(context_candidate, dict):
                            external_context_report.update(context_candidate)
                        if isinstance(payload.get("segments"), list):
                            external_context_report["segments"] = payload.get("segments")
                        for key in ("used", "size"):
                            if payload.get(key) is not None:
                                external_context_report[key] = payload.get(key)
                    elif event_type == "session.updated":
                        next_session_id = str(payload.get("sessionId") or payload.get("session_id") or "").strip()
                        if next_session_id:
                            external_session_id = next_session_id
                        commands = payload.get("commands")
                        if isinstance(commands, list):
                            external_commands = commands[:200]
                        if payload.get("mode") is not None:
                            external_agent_mode = payload.get("mode")
                        plan = payload.get("plan")
                        if isinstance(plan, dict):
                            external_plan = dict(plan)
                            external_plan.setdefault("status", "active")
                        config_option = payload.get("configOption")
                        if isinstance(config_option, dict) and str(config_option.get("id") or ""):
                            external_config_options[str(config_option.get("id") or "")] = config_option
                        for config_option in payload.get("configOptions") or []:
                            if isinstance(config_option, dict) and str(config_option.get("id") or ""):
                                external_config_options[str(config_option.get("id") or "")] = config_option
                    elif event_type in {"artifact.created", "artifact.updated"}:
                        attachment = payload.get("attachment")
                        if isinstance(attachment, dict):
                            public_attachment = {
                                key: attachment[key]
                                for key in (
                                    "id",
                                    "name",
                                    "content_type",
                                    "size",
                                    "kind",
                                    "url",
                                    "width",
                                    "height",
                                )
                                if key in attachment
                            }
                            artifact_id = str(payload.get("artifactId") or "")
                            if artifact_id:
                                public_attachment["artifactId"] = artifact_id
                            artifact_key = str(public_attachment.get("artifactId") or public_attachment.get("id") or public_attachment.get("url") or "")
                            if artifact_key:
                                artifact_index = next(
                                    (
                                        index
                                        for index, item in enumerate(external_artifacts)
                                        if str(item.get("artifactId") or item.get("id") or item.get("url") or "") == artifact_key
                                    ),
                                    -1,
                                )
                                if artifact_index >= 0:
                                    external_artifacts[artifact_index] = public_attachment
                                else:
                                    external_artifacts.append(public_attachment)
                    elif event_type:
                        from cyrene.agent_runtime.events import CORE_EVENT_TYPES

                        if event_type not in CORE_EVENT_TYPES:
                            external_trace.append(
                                {
                                    "kind": "event",
                                    "toolCallId": str(event.get("eventId") or event.get("event_id") or ""),
                                    "tool": f"Agent event · {event_type}",
                                    "status": "completed",
                                    "reasoningOffset": len("".join(external_reasoning_parts)),
                                    "startedAt": str(event.get("timestamp") or _utc_now_iso()),
                                    "output": payload,
                                    "presentation": {"kind": "event"},
                                }
                            )
                    await run.publish(event)

                result = await run_external_agent_turn(
                    chat=chat,
                    message=agent_message,
                    publish=publish_external,
                    attachments=normalized,
                    workspace_path=workspace_dir,
                    run_id=run.run_id,
                )
                external_session_id = str(result.get("sessionId") or external_session_id or "")
                if external_session_id:
                    await asyncio.to_thread(
                        service.set_chat_external_session_id,
                        chat_id,
                        external_session_id,
                    )
                if external_context_report:
                    await asyncio.to_thread(
                        service.update_chat_agent_context_report,
                        chat_id,
                        external_context_report,
                    )
                return completed_reply or "".join(reply_parts)

            from cyrene.workbench.project_memory_prompt import build_main_agent_suffix
            from cyrene.runtime.host_bridge import resolve_conversation_source

            conversation_source = "side_agent" if is_side_agent else await resolve_conversation_source(ui_instance_id)

            return await run_agent(
                user_message=agent_message,
                bot=bot,
                chat_id=R.chat_id,
                db_path=db_path,
                session_id=chat_id,
                permission_mode=mode,
                command=command,
                public_user_message=message or None,
                public_attachments=public_attachments or None,
                workspace_dir=workspace_dir,
                soul_enabled=_chat_soul_active(chat),
                workspace_enabled=_chat_workspace_active(chat),
                final_system_extra=build_main_agent_suffix(
                    chat.get("projectMemorySnapshot") if isinstance(chat.get("projectMemorySnapshot"), dict) else None,
                    include_trigger=not is_side_agent,
                ),
                response_capabilities=("interactive_blocks",),
                ui_instance_id=ui_instance_id,
                conversation_source=conversation_source,
            )

        def _finalize(reply_text: str) -> dict[str, Any]:
            """Persist mid-run messages plus the final assistant reply in order."""
            state_messages = _session_state_messages(chat_id)
            timeline_entries, usage, files = _extract_exchange_timeline(state_messages, state_ids_before)
            with _CHATS_STORE_JSON_LOCK:
                fresh_chat = _get_workbench_chat(chat_id)
                if not fresh_chat:
                    return {}
                fresh_base = copy.deepcopy(fresh_chat)
                _commit_retry_cut(fresh_chat)
                configured_model = str(fresh_chat.get("model") or "")
                model_name = _last_exchange_model(state_messages, state_ids_before) or configured_model
                for entry in timeline_entries:
                    entry.setdefault("model", model_name)
                assistant_entry: dict[str, Any] = {
                    "id": _short_id("msg"),
                    "role": "assistant",
                    "content": str(reply_text or ""),
                    "createdAt": _utc_now_iso(),
                    "model": model_name,
                    "processingDurationMs": max(0, int(round((time.monotonic() - processing_started_at) * 1000))),
                }
                effective_usage = dict(usage)
                if is_external_agent:
                    effective_usage.update(external_usage)
                if any(effective_usage.values()):
                    assistant_entry["usage"] = effective_usage
                reply_files: list[dict[str, Any]] = []
                known_reply_files: set[str] = set()
                for file in [*files, *external_artifacts]:
                    if not isinstance(file, dict):
                        continue
                    key = str(file.get("id") or file.get("url") or file.get("path") or "")
                    if not key or key in known_reply_files:
                        continue
                    known_reply_files.add(key)
                    reply_files.append(file)
                if reply_files:
                    assistant_entry["attachments"] = reply_files
                if external_commands is not None:
                    fresh_chat["agentCommands"] = external_commands
                if isinstance(external_plan, dict):
                    fresh_chat["activePlan"] = external_plan
                if external_agent_mode is not None:
                    fresh_chat["agentMode"] = external_agent_mode
                if external_config_options:
                    config_options = [
                        item for item in (fresh_chat.get("agentConfigOptions") or []) if isinstance(item, dict) and str(item.get("id") or "") not in external_config_options
                    ]
                    config_options.extend(external_config_options.values())
                    fresh_chat["agentConfigOptions"] = config_options[:100]
                fresh_chat["lastModel"] = model_name
                if external_trace or external_reasoning_parts:
                    timeline_entries.insert(
                        0,
                        {
                            "id": _short_id("activity"),
                            "role": "assistant",
                            "content": "",
                            "createdAt": assistant_entry["createdAt"],
                            "activityCard": True,
                            "reasoning": "".join(external_reasoning_parts),
                            "trace": external_trace[-40:],
                            "intermediate": True,
                            "model": model_name,
                        },
                    )
                if external_notifications:
                    timeline_entries[0:0] = [
                        {
                            "id": str(notice.get("eventId") or _short_id("notice")),
                            "role": "assistant",
                            "content": "",
                            "createdAt": str(notice.get("createdAt") or assistant_entry["createdAt"]),
                            "notificationCard": True,
                            "notification": {key: notice[key] for key in ("severity", "category", "message", "source", "terminal") if key in notice},
                            "intermediate": True,
                            "model": model_name,
                        }
                        for notice in external_notifications
                    ]
                saved_messages = [*timeline_entries, assistant_entry]
                _merge_chat_messages_chronologically(fresh_chat, saved_messages)
                completed_turn_count = _next_completed_turn_count(
                    {"completedTurnCount": completed_turn_count_before},
                    retry=retry,
                    command=command,
                    is_side_agent=is_side_agent,
                )
                fresh_chat["completedTurnCount"] = completed_turn_count
                fresh_chat["status"] = "idle"
                fresh_chat.pop("pendingQuestion", None)
                fresh_chat["updatedAt"] = assistant_entry["createdAt"]
                _write_chat_store(fresh_chat, base_chat=fresh_base)
            # Persist this exchange to the workspace's per-session conversation
            # file so the conversation survives outside the JSON store and the
            # agent can read its own history by id. Best-effort; never block reply.
            try:
                archive_session_exchange(
                    chat_id,
                    message,
                    str(reply_text or ""),
                    workspace_dir=workspace_dir,
                    session_title=str(fresh_chat.get("title") or ""),
                )
            except Exception:
                logger.exception("Failed to archive workbench conversation %s", chat_id)
            if not command and not retry and not is_side_agent:
                append_notification(
                    title="Agent 回复完成",
                    body=f"Agent 在「{fresh_chat.get('title') or '新对话'}」中回复了你。",
                    tab="mention",
                    project_ref=project_id,
                    source="workbench_chat_reply",
                    source_label="对话",
                    link_label=str(fresh_chat.get("title") or ""),
                    meta={"chatId": chat_id},
                )
            return {
                "assistantMessage": assistant_entry,
                "assistantMessages": saved_messages,
                "completedTurnCount": completed_turn_count,
            }

        async def _finalize_async(reply_text: str) -> dict[str, Any]:
            finalized = await asyncio.to_thread(_finalize, reply_text)
            if finalized and not is_side_agent:
                _schedule_post_reply_bookkeeping(
                    chat_id=chat_id,
                    project_id=project_id,
                    user_text=message,
                    reply_text=str(reply_text or ""),
                    prior_message_ids=state_ids_before,
                    command=command,
                    retry=retry,
                    turn_count=int(finalized.get("completedTurnCount") or 0),
                )
            return finalized

        def _restore_retry_state() -> None:
            if retry_state_backup is None:
                return
            state_path, previous = retry_state_backup
            try:
                if previous is None:
                    state_path.unlink(missing_ok=True)
                else:
                    state_path.parent.mkdir(parents=True, exist_ok=True)
                    state_path.write_bytes(previous)
            except Exception:
                logger.exception("Failed to restore retry state for %s", chat_id)

        def _commit_retry_cut(target_chat: dict[str, Any]) -> None:
            if not retry or not truncate_after_id:
                return
            # Delete only the stale tail captured when retry began. Guidance or
            # proactive entries added during the new run must survive.
            _remove_retry_replaced_messages(target_chat, truncate_after_id, retry_replaced_message_ids)

        def _settle_status() -> None:
            _settle_chat_running_status(chat_id)

        def _stash_chat_pending(pending: dict[str, Any] | None) -> list[dict[str, Any]]:
            """Persist a paused run's pending question on the chat record so the
            transcript shows an answer prompt (not the raw awaiting-user sentinel)."""
            fresh_chat = _get_workbench_chat(chat_id)
            if not fresh_chat:
                return []
            fresh_base = copy.deepcopy(fresh_chat)
            saved_messages: list[dict[str, Any]] = []
            fresh_chat["status"] = "idle"
            if pending:
                fresh_chat["pendingQuestion"] = pending
                state_messages = _session_state_messages(chat_id)
                timeline_entries, usage, files = _extract_exchange_timeline(
                    state_messages,
                    state_ids_before,
                    include_open_tool_preamble=True,
                )
                model_name = _last_exchange_model(state_messages, state_ids_before) or str(fresh_chat.get("model") or "")
                for entry in timeline_entries:
                    entry.setdefault("model", model_name)
                question_entry = _pending_question_message(
                    pending,
                    usage=usage,
                    files=files,
                    model=model_name,
                )
                saved_messages = [*timeline_entries, question_entry]
                fresh_chat["lastModel"] = model_name
                _merge_chat_messages_chronologically(fresh_chat, saved_messages)
            else:
                fresh_chat.pop("pendingQuestion", None)
            fresh_chat["updatedAt"] = _utc_now_iso()
            _write_chat_store(fresh_chat, base_chat=fresh_base)
            return [_public_message(item) for item in saved_messages]

        async def run_non_streaming(run: ChatRun) -> None:
            changes_before = await _capture_workspace_changes_baseline(workspace_dir, run.run_id)
            try:
                reply = await _run(run)
            except asyncio.CancelledError:
                await _finalize_workspace_changes(
                    chat_id=chat_id,
                    run_id=run.run_id,
                    workspace_dir=workspace_dir,
                    before=changes_before,
                    status="cancelled",
                    run=run,
                )
                await asyncio.to_thread(_restore_retry_state)
                raise
            except Exception as exc:
                logger.exception("Workbench chat run failed for %s", chat_id)
                await _finalize_workspace_changes(
                    chat_id=chat_id,
                    run_id=run.run_id,
                    workspace_dir=workspace_dir,
                    before=changes_before,
                    status="error",
                    run=run,
                )
                await asyncio.to_thread(_restore_retry_state)
                await asyncio.to_thread(_settle_status)
                from cyrene.observability import debug

                await debug.publish_event(
                    {
                        "type": "workbench_chat_changed",
                        "change": "settled",
                        "session_id": chat_id,
                        "chat_id": chat_id,
                        "project_id": project_id,
                    },
                    session_id=chat_id,
                )
                run.outcome = {"kind": "error", "exc": exc}
                return
            run.status = "finishing"
            if reply == R.awaiting_user_sentinel:
                await _finalize_workspace_changes(
                    chat_id=chat_id,
                    run_id=run.run_id,
                    workspace_dir=workspace_dir,
                    before=changes_before,
                    status="awaiting_user",
                    run=run,
                )
                if retry:

                    def commit_retry() -> None:
                        fresh_chat = _get_workbench_chat(chat_id)
                        if fresh_chat:
                            fresh_base = copy.deepcopy(fresh_chat)
                            _commit_retry_cut(fresh_chat)
                            _write_chat_store(fresh_chat, base_chat=fresh_base)

                    await asyncio.to_thread(commit_retry)
                pending = await asyncio.to_thread(R.pending_question_for, chat_id)
                awaiting_messages = await asyncio.to_thread(_stash_chat_pending, pending)
                await asyncio.to_thread(notify_voice_command_attention, pending)
                run.outcome = {"kind": "awaiting", "pending": pending}
                run.outcome["assistantMessages"] = awaiting_messages
                return
            finalized = await _finalize_async(reply)
            # Finalize the workspace change set after the timeline write so the
            # two chats-store writers never run concurrently (JSON store mode
            # has no merge lock; _finalize_async and the detached finalize both
            # hold _CHATS_STORE_JSON_LOCK for their read-modify-write).
            _schedule_workspace_changes_finalize(
                chat_id=chat_id,
                run_id=run.run_id,
                workspace_dir=workspace_dir,
                before=changes_before,
                status="completed",
            )
            from cyrene.runtime.host_actions import finalize_origin

            asyncio.create_task(
                finalize_origin(
                    chat_id,
                    "",
                    origin_run_id=client_request_id,
                )
            )
            run.outcome = {
                "kind": "reply",
                "payload": finalized,
            }

        if not wants_stream:
            run, is_new = _CHAT_RUN_MANAGER.start_or_get(
                chat_id,
                {"type": "ack", "chatId": chat_id},
                run_non_streaming,
                stream=False,
            )
            if not is_new:
                return JSONResponse(
                    {"error": "chat already has a running reply", "code": "chat_run_in_progress"},
                    status_code=409,
                )
            await run.done.wait()
            outcome = run.outcome or {}
            kind = str(outcome.get("kind") or "")
            if kind == "error":
                exc = outcome.get("exc")
                if not isinstance(exc, Exception):
                    exc = RuntimeError("agent run failed")
                message = _workbench_chat_run_error_message(exc, lang)
                error = message if isinstance(exc, httpx.TransportError) else "agent run failed"
                return JSONResponse(
                    {
                        "error": error,
                        "detail": str(exc),
                        **service.chat_error_metadata(exc),
                    },
                    status_code=502,
                )
            if kind == "awaiting":
                pending = outcome.get("pending")
                return {
                    "ok": True,
                    "awaitingUser": True,
                    "pendingQuestion": pending,
                    "assistantMessages": outcome.get("assistantMessages") or [],
                    "userMessage": _public_message(user_entry),
                    "retry": retry,
                    "retryReplacedMessageIds": sorted(retry_replaced_message_ids),
                }
            finalized = outcome.get("payload")
            if not isinstance(finalized, dict):
                finalized = {}
            return {
                "ok": True,
                "userMessage": _public_message(user_entry),
                "assistantMessage": finalized.get("assistantMessage") or {},
                "assistantMessages": finalized.get("assistantMessages") or [],
                "retry": retry,
            }

        ack: dict[str, Any] = {"type": "ack", "chatId": chat_id}
        if retry:
            ack["retry"] = True
            ack["truncateAfterMessageId"] = truncate_after_id
        else:
            ack["userMessage"] = _public_message(user_entry)

        async def run_streaming(run: ChatRun) -> None:
            logger.info("Workbench chat run_streaming entered [chat=%s run=%s]", chat_id, run.run_id)
            changes_before = await _capture_workspace_changes_baseline(workspace_dir, run.run_id)
            live_segments_stop = asyncio.Event()
            live_segments_task = asyncio.create_task(_publish_live_exchange_segments_loop(run, chat_id, state_ids_before, live_segments_stop))
            try:
                try:
                    reply = await _run(run)
                except asyncio.CancelledError:
                    await _finalize_workspace_changes(
                        chat_id=chat_id,
                        run_id=run.run_id,
                        workspace_dir=workspace_dir,
                        before=changes_before,
                        status="cancelled",
                        run=run,
                    )
                    await asyncio.to_thread(_restore_retry_state)
                    raise
                except Exception as exc:
                    logger.exception("Workbench chat streaming run failed for %s", chat_id)
                    await _finalize_workspace_changes(
                        chat_id=chat_id,
                        run_id=run.run_id,
                        workspace_dir=workspace_dir,
                        before=changes_before,
                        status="error",
                        run=run,
                    )
                    await asyncio.to_thread(_restore_retry_state)
                    await asyncio.to_thread(_settle_status)
                    run.outcome = {"kind": "error", "exc": exc}
                    await run.publish(
                        {
                            "type": "error",
                            "error": "model_call_failed",
                            "message": _workbench_chat_run_error_message(exc, lang),
                            **service.chat_error_metadata(exc),
                        }
                    )
                    return
                # The agent has returned and can no longer absorb new guidance.
                # Keep the run available for stream finalization/replay, but make
                # the guidance endpoint reject this narrow terminal window.
                run.status = "finishing"
                live_segments_stop.set()
                await live_segments_task
                if reply == R.awaiting_user_sentinel:
                    await _finalize_workspace_changes(
                        chat_id=chat_id,
                        run_id=run.run_id,
                        workspace_dir=workspace_dir,
                        before=changes_before,
                        status="awaiting_user",
                        run=run,
                    )
                    # Run paused for a permission / clarification answer — surface
                    # the question instead of streaming the sentinel as a reply.
                    if retry:

                        def commit_stream_retry() -> None:
                            fresh_chat = _get_workbench_chat(chat_id)
                            if fresh_chat:
                                fresh_base = copy.deepcopy(fresh_chat)
                                _commit_retry_cut(fresh_chat)
                                _write_chat_store(fresh_chat, base_chat=fresh_base)

                        await asyncio.to_thread(commit_stream_retry)
                    pending = await asyncio.to_thread(R.pending_question_for, chat_id)
                    awaiting_messages = await asyncio.to_thread(_stash_chat_pending, pending)
                    await asyncio.to_thread(notify_voice_command_attention, pending)
                    run.outcome = {"kind": "awaiting", "pending": pending}
                    await run.publish(
                        {
                            "type": "awaiting_user",
                            "pending_question": pending,
                            "assistantMessages": awaiting_messages,
                            "retry": retry,
                            "retryReplacedMessageIds": sorted(retry_replaced_message_ids),
                            "truncateAfterMessageId": truncate_after_id,
                        }
                    )
                    return
                if not run.saw_reply_events:
                    if not is_external_agent:
                        await run.publish({"type": "reply_start"})
                        for chunk in R.reply_stream_chunks(reply):
                            await run.publish({"type": "reply_delta", "delta": chunk})
                # A streamed model call can finish before the agent reopens the
                # tool channel, so its reply_done is not necessarily the text
                # that _finalize_async will persist. Publish one authoritative
                # terminal snapshot from the agent coroutine's return value.
                # The client replaces (rather than appends) on reply_done, which
                # also makes this harmless when the last model call already
                # streamed exactly the same text.
                if not is_external_agent:
                    await run.publish({"type": "reply_done", "response": reply})
                # The agent coroutine has returned and only durable finalization
                # remains. The UI can stop tool animations without pretending the
                # transcript and workspace change set are already saved.
                await run.publish(
                    {
                        "type": "run_finalizing",
                        "chatId": chat_id,
                        "runId": run.run_id,
                    }
                )
                finalized = await _finalize_async(reply)
                # See the non-streaming path: finalize after the timeline write
                # so the chats-store writers are never concurrent.
                _schedule_workspace_changes_finalize(
                    chat_id=chat_id,
                    run_id=run.run_id,
                    workspace_dir=workspace_dir,
                    before=changes_before,
                    status="completed",
                )
                saved_event = {
                    "type": "saved",
                    **finalized,
                    "retry": retry,
                    "retryReplacedMessageIds": sorted(retry_replaced_message_ids),
                    "truncateAfterMessageId": truncate_after_id,
                }
                run.outcome = {"kind": "reply", "payload": saved_event}
                await run.publish(saved_event)
                from cyrene.runtime.host_actions import finalize_origin

                asyncio.create_task(
                    finalize_origin(
                        chat_id,
                        "",
                        origin_run_id=client_request_id,
                    )
                )
            finally:
                if not live_segments_stop.is_set():
                    live_segments_stop.set()
                    try:
                        await live_segments_task
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.debug("Workbench chat live segment publisher failed for %s", chat_id, exc_info=True)
                await asyncio.to_thread(_settle_status)
                from cyrene.observability import debug

                await debug.publish_event(
                    {
                        "type": "workbench_chat_changed",
                        "change": "settled",
                        "session_id": chat_id,
                        "chat_id": chat_id,
                        "project_id": project_id,
                    },
                    session_id=chat_id,
                )

        run, is_new = _CHAT_RUN_MANAGER.start_or_get(
            chat_id,
            ack,
            run_streaming,
            stream=True,
        )
        if is_new:
            from cyrene.observability import debug

            await debug.publish_event(
                {
                    "type": "workbench_chat_changed",
                    "change": "running",
                    "session_id": chat_id,
                    "chat_id": chat_id,
                    "project_id": project_id,
                },
                session_id=chat_id,
            )
        if detached:
            if not is_new:
                return JSONResponse(
                    {
                        "error": "chat already has a running reply",
                        "code": "chat_run_in_progress",
                    },
                    status_code=409,
                )
            return JSONResponse(
                {
                    "run_id": run.run_id,
                    "chat_id": chat_id,
                    "status": run.status,
                    "created_at": run.created_at,
                    "event_cursor": 0,
                },
                status_code=202,
            )
        return StreamingResponse(
            _CHAT_RUN_MANAGER.stream(run),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache"},
        )

    @router.post("/api/workbench/chats/{chat_id}/messages")
    async def api_workbench_chat_send(chat_id: str, body_model: api_models.ChatMessageBody):
        return await _workbench_chat_send_impl(
            chat_id,
            api_models.body_dict(body_model),
        )

    @router.post("/api/workbench/chats/{chat_id}/agent-requests/{request_id}/respond")
    async def api_workbench_agent_request_respond(
        chat_id: str,
        request_id: str,
        body_model: api_models.AgentRequestResponseBody,
    ):
        """Forward a dynamic Agent-owned permission or elicitation response."""
        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        if _CHAT_RUN_MANAGER.get(chat_id) is None:
            return JSONResponse(
                {
                    "error": "the Agent request is no longer active",
                    "code": "request_expired",
                    "failureKind": "request_expired",
                },
                status_code=409,
            )
        from cyrene.agent_runtime import (
            AgentRuntimeError,
            respond_to_external_agent_request,
        )

        body = api_models.body_dict(body_model)
        try:
            return await respond_to_external_agent_request(
                chat_id,
                request_id,
                body.get("response") if isinstance(body.get("response"), dict) else {},
            )
        except AgentRuntimeError as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc), **exc.to_public_dict()},
                status_code=409 if exc.kind == "request_expired" else 400,
            )

    @router.post("/api/workbench/chats/{chat_id}/actions")
    async def api_workbench_chat_action(chat_id: str, body_model: api_models.ChatActionBody):
        """Handle a `:::button` click (block_actions protocol).

        ``mode: "model"`` buttons land here. The source message is updated in
        place (chat.update semantics: the clicked block flips to
        ``disabled: true`` so one click is consumed exactly once), then the
        event is routed through the normal send pipeline as a user turn so
        the agent can answer semantically and the reply is appended.
        """
        body = api_models.body_dict(body_model)
        action_id = str(body.get("actionId") or "").strip()
        value = str(body.get("value") or "")
        message_id = str(body.get("messageId") or "").strip()
        if not action_id or not message_id:
            return JSONResponse({"error": "actionId and messageId are required"}, status_code=400)
        # Mirrors the frontend spec validation: the event router is an attack
        # surface, so action ids stay whitelisted and bounded.
        if not re.fullmatch(r"[a-z0-9_]+", action_id) or len(action_id) > 32:
            return JSONResponse({"error": "invalid action_id"}, status_code=400)
        if len(value) > 256:
            return JSONResponse({"error": "value too long"}, status_code=400)
        if chat_id.startswith("legacy:"):
            return JSONResponse({"error": "legacy chats cannot run actions"}, status_code=403)

        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        messages = chat.get("messages") if isinstance(chat.get("messages"), list) else []
        target = next(
            (entry for entry in messages if str(entry.get("id") or "") == message_id),
            None,
        )
        if target is None:
            return JSONResponse({"error": "message not found"}, status_code=404)
        if str(target.get("role") or "") != "assistant":
            return JSONResponse({"error": "actions target assistant messages"}, status_code=400)

        content = str(target.get("content") or "")
        if not has_button_block(content, action_id):
            return JSONResponse({"error": "action not found in message"}, status_code=404)
        updated_content, label = disable_button_block(content, action_id)
        if updated_content is None:
            # The block is already disabled: a duplicate click. Reject so the
            # event stays idempotent regardless of client retries.
            return JSONResponse(
                {"error": "action already handled", "code": "action_duplicate"},
                status_code=409,
            )
        target["content"] = updated_content
        chat["updatedAt"] = _utc_now_iso()
        await asyncio.to_thread(_write_chats_store, payload)

        label_text = label or action_id
        if value:
            label_text = f"{label_text} ({action_id}: {value})"
        # Route through the full send pipeline: budget gate, permission mode,
        # model selection and run/finalize are all handled there.
        return await _workbench_chat_send_impl(
            chat_id,
            {
                "message": f"[按钮操作] {label_text}",
                "stream": False,
            },
        )

    @router.post("/api/workbench/chats/{chat_id}/answer")
    async def api_workbench_chat_answer(chat_id: str, body_model: api_models.AnswerBody):
        """Answer a paused chat run's permission / clarification question and
        resume the SAME round. Returns the continued reply (appended as an
        assistant message) or a follow-up question. Session-scoped to this chat."""
        body = api_models.body_dict(body_model)
        if bool(body.get("stream")):

            async def event_stream():
                from cyrene.agent.context import bind_run_context

                queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
                saw_reply_events = False
                subscriber_active = True

                async def publish(event: dict[str, Any]) -> None:
                    if subscriber_active:
                        await queue.put(dict(event))

                next_body = api_models.AnswerBody(**{**body, "stream": False})
                binding = bind_run_context(
                    reply_stream_writer=publish,
                    runtime_event_writer=publish,
                )
                try:
                    task = asyncio.create_task(api_workbench_chat_answer(chat_id, next_body))
                    _DETACHED_ANSWER_TASKS.add(task)
                    task.add_done_callback(_finish_detached_answer_task)
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
                        yield json.dumps(event, ensure_ascii=False) + "\n"

                    try:
                        response = await task
                    except asyncio.CancelledError:
                        yield (
                            json.dumps(
                                {
                                    "type": "interrupted",
                                    "chatId": chat_id,
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                        return
                    if isinstance(response, JSONResponse):
                        try:
                            error_payload = json.loads(bytes(response.body).decode("utf-8"))
                        except Exception:
                            error_payload = {}
                        yield (
                            json.dumps(
                                {
                                    "type": "error",
                                    "error": str(error_payload.get("error") or "answer_failed"),
                                    "message": str(error_payload.get("detail") or error_payload.get("error") or "Failed to resume the conversation."),
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                        return

                    if not isinstance(response, dict):
                        yield (
                            json.dumps(
                                {
                                    "type": "error",
                                    "error": "invalid_answer_response",
                                    "message": "Invalid answer response from the daemon.",
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                        return
                    if bool(response.get("interrupted")):
                        yield (
                            json.dumps(
                                {
                                    "type": "interrupted",
                                    "chatId": chat_id,
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                        return
                    if bool(response.get("awaitingUser")):
                        yield (
                            json.dumps(
                                {
                                    "type": "awaiting_user",
                                    "pending_question": response.get("pendingQuestion"),
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                        return
                    assistant = response.get("assistantMessage")
                    reply = str(assistant.get("content") or "") if isinstance(assistant, dict) else ""
                    if not saw_reply_events:
                        yield json.dumps({"type": "reply_start"}, ensure_ascii=False) + "\n"
                        if reply:
                            yield (
                                json.dumps(
                                    {
                                        "type": "reply_delta",
                                        "delta": reply,
                                    },
                                    ensure_ascii=False,
                                )
                                + "\n"
                            )
                    # Always publish one authoritative terminal snapshot. The
                    # renderer replaces/settles its accumulated text from this.
                    yield (
                        json.dumps(
                            {
                                "type": "reply_done",
                                "response": reply,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    yield (
                        json.dumps(
                            {
                                "type": "saved",
                                "assistantMessage": assistant or {},
                                "assistantMessages": response.get("assistantMessages") or [],
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                finally:
                    # The continuation owns persistence for the resumed round.
                    # Detaching a terminal subscriber must not cancel that work.
                    subscriber_active = False

            return StreamingResponse(
                event_stream(),
                media_type="application/x-ndjson",
                headers={"Cache-Control": "no-cache"},
            )

        question_id = str(body.get("question_id") or "").strip()
        answer_text = str(body.get("answer") or body.get("selected_option") or "").strip()
        ui_instance_id = str(body.get("uiInstanceId") or "").strip()
        processing_started_at = time.monotonic()
        from cyrene.agent.state import PERMISSION_MODES

        requested_mode = str(body.get("mode") or "").strip().lower()
        if not question_id or not answer_text:
            return JSONResponse({"error": "question_id and answer are required"}, status_code=400)
        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        is_side_agent = str(chat.get("kind") or "") == "side-agent"
        stored_mode = str(chat.get("permissionMode") or "").strip().lower()
        if requested_mode:
            mode = requested_mode if requested_mode in PERMISSION_MODES else "default"
        else:
            mode = stored_mode if stored_mode in PERMISSION_MODES else "default"
        chat["permissionMode"] = mode
        pending = chat.get("pendingQuestion") if isinstance(chat.get("pendingQuestion"), dict) else None
        if not pending or str(pending.get("id") or "") != question_id:
            return JSONResponse({"error": "no matching pending question"}, status_code=409)

        R = _routes()
        project_id = str(chat.get("projectId") or "")
        project_store = await asyncio.to_thread(R.read_store)
        project = R.find_project(project_store, project_id)
        if not project:
            return JSONResponse({"error": "project not found"}, status_code=404)
        try:
            workspace_dir = _resolve_chat_workspace_dir(chat, project, R.resolve_workspace_dir)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        now = _utc_now_iso()
        answer_entry: dict[str, Any] = {
            "id": _short_id("msg"),
            "role": "user",
            "content": answer_text,
            "createdAt": now,
            "answerToQuestionId": question_id,
        }
        _merge_chat_messages_chronologically(chat, [answer_entry])
        _mark_user_activity(chat, now)
        await asyncio.to_thread(_write_chats_store, payload)
        state_ids_before_resume: set[str] = set()
        for m in await asyncio.to_thread(_session_state_messages, chat_id):
            mid = str(m.get("message_id") or m.get("id") or "").strip()
            if mid:
                state_ids_before_resume.add(mid)
        resume_run_id = f"resume_{uuid.uuid4().hex}"
        changes_before = await _capture_workspace_changes_baseline(workspace_dir, resume_run_id)
        from cyrene.runtime.host_bridge import resolve_conversation_source

        conversation_source = await resolve_conversation_source(ui_instance_id)
        try:
            if mode == "default":
                reply = await R.answer_pending(
                    chat_id,
                    question_id,
                    answer_text,
                    workspace_dir,
                    ui_instance_id=ui_instance_id,
                    conversation_source=conversation_source,
                )
            else:
                reply = await R.answer_pending(
                    chat_id,
                    question_id,
                    answer_text,
                    workspace_dir,
                    permission_mode=mode,
                    ui_instance_id=ui_instance_id,
                    conversation_source=conversation_source,
                )
        except asyncio.CancelledError:
            await _finalize_workspace_changes(
                chat_id=chat_id,
                run_id=resume_run_id,
                workspace_dir=workspace_dir,
                before=changes_before,
                status="cancelled",
            )
            # The answer itself has already been accepted and persisted.  Do
            # not resurrect its consumed question after the resumed slice is
            # stopped; doing so leaves the UI offering an answer that the agent
            # state no longer recognizes.  Project the interruption just like a
            # ChatRunManager-owned run so list/topbar state also settles.
            await asyncio.to_thread(
                _stash_chat_pending_for,
                chat_id,
                None,
            )
            await asyncio.to_thread(
                _record_chat_run_outcome,
                chat_id,
                run_id=resume_run_id,
                status="cancelled",
                termination_reason="user_interrupted",
                outcome_kind="interrupted",
                created_at=now,
            )
            return {
                "ok": True,
                "interrupted": True,
                "awaitingUser": False,
                "runId": resume_run_id,
                "userMessage": _public_message(answer_entry),
            }
        except Exception as exc:
            await _finalize_workspace_changes(
                chat_id=chat_id,
                run_id=resume_run_id,
                workspace_dir=workspace_dir,
                before=changes_before,
                status="error",
            )
            logger.exception("Workbench chat answer-resume failed for %s", chat_id)
            return JSONResponse(
                {
                    "error": "answer resume failed",
                    "detail": str(exc),
                    **service.chat_error_metadata(exc),
                },
                status_code=502,
            )

        await _finalize_workspace_changes(
            chat_id=chat_id,
            run_id=resume_run_id,
            workspace_dir=workspace_dir,
            before=changes_before,
            status="awaiting_user" if reply == R.awaiting_user_sentinel else "completed",
        )

        if reply == R.awaiting_user_sentinel:
            new_pending = await asyncio.to_thread(R.pending_question_for, chat_id)

            resume_state_messages = await asyncio.to_thread(_session_state_messages, chat_id)

            def extract_pending() -> tuple[list[dict[str, Any]], dict[str, Any], list[Any]]:
                return _extract_exchange_timeline(
                    resume_state_messages,
                    state_ids_before_resume,
                    include_open_tool_preamble=True,
                )

            timeline_entries, usage, files = await asyncio.to_thread(extract_pending)
            pending_model = _last_exchange_model(resume_state_messages, state_ids_before_resume) or str(chat.get("model") or "")
            for entry in timeline_entries:
                entry.setdefault("model", pending_model)
            additions = [
                *timeline_entries,
                *(
                    [
                        _pending_question_message(
                            new_pending,
                            usage=usage,
                            files=files,
                            model=pending_model,
                        )
                    ]
                    if new_pending
                    else []
                ),
            ]
            await asyncio.to_thread(_stash_chat_pending_for, chat_id, new_pending, additions=additions)
            await asyncio.to_thread(
                _record_chat_run_outcome,
                chat_id,
                run_id=resume_run_id,
                status="done",
                termination_reason="awaiting_user",
                outcome_kind="awaiting",
                created_at=now,
            )
            return {
                "ok": True,
                "awaitingUser": True,
                "runId": resume_run_id,
                "pendingQuestion": new_pending,
                "userMessage": _public_message(answer_entry),
            }

        answer_state_messages = await asyncio.to_thread(_session_state_messages, chat_id)

        def extract_answer() -> tuple[list[dict[str, Any]], dict[str, Any], list[Any]]:
            return _extract_exchange_timeline(answer_state_messages, state_ids_before_resume)

        timeline_entries, usage, files = await asyncio.to_thread(extract_answer)
        fresh = await asyncio.to_thread(_read_chats_store)
        fresh_chat = _find_chat(fresh, chat_id)
        if not fresh_chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        model_name = _last_exchange_model(answer_state_messages, state_ids_before_resume) or str(fresh_chat.get("model") or "")
        for entry in timeline_entries:
            entry.setdefault("model", model_name)
        assistant_entry: dict[str, Any] = {
            "id": _short_id("msg"),
            "role": "assistant",
            "content": str(reply or ""),
            "createdAt": _utc_now_iso(),
            "model": model_name,
            "processingDurationMs": max(0, int(round((time.monotonic() - processing_started_at) * 1000))),
        }
        if any(usage.values()):
            assistant_entry["usage"] = usage
        if files:
            assistant_entry["attachments"] = files
        saved_messages = [*timeline_entries, assistant_entry]
        _merge_chat_messages_chronologically(fresh_chat, saved_messages)
        completed_turn_count = _next_completed_turn_count(
            fresh_chat,
            is_side_agent=is_side_agent,
        )
        fresh_chat["completedTurnCount"] = completed_turn_count
        fresh_chat["lastModel"] = model_name
        fresh_chat["status"] = "idle"
        fresh_chat.pop("pendingQuestion", None)
        fresh_chat["updatedAt"] = assistant_entry["createdAt"]
        await asyncio.to_thread(_write_chats_store, fresh)
        from cyrene.runtime.host_actions import finalize_origin

        asyncio.create_task(finalize_origin(chat_id, ""))
        await asyncio.to_thread(complete_chat_plan, chat_id)
        # Answer-resume runs do not pass through ChatRunManager, whose normal
        # finalizer projects the terminal outcome into ``lastRun``.  Record the
        # resumed reply explicitly so the lightweight conversation list cannot
        # fall back to the original paused run's stale ``awaiting`` outcome.
        await asyncio.to_thread(
            _record_chat_run_outcome,
            chat_id,
            run_id=resume_run_id,
            status="done",
            termination_reason="completed",
            outcome_kind="reply",
            created_at=now,
        )
        try:
            await asyncio.to_thread(
                archive_session_exchange,
                chat_id,
                answer_text,
                str(reply or ""),
                workspace_dir=workspace_dir,
                session_title=str(fresh_chat.get("title") or ""),
            )
        except Exception:
            logger.exception("Failed to archive workbench conversation %s", chat_id)
        if project_id and not is_side_agent:
            _schedule_structured_memory_capture(
                R,
                project_id=project_id,
                user_text=answer_text,
                agent_text=str(reply or ""),
                state_messages=answer_state_messages,
                prior_message_ids=state_ids_before_resume,
                session_id=chat_id,
            )

            from cyrene.workbench.project_memory_prompt import (
                completed_context_snapshot,
                context_auto_trigger_threshold,
                schedule_learning,
            )

            snapshot = await asyncio.to_thread(
                completed_context_snapshot,
                chat_id,
                project_id,
                completed_turn_count=completed_turn_count,
                final_assistant_text=str(reply or ""),
            )
            threshold = context_auto_trigger_threshold(project_id, chat_id, snapshot.get("messages") or []) if snapshot else None
            if snapshot and threshold is not None:
                snapshot["contextThresholdPercent"] = threshold
                schedule_learning(
                    project_id,
                    snapshot,
                    source="conversation_auto",
                    reason=f"context_{threshold}_percent",
                )
        return {
            "ok": True,
            "awaitingUser": False,
            "runId": resume_run_id,
            "userMessage": _public_message(answer_entry),
            "assistantMessage": _public_message(assistant_entry),
            "assistantMessages": [_public_message(item) for item in saved_messages],
        }

    return {
        "send_chat_detached": _workbench_chat_send_impl,
        "guide_chat": api_workbench_chat_guidance,
        "answer_chat": api_workbench_chat_answer,
        "run_manager": _CHAT_RUN_MANAGER,
    }
