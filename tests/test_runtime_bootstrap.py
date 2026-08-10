from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.asyncio
async def test_initialize_runtime_owns_shared_host_setup(monkeypatch, tmp_path):
    import cyrene.learning as learning
    from cyrene.observability import debug
    from cyrene.runtime import bootstrap

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
    ensure_soul = Mock()
    ensure_inbox = Mock()
    init_short_term = Mock()
    clean_temp = Mock()
    enable_events = Mock()
    init_learning = AsyncMock()
    monkeypatch.setattr(bootstrap, "init_db", init_db)
    monkeypatch.setattr(bootstrap, "ensure_soul", ensure_soul)
    monkeypatch.setattr(bootstrap, "ensure_inbox", ensure_inbox)
    monkeypatch.setattr(bootstrap, "init_short_term", init_short_term)
    monkeypatch.setattr(bootstrap, "cleanup_temporary_artifacts", clean_temp)
    monkeypatch.setattr(debug, "enable_event_bus", enable_events)
    monkeypatch.setattr(learning, "init", init_learning)

    await bootstrap.initialize_runtime(
        events=True,
        learning=True,
        include_temp=True,
        clean_temp=True,
    )

    assert all(path.is_dir() for path in (workspace, store, data, inbox, temp))
    init_db.assert_awaited_once_with(str(store / "cyrene.db"))
    ensure_soul.assert_called_once_with()
    ensure_inbox.assert_called_once_with("cyrene")
    init_short_term.assert_called_once_with(data)
    clean_temp.assert_called_once_with(temp)
    enable_events.assert_called_once_with()
    init_learning.assert_awaited_once_with(data, workspace)


@pytest.mark.asyncio
async def test_external_services_share_one_startup_policy(monkeypatch):
    from cyrene.runtime import bootstrap
    from cyrene.tooling.backends import mcp_manager, searxng_manager

    start_search = AsyncMock(return_value="http://127.0.0.1:8888")
    start_mcp = AsyncMock()
    monkeypatch.setattr(bootstrap, "SEARXNG_AUTO_START", True)
    monkeypatch.setattr(searxng_manager, "start_searxng", start_search)
    monkeypatch.setattr(mcp_manager, "start_mcp", start_mcp)

    await bootstrap.start_external_services()

    start_search.assert_awaited_once_with(
        bootstrap.SEARXNG_PORT,
        bootstrap.SEARXNG_HOST,
    )
    start_mcp.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_initialize_runtime_is_idempotent_for_one_context(monkeypatch, tmp_path):
    from cyrene.runtime import bootstrap

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
    ensure_soul = Mock()
    ensure_inbox = Mock()
    init_short_term = Mock()
    monkeypatch.setattr(bootstrap, "init_db", init_db)
    monkeypatch.setattr(bootstrap, "ensure_soul", ensure_soul)
    monkeypatch.setattr(bootstrap, "ensure_inbox", ensure_inbox)
    monkeypatch.setattr(bootstrap, "init_short_term", init_short_term)

    first = await bootstrap.initialize_runtime(context=context)
    second = await bootstrap.initialize_runtime(context=context)

    assert first is context
    assert second is context
    assert context.initialized_components == {"core"}
    init_db.assert_awaited_once_with(str(context.database_path))
    ensure_soul.assert_called_once_with()
    ensure_inbox.assert_called_once_with("cyrene")
    init_short_term.assert_called_once_with(context.paths.data)


@pytest.mark.asyncio
async def test_application_lifecycle_owns_tasks_managers_and_idempotent_shutdown(
    monkeypatch,
    tmp_path,
):
    from cyrene.runtime import bootstrap
    from cyrene.runtime.application import ApplicationLifecycle
    from cyrene.runtime import lifecycle as runtime_lifecycle

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

    async def stop_external_services_async(**_kwargs) -> None:
        events.append("external")

    def close_manager() -> None:
        events.append("manager")

    monkeypatch.setattr(
        runtime_lifecycle,
        "shutdown_background_work",
        shutdown_background_work,
    )
    monkeypatch.setattr(
        bootstrap,
        "stop_external_services_async",
        stop_external_services_async,
    )

    application.create_task(pending(), label="test pending task")
    application.register_manager("scheduler", object(), close=close_manager)
    await asyncio.sleep(0)
    await application.shutdown()
    await application.shutdown()

    assert events == ["background", "task", "external", "manager"]
    assert context.closed is True
    assert context.accepting_work is False
    assert context.background_tasks == set()
