"""Tests for the Agent package, kept outside the shipped source tree."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agent import AgentSession
from agent.plugin import (
    Plugin,
    PluginApplicationHost,
    PluginContext,
    PluginPack,
    PluginRegistry,
    PluginRuntime,
    resolve_agent_plugin_registry,
)
from agent.plugin.application import set_active_plugin_application_host
from agent.plugin.background import BackgroundPluginHost, background_job_spec
from cyrene.workbench import presentation_runtime
from cyrene.workbench.presentation_service import PresentationQueryService


def _host(tmp_path: Path, registry: PluginRegistry) -> PluginApplicationHost:
    return PluginApplicationHost(
        app=FastAPI(),
        registry=registry,
        bot=None,
        db_path=str(tmp_path / "app.db"),
        data_directory=tmp_path / "data",
        plugin_directory=tmp_path / "plugin_impl",
    )


def test_agent_registry_reuses_matching_application_host(tmp_path):
    registry = PluginRegistry()
    host = _host(tmp_path, registry)
    set_active_plugin_application_host(host)
    try:
        resolved, load_plugins = resolve_agent_plugin_registry(
            tmp_path / "plugin_impl"
        )
    finally:
        set_active_plugin_application_host(None)

    assert resolved is registry
    assert load_plugins is False


def test_background_interval_provider_is_lazy() -> None:
    calls = 0

    def interval() -> int:
        nonlocal calls
        calls += 1
        return 120

    plugin = Plugin(
        name="background.lazy",
        description="lazy interval",
        input_schema={"type": "object", "properties": {}},
        handler=lambda _arguments, _context: None,
        metadata={
            "model_visible": False,
            "background_job": {
                "id": "background-lazy",
                "interval_seconds": interval,
            },
        },
    )

    assert calls == 0
    spec = background_job_spec(plugin)
    assert spec is not None
    assert spec.interval_seconds == 120
    assert calls == 1


def test_application_setup_commits_routes_services_lifecycle_and_search(
    tmp_path,
    monkeypatch,
):
    events: list[str] = []

    async def search(query: str, limit: int) -> list[dict[str, Any]]:
        return [{"id": "one", "type": "demo", "title": query, "limit": limit}]

    def application_setup(context) -> None:
        @context.router.get("/plugin-demo")
        async def plugin_demo():
            return {"ok": True}

        context.provide("demo", object())
        context.provide_search("demo", search)
        context.expose_frontend("demo")
        context.on_startup(lambda: events.append("startup"))
        context.on_shutdown(lambda: events.append("shutdown"))

    registry = PluginRegistry(include_core=False)
    registry.register_pack(
        PluginPack(
            id="demo",
            description="demo",
            plugins=(),
            application_setup=application_setup,
        ),
        source="test",
    )
    host = _host(tmp_path, registry)
    router = APIRouter()
    host.attach(router)

    assert host.attached_packs == ("demo",)
    assert host.service("demo") is not None
    assert host.frontend_modules == ["demo"]
    assert {route.path for route in router.routes} == {"/plugin-demo"}

    async def core_search(query, types, limit, db_path):
        assert query == "needle"
        assert types == set()
        assert limit == 5
        assert db_path == str((tmp_path / "app.db").resolve())
        return {}

    async def ui_data(_timezone, _db_path):
        return {"ok": True}

    monkeypatch.setattr(presentation_runtime, "_search_workbench_items", core_search)
    monkeypatch.setattr(presentation_runtime, "_build_ui_data", ui_data)
    queries = PresentationQueryService(
        db_path=tmp_path / "app.db",
        frontend_modules=host.frontend_modules,
        search_providers=host.search_providers,
    )
    assert queries.search_types == frozenset({"project", "task", "chat", "demo"})

    async def scenario() -> None:
        assert await queries.search_workbench("needle", {"demo"}, 5) == {
            "demo": [{"id": "one", "type": "demo", "title": "needle", "limit": 5}]
        }
        assert await queries.ui_data() == {"ok": True, "pluginModules": ["demo"]}
        await host.startup()
        await host.shutdown()

    asyncio.run(scenario())
    assert events == ["startup", "shutdown"]


def test_required_application_setup_failure_aborts_attachment(tmp_path):
    def broken_setup(_context) -> None:
        raise RuntimeError("composer setup failed")

    registry = PluginRegistry(include_core=False)
    registry.register_pack(
        PluginPack(
            id="required-context-demo",
            description="required context",
            plugins=(),
            application_setup=broken_setup,
            metadata={"required": True},
        ),
        source="test",
    )
    host = _host(tmp_path, registry)

    with pytest.raises(
        RuntimeError,
        match="Required Plugin application pack failed to attach",
    ):
        host.attach(APIRouter())
    assert host.service("composer_context") is None


def test_application_contributions_follow_pack_activation_without_restart(tmp_path):
    events: list[str] = []

    async def search(_query: str, _limit: int) -> list[dict[str, Any]]:
        return []

    def application_setup(context) -> None:
        @context.router.get("/activation-demo")
        async def activation_demo():
            return {"ok": True}

        context.provide("activation_demo", object())
        context.provide_search("activation_demo", search)
        context.expose_frontend("activation_demo")
        context.on_startup(lambda: events.append("startup"))
        context.on_shutdown(lambda: events.append("shutdown"))

    registry = PluginRegistry(include_core=False)
    registry.register_pack(
        PluginPack(
            id="activation_demo",
            description="demo",
            plugins=(),
            application_setup=application_setup,
        ),
        source="test",
    )
    registry.configure_activation(plugins={}, packs={"activation_demo": False})
    host = _host(tmp_path, registry)
    router = APIRouter()
    host.attach(router)
    host.app.include_router(router)

    # Disabled optional application packs are not attached.  Enabling one is
    # an application-composition boundary and requires a restart.
    assert host.attached_packs == ()
    assert host.service("activation_demo") is None
    assert "activation_demo" not in host.active_services
    assert host.frontend_modules == []
    assert host.search_providers == {}
    with TestClient(host.app) as client:
        assert client.get("/activation-demo").status_code == 404

    async def scenario() -> None:
        await host.startup()
        assert events == []
        registry.configure_activation(plugins={}, packs={"activation_demo": True})
        await host.reconcile_activation()
        assert events == []
        assert host.service("activation_demo") is None
        assert "activation_demo" not in host.active_services
        assert host.frontend_modules == []
        assert "activation_demo" not in host.search_providers
        assert host.restart_required_packs == ("activation_demo",)
        registry.configure_activation(plugins={}, packs={"activation_demo": False})
        await host.reconcile_activation()
        assert events == []
        assert host.service("activation_demo") is None
        assert "activation_demo" not in host.active_services
        await host.shutdown()

    asyncio.run(scenario())


def test_removed_application_pack_becomes_unavailable_and_stops_lifecycle(tmp_path):
    events: list[str] = []

    def application_setup(context) -> None:
        @context.router.get("/removed-demo")
        async def removed_demo():
            return {"ok": True}

        context.expose_frontend("removed-demo")
        context.on_startup(lambda: events.append("startup"))
        context.on_shutdown(lambda: events.append("shutdown"))

    registry = PluginRegistry(include_core=False)
    registry.register_pack(
        PluginPack(
            id="removed-demo",
            description="demo",
            plugins=(),
            application_setup=application_setup,
        ),
        source="test",
    )
    host = _host(tmp_path, registry)
    router = APIRouter()
    host.attach(router)
    host.app.include_router(router)

    asyncio.run(host.startup())
    assert events == ["startup"]
    assert registry.unregister_pack("removed-demo") is True
    asyncio.run(host.reconcile_activation())

    assert events == ["startup", "shutdown"]
    assert host.frontend_modules == []
    with TestClient(host.app) as client:
        response = client.get("/removed-demo")
        assert response.status_code == 404
        assert response.json()["detail"] == "Plugin pack is unavailable: removed-demo"


def test_reload_failure_stops_application_pack_and_repair_requires_restart(
    tmp_path,
    monkeypatch,
):
    package = tmp_path / "plugin_impl" / "reload-demo"
    package.mkdir(parents=True)
    initializer = package / "__init__.py"

    def write_pack() -> None:
        initializer.write_text(
            '''\
from agent.plugin import PluginPack

def setup(context):
    events = context.services["events"]
    @context.router.get("/reload-demo")
    async def route():
        return {"ok": True}
    context.on_startup(lambda: events.append("startup"))
    context.on_shutdown(lambda: events.append("shutdown"))

plugin_pack = PluginPack(
    id="reload-demo",
    description="reload demo",
    plugins=(),
    application_setup=setup,
)
''',
            encoding="utf-8",
        )

    write_pack()
    registry = PluginRegistry(include_core=False)
    assert registry.load_directory(package.parent) == ()
    host = _host(tmp_path, registry)
    events: list[str] = []
    host.services["events"] = events
    router = APIRouter()
    host.attach(router)
    host.app.include_router(router)
    monkeypatch.setattr(
        "agent.plugin.application.seed_builtin_plugin_directory",
        lambda _directory: object(),
    )

    asyncio.run(host.startup())
    assert events == ["startup"]
    initializer.write_text("plugin_pack = None\n", encoding="utf-8")
    _seed, failures = asyncio.run(host.reload_user_plugins())

    assert len(failures) == 1
    assert events == ["startup", "shutdown"]
    with TestClient(host.app) as client:
        assert client.get("/reload-demo").status_code == 404

    write_pack()
    _seed, failures = asyncio.run(host.reload_user_plugins())
    assert failures == ()
    assert events == ["startup", "shutdown"]
    assert host.restart_required_packs == ("reload-demo",)
    assert host.pack_operational("reload-demo") is False
    from route.plugins import plugin_registry_status

    status = plugin_registry_status(host)
    assert status["application_restart_required"] is True
    pack_status = next(item for item in status["packs"] if item["id"] == "reload-demo")
    assert pack_status["effective_enabled"] is True
    assert pack_status["operational"] is False
    assert pack_status["restart_required"] is True
    with TestClient(host.app) as client:
        response = client.get("/reload-demo")
        assert response.status_code == 503
        assert "requires an application restart" in response.json()["detail"]


def test_background_host_uses_authoritative_state_and_cancels_inflight_work(
    tmp_path,
    monkeypatch,
):
    started = asyncio.Event()
    restarted = asyncio.Event()
    cancelled = asyncio.Event()
    invocations = 0

    async def handler(_arguments, _context):
        nonlocal invocations
        invocations += 1
        (started if invocations == 1 else restarted).set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    def application_setup(_context) -> None:
        return None

    registry = PluginRegistry(include_core=False)
    registry.register_pack(
        PluginPack(
            id="background-demo",
            description="demo",
            plugins=(
                Plugin(
                    name="background.demo",
                    description="demo",
                    input_schema={"type": "object", "properties": {}},
                    handler=handler,
                    metadata={
                        "model_visible": False,
                        "background_job": {
                            "id": "background-demo-job",
                            "interval_seconds": 60,
                        },
                    },
                ),
            ),
            application_setup=application_setup,
        ),
        source="test",
    )
    host = _host(tmp_path, registry)
    host.attach(APIRouter())

    class Scheduler:
        def __init__(self):
            self.jobs = {}

        def add_job(self, function, _trigger, *, id, args=None, **_options):
            self.jobs[id] = (function, list(args or ()))

        def remove_job(self, job_id):
            if job_id not in self.jobs:
                raise LookupError(job_id)
            self.jobs.pop(job_id)

    scheduler = Scheduler()
    plugin_directory = tmp_path / "background_plugins"
    plugin_directory.mkdir()
    monkeypatch.setattr(
        "agent.plugin.background.seed_builtin_plugin_directory",
        lambda _directory: None,
    )
    background = BackgroundPluginHost(
        scheduler,  # type: ignore[arg-type]
        plugin_directory=plugin_directory,
    )

    async def scenario() -> None:
        set_active_plugin_application_host(host)
        try:
            await host.startup()
            background.attach()
            function, arguments = scheduler.jobs["background-demo-job"]
            task = asyncio.create_task(function(*arguments))
            await asyncio.wait_for(started.wait(), timeout=1)

            registry.configure_activation(
                plugins={}, packs={"background-demo": False}
            )
            await host.reconcile_activation()

            await asyncio.wait_for(cancelled.wait(), timeout=1)
            assert task.cancelled()
            assert "background-demo-job" not in scheduler.jobs

            registry.configure_activation(
                plugins={}, packs={"background-demo": True}
            )
            await host.reconcile_activation()
            function, arguments = scheduler.jobs["background-demo-job"]
            restarted_task = asyncio.create_task(function(*arguments))
            await asyncio.wait_for(restarted.wait(), timeout=1)
            await background.shutdown()
            assert restarted_task.cancelled()
            assert "background-demo-job" not in scheduler.jobs
        finally:
            await background.shutdown()
            await host.shutdown()
            set_active_plugin_application_host(None)

    asyncio.run(scenario())


def test_model_pack_owns_settings_routes_services_and_frontend_marker(tmp_path):
    from agent.plugin.plugin_impl.cyrene_model import plugin_pack

    registry = PluginRegistry(include_core=False)
    registry.register_pack(plugin_pack, source="test")
    host = _host(tmp_path, registry)
    router = APIRouter()
    host.attach(router)
    host.app.include_router(router)

    route_paths = {route.path for route in router.routes}
    assert "/api/settings/model-config" in route_paths
    assert "/api/settings/model-config/provider-usage" in route_paths
    assert {
        "/api/settings/openai-oauth",
        "/api/settings/openai-oauth/login",
        "/api/settings/openai-oauth/logout",
        "/api/settings/openai-oauth/cli",
        "/api/settings/openai-oauth/cli/download",
        "/api/settings/openai-oauth/limits",
    } <= route_paths
    assert host.service("model_configuration") is not None
    assert host.service("model_probe") is not None
    assert host.frontend_modules == ["model"]

    registry.set_pack_enabled("cyrene_model", False)
    assert host.service("model_configuration") is None
    assert host.service("model_probe") is None
    assert host.frontend_modules == []
    with TestClient(host.app) as client:
        assert client.get("/api/settings/openai-oauth").status_code == 404
        assert client.get("/api/settings/openai-oauth/cli").status_code == 404


def test_code_pack_gates_http_websocket_frontend_and_wake_lifecycle(
    tmp_path,
    monkeypatch,
):
    from agent.plugin.plugin_impl.cyrene_code import plugin_pack
    from cyrene.runtime import shell_wake
    from agent.plugin.plugin_impl.cyrene_code.terminal import client as terminal_client

    class FakeConnection:
        async def read(self):
            await asyncio.Event().wait()

        async def send(self, _message):
            return None

        async def close(self):
            return None

    class FakeTerminalClient:
        async def list(self, project_id, *, owner_chat_id=None):
            return {"terminals": [], "activeTerminalId": None}

        async def connect_terminal(self, terminal_id, cursor):
            return FakeConnection(), {
                "type": "snapshot",
                "terminal": {"id": terminal_id, "status": "running"},
                "cursor": cursor,
            }

    class FakeWakeBridge:
        def __init__(self):
            self.started = 0
            self.stopped = 0

        async def start_daemon_bridge(self):
            self.started += 1

        async def stop_daemon_bridge(self):
            self.stopped += 1

    fake_client = FakeTerminalClient()
    wake_bridge = FakeWakeBridge()
    monkeypatch.setattr(
        terminal_client,
        "get_terminal_daemon_client",
        lambda: fake_client,
    )
    monkeypatch.setattr(shell_wake, "get_shell_wake_service", lambda: wake_bridge)

    registry = PluginRegistry(include_core=False)
    registry.register_pack(plugin_pack, source="test")
    registry.configure_activation(plugins={}, packs={"cyrene_code": False})
    host = _host(tmp_path, registry)
    router = APIRouter()
    host.attach(router)
    host.app.include_router(router)
    asyncio.run(host.startup())

    with TestClient(host.app) as client:
        assert client.get("/api/terminals?projectId=project-one").status_code == 404
        assert client.post(
            "/api/code/format",
            json={"code": "let x = 1;", "language": "javascript"},
        ).status_code == 404
        with pytest.raises(WebSocketDisconnect) as disabled_socket:
            with client.websocket_connect("/ws/terminals/terminal-one"):
                pass
        assert disabled_socket.value.code == 1000
        assert host.frontend_modules == []
        assert wake_bridge.started == 0

        registry.set_pack_enabled("cyrene_code", True)
        asyncio.run(host.reconcile_activation())
        # A pack disabled at composition time cannot be attached by a live
        # activation toggle; the next application composition is required.
        assert wake_bridge.started == 0
        assert host.frontend_modules == []
        assert client.get("/api/terminals?projectId=project-one").status_code == 404
        assert host.restart_required_packs == ("cyrene_code",)

        registry.set_pack_enabled("cyrene_code", False)
        asyncio.run(host.reconcile_activation())
        assert wake_bridge.stopped == 0
        assert host.frontend_modules == []
        assert client.get("/api/terminals?projectId=project-one").status_code == 404


def test_extensions_pack_gates_agent_routes_services_and_frontend_markers(tmp_path):
    from agent.plugin.plugin_impl.cyrene_extensions import plugin_pack

    registry = PluginRegistry(include_core=False)
    registry.register_pack(plugin_pack, source="test")
    registry.configure_activation(plugins={}, packs={"cyrene_extensions": False})
    host = _host(tmp_path, registry)
    router = APIRouter()
    host.attach(router)
    host.app.include_router(router)
    asyncio.run(host.startup())

    with TestClient(host.app) as client:
        assert client.get("/api/agents").status_code == 404
        assert host.service("extensions") is None
        assert host.frontend_modules == []

        registry.set_pack_enabled("cyrene_extensions", True)
        asyncio.run(host.reconcile_activation())
        assert client.get("/api/agents").status_code == 404
        assert host.service("extensions") is None
        assert host.frontend_modules == []
        assert host.restart_required_packs == ("cyrene_extensions",)

        registry.set_pack_enabled("cyrene_extensions", False)
        asyncio.run(host.reconcile_activation())
        assert client.get("/api/agents").status_code == 404
        assert host.service("extensions") is None
        assert host.frontend_modules == []


def test_cli_pack_owns_catalog_hook_routes_service_and_frontend_marker(tmp_path):
    from agent.plugin.plugin_impl.cyrene_cli import plugin_pack

    registry = PluginRegistry(include_core=False)
    registry.register_pack(plugin_pack, source="test")
    registry.configure_activation(plugins={}, packs={"cyrene_cli": True})
    host = _host(tmp_path, registry)
    router = APIRouter()
    host.attach(router)
    host.app.include_router(router)
    asyncio.run(host.startup())

    with TestClient(host.app) as client:
        listing = client.get("/api/plugin-center/cli")
        assert listing.status_code == 404
        assert host.service("cli") is None
        assert host.frontend_modules == []

        registry.set_pack_enabled("cyrene_cli", False)
        asyncio.run(host.reconcile_activation())
        assert client.get("/api/plugin-center/cli").status_code == 404
        assert client.get("/api/plugin-center/cli/hooks").status_code == 404
        assert host.service("cli") is None
        assert host.frontend_modules == []

        registry.set_pack_enabled("cyrene_cli", True)
        asyncio.run(host.reconcile_activation())
        assert client.get("/api/plugin-center/cli").status_code == 404
        assert client.get("/api/plugin-center/cli/hooks").status_code == 404
        assert host.service("cli") is None
        assert host.frontend_modules == []


def test_failed_application_startup_hides_pack_contributions(tmp_path):
    events: list[str] = []

    def application_setup(context) -> None:
        @context.router.get("/broken-demo")
        async def broken_demo():
            return {"ok": True}

        context.provide("broken_demo", object())
        context.provide_search("broken_demo", lambda _query, _limit: [])
        context.expose_frontend("broken_demo")

        def fail_startup() -> None:
            events.append("startup")
            raise RuntimeError("migration failed")

        context.on_startup(fail_startup)
        context.on_shutdown(lambda: events.append("shutdown"))

    registry = PluginRegistry(include_core=False)
    registry.register_pack(
        PluginPack(
            id="broken_demo",
            description="demo",
            plugins=(),
            application_setup=application_setup,
        ),
        source="test",
    )
    host = _host(tmp_path, registry)
    router = APIRouter()
    host.attach(router)
    host.app.include_router(router)

    asyncio.run(host.startup())

    assert events == ["startup", "shutdown"]
    assert host.startup_failures == {"broken_demo": "migration failed"}
    assert host.service("broken_demo") is None
    assert "broken_demo" not in host.active_services
    assert host.frontend_modules == []
    assert host.search_providers == {}
    from route.plugins import plugin_registry_status

    status = plugin_registry_status(host)
    pack_status = next(item for item in status["packs"] if item["id"] == "broken_demo")
    assert pack_status["effective_enabled"] is True
    assert pack_status["operational"] is False
    assert pack_status["running"] is False
    assert pack_status["startup_error"] == "migration failed"
    assert pack_status["restart_required"] is False
    from route.settings.plugin_service import get_plugin_settings

    set_active_plugin_application_host(host)
    try:
        settings_status = get_plugin_settings(registry)
    finally:
        set_active_plugin_application_host(None)
    settings_pack = next(
        item for item in settings_status["packs"] if item["id"] == "broken_demo"
    )
    assert settings_pack["effective_enabled"] is True
    assert settings_pack["operational"] is False
    assert settings_pack["running"] is False
    assert settings_pack["startup_error"]
    assert settings_pack["startup_error"] != "migration failed"
    with TestClient(host.app) as client:
        response = client.get("/broken-demo")
        assert response.status_code == 503
        assert response.json()["detail"] == "migration failed"


def test_application_startup_logs_pack_and_handler_timings(tmp_path, caplog):
    events: list[str] = []

    def application_setup(context) -> None:
        def prepare_store() -> None:
            events.append("store")

        async def connect_service() -> None:
            await asyncio.sleep(0)
            events.append("service")

        context.on_startup(prepare_store)
        context.on_startup(connect_service)

    registry = PluginRegistry(include_core=False)
    registry.register_pack(
        PluginPack(
            id="timed_demo",
            description="timed lifecycle demo",
            plugins=(),
            application_setup=application_setup,
        ),
        source="test",
    )
    host = _host(tmp_path, registry)
    host.attach(APIRouter())

    caplog.set_level("INFO", logger="agent.plugin.application")
    asyncio.run(host.startup())

    assert events == ["store", "service"]
    messages = [record.getMessage() for record in caplog.records]
    assert any("Plugin startup host begin" in message for message in messages)
    assert any(
        "Plugin startup pack begin pack=timed_demo handlers=2" in message
        for message in messages
    )
    assert any(
        "Plugin startup handler complete pack=timed_demo"
        in message and "elapsed_ms=" in message
        for message in messages
    )
    assert any(
        "Plugin startup pack complete pack=timed_demo handlers=2" in message
        for message in messages
    )
    assert any(
        "Plugin startup host complete running_packs=1 startup_failures=0"
        in message
        for message in messages
    )


def test_required_session_setup_requires_operational_application_pack(tmp_path):
    setup_calls: list[str] = []

    async def model(_arguments, _context):
        return {"content": "unused", "tool_calls": []}

    def session_setup(_context) -> None:
        setup_calls.append("session")

    def application_setup(context) -> None:
        def fail_startup() -> None:
            raise RuntimeError("required application startup failed")

        context.on_startup(fail_startup)

    registry = PluginRegistry(include_core=False)
    registry.register_plugin(
        Plugin(
            name="test.model",
            description="model",
            input_schema={"type": "object", "properties": {}},
            handler=model,
            kind="model",
        ),
        source="test",
    )
    registry.register_pack(
        PluginPack(
            id="required-application-demo",
            description="demo",
            plugins=(),
            setup=session_setup,
            application_setup=application_setup,
            metadata={"required": True},
        ),
        source="test",
    )
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()
    host = _host(tmp_path, registry)
    host.attach(APIRouter())

    async def scenario() -> None:
        set_active_plugin_application_host(host)
        try:
            await host.startup()
            session = AgentSession(
                tmp_path / "data",
                tmp_path / "workspace",
                plugin_directory,
                tree_id="required-application-session",
                registry=registry,
                model_plugin="test.model",
            )
            try:
                assert setup_calls == []
                with pytest.raises(
                    RuntimeError,
                    match=(
                        "Required Plugin session setup unavailable: "
                        "required-application-demo"
                    ),
                ):
                    session.active_plugin_services()
            finally:
                session.close()
        finally:
            await host.shutdown()
            set_active_plugin_application_host(None)

    asyncio.run(scenario())


def test_knowledge_pack_owns_tools_routes_search_and_frontend(tmp_path):
    from agent.plugin.plugin_impl.cyrene_knowledge import plugin_pack

    registry = PluginRegistry()
    registry.register_pack(plugin_pack, source="test")
    host = _host(tmp_path, registry)
    router = APIRouter()
    host.attach(router)
    knowledge_root = tmp_path / "data" / "plugin_data" / "cyrene_knowledge"

    assert host.attached_packs == ("cyrene_knowledge",)
    assert not knowledge_root.exists()
    assert host.service("knowledge") is not None
    assert "knowledge" in host.search_providers
    assert host.frontend_modules == ["knowledge"]
    route_paths = {route.path for route in router.routes}
    assert "/api/workbench/library/items" in route_paths
    assert "/api/workbench/library/search" in route_paths

    class FakeKnowledge:
        async def list_documents(self, context, *, status, limit):
            assert context.data == {"session_id": "chat_one"}
            assert status == "indexed"
            assert limit == 3
            return [{
                "id": "doc_one",
                "name": "Design.md",
                "path": str(tmp_path / "Design.md"),
                "status": "indexed",
                "chunk_count": 2,
                "size": 12,
            }]

    context = PluginContext(
        data={"session_id": "chat_one"},
        services={"knowledge": FakeKnowledge()},
    )
    runtime = PluginRuntime(registry)

    async def scenario() -> None:
        listing = await runtime.call("toolbox", {"operation": "list"}, context)
        assert listing.success is True
        assert "cyrene_knowledge" in listing.value["packs"]
        described = await runtime.call(
            "toolbox",
            {"operation": "describe", "name": "cyrene_knowledge"},
            context,
        )
        assert described.success is True
        assert {tool["name"] for tool in described.value["plugins"]} == {
            "ListKnowledgeDocuments",
            "SearchKnowledge",
            "ListLibraryItems",
            "SearchLibrary",
            "UpdateLibraryMetadata",
        }
        assert all(
            tool["pack"] == "cyrene_knowledge"
            for tool in described.value["plugins"]
        )
        invoked = await runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "ListKnowledgeDocuments",
                "arguments": {"status": "indexed", "limit": 3},
            },
            context,
        )
        assert invoked.success is True
        assert "Design.md" in invoked.value["result"]
        assert "doc_one" in invoked.value["result"]

    async def lifecycle_scenario() -> None:
        await host.startup()
        service = host.service("knowledge")
        assert service is not None
        assert service.store.db_path.is_file()
        assert service.storage_paths()["knowledge"][0] == knowledge_root
        await host.shutdown()

    asyncio.run(scenario())
    asyncio.run(lifecycle_scenario())


def test_host_without_knowledge_pack_has_no_knowledge_contributions(tmp_path):
    host = _host(tmp_path, PluginRegistry(include_core=False))
    router = APIRouter()
    host.attach(router)

    assert host.service("knowledge") is None
    assert "knowledge" not in host.search_providers
    assert "knowledge" not in host.frontend_modules
    assert not any("/library" in route.path for route in router.routes)
