from __future__ import annotations

import asyncio
import copy
import logging
import time
from functools import partial
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from cyrene.localization import localized
from cyrene.workbench.chat import chat_groups
from cyrene.workbench.chat.chat_events import publish_chat_changed
from cyrene.workbench.chat.chat_external_turn_service import (
    ExternalAgentTurnApplicationService,
    ExternalTurnDependencies,
    ExternalTurnProjection,
)
from cyrene.workbench.chat.chat_reply_finalization_service import (
    ChatReplyFinalizationApplicationService,
    ChatReplyFinalizationDependencies,
    ChatReplyFinalizationRequest,
)
from cyrene.workbench.chat.chat_run_lifecycle_service import (
    ChatRunDispatchResult,
    ChatRunLifecycleApplicationService,
    ChatRunLifecycleDependencies,
    ChatRunLifecycleRequest,
)
from cyrene.workbench.chat.chat_runs import ChatRun
from cyrene.workbench.chat.chat_send_preferences_service import (
    ChatSendPreferencesApplicationService,
    VoiceCommandAttention,
)
from cyrene.workbench.chat.chat_session_naming_service import (
    ChatSessionNamingApplicationService,
    ChatSessionNamingDependencies,
)
from cyrene.workbench.http import schemas as api_models
from cyrene.workbench.http.errors import localized_error_payload, localized_error_response
from cyrene.workbench.http.workbench.chat_routes.context import ChatRouteContext
from cyrene.workbench.http.workbench.chat_routes.shared import (
    schedule_workspace_changes_finalize,
    track_session_title_task,
)

logger = logging.getLogger(__name__)


def _composer_context_service():
    from cyrene.core.plugin import application_plugin_service

    service = application_plugin_service("composer_context")
    if service is None:
        raise RuntimeError(
            "Required Plugin application service is unavailable: composer_context"
        )
    return service


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
                error_message=self._public_run_error_message,
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

    @staticmethod
    def _public_run_error_message(_exc: Exception, language: str = "") -> str:
        return localized(
            "The Agent run failed. Please try again.",
            "Agent 运行失败，请重试。",
            language=language,
        )

    def _schedule_workspace_finalize(self, **kwargs: Any) -> None:
        schedule_workspace_changes_finalize(self.service, **kwargs)

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
        from cyrene.core.permission import PERMISSION_MODES

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
        if self.is_external_agent:
            await self._capture_state_ids()
        else:
            self.state_ids_before = set()
        return await self._dispatch()

    async def _parse_request(self):
        body = self.body
        self.message = str(body.get("message") or "").strip()
        self.public_message = self.message
        self.client_request_id = str(body.get("clientRequestId") or "").strip()
        self.ui_instance_id = str(body.get("uiInstanceId") or "").strip()
        self.conversation_source = str(
            body.get("conversationSource") or ""
        ).strip()
        self.agent_originated = body.get("agentOriginated") is True
        self.origin_session_id = str(
            body.get("sourceSessionId") or ""
        ).strip()
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
            return localized_error_response(
                "A message or attachment is required.",
                "请输入消息或添加附件。",
                400,
                "message_required",
                language=self.lang,
            )
        budget_error = await self.context.check_budget_gate(
            self.chat_id,
            language=self.lang,
        )
        if budget_error:
            return JSONResponse(budget_error, status_code=403)
        return None

    async def _load_chat(self, permission_modes):
        self.chat = await asyncio.to_thread(self.service.repository.get, self.chat_id)
        if not self.chat:
            return localized_error_response(
                "Chat not found.",
                "未找到对话。",
                404,
                "chat_not_found",
                language=self.lang,
            )
        self.base_chat = copy.deepcopy(self.chat)
        from cyrene.agent_runtime.builtin import normalize_agent_binding

        binding = normalize_agent_binding(self.chat.get("agent") if isinstance(self.chat.get("agent"), dict) else None)
        self.is_external_agent = not binding.is_builtin
        from cyrene.workbench.application.commands import parse_slash_command, parse_slash_invocation

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
                return localized_error_response(
                    "This Agent command is not available.",
                    "此 Agent 命令不可用。",
                    400,
                    "agent_command_unavailable",
                    language=self.lang,
                )
        else:
            from cyrene.workbench.chat.slash_commands import resolve_slash_command

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
            elif self.command:
                return localized_error_response(
                    "Unknown Cyrene command.",
                    "未知的 Cyrene 命令。",
                    400,
                    "unknown_command",
                    language=self.lang,
                )
        if self.command and not self.public_message:
            self.public_message = "/" + self.command

        composer_context = _composer_context_service()
        requested_activations = (
            self.requested_context_activations
            if self.requested_context_activations is not None
            else self.chat.get("contextActivations")
        )
        if self.dynamic_command and isinstance(
            self.dynamic_command.get("activation"), dict
        ):
            requested_activations = composer_context.normalize(requested_activations)
            activation = self.dynamic_command["activation"]
            activation_kind = str(activation.get("kind") or "")
            activation_id = str(activation.get("id") or "")
            if (
                activation_kind in requested_activations
                and activation_id
                and activation_id not in requested_activations[activation_kind]
            ):
                requested_activations[activation_kind].append(activation_id)
        self.context_activations = composer_context.normalize(requested_activations)
        self.resolved_context_activations = {}
        if self.is_external_agent and any(self.context_activations.values()):
            return localized_error_response(
                "Composer context capabilities require the built-in Cyrene Agent.",
                "编辑器上下文能力需要使用 Cyrene 内置 Agent。",
                400,
                "builtin_agent_required",
                language=self.lang,
            )
        requested_agent = self.body.get("agent") if isinstance(self.body.get("agent"), dict) else None
        installation_id = str((requested_agent or {}).get("installationId") or "").strip()
        if installation_id and installation_id != binding.installation_id:
            return localized_error_response(
                "The Agent binding cannot be changed while sending a message.",
                "发送消息时不能更改 Agent 绑定。",
                409,
                "agent_binding_locked",
                language=self.lang,
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
        if "remoteDeviceIds" in self.body:
            self.chat["remoteDeviceIds"] = list(
                self.body.get("remoteDeviceIds") or ()
            )
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
            return localized_error_response(
                "Project not found.",
                "未找到项目。",
                404,
                "project_not_found",
                language=self.lang,
            )
        if "workspaceOverride" in self.body:
            try:
                workspace = self.service.normalize_workspace_override(self.body.get("workspaceOverride"))
            except ValueError:
                logger.warning(
                    "Invalid workspace override for chat %s",
                    self.chat_id,
                    exc_info=True,
                )
                return localized_error_response(
                    "The workspace override is invalid.",
                    "工作区覆盖路径无效。",
                    400,
                    "invalid_workspace_override",
                    language=self.lang,
                )
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
                language=self.lang,
            )
        try:
            resolved_input = self.service.resolve_composer_input_context(
                {
                    **self.chat,
                    "contextActivations": self.context_activations,
                },
                self.workspace_dir,
                strict=True,
            )
        except (ValueError, RuntimeError) as exc:
            logger.warning(
                "Composer input context is unavailable for chat %s: %s",
                self.chat_id,
                exc,
            )
            invalid = isinstance(exc, ValueError)
            return localized_error_response(
                (
                    "The context configuration is invalid."
                    if invalid
                    else "The selected input context is unavailable."
                ),
                "上下文配置无效。" if invalid else "所选输入框上下文当前不可用。",
                400 if invalid else 503,
                (
                    "invalid_context_configuration"
                    if invalid
                    else "composer_context_unavailable"
                ),
                language=self.lang,
            )
        self.context_activations = dict(
            resolved_input["contextActivations"]
        )
        self.resolved_context_activations = dict(
            resolved_input["resolvedContextActivations"]
        )
        self.chat["contextActivations"] = self.context_activations
        self.chat["soulActive"] = bool(resolved_input["soulActive"])
        self.chat["workspaceActive"] = bool(
            resolved_input["workspaceActive"]
        )
        self.chat["remoteDeviceIds"] = list(
            resolved_input["remoteDeviceIds"]
        )
        return await self._select_model()

    async def _select_model(self):
        self.selected_candidate = None
        recovered_stale_selection = False
        self.agent_owns_models = self.is_external_agent and str((self.chat.get("modelAccess") or {}).get("mode") or "") == "agent_managed"
        selected_key = "" if self.agent_owns_models else self.requested_model or str(self.chat.get("modelSelectionId") or "").strip()
        if selected_key:
            from cyrene.core.plugin import application_plugin_service

            model_service = application_plugin_service("model_configuration")
            selectable_candidates = model_service.selectable_model_candidates() if model_service is not None else []

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
                    return localized_error_response(
                        "The configured model was not found.",
                        "未找到已配置的模型。",
                        400,
                        "model_not_found",
                        language=self.lang,
                    )
                models = model_service.candidates_for_route("primary") if model_service is not None else []
                self.selected_candidate = models[0] if models else None
                if self.selected_candidate is not None:
                    recovered_stale_selection = True
                    selected_key = str(self.selected_candidate.get("id") or self.selected_candidate.get("model") or self.selected_candidate.get("name") or "").strip()
        if self.selected_candidate is not None:
            self._persist_model_selection(selected_key, recovered_stale_selection)
        if self.service.run_manager.get(self.chat_id) is not None:
            return localized_error_response(
                "This chat already has a reply in progress.",
                "此对话已有回复正在生成。",
                409,
                "chat_run_in_progress",
                language=self.lang,
            )
        return None

    def _persist_model_selection(self, selected_key: str, recovered: bool) -> None:
        from cyrene.plugins.model_catalog import set_session_model_preference

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
                return localized_error_response(
                    "There is no message to retry.",
                    "没有可重试的消息。",
                    400,
                    "nothing_to_retry",
                    language=self.lang,
                )
            self.user_entry = messages[last_user_index]
            self.truncate_after_id = str(self.user_entry.get("id") or "")
            self.retry_replaced_message_ids = {str(item.get("id") or "") for item in messages[last_user_index + 1 :] if isinstance(item, dict) and str(item.get("id") or "")}
            self.message = str(self.user_entry.get("content") or "").strip()
            self.public_message = self.message
            self.command = str(self.user_entry.get("command") or "").strip()
            from cyrene.workbench.application.commands import parse_slash_invocation

            parsed_retry_command = parse_slash_invocation(self.message)
            if parsed_retry_command.get("matched") and (
                not self.command
                or self.command == str(parsed_retry_command.get("command") or "")
            ):
                self.command = str(parsed_retry_command.get("command") or "")
                self.message = str(parsed_retry_command.get("arguments") or "")
            if not self.is_external_agent and self.command:
                from cyrene.workbench.chat.slash_commands import resolve_slash_command

                descriptor = await resolve_slash_command(
                    self.command,
                    str(self.chat.get("projectId") or ""),
                )
                if descriptor is None:
                    self.command = ""
                elif descriptor.get("source") != "builtin":
                    self.dynamic_command = descriptor
            self.normalized = self.routes.normalize_attachments(self.user_entry.get("agentAttachments") or [])
            self.public_attachments = self.user_entry.get("attachments") if isinstance(self.user_entry.get("attachments"), list) else []
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
        if self.agent_originated:
            self.user_entry["agentOriginated"] = True
        if self.origin_session_id:
            self.user_entry["originSessionId"] = self.origin_session_id
        if self.public_attachments:
            self.user_entry["attachments"] = self.public_attachments
            self.user_entry["agentAttachments"] = self.normalized
        is_first_message = not any(item.get("role") == "user" for item in messages)
        messages.append(self.user_entry)
        if is_first_message:
            locked_agent = dict(self.chat.get("agent") or {})
            locked_agent["bindingLocked"] = True
            self.chat["agent"] = locked_agent
        if is_first_message and self.chat.get("title") in ("", "New chat", "新对话", None) and self.public_message:
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
                return localized_error_response(
                    "The chat group context could not be prepared.",
                    "无法准备对话群组上下文。",
                    503,
                    "chat_group_context_unavailable",
                    language=self.lang,
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
                project_id=str(getattr(self, "project_id", "") or ""),
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
            self.agent_message = localized(
                "You are an independent Side Agent attached to the main conversation. "
                "main_conversation contains the complete public conversation at the "
                "time of the question. Use the whole conversation to understand the "
                "request, with selected_quote as the user's current focus. Do not "
                "invent facts absent from the supplied context.\n\n"
                "<main_conversation>\n{conversation}\n</main_conversation>\n\n"
                "<selected_quote>\n{quote}\n</selected_quote>\n\n"
                "User question:\n{question}",
                "你是主对话旁的独立 Side Agent。以下 main_conversation 是提问发生时"
                "主对话的完整公开内容；请结合全部对话理解问题，并把 selected_quote "
                "作为用户当前关注的重点。不要假装上下文中未提供的事实。\n\n"
                "<main_conversation>\n{conversation}\n</main_conversation>\n\n"
                "<selected_quote>\n{quote}\n</selected_quote>\n\n"
                "用户问题：\n{question}",
                language=self.lang,
                conversation=self.parent_transcript
                or localized("(empty)", "（空）", language=self.lang),
                quote=source_quote
                or localized("(none)", "（无）", language=self.lang),
                question=self.message,
            )
        if self.normalized:
            self.agent_message = (
                self.agent_message
                or localized(
                    "[Attachment upload]", "[附件上传]", language=self.lang
                )
            ) + self.routes.attachment_prompt_block(
                self.normalized,
                language=self.lang,
            )

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
            attachment_id = str(item.get("id") or "").strip()
            if attachment_id:
                paths[attachment_id] = full_path
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

    async def _run_turn(self, run: ChatRun) -> Any:
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

        from cyrene.workbench.application.commands import command_system_prompt

        memory_snapshot = self.service.ensure_chat_memory_snapshot(self.chat)
        turn_system_extras = [
            command_system_prompt(self.command),
            self.dynamic_command_prompt,
        ]

        source = (
            "side_agent"
            if self.is_side_agent
            else self.conversation_source
            or await resolve_conversation_source(self.ui_instance_id)
        )
        from cyrene.workbench.core_adapter.conversation_runtime import ConversationConfig

        config = ConversationConfig(
            session_id=self.chat_id,
            workspace_dir=self.workspace_dir,
            db_path=self.context.db_path,
            bot=self.context.bot,
            host_chat_id=self.routes.chat_id,
            client_request_id=self.client_request_id,
            permission_mode=self.mode,
            command=self.command,
            public_user_message=self.public_message or None,
            attachment_paths=self._attachment_path_map(),
            remote_device_ids=tuple(
                str(item or "").strip()
                for item in (self.chat.get("remoteDeviceIds") or ())
                if str(item or "").strip()
            ),
            soul_enabled=self.service.chat_soul_active(self.chat),
            workspace_enabled=self.service.chat_workspace_active(self.chat),
            context_activations=self.context_activations,
            resolved_context_activations=self.resolved_context_activations,
            system_extra="\n\n".join(
                part for part in turn_system_extras if part
            ),
            project_id=self.project_id,
            project_memory_snapshot=memory_snapshot,
            session_title=str(self.chat.get("title") or ""),
            memory_write_enabled=not self.is_side_agent,
            memory_trigger_enabled=not self.is_side_agent,
            memory_archive_enabled=True,
            retry=self.retry,
            completed_turn_count=(
                int(getattr(self, "completed_turn_count_before", 0) or 0) + 1
            ),
            response_capabilities=("interactive_blocks",),
            ui_instance_id=self.ui_instance_id,
            conversation_source=source,
        )
        result = await self.service.run_manager.conversation_runtime.send(
            config,
            (
                self.agent_message
                or self.public_message
                or (f"/{self.command}" if self.command else "")
            ),
            run_id=run.run_id,
            metadata={
                "client_request_id": self.client_request_id,
                "public_user_message": self.public_message,
                "public_attachments": [dict(item) for item in self.public_attachments],
                "command": self.command,
                "retry": self.retry,
                "fork_replay": self.fork_replay,
                "ephemeral_context": "\n\n".join(
                    part for part in turn_system_extras if part
                ),
            },
            publish=run.publish,
        )
        self.external.usage = dict(result.usage)
        self.external.latest_request_usage = dict(result.latest_request_usage)
        self.external.model = str(result.model or "")
        self.external.model_identity = dict(result.model_identity)
        self.external.generation_duration_ms = result.generation_duration_ms
        self.external.output_tokens_per_second = result.output_tokens_per_second
        self.external.activity_messages = [
            dict(message) for message in result.activity_messages
        ]
        plan = result.active_plan
        for event in run.events:
            if str(event.get("type") or "") not in {"plan", "plan_progress"}:
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
            candidate = payload.get("plan") if isinstance(payload, dict) else None
            if isinstance(candidate, dict):
                plan = candidate
        if isinstance(plan, dict):
            self.external.plan = copy.deepcopy(plan)
        self.agent_result = result
        return result

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

    def _runtime_message_fields(self, result: Any) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        usage = dict(getattr(result, "usage", {}) or {})
        if any(usage.values()):
            fields["usage"] = usage
        latest_usage = dict(getattr(result, "latest_request_usage", {}) or {})
        if any(latest_usage.values()):
            fields["latestRequestUsage"] = latest_usage
        identity = dict(getattr(result, "model_identity", {}) or {})
        if identity:
            fields["modelIdentity"] = identity
        duration = getattr(result, "generation_duration_ms", None)
        if isinstance(duration, (int, float)) and duration > 0:
            fields["modelGenerationDurationMs"] = round(float(duration), 3)
        rate = getattr(result, "output_tokens_per_second", None)
        if isinstance(rate, (int, float)) and rate > 0:
            fields["outputTokensPerSecond"] = round(float(rate), 3)
        return fields

    @staticmethod
    def _checkpointed_run_messages(
        chat: dict[str, Any], run_id: str
    ) -> list[dict[str, Any]]:
        """Return user-visible intermediate messages already saved for this run."""

        if not run_id:
            return []
        return [
            copy.deepcopy(dict(item))
            for item in (chat.get("messages") or ())
            if isinstance(item, dict)
            and str(item.get("role") or "") == "assistant"
            and item.get("intermediate") is True
            and str(item.get("roundId") or item.get("round_id") or "") == run_id
        ]

    def _persist_builtin_result(self, result: Any) -> dict[str, Any]:
        """Project one typed ContextTree outcome into the public chat record."""

        pending_value = getattr(result, "pending_question", None)
        pending = pending_value.as_dict() if pending_value is not None else None
        now = self.service.utc_now_iso()
        model = str(getattr(result, "model", "") or self.chat.get("model") or "")
        run_id = str(getattr(result, "run_id", "") or "")
        activities = [
            copy.deepcopy(dict(item))
            for item in (getattr(result, "activity_messages", ()) or ())
            if isinstance(item, dict)
        ]
        for item in activities:
            item.setdefault("model", model)
            item.setdefault("createdAt", now)
        with self.service.repository.lock:
            chat = self.service.repository.get(self.chat_id)
            if not chat:
                raise RuntimeError("chat disappeared while persisting Agent outcome")
            base_chat = copy.deepcopy(chat)
            self._commit_retry_cut(chat)
            checkpointed_messages = self._checkpointed_run_messages(chat, run_id)
            additions = activities
            if pending is not None:
                question = self.service.pending_question_message(
                    pending,
                    usage=dict(getattr(result, "usage", {}) or {}),
                    model=model,
                )
                additions = [*activities, question]
                chat["pendingQuestion"] = pending
                chat["status"] = "idle"
            else:
                assistant = {
                    "id": self.service.short_id("msg"),
                    "role": "assistant",
                    "content": str(getattr(result, "text", "") or ""),
                    "createdAt": now,
                    "model": model,
                    "processingDurationMs": max(
                        0,
                        int(round((time.monotonic() - self.processing_started_at) * 1000)),
                    ),
                    **self._runtime_message_fields(result),
                }
                additions = [*activities, assistant]
                chat["completedTurnCount"] = self.service.next_completed_turn_count(
                    {"completedTurnCount": self.completed_turn_count_before},
                    retry=self.retry,
                    command=self.command,
                    is_side_agent=self.is_side_agent,
                )
                chat.pop("pendingQuestion", None)
                chat["status"] = "idle"
            self.service.merge_chat_messages_chronologically(chat, additions)
            if model:
                chat["lastModel"] = model
            if isinstance(self.external.plan, dict):
                chat["activePlan"] = copy.deepcopy(self.external.plan)
            chat["updatedAt"] = now
            self.service.repository.write_one(chat, base_chat=base_chat)
        terminal_additions: list[dict[str, Any]] = []
        terminal_message_ids: set[str] = set()
        for item in [*checkpointed_messages, *additions]:
            message_id = str(item.get("id") or "")
            if message_id and message_id in terminal_message_ids:
                continue
            if message_id:
                terminal_message_ids.add(message_id)
            terminal_additions.append(item)
        public_additions = [
            self.service.public_message(item) for item in terminal_additions
        ]
        summary = self.service.public_chat_light(chat)
        summary["runStatus"] = (
            "awaiting_user" if pending is not None else "completed"
        )
        payload: dict[str, Any] = {
            "ok": True,
            "awaitingUser": pending is not None,
            "runId": run_id,
            "userMessage": self.service.public_message(self.user_entry),
            "assistantMessages": public_additions,
            "chatSummary": summary,
            "retry": self.retry,
        }
        if pending is not None:
            payload["pendingQuestion"] = pending
            payload["retryReplacedMessageIds"] = sorted(
                self.retry_replaced_message_ids
            )
        else:
            payload["assistantMessage"] = self.service.public_message(assistant)
        return payload

    async def _run_builtin(self, run: ChatRun) -> None:
        before = await self.service.capture_workspace_changes_baseline(
            self.workspace_dir,
            run.run_id,
        )
        try:
            result = await self._run_turn(run)
            run.status = "finishing"
            awaiting = str(getattr(result, "status", "")) == "awaiting_user"
            if awaiting:
                await self.service.finalize_workspace_changes(
                    chat_id=self.chat_id,
                    run_id=str(getattr(result, "run_id", "") or run.run_id),
                    workspace_dir=self.workspace_dir,
                    before=before,
                    status="awaiting_user",
                    run=run,
                )
            else:
                self.controller._schedule_workspace_finalize(
                    chat_id=self.chat_id,
                    run_id=str(getattr(result, "run_id", "") or run.run_id),
                    workspace_dir=self.workspace_dir,
                    before=before,
                    status="completed",
                )
            payload = await asyncio.to_thread(self._persist_builtin_result, result)
            if awaiting:
                pending = payload.get("pendingQuestion")
                await asyncio.to_thread(self.notify_attention, pending)
                run.outcome = {
                    "kind": "awaiting",
                    "pending": pending,
                    "assistantMessages": payload.get("assistantMessages") or [],
                    "payload": payload,
                }
                await run.publish(
                    {
                        "type": "awaiting_user",
                        "pending_question": pending,
                        "pendingQuestion": pending,
                        "assistantMessages": payload.get("assistantMessages") or [],
                        "retry": self.retry,
                        "retryReplacedMessageIds": sorted(
                            self.retry_replaced_message_ids
                        ),
                        "truncateAfterMessageId": self.truncate_after_id,
                    }
                )
                return
            await run.publish(
                {"type": "run_finalizing", "chatId": self.chat_id, "runId": result.run_id}
            )
            saved = {
                "type": "saved",
                **payload,
                "retryReplacedMessageIds": sorted(self.retry_replaced_message_ids),
                "truncateAfterMessageId": self.truncate_after_id,
            }
            run.outcome = {"kind": "reply", "payload": payload}
            await run.publish(saved)
            from cyrene.runtime.host_actions import finalize_origin

            asyncio.create_task(
                finalize_origin(
                    self.chat_id,
                    "",
                    origin_run_id=self.client_request_id,
                )
            )
        except asyncio.CancelledError:
            await self.service.finalize_workspace_changes(
                chat_id=self.chat_id,
                run_id=run.run_id,
                workspace_dir=self.workspace_dir,
                before=before,
                status="cancelled",
                run=run,
            )
            await asyncio.to_thread(
                self.service.settle_chat_running_status,
                self.chat_id,
            )
            raise
        except Exception as exc:
            logger.exception("Workbench ContextTree run failed for %s", self.chat_id)
            await self.service.finalize_workspace_changes(
                chat_id=self.chat_id,
                run_id=run.run_id,
                workspace_dir=self.workspace_dir,
                before=before,
                status="error",
                run=run,
            )
            run.outcome = {"kind": "error", "exc": exc}
            await asyncio.to_thread(
                self.service.settle_chat_running_status,
                self.chat_id,
            )
            await run.publish(
                {
                    "type": "error",
                    "error": "agent_run_failed",
                    "message": localized(
                        "The Agent run failed. Please try again.",
                        "Agent 运行失败，请重试。",
                        language=self.lang,
                    ),
                    **self.service.chat_error_metadata(exc),
                }
            )

    async def _publish_builtin_settled(self, run: ChatRun) -> None:
        outcome = run.outcome or {}
        kind = str(outcome.get("kind") or "")
        payload = outcome.get("payload") if isinstance(outcome.get("payload"), dict) else {}
        summary = payload.get("chatSummary") if isinstance(payload, dict) else None
        if not isinstance(summary, dict) or not summary:
            summary = await asyncio.to_thread(
                self.controller._load_chat_summary,
                self.chat_id,
            )
        await publish_chat_changed(
            self.chat_id,
            self.project_id,
            "settled",
            run_id=(
                str(payload.get("runId") or run.run_id)
                if isinstance(payload, dict)
                else run.run_id
            ),
            run_status={
                "reply": "completed",
                "awaiting": "awaiting_user",
                "error": "failed",
            }.get(kind, "cancelled"),
            chatSummary=summary,
            assistantMessages=(
                payload.get("assistantMessages") or []
                if isinstance(payload, dict)
                else []
            ),
        )

    async def _dispatch_builtin(self):
        ack: dict[str, Any] = {"type": "ack", "chatId": self.chat_id}
        if self.retry:
            ack.update(
                {
                    "retry": True,
                    "truncateAfterMessageId": self.truncate_after_id,
                }
            )
        else:
            ack["userMessage"] = self.service.public_message(self.user_entry)

        async def runner(run: ChatRun) -> None:
            await self._run_builtin(run)

        run, is_new = self.service.run_manager.start_or_get(
            self.chat_id,
            ack,
            runner,
            stream=self.wants_stream,
            settler=self._publish_builtin_settled,
        )
        if not is_new:
            return self._builtin_dispatch_response(
                payload=localized_error_payload(
                    "This chat already has a reply in progress.",
                    "此对话已有回复正在生成。",
                    "chat_run_in_progress",
                    language=self.lang,
                ),
                status_code=409,
            )
        await publish_chat_changed(
            self.chat_id,
            self.project_id,
            "running",
            run_id=run.run_id,
            run_status="running",
            chatSummary=self.service.public_chat_light(self.chat),
            userMessage=self.service.public_message(self.user_entry),
        )
        if self.wants_stream:
            if self.detached:
                return self._builtin_dispatch_response(
                    payload={
                        "run_id": run.run_id,
                        "chat_id": self.chat_id,
                        "status": run.status,
                        "created_at": run.created_at,
                        "event_cursor": 0,
                    },
                    status_code=202,
                )
            return self._builtin_dispatch_response(
                stream=self.service.run_manager.stream(run),
            )
        await run.done.wait()
        outcome = run.outcome or {}
        if str(outcome.get("kind") or "") == "error":
            exc = outcome.get("exc")
            if not isinstance(exc, Exception):
                exc = RuntimeError("agent run failed")
            metadata = dict(self.service.chat_error_metadata(exc))
            code = str(metadata.pop("code", "") or "agent_run_failed")
            metadata.pop("detail", None)
            metadata.pop("message", None)
            metadata.pop("error", None)
            return self._builtin_dispatch_response(
                payload=localized_error_payload(
                    "The Agent run failed. Please try again.",
                    "Agent 运行失败，请重试。",
                    code,
                    language=self.lang,
                    **metadata,
                ),
                status_code=502,
            )
        payload = outcome.get("payload")
        return self._builtin_dispatch_response(
            payload=(
                payload
                if isinstance(payload, dict)
                else localized_error_payload(
                    "The Agent run ended without a result.",
                    "Agent 运行结束，但未产生结果。",
                    "agent_outcome_missing",
                    language=self.lang,
                )
            ),
            status_code=200 if isinstance(payload, dict) else 500,
        )

    def _builtin_dispatch_response(
        self,
        *,
        payload: dict[str, Any] | None = None,
        status_code: int = 200,
        stream: Any = None,
    ):
        if self.domain:
            return ChatRunDispatchResult(
                payload=payload,
                status_code=status_code,
                stream=stream,
            )
        if stream is not None:
            return StreamingResponse(
                stream,
                media_type="application/x-ndjson",
                headers={"Cache-Control": "no-cache"},
            )
        if status_code != 200:
            return JSONResponse(payload or {}, status_code=status_code)
        return payload or {}

    async def _dispatch(self):
        self.external = ExternalTurnProjection()
        if not self.is_external_agent:
            return await self._dispatch_builtin()
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
            payload = dict(lifecycle.payload or {})
            code = str(
                payload.get("code")
                or payload.get("failureKind")
                or "agent_run_failed"
            )
            metadata = {
                key: value
                for key, value in payload.items()
                if key
                not in {
                    "code",
                    "detail",
                    "error",
                    "message",
                }
            }
            return JSONResponse(
                localized_error_payload(
                    "The Agent run failed. Please try again.",
                    "Agent 运行失败，请重试。",
                    code,
                    language=self.lang,
                    **metadata,
                ),
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
