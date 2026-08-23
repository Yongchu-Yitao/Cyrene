"""Concrete composition for the Workbench project application service."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from cyrene.agent import clear_session_id, interrupt_active_run
from cyrene.config import WORKSPACE_DIR, cyrene_dir, get_knowledge_db_path
from cyrene.runtime.data_reset import remove_path
from cyrene.runtime.persistence.scheduler import SchedulerRepository
from cyrene.workbench.chat import remove_project_chats
from cyrene.workbench.chat_repository import ChatRepository
from cyrene.workbench.memory import delete_workspace_memory
from cyrene.workbench.project_memory_prompt import (
    cancel_project_jobs,
    delete_project_memory,
)
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
    validate_workspace: Callable[..., Path],
) -> ProjectApplicationService:
    """Bind project ports to their concrete application owners."""
    chats = ChatRepository()
    chats.configure(db_path)
    scheduler = SchedulerRepository(db_path)

    def list_project_chat_ids(project_id: str) -> list[str]:
        return [
            str(item.get("id") or "")
            for item in chats.read().get("chats") or []
            if isinstance(item, dict)
            and str(item.get("projectId") or "") == project_id
            and str(item.get("id") or "")
        ]

    repository = ProjectRepository(
        read_store=dependencies.read_store,
        read_store_lightweight=dependencies.read_store_lightweight,
        write_store=dependencies.write_store,
        find_project=dependencies.find_project,
        find_session=dependencies.find_session,
    )
    lifecycle = ProjectLifecyclePort(
        legacy_data_key=dependencies.legacy_data_key,
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
        project_memory_key=dependencies.project_memory_key,
        notify=dependencies.append_notification,
        list_notifications=dependencies.list_notifications,
        mark_notifications_read=dependencies.mark_notifications_read,
    )
    return ProjectApplicationService(
        repository,
        lifecycle=lifecycle,
        agent_runs=AgentRunProjectPort(
            interrupt=interrupt_active_run,
            clear_session=clear_session_id,
        ),
        chats=ChatProjectPort(
            list_project_chat_ids=list_project_chat_ids,
            remove_project=remove_project_chats,
        ),
        knowledge=KnowledgeProjectPort(
            delete_database=lambda workspace: remove_path(
                get_knowledge_db_path(workspace)
            ),
        ),
        memory=MemoryProjectPort(
            delete_workspace=delete_workspace_memory,
            cancel_jobs=cancel_project_jobs,
            delete_project=delete_project_memory,
        ),
        schedules=ScheduleProjectPort(
            delete_project_tasks=scheduler.delete_project,
        ),
        lightweight_store=dependencies.lightweight_store,
        persist_selection=dependencies.persist_selection,
    )


__all__ = ["build_project_application_service"]
