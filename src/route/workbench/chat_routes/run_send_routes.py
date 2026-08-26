from __future__ import annotations

import asyncio
import copy
import logging
import time
from functools import partial
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from cyrene.workbench import chat_groups
from cyrene.workbench.chat_events import publish_chat_changed
from cyrene.workbench.chat_external_turn_service import (
    ExternalAgentTurnApplicationService,
    ExternalTurnDependencies,
    ExternalTurnProjection,
)
from cyrene.workbench.chat_reply_finalization_service import (
    ChatReplyFinalizationApplicationService,
    ChatReplyFinalizationDependencies,
    ChatReplyFinalizationRequest,
)
from cyrene.workbench.chat_run_lifecycle_service import (
    ChatRunLifecycleApplicationService,
    ChatRunLifecycleDependencies,
    ChatRunLifecycleRequest,
)
from cyrene.workbench.chat_runs import ChatRun
from cyrene.workbench.chat_send_preferences_service import (
    ChatSendPreferencesApplicationService,
    VoiceCommandAttention,
)
from cyrene.workbench.chat_session_naming_service import (
    ChatSessionNamingApplicationService,
    ChatSessionNamingDependencies,
)
from route import schemas as api_models
from route.workbench.chat_routes.context import ChatRouteContext
from route.workbench.chat_routes.shared import (
    schedule_reply_bookkeeping,
    schedule_workspace_changes_finalize,
    track_session_title_task,
)

logger = logging.getLogger(__name__)


class ChatSendController:
    def __init__(self, context: ChatRouteContext):
        self.context = context
        self.service = context.service
        service = self.service
        self.external_turn = ExternalAgentTurnApplicationService(
            ExternalTurnDependencies(
                run_turn=service.run_external_agent_turn,
                set_session_id=service.set_chat_external_session_id,
                update_context_report=service.update_chat_agent_context_report,
                utc_now_iso=service.utc_now_iso,
            )
        )
        self.lifecycle = ChatRunLifecycleApplicationService(
            ChatRunLifecycleDependencies(
                run_manager=service.run_manager,
                capture_workspace_baseline=service.capture_workspace_changes_baseline,
                finalize_workspace_changes=service.finalize_workspace_changes,
                schedule_workspace_finalize=self._schedule_workspace_finalize,
                publish_live_segments=service.publish_live_exchange_segments_loop,
                publish_chat_changed=publish_chat_changed,
                load_chat_summary=self._load_chat_summary,
                public_message=service.public_message,
                error_message=service.chat_run_error_message,
                error_metadata=service.chat_error_metadata,
            )
        )
        self.reply_finalization = ChatReplyFinalizationApplicationService(
            ChatReplyFinalizationDependencies(
                lock=service.repository.lock,
                get_chat=service.repository.get,
                write_chat=service.repository.write_one,
                state_messages=service.session_state_messages,
                extract_timeline=service.extract_exchange_timeline,
                last_model=service.last_exchange_model,
                short_id=service.short_id,
                utc_now_iso=service.utc_now_iso,
                merge_messages=service.merge_chat_messages_chronologically,
                next_turn_count=service.next_completed_turn_count,
                public_chat_light=service.public_chat_light,
            )
        )
        self.session_naming = ChatSessionNamingApplicationService(
            ChatSessionNamingDependencies(
                mutate_chat=service.repository.mutate_one,
                utc_now_iso=service.utc_now_iso,
            )
        )
        self.preferences = ChatSendPreferencesApplicationService()

    def _load_chat_summary(self, chat_id: str) -> dict[str, Any]:
        chat = self.service.repository.get(chat_id)
        return self.service.public_chat_light(chat) if chat else {}

    def _schedule_workspace_finalize(self, **kwargs: Any) -> None:
        schedule_workspace_changes_finalize(self.service, **kwargs)

    def schedule_reply_bookkeeping(self, **kwargs: Any) -> None:
        schedule_reply_bookkeeping(self.service, **kwargs)

    async def send(
        self,
        chat_id: str,
        body: dict[str, Any],
        *,
        detached: bool = False,
    ):
        return await _SendOperation(self, chat_id, body, detached=detached).execute()

    async def send_domain(
        self, chat_id: str, body: dict[str, Any], *, detached: bool = False
    ):
        """Return the application lifecycle result before HTTP serialization."""
        return await _SendOperation(
            self, chat_id, body, detached=detached, domain=True
        ).execute()


class _SendOperation:
    def __init__(
        self,
        controller: ChatSendController,
        chat_id: str,
        body: dict[str, Any],
        *,
        detached: bool,
        domain: bool = False,
    ):
        self.processing_started_at = time.monotonic()
        self.controller = controller
        self.context = controller.context
        self.service = controller.service
        self.chat_id = chat_id
        self.body = body
        self.detached = detached
        self.domain = domain
        self.retry_state_backup: tuple[Any, bytes | None] | None = None
        self.retry_replaced_message_ids: set[str] = set()
        self.truncate_after_id = ""

    async def execute(self):
        from cyrene.agent import run_agent
        from cyrene.agent.context import set_attachment_paths
        from cyrene.agent.state import PERMISSION_MODES

        self.run_agent = run_agent
        self.set_attachment_paths = set_attachment_paths
        error = await self._parse_request()
        if error is not None:
            return error
        error = await self._load_chat(PERMISSION_MODES)
        if error is not None:
            return error
        error = await self._load_project_and_model()
        if error is not None:
            return error
        error = await self._prepare_user_turn()
        if error is not None:
            return error
        error = await self._persist_user_turn()
        if error is not None:
            return error
        self._build_agent_message()
        await self._capture_state_ids()
        return await self._dispatch()

    async def _parse_request(self):
        body = self.body
        self.message = str(body.get("message") or "").strip()
        self.public_message = self.message
        self.client_request_id = str(body.get("clientRequestId") or "").strip()
        self.ui_instance_id = str(body.get("uiInstanceId") or "").strip()
        attachments = body.get("attachments") if isinstance(body.get("attachments"), list) else []
        if attachments:
            attachments = [await self.context.resolve_library_file_payload(item) if isinstance(item, dict) else item for item in attachments]
        self.command = str(body.get("command") or "").strip()
        self.requested_context_activations = (
            body.get("contextActivations")
            if "contextActivations" in body
            else None
        )
        self.wants_stream = bool(body.get("stream"))
        self.retry = bool(body.get("retry"))
        self.fork_replay = bool(body.get("forkReplay"))
        self.requested_mode = str(body.get("mode") or "").strip().lower()
        self.requested_model = str(body.get("model") or "").strip()
        self.requested_effort = str(body.get("reasoningEffort") or "").strip().lower()
        self.lang = str(body.get("lang") or "").strip().lower()
        self.voice_command = body.get("voiceCommand") is True
        self.controller.preferences.persist_language(self.lang)
        self.routes = self.context.runtime()
        self.normalized = self.routes.normalize_attachments(attachments)
        self.public_attachments = [self.routes.build_public_attachment_payload(item) for item in self.normalized]
        if not self.retry and not self.message and not self.normalized and not self.command:
            return JSONResponse({"error": "message is required"}, status_code=400)
        budget_error = await self.context.workbench_runtime.check_budget_gate(self.chat_id)
        if budget_error:
            return JSONResponse(budget_error, status_code=403)
        return None

    async def _load_chat(self, permission_modes):
        self.chat = await asyncio.to_thread(self.service.repository.get, self.chat_id)
        if not self.chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        self.base_chat = copy.deepcopy(self.chat)
        from cyrene.agent_runtime.builtin import normalize_agent_binding

        binding = normalize_agent_binding(self.chat.get("agent") if isinstance(self.chat.get("agent"), dict) else None)
        self.is_external_agent = not binding.is_builtin
        from cyrene.agent.commands import parse_slash_command, parse_slash_invocation

        self.dynamic_command = None
        self.dynamic_command_prompt = ""

        if self.is_external_agent:
            declared_commands = [
                str(item.get("id") or item.get("name") or item.get("command") or "")
                if isinstance(item, dict) else str(item or "")
                for item in (self.chat.get("agentCommands") or [])
            ]
            if not self.command and declared_commands:
                parsed = parse_slash_command(
                    self.message,
                    allowed_commands=declared_commands,
                )
                if parsed.get("matched"):
                    self.command = str(parsed.get("command") or "")
                    self.message = str(parsed.get("arguments") or "")
            if self.command and declared_commands and self.command not in declared_commands:
                return JSONResponse({"error": "Agent command is not available"}, status_code=400)
        else:
            from cyrene.workbench.slash_commands import (
                prepare_plugin_command_prompt,
                resolve_slash_command,
            )

            parsed = parse_slash_invocation(self.message) if not self.command else None
            candidate = self.command or str((parsed or {}).get("command") or "")
            descriptor = await resolve_slash_command(
                candidate,
                str(self.chat.get("projectId") or ""),
            ) if candidate else None
            if descriptor is not None:
                self.command = str(descriptor.get("id") or "")
                self.dynamic_command = (
                    descriptor if descriptor.get("source") != "builtin" else None
                )
                if parsed and parsed.get("matched"):
                    self.message = str(parsed.get("arguments") or "")
                if descriptor.get("source") == "plugin":
                    try:
                        self.dynamic_command_prompt = await prepare_plugin_command_prompt(
                            descriptor,
                            arguments=self.message,
                            chat_id=self.chat_id,
                            project_id=str(self.chat.get("projectId") or ""),
                        )
                    except Exception as exc:
                        return JSONResponse(
                            {"error": f"Plugin command could not be prepared: {exc}"},
                            status_code=400,
                        )
            elif self.command:
                return JSONResponse({"error": "unknown Cyrene command"}, status_code=400)
        if self.command and not self.public_message:
            self.public_message = "/" + self.command

        from cyrene.workbench.composer_context import resolve_context_activations

        requested_activations = (
            self.requested_context_activations
            if self.requested_context_activations is not None
            else self.chat.get("contextActivations")
        )
        if self.dynamic_command and isinstance(
            self.dynamic_command.get("activation"), dict
        ):
            from cyrene.workbench.composer_context import normalize_context_activations

            requested_activations = normalize_context_activations(requested_activations)
            activation = self.dynamic_command["activation"]
            activation_kind = str(activation.get("kind") or "")
            activation_id = str(activation.get("id") or "")
            if (
                activation_kind in requested_activations
                and activation_id
                and activation_id not in requested_activations[activation_kind]
            ):
                requested_activations[activation_kind].append(activation_id)
        self.context_activations = resolve_context_activations(
            requested_activations
        )
        if self.is_external_agent and any(self.context_activations.values()):
            return JSONResponse(
                {"error": "Composer context capabilities require the built-in Cyrene Agent"},
                status_code=400,
            )
        self.chat["contextActivations"] = self.context_activations
        requested_agent = self.body.get("agent") if isinstance(self.body.get("agent"), dict) else None
        installation_id = str((requested_agent or {}).get("installationId") or "").strip()
        if installation_id and installation_id != binding.installation_id:
            return JSONResponse(
                {
                    "error": "Agent binding cannot be changed from the message endpoint",
                    "code": "agent_binding_locked",
                },
                status_code=409,
            )
        self.is_side_agent = str(self.chat.get("kind") or "") == "side-agent"
        self.completed_turn_count_before = self.service.completed_turn_count(self.chat)
        parent = (
            await asyncio.to_thread(
                self.service.repository.get,
                str(self.chat.get("parentChatId") or ""),
            )
            if self.is_side_agent
            else None
        )
        self.parent_transcript = self.service.side_agent_parent_transcript(parent)
        stored_mode = str(self.chat.get("permissionMode") or "").strip().lower()
        selected_mode = self.requested_mode or stored_mode
        self.mode = selected_mode if selected_mode in permission_modes else "default"
        self.chat["permissionMode"] = self.mode
        if "soulActive" in self.body:
            self.chat["soulActive"] = bool(self.body.get("soulActive"))
        if "workspaceActive" in self.body:
            self.chat["workspaceActive"] = bool(self.body.get("workspaceActive"))
        self.project_id = str(self.chat.get("projectId") or "")
        attention = VoiceCommandAttention(
            enabled=self.voice_command,
            chat_id=self.chat_id,
            project_id=self.project_id,
            chat_title=str(self.chat.get("title") or ""),
        )
        self.notify_attention = partial(
            self.controller.preferences.notify_voice_attention,
            attention,
        )
        return None

    async def _load_project_and_model(self):
        project_store = await asyncio.to_thread(self.routes.read_store)
        self.project = self.routes.find_project(project_store, self.project_id)
        if not self.project:
            return JSONResponse({"error": "project not found"}, status_code=404)
        if "workspaceOverride" in self.body:
            try:
                workspace = self.service.normalize_workspace_override(self.body.get("workspaceOverride"))
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            if workspace:
                self.chat["workspaceOverride"] = workspace
            else:
                self.chat.pop("workspaceOverride", None)
        try:
            self.workspace_dir = self.service.resolve_chat_workspace_dir(
                self.chat,
                self.project,
                self.routes.resolve_workspace_dir,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return await self._select_model()

    async def _select_model(self):
        self.selected_candidate = None
        recovered_stale_selection = False
        self.agent_owns_models = self.is_external_agent and str((self.chat.get("modelAccess") or {}).get("mode") or "") == "agent_managed"
        selected_key = "" if self.agent_owns_models else self.requested_model or str(self.chat.get("modelSelectionId") or "").strip()
        if selected_key:
            from cyrene.runtime.model_configuration import selectable_model_candidates
            from cyrene.plugins.integrations import chat_model_candidates

            selectable_candidates = selectable_model_candidates()
            selectable_candidates.extend(await chat_model_candidates(self.project_id))

            self.selected_candidate = next(
                (
                    candidate
                    for candidate in selectable_candidates
                    if selected_key
                    in {
                        str(candidate.get("id") or "").strip(),
                        str(candidate.get("model") or "").strip(),
                        str(candidate.get("name") or "").strip(),
                    }
                ),
                None,
            )
            if self.selected_candidate is None:
                if self.requested_model:
                    return JSONResponse(
                        {"error": "configured model not found"},
                        status_code=400,
                    )
                from cyrene.runtime.model_configuration import candidates_for_route

                models = candidates_for_route("primary")
                self.selected_candidate = models[0] if models else None
                if self.selected_candidate is not None:
                    recovered_stale_selection = True
                    selected_key = str(self.selected_candidate.get("id") or self.selected_candidate.get("model") or self.selected_candidate.get("name") or "").strip()
        if self.selected_candidate is not None:
            self._persist_model_selection(selected_key, recovered_stale_selection)
        if self.service.run_manager.get(self.chat_id) is not None:
            return JSONResponse(
                {
                    "error": "chat already has a running reply",
                    "code": "chat_run_in_progress",
                },
                status_code=409,
            )
        return None

    def _persist_model_selection(self, selected_key: str, recovered: bool) -> None:
        from cyrene.model_runtime.client import set_session_model_preference

        candidate = self.selected_candidate
        selected_model = str(candidate.get("model") or candidate.get("name") or selected_key).strip()
        selected_model_id = str(candidate.get("id") or selected_key).strip()
        selected_effort = (
            self.requested_effort
            or str((candidate.get("reasoning_effort") if recovered else self.chat.get("reasoningEffort")) or candidate.get("reasoning_effort") or "").strip().lower()
        )
        set_session_model_preference(self.chat_id, candidate, selected_effort)
        self.chat["modelSelectionId"] = selected_model_id
        self.chat["model"] = selected_model
        self.chat["reasoningEffort"] = selected_effort
        if self.requested_model:
            self.chat.pop("lastModel", None)

    async def _prepare_user_turn(self):
        self.now = self.service.utc_now_iso()
        messages = self.chat.setdefault("messages", [])
        self.should_generate_title = False
        if self.retry:
            last_user_index = next(
                (index for index in range(len(messages) - 1, -1, -1) if messages[index].get("role") == "user"),
                -1,
            )
            if last_user_index < 0:
                return JSONResponse({"error": "nothing to retry"}, status_code=400)
            self.user_entry = messages[last_user_index]
            self.truncate_after_id = str(self.user_entry.get("id") or "")
            self.retry_replaced_message_ids = {str(item.get("id") or "") for item in messages[last_user_index + 1 :] if isinstance(item, dict) and str(item.get("id") or "")}
            self.message = str(self.user_entry.get("content") or "").strip()
            self.public_message = self.message
            self.command = str(self.user_entry.get("command") or "").strip()
            from cyrene.agent.commands import parse_slash_invocation

            parsed_retry_command = parse_slash_invocation(self.message)
            if parsed_retry_command.get("matched") and (
                not self.command
                or self.command == str(parsed_retry_command.get("command") or "")
            ):
                self.command = str(parsed_retry_command.get("command") or "")
                self.message = str(parsed_retry_command.get("arguments") or "")
            if not self.is_external_agent and self.command:
                from cyrene.workbench.slash_commands import (
                    prepare_plugin_command_prompt,
                    resolve_slash_command,
                )

                descriptor = await resolve_slash_command(
                    self.command,
                    str(self.chat.get("projectId") or ""),
                )
                if descriptor is None:
                    self.command = ""
                elif descriptor.get("source") != "builtin":
                    self.dynamic_command = descriptor
                    if descriptor.get("source") == "plugin":
                        try:
                            self.dynamic_command_prompt = await prepare_plugin_command_prompt(
                                descriptor,
                                arguments=self.message,
                                chat_id=self.chat_id,
                                project_id=str(self.chat.get("projectId") or ""),
                            )
                        except Exception as exc:
                            return JSONResponse(
                                {"error": f"Plugin command could not be prepared: {exc}"},
                                status_code=400,
                            )
            self.normalized = self.routes.normalize_attachments(self.user_entry.get("agentAttachments") or [])
            self.public_attachments = self.user_entry.get("attachments") if isinstance(self.user_entry.get("attachments"), list) else []
            if not self.fork_replay:
                state_path = self.context.workbench_runtime.session_state_file(self.chat_id)
                previous = await asyncio.to_thread(lambda: state_path.read_bytes() if state_path.exists() else None)
                self.retry_state_backup = (state_path, previous)
                await asyncio.to_thread(
                    self.service.truncate_state_for_retry,
                    self.chat_id,
                )
            return None
        self._append_user_message(messages)
        return None

    def _append_user_message(self, messages: list[dict[str, Any]]) -> None:
        self.user_entry = {
            "id": self.service.short_id("msg"),
            "role": "user",
            "content": self.public_message,
            "createdAt": self.now,
        }
        if self.command:
            self.user_entry["command"] = self.command
        if self.client_request_id:
            self.user_entry["clientRequestId"] = self.client_request_id
        if self.public_attachments:
            self.user_entry["attachments"] = self.public_attachments
            self.user_entry["agentAttachments"] = self.normalized
        is_first_message = not any(item.get("role") == "user" for item in messages)
        messages.append(self.user_entry)
        if is_first_message:
            locked_agent = dict(self.chat.get("agent") or {})
            locked_agent["bindingLocked"] = True
            self.chat["agent"] = locked_agent
        if is_first_message and self.chat.get("title") in ("", "新对话", None) and self.public_message:
            self.chat["title"] = self.public_message.replace("\n", " ")[:24]
        if is_first_message and bool(self.public_message) and not bool(self.chat.get("titleLocked")) and not self.chat.get("titleNamingStatus"):
            self.should_generate_title = True
            self.chat["titleNamingStatus"] = "pending"
            self.chat["titleNamingStartedAt"] = self.now

    async def _persist_user_turn(self):
        if not self.is_side_agent:
            try:
                await chat_groups.reconcile_session(self.chat_id)
            except Exception:
                logger.exception(
                    "Failed to reconcile chat-group context for %s",
                    self.chat_id,
                )
                await self._restore_retry_state_async()
                return JSONResponse(
                    {"error": "chat group context could not be prepared"},
                    status_code=503,
                )
        self.chat["status"] = "running"
        if self.selected_candidate is None and not self.agent_owns_models:
            self.chat["model"] = self.routes.get_model()
        self.service.mark_user_activity(self.chat, self.now)
        await asyncio.to_thread(
            self.service.repository.write_one,
            self.chat,
            base_chat=self.base_chat,
        )
        if self.normalized and not self.retry:
            await self.routes.register_attachments_kb(self.chat_id, self.normalized)
        if self.should_generate_title:
            task = self.controller.session_naming.generate_and_persist(
                chat_id=self.chat_id,
                project_id=self.project_id,
                message=self.public_message,
            )
            track_session_title_task(asyncio.create_task(task))
        return None

    async def _restore_retry_state_async(self) -> None:
        if self.retry_state_backup is None:
            return
        state_path, previous = self.retry_state_backup
        if previous is None:
            await asyncio.to_thread(state_path.unlink, missing_ok=True)
        else:
            await asyncio.to_thread(state_path.write_bytes, previous)

    def _build_agent_message(self) -> None:
        self.agent_message = self.message
        if self.is_external_agent and self.command:
            self.agent_message = "/" + self.command + ((" " + self.message) if self.message else "")
        if self.is_side_agent:
            source_quote = str(self.chat.get("sourceQuote") or "").strip()
            self.agent_message = (
                "你是主对话旁的独立 Side Agent。以下 main_conversation 是提问"
                "发生时主对话的完整公开内容；结合全部对话理解问题，并把"
                " selected_quote 作为用户当前关注的重点。不要假装上下文中未提供"
                "的事实。\n\n<main_conversation>\n"
                + (self.parent_transcript or "(empty)")
                + "\n</main_conversation>\n\n<selected_quote>\n"
                + (source_quote or "(none)")
                + "\n</selected_quote>\n\n用户问题：\n"
                + self.message
            )
        if self.normalized:
            self.agent_message = (self.agent_message or "[Attachment upload]") + self.routes.attachment_prompt_block(self.normalized)
            self.set_attachment_paths(self._attachment_path_map())

    def _attachment_path_map(self) -> dict[str, str]:
        from pathlib import Path

        paths: dict[str, str] = {}
        for item in self.normalized:
            full_path = str(item.get("path") or "").strip()
            if not full_path:
                continue
            uuid_name = Path(full_path).name
            paths[uuid_name] = full_path
            parts = uuid_name.split("_", 1)
            if len(parts) == 2:
                paths[parts[1]] = full_path
        return paths

    async def _capture_state_ids(self) -> None:
        self.state_ids_before: set[str] = set()
        messages = await asyncio.to_thread(
            self.service.session_state_messages,
            self.chat_id,
        )
        for message in messages:
            message_id = str(message.get("message_id") or message.get("id") or "").strip()
            if message_id:
                self.state_ids_before.add(message_id)

    async def _run_turn(self, run: ChatRun) -> str:
        logger.info(
            "Workbench chat _run entered [chat=%s run=%s]",
            self.chat_id,
            run.run_id,
        )
        if self.is_external_agent:
            return await self.controller.external_turn.run(
                run=run,
                chat_id=self.chat_id,
                chat=self.chat,
                message=self.agent_message,
                attachments=self.normalized,
                workspace_path=self.workspace_dir,
                projection=self.external,
            )
        from cyrene.runtime.host_bridge import resolve_conversation_source
        from cyrene.workbench.project_memory_prompt import build_main_agent_suffix

        from cyrene.workbench.composer_context import build_context_activation_prompt

        system_extras = [
            build_main_agent_suffix(
                self.chat.get("projectMemorySnapshot") if isinstance(self.chat.get("projectMemorySnapshot"), dict) else None,
                include_trigger=not self.is_side_agent,
            ),
            build_context_activation_prompt(self.context_activations),
            self.dynamic_command_prompt,
        ]

        source = "side_agent" if self.is_side_agent else await resolve_conversation_source(self.ui_instance_id)
        from agent.workbench.chat_runtime import (
            run_workbench_chat,
            workbench_chat_kernel_enabled,
        )

        if workbench_chat_kernel_enabled():
            return await run_workbench_chat(
                run=run,
                user_message=(
                    self.agent_message
                    or self.public_message
                    or (f"/{self.command}" if self.command else "")
                ),
                bot=self.context.bot,
                legacy_chat_id=self.routes.chat_id,
                db_path=self.context.db_path,
                session_id=self.chat_id,
                workspace_dir=self.workspace_dir,
                client_request_id=self.client_request_id,
                permission_mode=self.mode,
                command=self.command,
                public_user_message=self.public_message or None,
                public_attachments=self.public_attachments or None,
                attachment_paths=self._attachment_path_map(),
                soul_enabled=self.service.chat_soul_active(self.chat),
                workspace_enabled=self.service.chat_workspace_active(self.chat),
                system_extra="\n\n".join(part for part in system_extras if part),
                response_capabilities=("interactive_blocks",),
                ui_instance_id=self.ui_instance_id,
                conversation_source=source,
            )
        return await self.run_agent(
            user_message=self.agent_message,
            bot=self.context.bot,
            chat_id=self.routes.chat_id,
            db_path=self.context.db_path,
            session_id=self.chat_id,
            permission_mode=self.mode,
            command=self.command,
            public_user_message=self.public_message or None,
            public_attachments=self.public_attachments or None,
            workspace_dir=self.workspace_dir,
            soul_enabled=self.service.chat_soul_active(self.chat),
            workspace_enabled=self.service.chat_workspace_active(self.chat),
            final_system_extra="\n\n".join(part for part in system_extras if part),
            response_capabilities=("interactive_blocks",),
            ui_instance_id=self.ui_instance_id,
            conversation_source=source,
        )

    async def _finalize_reply(self, reply_text: str) -> dict[str, Any]:
        request = ChatReplyFinalizationRequest(
            chat_id=self.chat_id,
            project_id=self.project_id,
            workspace_dir=self.workspace_dir,
            message=self.public_message,
            command=self.command,
            retry=self.retry,
            is_side_agent=self.is_side_agent,
            is_external_agent=self.is_external_agent,
            completed_turn_count_before=self.completed_turn_count_before,
            processing_started_at=self.processing_started_at,
            state_ids_before=self.state_ids_before,
            projection=self.external,
            commit_retry_cut=self._commit_retry_cut,
        )
        finalized = await asyncio.to_thread(
            self.controller.reply_finalization.finalize,
            request,
            reply_text,
        )
        if finalized and not self.is_side_agent:
            self.controller.schedule_reply_bookkeeping(
                chat_id=self.chat_id,
                project_id=self.project_id,
                user_text=self.message,
                reply_text=str(reply_text or ""),
                prior_message_ids=self.state_ids_before,
                command=self.command,
                retry=self.retry,
                turn_count=int(finalized.get("completedTurnCount") or 0),
            )
        return finalized

    def _restore_retry_state(self) -> None:
        if self.retry_state_backup is None:
            return
        state_path, previous = self.retry_state_backup
        try:
            if previous is None:
                state_path.unlink(missing_ok=True)
            else:
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state_path.write_bytes(previous)
        except Exception:
            logger.exception("Failed to restore retry state for %s", self.chat_id)

    def _commit_retry_cut(self, target_chat: dict[str, Any]) -> None:
        if not self.retry or not self.truncate_after_id:
            return
        self.service.remove_retry_replaced_messages(
            target_chat,
            self.truncate_after_id,
            self.retry_replaced_message_ids,
        )

    def _stash_pending(self, pending: dict[str, Any] | None) -> list[dict[str, Any]]:
        chat = self.service.repository.get(self.chat_id)
        if not chat:
            return []
        base_chat = copy.deepcopy(chat)
        saved_messages: list[dict[str, Any]] = []
        chat["status"] = "idle"
        if pending:
            chat["pendingQuestion"] = pending
            state_messages = self.service.session_state_messages(self.chat_id)
            timeline, usage, files = self.service.extract_exchange_timeline(
                state_messages,
                self.state_ids_before,
                include_open_tool_preamble=True,
            )
            model = self.service.last_exchange_model(
                state_messages,
                self.state_ids_before,
            ) or str(chat.get("model") or "")
            for entry in timeline:
                entry.setdefault("model", model)
            question = self.service.pending_question_message(
                pending,
                usage=usage,
                files=files,
                model=model,
            )
            saved_messages = [*timeline, question]
            chat["lastModel"] = model
            self.service.merge_chat_messages_chronologically(chat, saved_messages)
        else:
            chat.pop("pendingQuestion", None)
        chat["updatedAt"] = self.service.utc_now_iso()
        self.service.repository.write_one(chat, base_chat=base_chat)
        return [self.service.public_message(item) for item in saved_messages]

    def _commit_retry(self) -> None:
        chat = self.service.repository.get(self.chat_id)
        if not chat:
            return
        base_chat = copy.deepcopy(chat)
        self._commit_retry_cut(chat)
        self.service.repository.write_one(chat, base_chat=base_chat)

    async def _dispatch(self):
        self.external = ExternalTurnProjection()
        lifecycle = await self.controller.lifecycle.dispatch(
            ChatRunLifecycleRequest(
                chat_id=self.chat_id,
                project_id=self.project_id,
                workspace_dir=self.workspace_dir,
                lang=self.lang,
                client_request_id=self.client_request_id,
                retry=self.retry,
                detached=self.detached,
                wants_stream=self.wants_stream,
                is_external_agent=self.is_external_agent,
                user_entry=self.user_entry,
                retry_replaced_message_ids=self.retry_replaced_message_ids,
                truncate_after_id=self.truncate_after_id,
                state_ids_before=self.state_ids_before,
                awaiting_user_sentinel=self.routes.awaiting_user_sentinel,
                run_turn=self._run_turn,
                finalize_reply=self._finalize_reply,
                restore_retry_state=self._restore_retry_state,
                settle_status=partial(
                    self.service.settle_chat_running_status,
                    self.chat_id,
                ),
                commit_retry=self._commit_retry,
                stash_pending=self._stash_pending,
                notify_attention=self.notify_attention,
                pending_question_for=self.routes.pending_question_for,
                reply_stream_chunks=self.routes.reply_stream_chunks,
                running_summary=self.service.public_chat_light(self.chat),
            )
        )
        if self.domain:
            return lifecycle
        if lifecycle.stream is not None:
            return StreamingResponse(
                lifecycle.stream,
                media_type="application/x-ndjson",
                headers={"Cache-Control": "no-cache"},
            )
        if lifecycle.status_code != 200:
            return JSONResponse(
                lifecycle.payload or {},
                status_code=lifecycle.status_code,
            )
        return lifecycle.payload or {}


def register_run_send_routes(
    router: APIRouter,
    context: ChatRouteContext,
) -> dict[str, Any]:
    controller = ChatSendController(context)

    @router.post("/api/workbench/chats/{chat_id}/messages")
    async def api_workbench_chat_send(
        chat_id: str,
        body_model: api_models.ChatMessageBody,
    ):
        return await controller.send(chat_id, api_models.body_dict(body_model))

    return {"send_chat_detached": controller.send}
