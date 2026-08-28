from types import SimpleNamespace

import pytest

from agent.plugin.plugin_impl.cyrene_composer_context.application import (
    ComposerContextService,
)
from agent.plugin.plugin_impl.cyrene_composer_context.context_mount import (
    setup_composer_context,
)
from agent.plugin.plugin_impl.cyrene_context.service import setup_runtime_context
from cyrene.workbench import slash_commands


def _catalog(*, enabled: bool = True):
    return {
        "mcpServers": [{"id": "docs", "enabled": enabled, "available": enabled}],
        "skills": [{"id": "writer", "enabled": enabled, "available": enabled}],
        "pluginPacks": [{"id": "code_tools", "enabled": enabled, "available": enabled}],
    }


def _composer_service(monkeypatch, *, enabled: bool = True):
    service = ComposerContextService(SimpleNamespace())
    catalog = _catalog(enabled=enabled)
    monkeypatch.setattr(service, "_mcp_catalog", lambda _services=None: catalog["mcpServers"])
    monkeypatch.setattr(service, "_skill_catalog", lambda _services=None: catalog["skills"])
    monkeypatch.setattr(service, "_plugin_pack_catalog", lambda: catalog["pluginPacks"])
    monkeypatch.setattr(service, "_remote_device_catalog", lambda _services=None: [])
    monkeypatch.setattr(
        service,
        "_option_catalog",
        lambda _services=None: {
            "soul": {"available": enabled},
            "workspace": {"available": True},
            "remoteDevices": {"available": enabled},
        },
    )
    monkeypatch.setattr(
        "agent.plugin.active_plugin_service",
        lambda name: service if name == "composer_context" else None,
    )
    return service


def test_context_activation_normalization_validation_and_stale_pruning(monkeypatch) -> None:
    service = _composer_service(monkeypatch)
    raw = {
        "mcpServers": [" docs ", "docs", ""],
        "skills": ["writer"],
        "pluginPacks": ["missing"],
    }
    assert service.normalize(raw) == {
        "mcpServers": ["docs"],
        "skills": ["writer"],
        "pluginPacks": ["missing"],
    }

    with pytest.raises(ValueError, match="missing"):
        service.validate(raw)

    assert service.resolve(raw) == {
        "mcpServers": ["docs"],
        "skills": ["writer"],
        "pluginPacks": [],
    }


def test_disabled_skills_pack_removes_catalog_and_prompt_injection() -> None:
    registry = SimpleNamespace(
        list_packs=lambda: [],
        pack_source=lambda _pack_id: "",
    )
    service = ComposerContextService(
        registry,
        service_resolver=lambda _name: None,
    )

    assert service._skill_catalog() == []
    with pytest.raises(ValueError, match="writer"):
        service.validate({"skills": ["writer"]})


def test_input_context_state_prunes_unavailable_toggle_but_session_fails_closed(
    monkeypatch,
) -> None:
    service = _composer_service(monkeypatch)
    unavailable_soul = {
        "soul": {"available": False},
        "workspace": {"available": True},
        "remoteDevices": {"available": False},
    }
    monkeypatch.setattr(
        service,
        "_option_catalog",
        lambda _services=None: unavailable_soul,
    )
    monkeypatch.setattr(service, "_remote_device_catalog", lambda _services=None: [])

    state = service.resolve_input_context(
        soul_active=True,
        workspace_active=False,
        strict=False,
    )
    assert state["soulActive"] is False
    assert state["state"]["soul"] == {
        "available": False,
        "selected": False,
    }

    with pytest.raises(RuntimeError, match="SOUL context is enabled"):
        service.resolve_input_context(
            soul_active=True,
            workspace_active=False,
            strict=True,
        )


def test_activate_workspace_is_owned_by_composer_context(
    monkeypatch, tmp_path,
) -> None:
    from cyrene.runtime import settings_store

    selected = str((tmp_path / "selected").resolve())
    old = str((tmp_path / "old").resolve())
    state = {
        "workspace_active": False,
        "workspace_history": [old, selected],
    }
    writes = []
    monkeypatch.setattr(
        settings_store,
        "get",
        lambda key, default=None: state.get(key, default),
    )

    def update(values):
        writes.append(dict(values))
        state.update(values)

    monkeypatch.setattr(settings_store, "update_atomic", update)

    ComposerContextService(SimpleNamespace()).activate_workspace(selected)

    assert state["workspace_active"] is True
    assert state["workspace_history"] == [selected, old]
    assert writes == [{
        "workspace_active": True,
        "workspace_history": [selected, old],
    }]


@pytest.mark.asyncio
async def test_runtime_and_composer_context_are_independent_hooks() -> None:
    registered = []

    class Hooks:
        @staticmethod
        def list():
            return []

        @staticmethod
        def register(event, handler, **options):
            registered.append((event, handler, options))

    setup_runtime_context(SimpleNamespace(data={}, hooks=Hooks()))
    composer = SimpleNamespace(build_session_context=lambda *_args, **_kwargs: "selected context")
    setup_composer_context(
        SimpleNamespace(
            services={"composer_context": composer},
            data={"resolved_context_activations": {"skills": ["writer"]}},
            workspace="/workspace",
            hooks=Hooks(),
        )
    )

    assert len(registered) == 2
    composer_hook = next(
        item
        for item in registered
        if item[2]["hook_id"] == "cyrene-composer-context-session-start"
    )
    assert await composer_hook[1](SimpleNamespace(payload={})) == {
        "context": "selected context"
    }
    assert composer_hook[2]["failure_policy"] == "closed"


@pytest.mark.asyncio
async def test_command_only_send_request_is_accepted() -> None:
    from route.workbench.chat_routes.run_send_routes import _SendOperation

    class Runtime:
        @staticmethod
        def normalize_attachments(_attachments):
            return []

    class BudgetGate:
        @staticmethod
        async def check_budget_gate(_chat_id, **_kwargs):
            return None

    controller = SimpleNamespace(
        context=SimpleNamespace(
            runtime=lambda: Runtime(),
            workbench_runtime=BudgetGate(),
            check_budget_gate=BudgetGate.check_budget_gate,
        ),
        service=SimpleNamespace(),
        preferences=SimpleNamespace(persist_language=lambda _lang: None),
    )
    operation = _SendOperation(
        controller,
        "chat-1",
        {"command": "daily-review", "message": ""},
        detached=False,
    )

    assert await operation._parse_request() is None
    assert operation.command == "daily-review"
    assert operation.message == ""


@pytest.mark.asyncio
async def test_dynamic_skill_slash_command_activates_context(monkeypatch) -> None:
    from route.workbench.chat_routes.run_send_routes import _SendOperation

    _composer_service(monkeypatch)
    chat = {
        "id": "chat-1",
        "projectId": "project-1",
        "messages": [],
    }

    class Runtime:
        @staticmethod
        def normalize_attachments(_attachments):
            return []

    class BudgetGate:
        @staticmethod
        async def check_budget_gate(_chat_id, **_kwargs):
            return None

    service = SimpleNamespace(
        repository=SimpleNamespace(get=lambda _chat_id: dict(chat)),
        completed_turn_count=lambda _chat: 0,
        side_agent_parent_transcript=lambda _parent: "",
    )
    controller = SimpleNamespace(
        context=SimpleNamespace(
            runtime=lambda: Runtime(),
            workbench_runtime=BudgetGate(),
            check_budget_gate=BudgetGate.check_budget_gate,
        ),
        service=service,
        preferences=SimpleNamespace(
            persist_language=lambda _lang: None,
            notify_voice_attention=lambda _attention: None,
        ),
    )
    operation = _SendOperation(
        controller,
        "chat-1",
        {"message": "/skill:writer make an outline"},
        detached=False,
    )

    assert await operation._parse_request() is None
    assert await operation._load_chat({"default", "auto", "plan"}) is None
    assert operation.command == "skill:writer"
    assert operation.message == "make an outline"
    assert operation.public_message == "/skill:writer make an outline"
    assert operation.context_activations["skills"] == ["writer"]


@pytest.mark.asyncio
async def test_dynamic_slash_catalog_exposes_context_commands(monkeypatch) -> None:
    _composer_service(monkeypatch)
    catalog = await slash_commands.slash_command_catalog("project-1")
    by_id = {item["id"]: item for item in catalog}

    assert by_id["skill:writer"]["activation"] == {
        "kind": "skills",
        "id": "writer",
    }
    assert by_id["mcp:docs"]["activation"]["kind"] == "mcpServers"
    assert by_id["plugin:code_tools"]["activation"]["kind"] == "pluginPacks"
    assert await slash_commands.resolve_slash_command("review", "project-1") is None


@pytest.mark.asyncio
async def test_directory_picker_error_uses_the_app_language(monkeypatch) -> None:
    from agent.plugin.plugin_impl.cyrene_composer_context import application

    monkeypatch.setattr(
        application,
        "localized",
        lambda _en, zh, **values: zh.format(**values),
    )
    service = ComposerContextService(
        SimpleNamespace(),
        system_name=lambda: "Linux",
    )

    assert await service.pick_directory() == {
        "path": "",
        "error": "Linux 不支持目录选择器。",
    }
