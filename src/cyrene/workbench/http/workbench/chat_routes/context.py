"""Shared, explicit dependencies for Workbench chat route slices."""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
from dataclasses import dataclass
from typing import Any

from cyrene.config import WORKSPACE_DIR
from cyrene.localization import localized
from cyrene.observability.context_trace import approx_token_count
from cyrene.workbench.chat.chat_service import ChatService
from cyrene.workbench.projects import project_runtime
from cyrene.workbench.chat.conversation_context_service import (
    AgentContextRepository,
    ConversationContextQueryService,
    ConversationInboxQueryService,
)

logger = logging.getLogger(__name__)

_AWAITING_USER_SENTINEL = "[[cyrene.awaiting_user]]"


@dataclass(slots=True)
class ChatRouteContext:
    bot: Any
    db_path: str
    service: ChatService
    conversation_context: ConversationContextQueryService
    conversation_inbox: ConversationInboxQueryService

    @classmethod
    def create(
        cls,
        *,
        bot: Any,
        db_path: str,
    ) -> "ChatRouteContext":
        service = ChatService(db_path)
        from cyrene.workbench.chat import chat_groups, pinned_resources
        from cyrene.workbench.projects.project_repository import configure_workbench_store

        pinned_resources.configure(db_path)
        chat_groups.configure_store(db_path)
        configure_workbench_store(db_path)
        from cyrene.workbench.core_adapter.chat_runtime import workbench_agent_data_directory
        from cyrene.runtime.inbox import peek_messages as peek_agent_inbox_messages

        agent_state_root = workbench_agent_data_directory(str(db_path))

        async def compact_agent_context(
            chat_id: str,
            context_limit: int,
        ) -> dict[str, Any]:
            from cyrene.workbench.core_adapter.conversation_runtime import ConversationConfig
            from cyrene.workbench.projects.project_repository import (
                find_workbench_project_lightweight,
            )

            chat = await asyncio.to_thread(service.repository.get, str(chat_id))
            if not isinstance(chat, dict):
                raise LookupError("chat not found")
            project_id = str(chat.get("projectId") or "")
            project = await asyncio.to_thread(
                find_workbench_project_lightweight,
                project_id,
            )
            workspace_dir = str(
                chat.get("workspaceOverride")
                or (project or {}).get("workspacePath")
                or WORKSPACE_DIR
            )
            input_context = service.resolve_composer_input_context(
                chat,
                workspace_dir,
                strict=True,
            )
            compact_config = ConversationConfig(
                session_id=str(chat_id),
                workspace_dir=workspace_dir,
                db_path=str(db_path),
                bot=bot,
                project_id=project_id,
                session_title=str(chat.get("title") or ""),
                remote_device_ids=tuple(input_context["remoteDeviceIds"]),
                soul_enabled=bool(input_context["soulActive"]),
                workspace_enabled=bool(input_context["workspaceActive"]),
                context_activations=dict(input_context["contextActivations"]),
                resolved_context_activations=dict(
                    input_context["resolvedContextActivations"]
                ),
                completed_turn_count=max(
                    0,
                    int(chat.get("completedTurnCount") or 0),
                ),
            )
            return await service.run_manager.conversation_runtime.compact(
                compact_config,
                context_limit=max(0, int(context_limit or 0)),
            )

        async def agent_inbox_messages(
            chat_id: str,
            round_id: str,
            limit: int,
        ) -> dict[str, Any]:
            return await asyncio.to_thread(
                peek_agent_inbox_messages,
                "main",
                str(chat_id),
                round_id=str(round_id or ""),
                limit=int(limit),
            )

        context = cls(
            bot=bot,
            db_path=str(db_path),
            service=service,
            conversation_context=ConversationContextQueryService(
                chats=service.repository,
                default_model=project_runtime._get_model,
                context_limit=project_runtime._ctx_limit_for_model,
                approx_token_count=lambda text: approx_token_count(str(text or "")),
                agent_states=AgentContextRepository(agent_state_root / "context"),
                compact_agent=compact_agent_context,
            ),
            conversation_inbox=ConversationInboxQueryService(
                chats=service.repository,
                run_manager=service.run_manager,
                utc_now=service.utc_now_iso,
                agent_messages=agent_inbox_messages,
            ),
        )
        context._configure_shell_wake()
        return context

    @property
    def knowledge(self) -> Any:
        from cyrene.core.plugin import application_plugin_service

        return application_plugin_service("knowledge")

    @property
    def memory(self) -> Any:
        from cyrene.core.plugin import application_plugin_service

        return application_plugin_service("memory")

    def runtime(self):
        """Return the explicit route dependency object used by old call sites."""

        return self

    @property
    def awaiting_user_sentinel(self) -> str:
        return _AWAITING_USER_SENTINEL

    @property
    def chat_id(self) -> int:
        return -1

    @staticmethod
    def read_store() -> dict[str, Any]:
        from cyrene.workbench.projects.project_repository import read_workbench_store

        return read_workbench_store()

    @staticmethod
    def find_project(
        payload: dict[str, Any],
        project_id: str,
    ) -> dict[str, Any] | None:
        from cyrene.workbench.projects.project_repository import find_workbench_project

        return find_workbench_project(payload, str(project_id or ""))

    @staticmethod
    def find_project_lightweight(project_id: str) -> dict[str, Any] | None:
        from cyrene.workbench.projects.project_repository import (
            find_workbench_project_lightweight,
        )

        return find_workbench_project_lightweight(str(project_id or ""))

    @staticmethod
    def resolve_workspace_dir(project: dict[str, Any] | None) -> str:
        from cyrene.workbench.projects.project_repository import (
            resolve_project_workspace_dir,
        )

        return resolve_project_workspace_dir(project)

    @staticmethod
    def get_model() -> str:
        return project_runtime._get_model()

    @staticmethod
    def normalize_attachments(attachments: Any) -> list[dict[str, Any]]:
        from cyrene.workbench.chat.chat_attachment_service import (
            normalize_chat_attachments,
        )

        return normalize_chat_attachments(attachments)

    @staticmethod
    def build_public_attachment_payload(item: dict[str, Any]) -> dict[str, Any]:
        from cyrene.workbench.chat.chat_attachment_service import public_chat_attachment

        return public_chat_attachment(item)

    async def register_attachments_kb(
        self,
        session_id: str,
        items: list[dict[str, Any]],
    ) -> None:
        knowledge = self.knowledge
        if knowledge is None:
            return
        register = getattr(knowledge, "register_attachments", None)
        if not callable(register):
            return
        try:
            result = register(str(session_id or ""), items)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception(
                "Failed to register chat attachments for %s",
                session_id,
            )

    @staticmethod
    def attachment_prompt_block(
        items: list[dict[str, Any]],
        *,
        language: Any = None,
    ) -> str:
        if not items:
            return ""
        lines = [
            "",
            localized(
                "[Uploaded attachments]",
                "[已上传附件]",
                language=language,
            ),
            localized(
                "The user uploaded the following files into the local runtime data directory, which is accessible from the workspace.",
                "用户已将以下文件上传到工作区可访问的本地运行时数据目录。",
                language=language,
            ),
            localized(
                "Before answering questions about these files, inspect every relevant attachment with the attachment-analysis Plugin.",
                "回答与这些文件有关的问题前，请使用附件分析 Plugin 检查每个相关附件。",
                language=language,
            ),
            localized(
                "Do not infer file contents from a filename, extension, or metadata alone.",
                "不要仅根据文件名、扩展名或元数据推断文件内容。",
                language=language,
            ),
            localized(
                "If an attachment is missing or unavailable, stop and ask the user to upload it again.",
                "如果附件缺失或不可用，请停止处理并让用户重新上传。",
                language=language,
            ),
            localized(
                "Do not scan unrelated device directories for a replacement copy.",
                "不要扫描设备上无关的目录来寻找替代副本。",
                language=language,
            ),
        ]
        lines.extend(
            f'- {item["name"]} ({item["content_type"]}): {item["path"]}'
            for item in items
        )
        return "\n".join(lines)

    def pending_question_for(self, chat_id: str) -> dict[str, Any] | None:
        checkpoint = self.service.run_manager.conversation_runtime.context_checkpoint(
            str(chat_id or "")
        )
        pending = (
            checkpoint.get("pending_question")
            if isinstance(checkpoint, dict)
            and checkpoint.get("status") == "awaiting_user"
            else None
        )
        return pending.as_dict() if hasattr(pending, "as_dict") else None

    @staticmethod
    def reply_stream_chunks(text: str, target_chars: int = 36) -> list[str]:
        source = str(text or "")
        if not source:
            return []
        chunks: list[str] = []
        for block in re.split(r"(\n\n+)", source):
            if not block:
                continue
            if block.startswith("\n"):
                chunks.append(block)
                continue
            remaining = block
            while remaining:
                if len(remaining) <= target_chars:
                    chunks.append(remaining)
                    break
                split_at = target_chars
                for index in range(
                    target_chars - 1,
                    max(0, target_chars - 14) - 1,
                    -1,
                ):
                    if remaining[index] in "，。！？；：,.!?;: ":
                        split_at = index + 1
                        break
                chunks.append(remaining[:split_at])
                remaining = remaining[split_at:]
        return [chunk for chunk in chunks if chunk]

    def project_data_key(self, project_id: str) -> str:
        from cyrene.workbench.projects.project_runtime import workbench_project_data_key

        project = self.find_project_lightweight(project_id)
        return workbench_project_data_key(project) if project else project_id

    async def check_budget_gate(
        self,
        session_id: str,
        *,
        language: Any = None,
    ) -> dict[str, Any] | None:
        """Apply the application spending guard without the retired Agent."""

        from cyrene.observability import debug
        from cyrene.runtime.budget import check_budget_and_block
        from cyrene.runtime.settings_store import get_all

        settings = get_all()
        result = await check_budget_and_block(
            self.db_path,
            monthly=float(settings.get("budget_monthly") or 0),
            enabled=bool(settings.get("budget_enabled", False)),
        )
        if not result:
            return None
        if result.get("warning"):
            await debug.publish_event(
                {
                    "type": "budget_warning",
                    "code": str(result.get("code") or ""),
                    "message": str(result.get("message") or ""),
                },
                session_id=str(session_id),
            )
            return None
        return {
            "error": localized(
                "The monthly budget has been exhausted.",
                "本月预算已用尽。",
                language=language,
            ),
            "code": str(result.get("code") or "budget_exhausted"),
        }

    async def project_memory_snapshot(
        self,
        project_id: str,
    ) -> dict[str, Any] | None:
        memory = self.memory
        if memory is None:
            return None
        loader = getattr(memory, "current_snapshot", None)
        if not callable(loader):
            return None
        try:
            value = await asyncio.to_thread(loader, str(project_id or ""))
        except Exception:
            logger.exception(
                "Failed to load project-memory snapshot for %s",
                project_id,
            )
            return None
        return dict(value) if isinstance(value, dict) else None

    async def delete_chat_memory(self, chat_id: str) -> None:
        memory = self.memory
        if memory is None:
            return
        delete = getattr(memory, "delete_chat", None)
        if not callable(delete):
            return
        result = delete(str(chat_id or ""))
        if inspect.isawaitable(result):
            await result

    async def resolve_library_file_payload(
        self,
        raw: dict[str, Any],
    ) -> dict[str, Any]:
        body = dict(raw or {})
        nested = body.get("file") if isinstance(body.get("file"), dict) else {}
        source_kind = str(body.get("sourceKind") or nested.get("sourceKind") or "")
        item_id = str(body.get("libraryItemId") or nested.get("libraryItemId") or "")
        workspace = str(body.get("ownerProjectId") or nested.get("ownerProjectId") or "")
        if source_kind != "library" or not item_id or not workspace:
            return body
        knowledge = self.knowledge
        if knowledge is None:
            return body
        try:
            return await knowledge.resolve_library_file_payload(body)
        except Exception:
            logger.exception(
                "Failed to resolve dragged library item %s in %s",
                item_id,
                workspace,
            )
            return body

    @staticmethod
    def public_pinned_resource(item: dict[str, Any]) -> dict[str, Any]:
        public = dict(item)
        public.pop("path", None)
        nested = public.get("file")
        if isinstance(nested, dict):
            public["file"] = {key: value for key, value in nested.items() if key != "path"}
        return public

    def _configure_shell_wake(self) -> None:
        from cyrene.runtime.shell_wake import get_shell_wake_service

        async def dispatch(wake: dict[str, Any]) -> str:
            return await self.service.dispatch_shell_wake_run(
                wake,
                bot=self.bot,
                db_path=self.db_path,
            )

        get_shell_wake_service().configure(
            dispatcher=dispatch,
            is_busy=lambda chat_id: (
                self.service.run_manager.get(str(chat_id)) is not None
            ),
        )

__all__ = ["ChatRouteContext"]
