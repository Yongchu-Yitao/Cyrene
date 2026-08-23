"""Explicit application-service seam used by Workbench chat HTTP routes."""

from __future__ import annotations

from typing import Any, cast

from cyrene.agent_runtime import builtin as agent_runtime_builtin
from cyrene.workbench import chat as _legacy
from cyrene.workbench.chat_dto import (
    ChatContextDTO,
    ChatCreateDTO,
    ChatDetailDTO,
    ChatMessageDTO,
    ChatSummaryDTO,
)
from cyrene.workbench.chat_repository import ChatRepository
from cyrene.workbench.chat_runs import ChatRunManager


def get_chat_run_manager() -> ChatRunManager:
    """Return the process-wide durable run manager through a public seam."""
    return _legacy._CHAT_RUN_MANAGER


def settle_chat_running_status(chat_id: str) -> None:
    """Reconcile a durable chat record after an interrupt or lost stream."""
    _legacy._settle_chat_running_status(chat_id)


class _ChatRunManagerProxy:
    """Resolve the legacy process-wide manager at each operation.

    Some embedders and compatibility tests replace the manager after route
    registration.  Keeping the proxy stable lets routes retain an explicit
    service dependency without freezing the replaceable process singleton.
    """

    def __getattr__(self, name: str) -> Any:
        return getattr(get_chat_run_manager(), name)


_CHAT_RUN_MANAGER_PROXY = _ChatRunManagerProxy()


class ChatService:
    """Application operations required by HTTP adapters.

    This class is deliberately explicit: adding a new route dependency now
    requires adding a named method here instead of silently copying every
    private symbol from ``cyrene.workbench.chat`` into a route module.
    """

    def __init__(self, db_path: str, repository: ChatRepository | None = None):
        self.db_path = str(db_path)
        self.repository = repository or ChatRepository()
        self.repository.configure(self.db_path)
        self.run_manager.configure(self.db_path)

    @property
    def run_manager(self) -> _ChatRunManagerProxy:
        return _CHAT_RUN_MANAGER_PROXY

    @property
    def agent_runtime_builtin(self):
        return agent_runtime_builtin

    def new_chat(self, request: ChatCreateDTO) -> ChatDetailDTO:
        return cast(
            ChatDetailDTO,
            _legacy._new_chat(
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
        request = ChatCreateDTO(
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
        return self.new_chat(request)

    def chat_context_payload(self, *args: Any, **kwargs: Any) -> ChatContextDTO:
        return cast(ChatContextDTO, _legacy._chat_context_payload(*args, **kwargs))

    def public_chat_light(self, chat: dict[str, Any]) -> ChatSummaryDTO:
        return cast(ChatSummaryDTO, _legacy._public_chat_light(chat))

    def public_chat_full(self, chat: dict[str, Any]) -> ChatDetailDTO:
        return cast(ChatDetailDTO, _legacy._public_chat_full(chat))

    def public_message(self, message: dict[str, Any]) -> ChatMessageDTO:
        return cast(ChatMessageDTO, _legacy._public_message(message))

    async def capture_workspace_changes_baseline(self, *args: Any, **kwargs: Any):
        return await _legacy._capture_workspace_changes_baseline(*args, **kwargs)

    async def run_external_agent_turn(self, *args: Any, **kwargs: Any):
        from cyrene.agent_runtime import run_external_agent_turn

        return await run_external_agent_turn(*args, **kwargs)

    def prewarm_workspace_changes(self, *args: Any, **kwargs: Any) -> None:
        _legacy.prewarm_workspace_changes(*args, **kwargs)

    def chat_preview(self, *args: Any, **kwargs: Any):
        return _legacy._chat_preview(*args, **kwargs)

    def chat_soul_active(self, *args: Any, **kwargs: Any):
        return _legacy._chat_soul_active(*args, **kwargs)

    def chat_workspace_active(self, *args: Any, **kwargs: Any):
        return _legacy._chat_workspace_active(*args, **kwargs)

    def clear_fork_metadata(self, *args: Any, **kwargs: Any):
        return _legacy._clear_fork_metadata(*args, **kwargs)

    def coerce_brief_acceptance(self, *args: Any, **kwargs: Any):
        return _legacy._coerce_brief_acceptance(*args, **kwargs)

    def coerce_brief_constraints(self, *args: Any, **kwargs: Any):
        return _legacy._coerce_brief_constraints(*args, **kwargs)

    def completed_turn_count(self, *args: Any, **kwargs: Any):
        return _legacy._completed_turn_count(*args, **kwargs)

    def context_segment_tokens(self, *args: Any, **kwargs: Any):
        return _legacy._context_segment_tokens(*args, **kwargs)

    async def compact_session(self, *args: Any, **kwargs: Any):
        from cyrene import agent

        return await agent.compact_session_if_needed(*args, **kwargs)

    def extract_exchange_timeline(self, *args: Any, **kwargs: Any):
        return _legacy._extract_exchange_timeline(*args, **kwargs)

    async def finalize_workspace_changes(self, *args: Any, **kwargs: Any):
        return await _legacy._finalize_workspace_changes(*args, **kwargs)

    def last_exchange_model(self, *args: Any, **kwargs: Any):
        return _legacy._last_exchange_model(*args, **kwargs)

    def legacy_chats(self, *args: Any, **kwargs: Any):
        return _legacy._legacy_chats(*args, **kwargs)

    def mark_user_activity(self, *args: Any, **kwargs: Any):
        return _legacy._mark_user_activity(*args, **kwargs)

    def merge_chat_messages_chronologically(self, *args: Any, **kwargs: Any):
        return _legacy._merge_chat_messages_chronologically(*args, **kwargs)

    def next_completed_turn_count(self, *args: Any, **kwargs: Any):
        return _legacy._next_completed_turn_count(*args, **kwargs)

    def normalize_workspace_override(self, *args: Any, **kwargs: Any):
        return _legacy._normalize_workspace_override(*args, **kwargs)

    def pending_question_message(self, *args: Any, **kwargs: Any):
        return _legacy._pending_question_message(*args, **kwargs)

    def prune_orphaned_fork_metadata(self, *args: Any, **kwargs: Any):
        return _legacy._prune_orphaned_fork_metadata(*args, **kwargs)

    async def publish_live_exchange_segments_loop(self, *args: Any, **kwargs: Any):
        return await _legacy._publish_live_exchange_segments_loop(*args, **kwargs)

    def record_chat_run_outcome(self, *args: Any, **kwargs: Any):
        return _legacy._record_chat_run_outcome(*args, **kwargs)

    def remove_retry_replaced_messages(self, *args: Any, **kwargs: Any):
        return _legacy._remove_retry_replaced_messages(*args, **kwargs)

    def resolve_chat_workspace_dir(self, *args: Any, **kwargs: Any):
        return _legacy._resolve_chat_workspace_dir(*args, **kwargs)

    def sanitize_durable_traces(self, *args: Any, **kwargs: Any):
        return _legacy._sanitize_durable_traces(*args, **kwargs)

    def session_state_messages(self, *args: Any, **kwargs: Any):
        return _legacy._session_state_messages(*args, **kwargs)

    def settle_chat_running_status(self, *args: Any, **kwargs: Any):
        return _legacy._settle_chat_running_status(*args, **kwargs)

    def short_id(self, *args: Any, **kwargs: Any):
        return _legacy._short_id(*args, **kwargs)

    def side_agent_parent_transcript(self, *args: Any, **kwargs: Any):
        return _legacy._side_agent_parent_transcript(*args, **kwargs)

    def stash_chat_pending_for(self, *args: Any, **kwargs: Any):
        return _legacy._stash_chat_pending_for(*args, **kwargs)

    async def summarize_chat_to_brief(self, *args: Any, **kwargs: Any):
        return await _legacy._summarize_chat_to_brief(*args, **kwargs)

    def sync_chat_generated_files(self, *args: Any, **kwargs: Any):
        return _legacy._sync_chat_generated_files(*args, **kwargs)

    def truncate_state_file_at_user_ordinal(self, *args: Any, **kwargs: Any):
        return _legacy._truncate_state_file_at_user_ordinal(*args, **kwargs)

    def truncate_state_for_retry(self, *args: Any, **kwargs: Any):
        return _legacy._truncate_state_for_retry(*args, **kwargs)

    def utc_now_iso(self, *args: Any, **kwargs: Any):
        return _legacy._utc_now_iso(*args, **kwargs)

    def chat_run_error_message(self, *args: Any, **kwargs: Any):
        return _legacy._workbench_chat_run_error_message(*args, **kwargs)

    def chat_error_metadata(self, *args: Any, **kwargs: Any):
        return _legacy._workbench_chat_error_metadata(*args, **kwargs)

    def subagent_payload(self, *args: Any, **kwargs: Any):
        return _legacy._workbench_subagent_payload(*args, **kwargs)

    async def generate_chat_group_metadata(self, *args: Any, **kwargs: Any):
        return await _legacy.generate_chat_group_metadata(*args, **kwargs)

    def set_chat_external_session_id(self, *args: Any, **kwargs: Any):
        return _legacy.set_chat_external_session_id(*args, **kwargs)

    def update_chat_agent_context_report(self, *args: Any, **kwargs: Any):
        return _legacy.update_chat_agent_context_report(*args, **kwargs)

    def complete_chat_plan(self, *args: Any, **kwargs: Any):
        return _legacy.complete_chat_plan(*args, **kwargs)

    def disable_button_block(self, *args: Any, **kwargs: Any):
        return _legacy.disable_button_block(*args, **kwargs)

    async def dispatch_shell_wake_run(self, *args: Any, **kwargs: Any):
        return await _legacy.dispatch_shell_wake_run(*args, **kwargs)

    def has_button_block(self, *args: Any, **kwargs: Any):
        return _legacy.has_button_block(*args, **kwargs)

    async def terminate_chat_agents(self, *args: Any, **kwargs: Any):
        return await _legacy.terminate_chat_agents(*args, **kwargs)


__all__ = ["ChatService", "get_chat_run_manager", "settle_chat_running_status"]
