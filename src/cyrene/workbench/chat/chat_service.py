"""Workbench Chat application boundary backed by the native Agent kernel."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import time
from collections.abc import Mapping
from typing import Any, cast

from cyrene.localization import app_language, localized
from cyrene.workbench.chat.chat_application import (
    ContextTreeTranscript,
    WorkspaceChangeService,
    chat_error_metadata,
    chat_preview,
    chat_run_error_message,
    chat_soul_active,
    chat_transcript_for_brief,
    chat_workspace_active,
    clear_fork_metadata,
    coerce_brief_acceptance,
    coerce_brief_constraints,
    completed_turn_count,
    disable_button_block,
    extract_exchange_timeline,
    has_button_block,
    last_exchange_model,
    mark_user_activity,
    merge_chat_messages_chronologically,
    new_chat,
    next_completed_turn_count,
    normalize_workspace_override,
    parse_json_object,
    pending_question_message,
    prune_orphaned_fork_metadata,
    public_chat_full,
    public_chat_light,
    public_chats_light,
    public_message,
    remove_retry_replaced_messages,
    resolve_chat_workspace_dir,
    resolve_composer_input_context,
    sanitize_durable_traces,
    short_id,
    side_agent_parent_transcript,
    utc_now_iso,
)
from cyrene.workbench.chat.chat_dto import (
    ChatCreateDTO,
    ChatDetailDTO,
    ChatMessageDTO,
    ChatSummaryDTO,
)
from cyrene.workbench.chat.chat_repository import ChatRepository
from cyrene.workbench.chat.chat_runs import (
    ChatRun,
    ChatRunManager,
    get_chat_run_manager as _get_chat_run_manager,
)
from cyrene.workbench.application.notifications import append_notification

logger = logging.getLogger(__name__)

_WORKSPACE_SERVICES: dict[str, WorkspaceChangeService] = {}


def get_chat_run_manager() -> ChatRunManager:
    return _get_chat_run_manager()


def _workspace_service(
    db_path: str,
    repository: ChatRepository,
) -> WorkspaceChangeService:
    key = str(db_path or "")
    service = _WORKSPACE_SERVICES.get(key)
    if service is None:
        service = WorkspaceChangeService(key, repository)
        _WORKSPACE_SERVICES[key] = service
    return service


async def shutdown_chat_services() -> None:
    services = list(_WORKSPACE_SERVICES.values())
    _WORKSPACE_SERVICES.clear()
    for service in services:
        await service.shutdown()


def settle_chat_running_status(chat_id: str) -> None:
    get_chat_run_manager().settle_chat_running_status(str(chat_id or ""))


class ChatService:
    """Application operations consumed by the split Workbench HTTP routes."""

    def __init__(self, db_path: str, repository: ChatRepository | None = None):
        self.db_path = str(db_path)
        self.repository = repository or ChatRepository()
        self.repository.configure(self.db_path)
        manager = get_chat_run_manager()
        if manager.configured_db_path != self.db_path:
            manager.configure(self.db_path)
        self._workspace = _workspace_service(self.db_path, self.repository)
        self._transcript = ContextTreeTranscript(self.db_path)

    @property
    def run_manager(self) -> ChatRunManager:
        return get_chat_run_manager()

    def new_chat(self, request: ChatCreateDTO) -> ChatDetailDTO:
        return cast(
            ChatDetailDTO,
            new_chat(
                request["project_id"],
                request.get("title", ""),
                request.get("model", ""),
                project_memory_snapshot=request.get("project_memory_snapshot"),
                agent=request.get("agent"),
                model_access=request.get("model_access"),
                capabilities=request.get("capabilities"),
                soul_active=request.get("soul_active"),
                workspace_active=request.get("workspace_active"),
                reasoning_effort=request.get("reasoning_effort", ""),
            ),
        )

    def create_chat(
        self,
        project_id: str,
        title: str = "",
        model: str = "",
        **options: Any,
    ) -> ChatDetailDTO:
        return self.new_chat(
            ChatCreateDTO(
                project_id=project_id,
                title=title,
                model=model,
                project_memory_snapshot=options.get("project_memory_snapshot"),
                agent=options.get("agent"),
                model_access=options.get("model_access"),
                capabilities=options.get("capabilities"),
                soul_active=options.get("soul_active"),
                workspace_active=options.get("workspace_active"),
                reasoning_effort=str(options.get("reasoning_effort") or ""),
            )
        )

    def ensure_chat_memory_snapshot(
        self,
        chat: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Lazily freeze legacy chats to the first complete memory snapshot.

        New chats already persist short-term and structured memory alongside
        the versioned project prompt.  Chats created by older builds lack
        those fields, so pin them once before their next run and never replace
        the pinned values with later background-learning writes.
        """

        raw = chat.get("projectMemorySnapshot")
        existing = dict(raw) if isinstance(raw, Mapping) else {}
        required = {"shortTermContext", "structuredContext"}
        if required.issubset(existing):
            return existing

        from cyrene.core.plugin import application_plugin_service

        memory = application_plugin_service("memory")
        freezer = getattr(memory, "freeze_snapshot", None)
        if not callable(freezer):
            return existing or None
        candidate = freezer(
            str(chat.get("projectId") or ""),
            existing or None,
        )
        if not isinstance(candidate, Mapping):
            return existing or None
        candidate = dict(candidate)

        def persist(stored: dict[str, Any]) -> None:
            stored_raw = stored.get("projectMemorySnapshot")
            stored_snapshot = (
                dict(stored_raw) if isinstance(stored_raw, Mapping) else {}
            )
            if required.issubset(stored_snapshot):
                return
            stored["projectMemorySnapshot"] = copy.deepcopy(candidate)

        chat_id = str(chat.get("id") or "")
        updated = self.repository.mutate_one(chat_id, persist) if chat_id else None
        final_raw = (
            updated.get("projectMemorySnapshot")
            if isinstance(updated, Mapping)
            else candidate
        )
        final = dict(final_raw) if isinstance(final_raw, Mapping) else candidate
        chat["projectMemorySnapshot"] = copy.deepcopy(final)
        return final

    def public_chat_light(self, chat: dict[str, Any]) -> ChatSummaryDTO:
        run = self.run_manager.get(str(chat.get("id") or ""))
        return cast(ChatSummaryDTO, public_chat_light(chat, active_run=run))

    def public_chats_light(
        self,
        chats: list[dict[str, Any]],
    ) -> list[ChatSummaryDTO]:
        runs = {
            str(chat.get("id") or ""): run
            for chat in chats
            if (run := self.run_manager.get(str(chat.get("id") or ""))) is not None
        }
        return cast(
            list[ChatSummaryDTO],
            public_chats_light(chats, active_runs=runs),
        )

    def public_chat_full(self, chat: dict[str, Any]) -> ChatDetailDTO:
        run = self.run_manager.get(str(chat.get("id") or ""))
        return cast(ChatDetailDTO, public_chat_full(chat, active_run=run))

    def public_message(self, message: dict[str, Any]) -> ChatMessageDTO:
        return cast(ChatMessageDTO, public_message(message))

    async def capture_workspace_changes_baseline(
        self,
        workspace_dir: Any,
        run_id: str = "",
    ) -> Any:
        return await self._workspace.capture(workspace_dir, run_id)

    async def finalize_workspace_changes(self, **kwargs: Any) -> Any:
        return await self._workspace.finalize(**kwargs)

    def prewarm_workspace_changes(self, workspace_dir: Any) -> None:
        self._workspace.prewarm(workspace_dir)

    def sync_chat_generated_files(
        self,
        chat_id: str,
        change_set: dict[str, Any] | None = None,
    ) -> None:
        self._workspace.sync_generated_files(chat_id, change_set)

    async def run_external_agent_turn(self, *args: Any, **kwargs: Any):
        from cyrene.agent_runtime import run_external_agent_turn

        return await run_external_agent_turn(*args, **kwargs)

    def chat_preview(self, chat: Mapping[str, Any]) -> str:
        return chat_preview(chat)

    def chat_soul_active(self, chat: Mapping[str, Any]) -> bool:
        return chat_soul_active(chat)

    def chat_workspace_active(self, chat: Mapping[str, Any]) -> bool:
        return chat_workspace_active(chat)

    def clear_fork_metadata(self, chat: dict[str, Any]) -> bool:
        return clear_fork_metadata(chat)

    def coerce_brief_acceptance(self, raw: Any) -> list[dict[str, Any]]:
        return coerce_brief_acceptance(raw)

    def coerce_brief_constraints(self, raw: Any) -> list[str]:
        return coerce_brief_constraints(raw)

    def completed_turn_count(self, chat: Mapping[str, Any]) -> int:
        return completed_turn_count(chat)

    def extract_exchange_timeline(self, *args: Any, **kwargs: Any):
        return extract_exchange_timeline(*args, **kwargs)

    def last_exchange_model(self, *args: Any, **kwargs: Any) -> str:
        return last_exchange_model(*args, **kwargs)

    def mark_user_activity(self, chat: dict[str, Any], timestamp: str) -> None:
        mark_user_activity(chat, timestamp)

    def merge_chat_messages_chronologically(
        self,
        chat: dict[str, Any],
        additions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return merge_chat_messages_chronologically(chat, additions)

    def next_completed_turn_count(self, *args: Any, **kwargs: Any) -> int:
        return next_completed_turn_count(*args, **kwargs)

    def normalize_workspace_override(self, path: Any) -> str:
        return normalize_workspace_override(path)

    def pending_question_message(self, *args: Any, **kwargs: Any):
        return pending_question_message(*args, **kwargs)

    def prune_orphaned_fork_metadata(self, payload: dict[str, Any]) -> bool:
        return prune_orphaned_fork_metadata(payload)

    def remove_retry_replaced_messages(self, *args: Any, **kwargs: Any) -> None:
        remove_retry_replaced_messages(*args, **kwargs)

    def resolve_chat_workspace_dir(self, *args: Any, **kwargs: Any) -> str:
        return resolve_chat_workspace_dir(*args, **kwargs)

    def resolve_composer_input_context(
        self,
        chat: Mapping[str, Any],
        workspace_dir: str,
        *,
        strict: bool = True,
    ) -> dict[str, Any]:
        return resolve_composer_input_context(
            chat,
            workspace_dir,
            strict=strict,
        )

    def sanitize_durable_traces(self, traces: list[Any]):
        return sanitize_durable_traces(traces)

    def session_state_messages(self, session_id: str) -> list[dict[str, Any]]:
        return self._transcript.messages(session_id)

    def settle_chat_running_status(self, chat_id: str) -> None:
        self.run_manager.settle_chat_running_status(chat_id)

    def short_id(self, prefix: str) -> str:
        return short_id(prefix)

    def side_agent_parent_transcript(self, chat: Mapping[str, Any] | None) -> str:
        return side_agent_parent_transcript(chat)

    def utc_now_iso(self) -> str:
        return utc_now_iso()

    def chat_run_error_message(self, exc: Exception, lang: str = "") -> str:
        return chat_run_error_message(exc, lang)

    def chat_error_metadata(self, exc: Exception) -> dict[str, str]:
        return chat_error_metadata(exc)

    def disable_button_block(self, content: str, action_id: str):
        return disable_button_block(content, action_id)

    def has_button_block(self, content: str, action_id: str) -> bool:
        return has_button_block(content, action_id)

    def set_chat_external_session_id(
        self,
        chat_id: str,
        external_session_id: str,
    ) -> dict[str, Any] | None:
        def persist(chat: dict[str, Any]) -> None:
            agent = dict(chat.get("agent") or {})
            agent["externalSessionId"] = str(external_session_id or "").strip()
            chat["agent"] = agent
            chat["updatedAt"] = utc_now_iso()

        return self.repository.mutate_one(chat_id, persist)

    def update_chat_agent_context_report(
        self,
        chat_id: str,
        report: dict[str, Any],
    ) -> dict[str, Any] | None:
        def safe_int(value: Any) -> int:
            try:
                return max(0, int(value or 0))
            except (TypeError, ValueError, OverflowError):
                return 0

        segments: list[dict[str, Any]] = []
        raw_segments = report.get("segments")
        for index, item in enumerate(raw_segments if isinstance(raw_segments, list) else ()):
            if index >= 32 or not isinstance(item, Mapping):
                break
            tokens = safe_int(item.get("tokens") or item.get("tokens_est") or item.get("used"))
            if not tokens:
                continue
            key = str(item.get("key") or item.get("id") or item.get("type") or f"segment_{index + 1}")[:80]
            segments.append(
                {
                    "key": key,
                    "label": str(item.get("label") or item.get("name") or key)[:120],
                    "tokens": tokens,
                }
            )
        normalized = {
            "used": safe_int(report.get("used") or report.get("totalTokens")),
            "size": safe_int(report.get("size") or report.get("limit") or report.get("contextWindow")),
            "segments": segments,
            "updatedAt": utc_now_iso(),
        }
        if not normalized["used"] and segments:
            normalized["used"] = sum(item["tokens"] for item in segments)
        if not normalized["used"] and not normalized["size"] and not segments:
            return self.repository.get(chat_id)

        def persist(chat: dict[str, Any]) -> None:
            chat["agentContextReport"] = normalized
            chat["updatedAt"] = utc_now_iso()

        return self.repository.mutate_one(chat_id, persist)

    async def _secondary_json(
        self,
        prompt: str,
        *,
        max_tokens: int,
        caller: str,
    ) -> dict[str, Any] | None:
        from cyrene.core.plugin import application_plugin_service

        model = application_plugin_service("model")
        complete = getattr(model, "complete", None)
        if not callable(complete):
            return None
        try:
            response = await asyncio.wait_for(
                complete(
                    [{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                    route="secondary",
                    caller=caller,
                ),
                timeout=90,
            )
        except Exception:
            logger.exception("Secondary model call failed (%s)", caller)
            return None
        return parse_json_object(
            response.get("content") if isinstance(response, Mapping) else ""
        )

    async def generate_chat_group_metadata(
        self,
        members: list[dict[str, Any]],
        *,
        lang: str = "",
        title_locked: bool = False,
        current_title: str = "",
    ) -> dict[str, str]:
        target_lang = app_language(lang)

        def collapse(value: Any, limit: int) -> str:
            return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]

        cleaned = [
            {
                "title": collapse(item.get("title"), 160),
                "preview": collapse(item.get("preview"), 800),
            }
            for item in members[:50]
            if isinstance(item, Mapping)
            and (
                str(item.get("title") or "").strip()
                or str(item.get("preview") or "").strip()
            )
        ]
        if len(cleaned) < 2:
            raise ValueError(
                localized(
                    "At least two chat members are required.",
                    "至少需要两个对话成员。",
                    language=target_lang,
                )
            )
        language_rule = localized(
            "Write both user-visible values in English. Keep the title under 48 "
            "characters and the summary under 110 characters.",
            "标题和摘要必须使用简体中文。标题不超过 18 个汉字，摘要不超过 45 个汉字。",
            language=target_lang,
        )
        title_rule = (
            localized(
                "The user manually locked the title. Return an empty title and only "
                "update the summary.",
                "用户已手动锁定标题。title 必须返回空字符串，只更新 summary。",
                language=target_lang,
            )
            if title_locked
            else localized(
                "Generate a specific shared-topic title. Do not return generic "
                "placeholders such as Chat group, New chat group, 对话组, or 新对话组。",
                "生成具体的共同主题标题；禁止返回 Chat group、New chat group、对话组、"
                "新对话组等通用占位标题。",
                language=target_lang,
            )
        )
        generic_titles = {"chatgroup", "newchatgroup", "对话组", "新对话组"}

        def is_generic_title(value: str) -> bool:
            normalized = re.sub(r"[\s\-_.,，。!！?？:：]+", "", value).casefold()
            return normalized in generic_titles

        title = ""
        summary = ""
        for attempt in range(2):
            corrective = ""
            if attempt:
                corrective = localized(
                    (
                        "The previous attempt returned an empty or generic title, or an "
                        "empty summary. Both fields are required as non-empty strings."
                    ) if not title_locked else (
                        "The previous attempt returned an invalid locked title or an empty "
                        "summary. Return an empty title and a non-empty summary."
                    ),
                    (
                        "上一次返回了空标题、通用占位标题或空摘要。title 和 summary "
                        "都必须是非空字符串。"
                    ) if not title_locked else (
                        "上一次返回了无效的锁定标题或空摘要。title 必须为空字符串，"
                        "summary 必须为非空字符串。"
                    ),
                    language=target_lang,
                )
            prompt = localized(
                "You maintain metadata for a group of related AI conversations. Infer "
                "their shared intent from the supplied titles and previews. Return only "
                "one JSON object with string fields title and summary. The summary should "
                "describe the combined subject rather than list every conversation.\n"
                "{language_rule}\n{title_rule}\n{corrective}\nCurrent title: {title}\n"
                "Members JSON:\n{members}",
                "你负责维护一组相关 AI 对话的元数据。根据提供的标题和预览推断它们的"
                "共同意图。只返回一个 JSON 对象，包含字符串字段 title 和 summary。"
                "摘要应描述整体主题，不要逐条罗列对话。\n{language_rule}\n{title_rule}\n"
                "{corrective}\n当前标题：{title}\n对话成员 JSON：\n{members}",
                language=target_lang,
                language_rule=language_rule,
                title_rule=title_rule,
                corrective=corrective,
                title=current_title[:160],
                members=json.dumps(cleaned, ensure_ascii=False),
            )
            parsed = await self._secondary_json(
                prompt,
                max_tokens=512,
                caller="workbench_chat_group_metadata",
            )
            title = collapse((parsed or {}).get("title"), 60)
            summary = collapse((parsed or {}).get("summary"), 160)
            if is_generic_title(title):
                title = ""
            if (title_locked or title) and summary:
                break
            logger.warning(
                "Chat group metadata attempt %s produced invalid fields "
                "(title=%r, summary=%r)",
                attempt + 1,
                title,
                summary,
            )
        if not title_locked and not title:
            title = next(
                (
                    item["title"]
                    for item in cleaned
                    if item["title"] and not is_generic_title(item["title"])
                ),
                "",
            )
        if not summary:
            summary = next((item["preview"] for item in cleaned if item["preview"]), "")
        return {
            "title": "" if title_locked else title,
            "summary": summary,
            "lang": target_lang,
        }

    async def summarize_chat_to_brief(
        self,
        chat: dict[str, Any],
        project: dict[str, Any],
    ) -> dict[str, Any] | None:
        language = app_language()
        transcript = chat_transcript_for_brief(chat)
        if not transcript:
            return None
        project_name = str(
            project.get("name")
            or localized("Untitled project", "未命名项目", language=language)
        )
        chat_title = str(
            chat.get("title")
            or localized("New chat", "新对话", language=language)
        )
        prompt = localized(
            "Turn the complete conversation below into an execution-ready Task "
            "brief. Return only one JSON object with the exact fields title, goal, "
            "constraints (an array of strings), and acceptanceCriteria (an array "
            "of strings). Preserve concrete requirements and do not invent facts. "
            "Write every user-visible value in English.\n"
            "Project: {project}\nConversation title: {title}\n"
            "===== Conversation begins =====\n{transcript}\n"
            "===== Conversation ends =====",
            "把以下完整对话整理成可直接执行的任务简报。只返回一个 JSON 对象，字段必须为 "
            "title、goal、constraints（字符串数组）和 acceptanceCriteria（字符串数组）。"
            "保留具体要求，不要编造事实；所有用户可见字段值均使用简体中文。\n"
            "项目：{project}\n对话标题：{title}\n"
            "===== 对话开始 =====\n{transcript}\n===== 对话结束 =====",
            language=language,
            project=project_name,
            title=chat_title,
            transcript=transcript,
        )
        return await self._secondary_json(
            prompt,
            max_tokens=6000,
            caller="workbench_chat_to_task_brief",
        )

    async def dispatch_shell_wake_run(
        self,
        wake: dict[str, Any],
        *,
        bot: Any,
        db_path: str,
    ) -> str:
        """Continue the same ContextTree after a shell/media/session wake."""

        from cyrene.workbench.core_adapter.conversation_runtime import ConversationConfig
        from cyrene.workbench.projects.project_repository import (
            find_workbench_project_lightweight,
            resolve_project_workspace_dir,
        )

        chat_id = str(wake.get("chat_id") or "").strip()
        prompt = str(wake.get("prompt") or "").strip()
        source = str(wake.get("source") or "")
        agent_originated = source == "agent_session"
        media_wake = source == "media_job"
        terminal_id = str(
            wake.get("terminal_id") or wake.get("shell_id") or ""
        ).strip()
        if (
            not chat_id
            or (agent_originated and not prompt)
            or (media_wake and not str(wake.get("batch_id") or "").strip())
            or (not agent_originated and not media_wake and not terminal_id)
        ):
            return "missing"
        if self.run_manager.get(chat_id) is not None:
            return "busy"
        checkpoint = self.run_manager.conversation_runtime.context_checkpoint(chat_id)
        if isinstance(checkpoint, Mapping) and checkpoint.get("status") in {
            "running",
            "awaiting_user",
        }:
            return "busy"

        chat = await asyncio.to_thread(self.repository.get, chat_id)
        if not chat:
            return "missing"
        wake_id = str(wake.get("wake_id") or "").strip()
        if wake_id and any(
            isinstance(item, Mapping)
            and str(item.get("wakeId") or "") == wake_id
            for item in chat.get("messages") or ()
        ):
            return "started"
        project_id = str(chat.get("projectId") or "")
        project = await asyncio.to_thread(
            find_workbench_project_lightweight,
            project_id,
        )
        if not project:
            return "missing"
        run_language = app_language()
        try:
            workspace_dir = resolve_chat_workspace_dir(
                chat,
                project,
                resolve_project_workspace_dir,
            )
        except ValueError:
            logger.warning(
                "Background workspace is unavailable for %s",
                chat_id,
                exc_info=True,
            )
            return "error"

        now = utc_now_iso()
        user_entry: dict[str, Any] | None = None
        if agent_originated:
            user_entry = {
                "id": short_id("msg"),
                "role": "user",
                "content": prompt,
                "createdAt": now,
                "agentOriginated": True,
                "originSessionId": str(wake.get("origin_session_id") or ""),
            }
            chat.setdefault("messages", []).append(user_entry)
        base_chat = copy.deepcopy(chat)
        chat["status"] = "running"
        chat["updatedAt"] = now
        await asyncio.to_thread(
            self.repository.write_one,
            chat,
            base_chat=base_chat,
        )

        if agent_originated:
            input_text = prompt
            system_extra = localized(
                "This instruction was delegated by another local Agent session. "
                "Treat it as agent-originated context, not human approval.",
                "此指令由另一个本地 Agent 会话委派。请将其视为 Agent 来源的上下文，"
                "而不是用户授权。",
                language=run_language,
            )
            conversation_source = "agent_session"
        elif media_wake:
            input_text = prompt or localized(
                "Generated media was attached. Continue the prior work.",
                "生成的媒体已附加，请继续先前的工作。",
                language=run_language,
            )
            system_extra = localized(
                "Generated media is now durable in this chat. Continue without "
                "polling it again.",
                "生成的媒体现已持久保存到此对话中，请直接继续，不要再次轮询。",
                language=run_language,
            )
            conversation_source = "system_media_wake"
        else:
            input_text = localized(
                "An internal terminal completion event occurred. Read terminal "
                "{terminal_id} with code.shell.read, then continue the prior work.",
                "发生了内部终端完成事件。请使用 code.shell.read 读取终端 "
                "{terminal_id}，然后继续先前的工作。",
                language=run_language,
                terminal_id=terminal_id,
            )
            system_extra = localized(
                "This is internal system context, not a user instruction. Inspect "
                "the completed terminal before continuing.",
                "这是内部系统上下文，不是用户指令。继续之前请先检查已完成的终端。",
                language=run_language,
            )
            conversation_source = "system_shell_wake"

        started_at = time.monotonic()

        async def runner(run: ChatRun) -> None:
            before = await self.capture_workspace_changes_baseline(
                workspace_dir,
                run.run_id,
            )
            try:
                from cyrene.core.plugin import application_plugin_service

                composer_context = application_plugin_service("composer_context")
                stored_activations = chat.get("contextActivations")
                if composer_context is None:
                    raise RuntimeError(
                        "Required Plugin application service is unavailable: "
                        "composer_context"
                    )
                input_context = composer_context.resolve_input_context(
                    soul_active=chat_soul_active(chat),
                    workspace_active=chat_workspace_active(chat),
                    workspace_dir=workspace_dir,
                    remote_device_ids=chat.get("remoteDeviceIds") or (),
                    context_activations=stored_activations,
                    strict=True,
                )
                memory_snapshot = self.ensure_chat_memory_snapshot(chat)
                config = ConversationConfig(
                    session_id=chat_id,
                    workspace_dir=workspace_dir,
                    db_path=str(db_path or self.db_path),
                    bot=bot,
                    permission_mode="default" if agent_originated else "auto",
                    public_user_message=prompt if agent_originated else "",
                    remote_device_ids=tuple(input_context["remoteDeviceIds"]),
                    soul_enabled=bool(input_context["soulActive"]),
                    workspace_enabled=bool(input_context["workspaceActive"]),
                    context_activations=dict(input_context["contextActivations"]),
                    resolved_context_activations=dict(
                        input_context["resolvedContextActivations"]
                    ),
                    system_extra=system_extra,
                    project_id=project_id,
                    project_memory_snapshot=memory_snapshot,
                    session_title=str(chat.get("title") or ""),
                    completed_turn_count=(
                        completed_turn_count(chat) + (1 if agent_originated else 0)
                    ),
                    response_capabilities=("interactive_blocks",),
                    conversation_source=conversation_source,
                    guidance_channel=run.guidance_channel,
                )
                result = await self.run_manager.conversation_runtime.send(
                    config,
                    input_text,
                    run_id=run.run_id,
                    metadata={
                        "system_initiated": not agent_originated,
                        "wake_id": wake_id,
                        "source": conversation_source,
                    },
                    publish=run.publish,
                )
                fresh = await asyncio.to_thread(self.repository.get, chat_id)
                if not fresh:
                    raise RuntimeError(
                        localized(
                            "The chat disappeared during background continuation.",
                            "后台接续期间对话已不存在。",
                            language=run_language,
                        )
                    )
                fresh_base = copy.deepcopy(fresh)
                model = str(result.model or fresh.get("model") or "")
                additions = [
                    {
                        **copy.deepcopy(dict(item)),
                        "model": str(item.get("model") or model),
                    }
                    for item in result.activity_messages
                    if isinstance(item, Mapping)
                ]
                if (
                    result.status == "awaiting_user"
                    and result.pending_question is not None
                ):
                    pending = result.pending_question.as_dict()
                    additions.append(
                        pending_question_message(
                            pending,
                            usage=result.usage,
                            model=model,
                        )
                    )
                    fresh["pendingQuestion"] = pending
                    fresh["status"] = "idle"
                    run.outcome = {"kind": "awaiting", "pending": pending}
                else:
                    assistant: dict[str, Any] = {
                        "id": short_id("msg"),
                        "role": "assistant",
                        "content": str(result.text or ""),
                        "createdAt": utc_now_iso(),
                        "model": model,
                        "processingDurationMs": max(
                            0,
                            int(
                                round(
                                    (time.monotonic() - started_at) * 1000
                                )
                            ),
                        ),
                        "shellWake": not agent_originated and not media_wake,
                        "mediaWake": media_wake,
                        "wakeId": wake_id,
                    }
                    if any(result.usage.values()):
                        assistant["usage"] = dict(result.usage)
                    if result.model_identity:
                        assistant["modelIdentity"] = dict(result.model_identity)
                    if result.generation_duration_ms:
                        assistant["modelGenerationDurationMs"] = round(
                            result.generation_duration_ms,
                            3,
                        )
                    if result.output_tokens_per_second:
                        assistant["outputTokensPerSecond"] = round(
                            result.output_tokens_per_second,
                            3,
                        )
                    additions.append(assistant)
                    fresh.pop("pendingQuestion", None)
                    fresh["status"] = "idle"
                    if agent_originated:
                        fresh["completedTurnCount"] = (
                            completed_turn_count(fresh) + 1
                        )
                    run.outcome = {
                        "kind": "reply",
                        "payload": {
                            "assistantMessage": assistant,
                            "assistantMessages": additions,
                        },
                    }
                merge_chat_messages_chronologically(fresh, additions)
                if isinstance(result.active_plan, Mapping):
                    fresh["activePlan"] = copy.deepcopy(dict(result.active_plan))
                fresh["lastModel"] = model
                fresh["updatedAt"] = utc_now_iso()
                await asyncio.to_thread(
                    self.repository.write_one,
                    fresh,
                    base_chat=fresh_base,
                )

                if result.status == "awaiting_user":
                    event: dict[str, Any] = {
                        "type": "awaiting_user",
                        "pendingQuestion": (run.outcome or {}).get("pending"),
                        "assistantMessages": [
                            public_message(item) for item in additions
                        ],
                    }
                else:
                    event = {
                        "type": "saved",
                        "assistantMessage": public_message(additions[-1]),
                        "assistantMessages": [
                            public_message(item) for item in additions
                        ],
                    }
                if user_entry is not None:
                    event["userMessage"] = public_message(user_entry)
                await run.publish(event)
                try:
                    language = app_language()
                    chat_title = fresh.get('title') or localized(
                        "New chat", "新对话", language=language
                    )
                    append_notification(
                        title=localized(
                            "Background work resumed",
                            "后台工作已接续",
                            language=language,
                        ),
                        body=localized(
                            'The Agent resumed work in "{title}".',
                            'Agent 已在「{title}」中继续处理。',
                            language=language,
                            title=chat_title,
                        ),
                        tab="mention",
                        project_ref=project_id,
                        source=conversation_source,
                        link_label=str(fresh.get("title") or ""),
                        meta={"chatId": chat_id, "wakeId": wake_id},
                        language=language,
                    )
                except Exception:
                    logger.debug(
                        "Background continuation notification failed",
                        exc_info=True,
                    )
                await self.finalize_workspace_changes(
                    chat_id=chat_id,
                    run_id=run.run_id,
                    workspace_dir=workspace_dir,
                    before=before,
                    status=(
                        "awaiting_user"
                        if result.status == "awaiting_user"
                        else "completed"
                    ),
                    run=run,
                )
            except asyncio.CancelledError:
                await self.finalize_workspace_changes(
                    chat_id=chat_id,
                    run_id=run.run_id,
                    workspace_dir=workspace_dir,
                    before=before,
                    status="cancelled",
                    run=run,
                )
                raise
            except Exception as exc:
                logger.exception(
                    "Background conversation continuation failed for %s",
                    chat_id,
                )
                self.settle_chat_running_status(chat_id)
                run.outcome = {"kind": "error", "exc": exc}
                await run.publish(
                    {
                        "type": "error",
                        "error": "background_chat_run_failed",
                        "code": "background_chat_run_failed",
                        "message": localized(
                            "Background conversation run failed.",
                            "后台对话运行失败。",
                            language=run_language,
                        ),
                    }
                )
                await self.finalize_workspace_changes(
                    chat_id=chat_id,
                    run_id=run.run_id,
                    workspace_dir=workspace_dir,
                    before=before,
                    status="error",
                    run=run,
                )

        ack: dict[str, Any] = {
            "type": "ack",
            "chatId": chat_id,
            "shellWake": not agent_originated and not media_wake,
            "mediaWake": media_wake,
            "agentOriginated": agent_originated,
        }
        if user_entry is not None:
            ack["userMessage"] = public_message(user_entry)
        _run, is_new = self.run_manager.start_or_get(
            chat_id,
            ack,
            runner,
            stream=True,
        )
        if not is_new:
            if user_entry is not None:

                def rollback(fresh: dict[str, Any]) -> None:
                    fresh["messages"] = [
                        item
                        for item in fresh.get("messages") or ()
                        if not isinstance(item, Mapping)
                        or str(item.get("id") or "")
                        != str(user_entry.get("id") or "")
                    ]

                await asyncio.to_thread(
                    self.repository.mutate_one,
                    chat_id,
                    rollback,
                )
            return "busy"
        return "started"

    async def dispatch_media_wake_run(
        self,
        wake: dict[str, Any],
        *,
        bot: Any,
        db_path: str,
    ) -> str:
        return await self.dispatch_shell_wake_run(
            {**dict(wake or {}), "source": "media_job"},
            bot=bot,
            db_path=db_path,
        )

    async def terminate_chat_agents(
        self,
        chat_ids: list[str] | set[str] | tuple[str, ...],
    ) -> None:
        for chat_id in dict.fromkeys(
            str(item or "").strip() for item in chat_ids
        ):
            if not chat_id:
                continue
            await self.run_manager.terminate(
                chat_id,
                termination_reason="chat_deleted",
            )
            await asyncio.to_thread(
                self.run_manager.conversation_runtime.delete_context,
                chat_id,
            )


__all__ = [
    "ChatService",
    "get_chat_run_manager",
    "settle_chat_running_status",
    "shutdown_chat_services",
]
