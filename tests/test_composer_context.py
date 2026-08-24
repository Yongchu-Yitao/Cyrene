from types import SimpleNamespace

import pytest

from cyrene.workbench import composer_context
from cyrene.workbench import slash_commands


def _catalog(*, enabled: bool = True):
    return {
        "mcpServers": [{"id": "docs", "enabled": enabled}],
        "skills": [{"id": "writer", "enabled": enabled}],
        "toolPackages": [{"id": "code_tools", "enabled": enabled}],
        "customTools": [{"id": "custom.demo.echo", "enabled": enabled}],
    }


def _capability(identity: str, description: str):
    return SimpleNamespace(
        capability_id=identity,
        description=description,
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
    )


def test_context_activation_normalization_validation_and_stale_pruning(monkeypatch) -> None:
    raw = {
        "mcpServers": [" docs ", "docs", ""],
        "skills": ["writer"],
        "toolPackages": ["missing"],
        "customTools": "not-a-list",
    }
    assert composer_context.normalize_context_activations(raw) == {
        "mcpServers": ["docs"],
        "skills": ["writer"],
        "toolPackages": ["missing"],
        "customTools": [],
    }

    monkeypatch.setattr(composer_context, "context_activation_catalog", _catalog)
    with pytest.raises(ValueError, match="missing"):
        composer_context.validate_context_activations(raw)

    assert composer_context.resolve_context_activations(raw) == {
        "mcpServers": ["docs"],
        "skills": ["writer"],
        "toolPackages": [],
        "customTools": [],
    }


def test_activation_prompt_injects_skill_and_pre_described_tool_schemas(monkeypatch) -> None:
    import cyrene.learning.skills as skills
    import cyrene.tooling.catalog as catalog

    monkeypatch.setattr(composer_context, "context_activation_catalog", _catalog)
    monkeypatch.setattr(
        skills,
        "load_skill",
        lambda skill_id: {
            "id": skill_id,
            "name": "Writer",
            "instructions": "Always produce a concise outline first.",
            "resources": [{"path": "examples.md", "text": True}],
        },
    )
    monkeypatch.setattr(
        composer_context,
        "_selected_mcp_capabilities",
        lambda names: [{
            "capability_id": "mcp.docs.search",
            "description": "Search docs",
            "arguments_schema": {"type": "object"},
            "mcp_server": names[0],
        }],
    )
    native = _capability("code.shell.read", "Read a shell")
    custom = _capability("custom.demo.echo", "Echo text")
    skill_resource = _capability("skill.read_resource", "Read a Skill resource")
    monkeypatch.setattr(catalog, "capabilities_for_pack", lambda name: [native])
    monkeypatch.setattr(
        catalog,
        "get_capability",
        lambda identity: {
            custom.capability_id: custom,
            skill_resource.capability_id: skill_resource,
        }.get(identity),
    )

    prompt = composer_context.build_context_activation_prompt({
        "mcpServers": ["docs"],
        "skills": ["writer"],
        "toolPackages": ["code_tools"],
        "customTools": ["custom.demo.echo"],
    })

    assert "Always produce a concise outline first." in prompt
    assert "mcp.docs.search" in prompt
    assert "code.shell.read" in prompt
    assert "custom.demo.echo" in prompt
    assert "skill.read_resource" in prompt
    assert "do not call toolbox search or describe" in prompt
    assert "operation=invoke" in prompt


@pytest.mark.asyncio
async def test_command_only_send_request_is_accepted() -> None:
    from route.workbench.chat_routes.run_send_routes import _SendOperation

    class Runtime:
        @staticmethod
        def normalize_attachments(_attachments):
            return []

    class BudgetGate:
        @staticmethod
        async def check_budget_gate(_chat_id):
            return None

    controller = SimpleNamespace(
        context=SimpleNamespace(
            runtime=lambda: Runtime(),
            workbench_runtime=BudgetGate(),
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

    monkeypatch.setattr(composer_context, "context_activation_catalog", _catalog)
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
        async def check_budget_gate(_chat_id):
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
async def test_dynamic_slash_catalog_exposes_context_and_plugin_commands(monkeypatch) -> None:
    monkeypatch.setattr(composer_context, "context_activation_catalog", _catalog)

    class PluginManager:
        async def contributions(self, project_id, point):
            assert project_id == "project-1"
            assert point == "cyrene.command"
            return [{
                "point": point,
                "id": "review",
                "command": "review",
                "title": "Review changes",
                "description": "Project review workflow",
                "pluginId": "demo.plugin",
                "prompt": "Review the current changes.",
            }]

        async def call(self, *_args, **_kwargs):
            raise AssertionError("static prompt commands do not call the plugin")

    import cyrene.plugins.manager as plugin_manager

    monkeypatch.setattr(plugin_manager, "get_plugin_manager", lambda: PluginManager())
    catalog = await slash_commands.slash_command_catalog("project-1")
    by_id = {item["id"]: item for item in catalog}

    assert by_id["skill:writer"]["activation"] == {
        "kind": "skills",
        "id": "writer",
    }
    assert by_id["mcp:docs"]["activation"]["kind"] == "mcpServers"
    assert by_id["pack:code_tools"]["activation"]["kind"] == "toolPackages"
    assert by_id["custom:custom.demo.echo"]["activation"]["kind"] == "customTools"
    assert by_id["review"]["source"] == "plugin"
    assert "prompt" not in by_id["review"]

    descriptor = await slash_commands.resolve_slash_command("review", "project-1")
    prompt = await slash_commands.prepare_plugin_command_prompt(
        descriptor,
        arguments="focus on regressions",
        chat_id="chat-1",
        project_id="project-1",
    )
    assert "Review the current changes." in prompt
    assert "focus on regressions" in prompt
