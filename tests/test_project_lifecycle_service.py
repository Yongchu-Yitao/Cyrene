from pathlib import Path

import pytest

from cyrene.workbench.project_services import (
    AgentRunProjectPort,
    ChatProjectPort,
    KnowledgeProjectPort,
    MemoryProjectPort,
    ProjectApplicationService,
    ProjectLifecyclePort,
    ProjectRepository,
    ScheduleProjectPort,
)


@pytest.mark.asyncio
async def test_delete_project_preserves_cross_owner_cleanup_order():
    calls: list[str] = []
    payload = {
        "projects": [
            {
                "id": "project_delete",
                "dataKey": "delete_key",
                "sessions": [{"id": "session_delete"}],
            },
            {"id": "project_keep", "dataKey": "keep_key", "sessions": []},
        ],
        "activeProjectId": "project_delete",
        "activeSessionId": "session_delete",
    }

    def write_store(value, **_kwargs):
        assert value is payload
        calls.append("write_store")

    async def clear_session(*, session_id: str, deleting: bool):
        assert deleting is True
        calls.append(f"clear:{session_id}")

    async def remove_chats(project_id: str) -> int:
        calls.append(f"remove_chats:{project_id}")
        return 1

    async def cancel_jobs(project_id: str) -> None:
        calls.append(f"cancel_jobs:{project_id}")

    async def delete_tasks(data_key: str) -> int:
        calls.append(f"delete_tasks:{data_key}")
        return 2

    async def generate_init(_project, *, lang: str):
        del lang
        return None

    repository = ProjectRepository(
        read_store=lambda: payload,
        read_store_lightweight=lambda: payload,
        write_store=write_store,
        find_project=lambda value, project_id: next(
            (item for item in value["projects"] if item["id"] == project_id),
            None,
        ),
        find_session=lambda _value, _session_id: (None, None),
    )
    lifecycle = ProjectLifecyclePort(
        legacy_data_key="default",
        workspace_root=Path("/workspace/projects"),
        validate_workspace=lambda path, **_kwargs: Path(path),
        get_model=lambda: "model",
        safe_data_key=lambda value: value,
        short_id=lambda prefix: f"{prefix}_id",
        utc_now=lambda: "2026-01-01T00:00:00+00:00",
        default_init_form=lambda _project: {},
        default_project=lambda: {"projects": []},
        follow_up_seed=lambda *_args, **_kwargs: {},
        generate_init_form=generate_init,
        new_init_session=lambda *_args: {},
        new_session=lambda *_args: {},
        project_data_key=lambda project: str(project["dataKey"]),
        project_memory_key=lambda project: str(project["id"]),
        notify=lambda **_kwargs: None,
        list_notifications=lambda **_kwargs: {},
        mark_notifications_read=lambda *_args, **_kwargs: {},
    )
    service = ProjectApplicationService(
        repository,
        lifecycle=lifecycle,
        agent_runs=AgentRunProjectPort(
            interrupt=lambda *, session_id: calls.append(f"interrupt:{session_id}"),
            clear_session=clear_session,
        ),
        chats=ChatProjectPort(
            list_project_chat_ids=lambda project_id: (
                calls.append(f"list_chats:{project_id}") or ["chat_delete"]
            ),
            remove_project=remove_chats,
        ),
        knowledge=KnowledgeProjectPort(
            delete_database=lambda key: calls.append(f"knowledge:{key}"),
        ),
        memory=MemoryProjectPort(
            delete_workspace=lambda key: calls.append(f"workspace_memory:{key}"),
            cancel_jobs=cancel_jobs,
            delete_project=lambda project_id, chat_ids: calls.append(
                f"project_memory:{project_id}:{','.join(chat_ids)}"
            ),
        ),
        schedules=ScheduleProjectPort(delete_project_tasks=delete_tasks),
        lightweight_store=lambda value: value,
        persist_selection=lambda _project, _session: {},
    )

    result = await service.delete("project_delete")

    assert calls == [
        "list_chats:project_delete",
        "interrupt:session_delete",
        "clear:session_delete",
        "remove_chats:project_delete",
        "knowledge:project_delete",
        "workspace_memory:project_delete",
        "cancel_jobs:project_delete",
        "project_memory:project_delete:chat_delete",
        "delete_tasks:delete_key",
        "write_store",
    ]
    assert result["activeProjectId"] == "project_keep"
    assert [item["id"] for item in result["projects"]] == ["project_keep"]
