"""Concrete composition for the Workbench project application service."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

from agent.workbench import ConversationRuntime
from agent.workbench.task_runtime import TaskAgentRuntime
from cyrene.config import WORKSPACE_DIR, cyrene_dir
from cyrene.runtime.run_coordinator import run_coordinator_for
from cyrene.workbench import task_runs
from cyrene.workbench.chat_repository import ChatRepository
from cyrene.workbench.project_services import (
    AgentRunProjectPort,
    ChatProjectPort,
    KnowledgeProjectPort,
    MemoryProjectPort,
    ProjectApplicationService,
    ProjectLifecyclePort,
    ProjectRepository,
    ProjectRouteDependencies,
    ScheduleProjectPort,
)


def build_project_application_service(
    db_path: str,
    dependencies: ProjectRouteDependencies,
    *,
    agent_runtime: TaskAgentRuntime,
    validate_workspace: Callable[..., Path],
    knowledge_service: Any = None,
    memory_service: Any = None,
    schedule_service: Any = None,
) -> ProjectApplicationService:
    """Bind project ports to their concrete application owners."""
    chats = ChatRepository()
    chats.configure(db_path)
    conversations = ConversationRuntime(db_path)
    coordinator = run_coordinator_for(db_path)

    async def preserve_unloaded_schedule_data(_project_id: str) -> int:
        # Schedule data belongs to the Plugin. If that Plugin did not attach,
        # the core project service must not reach into its persistence tables.
        return 0

    def list_project_chat_ids(project_id: str) -> list[str]:
        return [
            str(item.get("id") or "")
            for item in chats.read_summaries().get("chats") or []
            if isinstance(item, dict)
            and str(item.get("projectId") or "") == project_id
            and str(item.get("id") or "")
        ]

    async def remove_project_chats(project_id: str) -> int:
        chat_ids = list_project_chat_ids(project_id)
        current = asyncio.current_task()
        tasks: list[asyncio.Task[Any]] = []
        for chat_id in chat_ids:
            conversations.request_cancel(chat_id, "project_deleted")
            lease = coordinator.get("conversation", chat_id)
            coordinator.interrupt(
                "conversation",
                chat_id,
                reason="project_deleted",
            )
            if (
                lease is not None
                and lease.task is not None
                and lease.task is not current
            ):
                tasks.append(lease.task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for chat_id in chat_ids:
            conversations.delete_context(chat_id)

        def remove(payload: dict[str, Any]) -> int:
            records = payload.get("chats") or []
            payload["chats"] = [
                item
                for item in records
                if str(item.get("projectId") or "") != project_id
            ]
            return len(records) - len(payload["chats"])

        return int(chats.mutate(remove) or 0)

    def interrupt_task_session(*, session_id: str) -> bool:
        return task_runs.interrupt_task_run(db_path, session_id)

    async def clear_task_session(*, session_id: str) -> bool:
        lease = task_runs.coordinator_for(db_path).get("task", session_id)
        current = asyncio.current_task()
        if (
            lease is not None
            and lease.task is not None
            and lease.task is not current
        ):
            await asyncio.gather(lease.task, return_exceptions=True)
        return await agent_runtime.clear_session(session_id)

    repository = ProjectRepository(
        read_store=dependencies.read_store,
        read_store_lightweight=dependencies.read_store_lightweight,
        write_store=dependencies.write_store,
        find_project=dependencies.find_project,
        find_session=dependencies.find_session,
    )
    lifecycle = ProjectLifecyclePort(
        workspace_root=cyrene_dir(WORKSPACE_DIR) / "projects",
        validate_workspace=validate_workspace,
        get_model=dependencies.get_model,
        safe_data_key=dependencies.safe_data_key,
        short_id=dependencies.short_id,
        utc_now=dependencies.utc_now,
        default_init_form=dependencies.default_init_form,
        default_project=dependencies.default_project,
        follow_up_seed=dependencies.follow_up_seed,
        generate_init_form=dependencies.generate_init_form,
        new_init_session=dependencies.new_init_session,
        new_session=dependencies.new_session,
        project_data_key=dependencies.project_data_key,
        project_resource_key=dependencies.project_resource_key,
        notify=dependencies.append_notification,
        list_notifications=dependencies.list_notifications,
        mark_notifications_read=dependencies.mark_notifications_read,
    )
    return ProjectApplicationService(
        repository,
        lifecycle=lifecycle,
        agent_runs=AgentRunProjectPort(
            interrupt=interrupt_task_session,
            clear_session=clear_task_session,
        ),
        chats=ChatProjectPort(
            list_project_chat_ids=list_project_chat_ids,
            remove_project=remove_project_chats,
        ),
        knowledge=KnowledgeProjectPort(
            delete_database=(
                knowledge_service.delete_workspace
                if knowledge_service is not None
                else lambda _workspace: None
            ),
        ),
        memory=MemoryProjectPort(
            delete_workspace=(
                memory_service.delete_workspace
                if memory_service is not None
                else lambda _workspace: None
            ),
            cancel_jobs=(
                memory_service.cancel_project_jobs
                if memory_service is not None
                else _noop_cancel_memory_jobs
            ),
            delete_project=(
                memory_service.delete_project
                if memory_service is not None
                else lambda _project_id, _chat_ids: None
            ),
        ),
        schedules=ScheduleProjectPort(
            delete_project_tasks=(
                schedule_service.delete_project
                if schedule_service is not None
                else preserve_unloaded_schedule_data
            ),
        ),
        lightweight_store=dependencies.lightweight_store,
        persist_selection=dependencies.persist_selection,
    )


async def _noop_cancel_memory_jobs(_project_id: str) -> None:
    return None


__all__ = ["build_project_application_service"]
