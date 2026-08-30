"""Built-in messaging-channel adapter for the Plugin Agent conversation.

Telegram and WeChat share this application service so both transports use the
same ChatRepository and ContextTree runtime as Workbench Chat.  The transport
handlers are intentionally limited to authentication, attachment download,
and response delivery.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyrene.core.plugin import application_plugin_service
from cyrene.workbench.core_adapter.conversation_runtime import ConversationConfig

from cyrene.config import WORKSPACE_DIR
from cyrene.localization import app_language, localized
from cyrene.platform.attachments import build_public_attachment_payload
from cyrene.workbench.projects import project_runtime
from cyrene.workbench.chat.chat_events import publish_chat_changed
from cyrene.workbench.chat.chat_service import ChatService
from cyrene.workbench.sessions.context import configure_store, read_project_state


@dataclass(frozen=True, slots=True)
class ChannelTurnResult:
    text: str
    pending_question: dict[str, Any] | None = None

    @property
    def awaiting_user(self) -> bool:
        return self.pending_question is not None


def _stable_session_id(channel: str, identity: str) -> str:
    normalized_channel = "".join(
        character
        for character in str(channel or "channel").strip().lower()
        if character.isalnum() or character in {"_", "-"}
    ) or "channel"
    digest = hashlib.sha256(str(identity or "").encode("utf-8")).hexdigest()[:24]
    return f"channel_{normalized_channel}_{digest}"


def _project_scope(project_id: str = "") -> tuple[str, str]:
    state = read_project_state()
    projects = [
        item
        for item in state.get("projects") or ()
        if isinstance(item, Mapping) and str(item.get("id") or "").strip()
    ]
    active_id = str(project_id or state.get("activeProjectId") or "").strip()
    project = next(
        (item for item in projects if str(item.get("id") or "") == active_id),
        projects[0] if projects else None,
    )
    if not isinstance(project, Mapping):
        return "default", str(Path(WORKSPACE_DIR).expanduser().resolve())
    workspace = str(project.get("workspacePath") or "").strip()
    if not workspace:
        workspace = str(WORKSPACE_DIR)
    return (
        str(project.get("id") or "default").strip() or "default",
        str(Path(workspace).expanduser().resolve()),
    )


class ChannelChatService:
    """Own one durable conversation for one transport identity."""

    def __init__(
        self,
        db_path: str,
        *,
        channel: str,
        identity: str,
        bot: Any,
        host_chat_id: Any,
    ) -> None:
        self.db_path = str(db_path)
        self.channel = str(channel or "channel").strip().lower() or "channel"
        self.identity = str(identity or "").strip()
        self.bot = bot
        self.host_chat_id = host_chat_id
        self.session_id = _stable_session_id(self.channel, self.identity)
        configure_store(self.db_path)
        self.service = ChatService(self.db_path)
        self._lock = asyncio.Lock()

    def _ensure_chat(self) -> dict[str, Any]:
        existing = self.service.repository.get(self.session_id)
        if existing is not None:
            return dict(existing)

        project_id, _workspace = _project_scope()
        title = "Telegram" if self.channel == "telegram" else localized(
            "WeChat", "微信"
        )
        memory_snapshot = None
        memory_service = application_plugin_service("memory")
        snapshot_loader = getattr(memory_service, "current_snapshot", None)
        if callable(snapshot_loader):
            loaded = snapshot_loader(project_id)
            if isinstance(loaded, Mapping):
                memory_snapshot = dict(loaded)
        created = self.service.create_chat(
            project_id,
            title,
            project_runtime._get_model(),
            project_memory_snapshot=memory_snapshot,
        )
        created["id"] = self.session_id
        created["channel"] = self.channel
        created["channelIdentity"] = self.identity

        def insert(payload: dict[str, Any]) -> dict[str, Any]:
            current = self.service.repository.find(payload, self.session_id)
            if current is not None:
                return dict(current)
            payload.setdefault("chats", []).insert(0, created)
            return dict(created)

        return self.service.repository.mutate(insert)

    def _config(
        self,
        chat: Mapping[str, Any],
        *,
        public_user_message: str,
        attachment_paths: Mapping[str, str] | None = None,
    ) -> ConversationConfig:
        project_id = str(chat.get("projectId") or "default")
        _active_project, workspace = _project_scope(project_id)
        language = app_language()
        response_language = "English" if language == "en" else "Simplified Chinese"
        composer_context = application_plugin_service("composer_context")
        if composer_context is None:
            raise RuntimeError(
                "Required Plugin application service is unavailable: "
                "composer_context"
            )
        input_context = composer_context.resolve_input_context(
            soul_active=self.service.chat_soul_active(chat),
            workspace_active=self.service.chat_workspace_active(chat),
            workspace_dir=workspace,
            remote_device_ids=chat.get("remoteDeviceIds") or (),
            context_activations=chat.get("contextActivations"),
            strict=True,
        )
        mutable_chat = dict(chat)
        memory_snapshot = self.service.ensure_chat_memory_snapshot(mutable_chat)
        return ConversationConfig(
            session_id=self.session_id,
            workspace_dir=workspace,
            db_path=self.db_path,
            bot=self.bot,
            host_chat_id=self.host_chat_id,
            permission_mode=str(chat.get("permissionMode") or "default"),
            public_user_message=str(public_user_message or ""),
            attachment_paths=dict(attachment_paths or {}),
            remote_device_ids=tuple(input_context["remoteDeviceIds"]),
            soul_enabled=bool(input_context["soulActive"]),
            workspace_enabled=bool(input_context["workspaceActive"]),
            context_activations=dict(input_context["contextActivations"]),
            resolved_context_activations=dict(
                input_context["resolvedContextActivations"]
            ),
            system_extra=(
                f"The user is talking through the {self.channel} channel. "
                "Return a portable text response; do not rely on Workbench-only "
                f"interactive controls. Respond in {response_language} unless "
                "the user explicitly requests another language."
            ),
            project_id=project_id,
            project_memory_snapshot=memory_snapshot,
            session_title=str(chat.get("title") or ""),
            completed_turn_count=int(chat.get("completedTurnCount") or 0) + 1,
            conversation_source=self.channel,
        )

    def pending_question(self) -> dict[str, Any] | None:
        checkpoint = self.service.run_manager.conversation_runtime.context_checkpoint(
            self.session_id
        )
        if not isinstance(checkpoint, Mapping):
            return None
        pending = checkpoint.get("pending_question")
        if pending is None:
            return None
        as_dict = getattr(pending, "as_dict", None)
        if callable(as_dict):
            return dict(as_dict())
        return dict(pending) if isinstance(pending, Mapping) else None

    @staticmethod
    def answer_value(question: Mapping[str, Any], raw: str) -> str:
        answer = str(raw or "").strip()
        options = question.get("options")
        options = list(options) if isinstance(options, Sequence) and not isinstance(options, str) else []
        if options and answer.isdigit():
            index = int(answer) - 1
            if 0 <= index < len(options):
                item = options[index]
                answer = str(
                    item.get("label", item) if isinstance(item, Mapping) else item
                ).strip()
        return answer

    async def turn(
        self,
        text: str,
        *,
        public_user_message: str | None = None,
        attachments: Sequence[Mapping[str, Any]] = (),
    ) -> ChannelTurnResult:
        async with self._lock:
            chat = await asyncio.to_thread(self._ensure_chat)
            original = str(public_user_message if public_user_message is not None else text)
            public_attachments = [
                build_public_attachment_payload(dict(item))
                for item in attachments
                if isinstance(item, Mapping)
            ]
            attachment_paths = {
                str(item.get("id") or ""): str(item.get("path") or "")
                for item in attachments
                if str(item.get("id") or "").strip()
                and str(item.get("path") or "").strip()
            }
            pending = self.pending_question()
            user_entry: dict[str, Any] = {
                "id": f"msg_{uuid.uuid4().hex[:12]}",
                "role": "user",
                "content": original,
                "createdAt": self.service.utc_now_iso(),
                "channel": self.channel,
            }
            if public_attachments:
                user_entry["attachments"] = public_attachments

            def start(current: dict[str, Any]) -> None:
                self.service.merge_chat_messages_chronologically(
                    current,
                    [user_entry],
                )
                current["status"] = "running"
                current["updatedAt"] = user_entry["createdAt"]

            updated = await asyncio.to_thread(
                self.service.repository.mutate_one,
                self.session_id,
                start,
            )
            if updated is None:
                raise RuntimeError("channel chat disappeared before the Agent turn")
            chat = dict(updated)
            run_id = f"channelrun_{uuid.uuid4().hex}"
            config = self._config(
                chat,
                public_user_message=original,
                attachment_paths=attachment_paths,
            )
            started_at = time.monotonic()
            try:
                if pending is not None:
                    question_id = str(pending.get("id") or "").strip()
                    if not question_id:
                        raise RuntimeError("pending channel question has no id")
                    result = await self.service.run_manager.conversation_runtime.answer(
                        config,
                        question_id,
                        self.answer_value(pending, original),
                    )
                else:
                    result = await self.service.run_manager.conversation_runtime.send(
                        config,
                        str(text or "").strip(),
                        run_id=run_id,
                        metadata={
                            "public_user_message": original,
                            "public_attachments": public_attachments,
                            "conversation_source": self.channel,
                        },
                    )
            except BaseException:
                def settle(current: dict[str, Any]) -> None:
                    current["status"] = "idle"
                    current["updatedAt"] = self.service.utc_now_iso()

                await asyncio.to_thread(
                    self.service.repository.mutate_one,
                    self.session_id,
                    settle,
                )
                raise

            additions = [
                copy.deepcopy(dict(item))
                for item in result.activity_messages
                if isinstance(item, Mapping)
            ]
            pending_payload: dict[str, Any] | None = None
            if result.status == "awaiting_user" and result.pending_question is not None:
                pending_payload = result.pending_question.as_dict()
                additions.append(
                    self.service.pending_question_message(
                        pending_payload,
                        usage=result.usage,
                        model=result.model,
                    )
                )
            else:
                assistant: dict[str, Any] = {
                    "id": f"msg_{uuid.uuid4().hex[:12]}",
                    "role": "assistant",
                    "content": str(result.text or ""),
                    "createdAt": self.service.utc_now_iso(),
                    "model": str(result.model or chat.get("model") or ""),
                    "processingDurationMs": max(
                        0,
                        int(round((time.monotonic() - started_at) * 1000)),
                    ),
                    "channel": self.channel,
                }
                if any(result.usage.values()):
                    assistant["usage"] = dict(result.usage)
                if result.model_identity:
                    assistant["modelIdentity"] = dict(result.model_identity)
                if result.generation_duration_ms is not None:
                    assistant["modelGenerationDurationMs"] = round(
                        result.generation_duration_ms,
                        3,
                    )
                if result.output_tokens_per_second is not None:
                    assistant["outputTokensPerSecond"] = round(
                        result.output_tokens_per_second,
                        3,
                    )
                additions.append(assistant)

            def finish(current: dict[str, Any]) -> None:
                self.service.merge_chat_messages_chronologically(current, additions)
                current["status"] = "idle"
                current["updatedAt"] = self.service.utc_now_iso()
                if pending_payload is not None:
                    current["pendingQuestion"] = pending_payload
                else:
                    current.pop("pendingQuestion", None)
                    current["completedTurnCount"] = (
                        int(current.get("completedTurnCount") or 0) + 1
                    )
                if isinstance(result.active_plan, Mapping):
                    current["activePlan"] = copy.deepcopy(dict(result.active_plan))
                current["lastModel"] = str(result.model or current.get("model") or "")

            final_chat = await asyncio.to_thread(
                self.service.repository.mutate_one,
                self.session_id,
                finish,
            )
            if final_chat is None:
                raise RuntimeError("channel chat disappeared after the Agent turn")
            await publish_chat_changed(
                self.session_id,
                str(final_chat.get("projectId") or ""),
                "updated",
                chatSummary=self.service.public_chat_light(final_chat),
                assistantMessages=[
                    self.service.public_message(item) for item in additions
                ],
            )
            return ChannelTurnResult(
                text=str(result.text or ""),
                pending_question=pending_payload,
            )

    async def clear(self) -> None:
        runtime = self.service.run_manager.conversation_runtime
        runtime.request_cancel(self.session_id, "channel_cleared")
        async with self._lock:
            await asyncio.to_thread(runtime.delete_context, self.session_id)
            chat = await asyncio.to_thread(self._ensure_chat)

            def reset(current: dict[str, Any]) -> None:
                current["messages"] = []
                current["status"] = "idle"
                current["completedTurnCount"] = 0
                current.pop("pendingQuestion", None)
                current.pop("activePlan", None)
                current["updatedAt"] = self.service.utc_now_iso()

            await asyncio.to_thread(
                self.service.repository.mutate_one,
                self.session_id,
                reset,
            )
            await publish_chat_changed(
                self.session_id,
                str(chat.get("projectId") or ""),
                "updated",
            )


_CHANNEL_SERVICES: dict[tuple[str, str, str], ChannelChatService] = {}


def get_channel_chat_service(
    db_path: str,
    *,
    channel: str,
    identity: str,
    bot: Any,
    host_chat_id: Any,
) -> ChannelChatService:
    key = (str(Path(db_path).expanduser().resolve()), str(channel), str(identity))
    service = _CHANNEL_SERVICES.get(key)
    if service is None:
        service = ChannelChatService(
            key[0],
            channel=channel,
            identity=identity,
            bot=bot,
            host_chat_id=host_chat_id,
        )
        _CHANNEL_SERVICES[key] = service
    else:
        service.bot = bot
        service.host_chat_id = host_chat_id
    return service


__all__ = [
    "ChannelChatService",
    "ChannelTurnResult",
    "get_channel_chat_service",
]
