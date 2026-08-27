from types import SimpleNamespace

import pytest

from cyrene.workbench import composer_context
from cyrene.workbench import slash_commands


def _catalog(*, enabled: bool = True):
    return {
        "mcpServers": [{"id": "docs", "enabled": enabled}],
        "skills": [{"id": "writer", "enabled": enabled}],
        "toolPackages": [{"id": "code_tools", "enabled": enabled}],
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
    }
    assert composer_context.normalize_context_activations(raw) == {
        "mcpServers": ["docs"],
        "skills": ["writer"],
        "toolPackages": ["missing"],
    }

    monkeypatch.setattr(composer_context, "context_activation_catalog", _catalog)
    with pytest.raises(ValueError, match="missing"):
        composer_context.validate_context_activations(raw)

    assert composer_context.resolve_context_activations(raw) == {
        "mcpServers": ["docs"],
        "skills": ["writer"],
        "toolPackages": [],
    }


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
async def test_dynamic_slash_catalog_exposes_context_commands(monkeypatch) -> None:
    monkeypatch.setattr(composer_context, "context_activation_catalog", _catalog)
    catalog = await slash_commands.slash_command_catalog("project-1")
    by_id = {item["id"]: item for item in catalog}

    assert by_id["skill:writer"]["activation"] == {
        "kind": "skills",
        "id": "writer",
    }
    assert by_id["mcp:docs"]["activation"]["kind"] == "mcpServers"
    assert by_id["pack:code_tools"]["activation"]["kind"] == "toolPackages"
    assert await slash_commands.resolve_slash_command("review", "project-1") is None
