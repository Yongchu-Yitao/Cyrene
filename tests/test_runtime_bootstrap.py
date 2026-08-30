from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.asyncio
async def test_initialize_runtime_owns_shared_host_setup(monkeypatch, tmp_path):
    from cyrene.observability import debug
    from cyrene.platform import bootstrap

    workspace = tmp_path / "workspace"
    store = tmp_path / "store"
    data = tmp_path / "data"
    inbox = data / "inbox"
    temp = tmp_path / "temp"
    for name, value in {
        "WORKSPACE_DIR": workspace,
        "STORE_DIR": store,
        "DATA_DIR": data,
        "INBOX_DIR": inbox,
        "TEMP_DIR": temp,
        "DB_PATH": store / "cyrene.db",
    }.items():
        monkeypatch.setattr(bootstrap, name, value)

    init_db = AsyncMock()
    ensure_inbox = Mock()
    clean_temp = Mock()
    enable_events = Mock()
    monkeypatch.setattr(bootstrap, "init_db", init_db)
    monkeypatch.setattr(bootstrap, "ensure_inbox", ensure_inbox)
    monkeypatch.setattr(bootstrap, "cleanup_temporary_artifacts", clean_temp)
    monkeypatch.setattr(debug, "enable_event_bus", enable_events)

    await bootstrap.initialize_runtime(
        events=True,
        include_temp=True,
        clean_temp=True,
    )

    assert all(path.is_dir() for path in (workspace, store, data, inbox, temp))
    init_db.assert_awaited_once_with(str(store / "cyrene.db"))
    ensure_inbox.assert_called_once_with("cyrene")
    clean_temp.assert_called_once_with(temp)
    enable_events.assert_called_once_with()


@pytest.mark.asyncio
async def test_initialize_runtime_is_idempotent_for_one_context(monkeypatch, tmp_path):
    from cyrene.platform import bootstrap

    context = bootstrap.create_runtime_context(host_mode="test")
    context.paths = type(context.paths)(
        install_resources=tmp_path,
        user_data=tmp_path / "user-data",
        runtime_base=tmp_path,
        workspace=tmp_path / "workspace",
        store=tmp_path / "store",
        data=tmp_path / "data",
        cache=tmp_path / "cache",
        temp=tmp_path / "temp",
    )
    context.database_path = tmp_path / "store" / "cyrene.db"
    context.inbox_path = tmp_path / "data" / "inbox"

    init_db = AsyncMock()
    ensure_inbox = Mock()
    monkeypatch.setattr(bootstrap, "init_db", init_db)
    monkeypatch.setattr(bootstrap, "ensure_inbox", ensure_inbox)

    first = await bootstrap.initialize_runtime(context=context)
    second = await bootstrap.initialize_runtime(context=context)

    assert first is context
    assert second is context
    assert context.initialized_components == {"core"}
    init_db.assert_awaited_once_with(str(context.database_path))
    ensure_inbox.assert_called_once_with("cyrene")


@pytest.mark.asyncio
async def test_application_lifecycle_owns_tasks_managers_and_idempotent_shutdown(
    monkeypatch,
    tmp_path,
):
    from cyrene.platform import bootstrap
    from cyrene.platform.application import ApplicationLifecycle
    from cyrene.platform import lifecycle as runtime_lifecycle

    context = bootstrap.create_runtime_context(host_mode="test")
    context.paths = type(context.paths)(
        install_resources=tmp_path,
        user_data=tmp_path / "user-data",
        runtime_base=tmp_path,
        workspace=tmp_path / "workspace",
        store=tmp_path / "store",
        data=tmp_path / "data",
        cache=tmp_path / "cache",
        temp=tmp_path / "temp",
    )
    context.database_path = tmp_path / "store" / "cyrene.db"
    context.inbox_path = tmp_path / "data" / "inbox"
    application = ApplicationLifecycle(context)
    events: list[str] = []

    async def pending() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            events.append("task")

    async def shutdown_background_work() -> None:
        events.append("background")

    def close_manager() -> None:
        events.append("manager")

    monkeypatch.setattr(
        runtime_lifecycle,
        "shutdown_background_work",
        shutdown_background_work,
    )
    application.create_task(pending(), label="test pending task")
    application.register_manager("scheduler", object(), close=close_manager)
    await asyncio.sleep(0)
    await application.shutdown()
    await application.shutdown()

    assert events == ["background", "task", "manager"]
    assert context.closed is True
    assert context.accepting_work is False
    assert context.background_tasks == set()
