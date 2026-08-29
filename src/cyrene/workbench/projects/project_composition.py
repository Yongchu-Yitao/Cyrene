"""Concrete composition for the Workbench project application service."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

from cyrene.workbench.core_adapter import ConversationRuntime
from cyrene.workbench.core_adapter.task_runtime import TaskAgentRuntime
from cyrene.config import WORKSPACE_DIR, cyrene_dir
from cyrene.runtime.run_coordinator import run_coordinator_for
from cyrene.workbench.tasks import task_runs
from cyrene.workbench.chat.chat_repository import ChatRepository
from cyrene.workbench.projects.project_services import (
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
) -> ProjectApplicationService:
    """Bind project ports to their concrete application owners."""
    chats = ChatRepository()
    chats.configure(db_path)
    conversations = ConversationRuntime(db_path)
    coordinator = run_coordinator_for(db_path)

    async def delete_project_schedules(project_id: str) -> int:
        # Resolve the Plugin at call time: enable/disable changes must take
        # effect without rebuilding the core project application service.
        from cyrene.core.plugin import application_plugin_service

        active_schedule = application_plugin_service("schedules")
        if active_schedule is None:
            return 0
        return int(await active_schedule.delete_project(project_id))

    def delete_project_knowledge(resource_key: str) -> None:
        from cyrene.core.plugin import application_plugin_service

        service = application_plugin_service("knowledge")
        if service is not None:
            service.delete_workspace(resource_key)

    def delete_memory_workspace(resource_key: str) -> None:
        from cyrene.core.plugin import application_plugin_service

        service = application_plugin_service("memory")
        if service is not None:
            service.delete_workspace(resource_key)

    async def cancel_memory_jobs(project_id: str) -> None:
        from cyrene.core.plugin import application_plugin_service

        service = application_plugin_service("memory")
        if service is not None:
            await service.cancel_project_jobs(project_id)

    def delete_project_memory(project_id: str, chat_ids: list[str]) -> None:
        from cyrene.core.plugin import application_plugin_service

        service = application_plugin_service("memory")
        if service is not None:
            service.delete_project(project_id, chat_ids)

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
            delete_database=delete_project_knowledge,
        ),
        memory=MemoryProjectPort(
            delete_workspace=delete_memory_workspace,
            cancel_jobs=cancel_memory_jobs,
            delete_project=delete_project_memory,
        ),
        schedules=ScheduleProjectPort(
            delete_project_tasks=delete_project_schedules,
        ),
        lightweight_store=dependencies.lightweight_store,
        persist_selection=dependencies.persist_selection,
    )
__all__ = ["build_project_application_service"]
