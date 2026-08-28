"""End-to-end contracts for external Agents using Cyrene model settings."""

from __future__ import annotations

import pytest


def _candidate() -> dict:
    return {
        "id": "configured-primary",
        "provider": "openai_compatible",
        "model": "configured-model",
        "name": "configured-model",
        "base_url": "https://models.example/v1",
        "api_key": "test-only",
        "reasoning_effort": "high",
    }


def test_gateway_binding_is_scoped_to_selected_chat_model_and_revocable(monkeypatch):
    from cyrene.agent_runtime import model_gateway
    from cyrene.agent_runtime.models import ModelAccess

    monkeypatch.setattr(
        "agent.plugin.model_catalog.resolve_session_model_candidate",
        lambda session_id: _candidate() if session_id == "chat_selected" else None,
    )
    env = model_gateway.issue_model_gateway_binding(
        ModelAccess(mode="cyrene_managed", profile_id="primary"),
        {"chat_id": "chat_selected", "run_id": "run_selected", "installation_id": "agent_x"},
    )
    token = env["OPENAI_API_KEY"]
    scope = model_gateway.authorize_model_gateway(f"Bearer {token}")
    assert scope is not None
    assert scope["chatId"] == "chat_selected"
    assert scope["runId"] == "run_selected"
    assert scope["installationId"] == "agent_x"
    assert scope["modelIdentity"]["candidateId"] == "configured-primary"
    assert scope["modelIdentity"]["reasoningEffort"] == "high"

    model_gateway.revoke_model_gateway_scope(chat_id="chat_selected", run_id="run_selected")
    assert model_gateway.authorize_model_gateway(f"Bearer {token}") is None


def test_gateway_binding_is_reused_across_turns_and_config_probes(monkeypatch):
    from cyrene.agent_runtime import model_gateway
    from cyrene.agent_runtime.models import ModelAccess

    model_gateway.revoke_all_model_gateway_scopes()
    monkeypatch.setattr(
        "agent.plugin.model_catalog.resolve_session_model_candidate",
        lambda session_id: _candidate() if session_id == "chat_session" else None,
    )
    access = ModelAccess(mode="cyrene_managed", profile_id="primary")
    first = model_gateway.issue_model_gateway_binding(
        access,
        {"chat_id": "chat_session", "run_id": "run_one", "installation_id": "agent_x"},
    )
    probe = model_gateway.issue_model_gateway_binding(
        access,
        {"chat_id": "chat_session", "run_id": "config-probe", "installation_id": "agent_x"},
    )
    second = model_gateway.issue_model_gateway_binding(
        access,
        {"chat_id": "chat_session", "run_id": "run_two", "installation_id": "agent_x"},
    )

    assert first["OPENAI_API_KEY"] == probe["OPENAI_API_KEY"] == second["OPENAI_API_KEY"]
    assert model_gateway.authorize_model_gateway(f"Bearer {first['OPENAI_API_KEY']}") is not None
    model_gateway.revoke_model_gateway_scope(chat_id="chat_session")
    assert model_gateway.authorize_model_gateway(f"Bearer {first['OPENAI_API_KEY']}") is None


def test_gateway_binding_rotates_when_selected_model_changes(monkeypatch):
    from cyrene.agent_runtime import model_gateway
    from cyrene.agent_runtime.models import ModelAccess

    model_gateway.revoke_all_model_gateway_scopes()
    candidate = _candidate()
    monkeypatch.setattr(
        "agent.plugin.model_catalog.resolve_session_model_candidate",
        lambda _session_id: dict(candidate),
    )
    access = ModelAccess(mode="cyrene_managed", profile_id="primary")
    first = model_gateway.issue_model_gateway_binding(
        access,
        {"chat_id": "chat_model_change", "run_id": "run_one", "installation_id": "agent_x"},
    )
    candidate["id"] = "configured-secondary"
    candidate["model"] = "configured-model-2"
    second = model_gateway.issue_model_gateway_binding(
        access,
        {"chat_id": "chat_model_change", "run_id": "run_two", "installation_id": "agent_x"},
    )

    assert first["OPENAI_API_KEY"] != second["OPENAI_API_KEY"]
    assert model_gateway.authorize_model_gateway(f"Bearer {first['OPENAI_API_KEY']}") is None
    assert model_gateway.authorize_model_gateway(f"Bearer {second['OPENAI_API_KEY']}") is not None


def test_gateway_session_is_current_only_for_its_chat_and_installation():
    from cyrene.agent_runtime import model_gateway

    model_gateway.revoke_all_model_gateway_scopes()
    assert not model_gateway.is_model_gateway_session_current(
        chat_id="chat_session", installation_id="agent_x", session_id="ses_one"
    )
    model_gateway.mark_model_gateway_session_current(
        chat_id="chat_session", installation_id="agent_x", session_id="ses_one"
    )
    assert model_gateway.is_model_gateway_session_current(
        chat_id="chat_session", installation_id="agent_x", session_id="ses_one"
    )
    assert not model_gateway.is_model_gateway_session_current(
        chat_id="chat_session", installation_id="agent_y", session_id="ses_one"
    )
    model_gateway.revoke_model_gateway_scope(chat_id="chat_session")
    assert not model_gateway.is_model_gateway_session_current(
        chat_id="chat_session", installation_id="agent_x", session_id="ses_one"
    )


def test_agent_managed_binder_injects_no_cyrene_credentials():
    from cyrene.agent_runtime.models import ModelAccess
    from cyrene.agent_runtime.runtime_service import EnvModelBinder

    binding = EnvModelBinder(lambda *_args: {"OPENAI_API_KEY": "must-not-be-used"}).bind(
        ModelAccess(mode="agent_managed"),
        installation={"installation_id": "agent_x"},
        session_context={"chat_id": "chat_x"},
    )
    assert binding.env == {}


@pytest.mark.asyncio
async def test_gateway_calls_exact_scoped_candidate_with_chat_affinity(monkeypatch):
    import agent.plugin
    from cyrene.agent_runtime import model_gateway

    captured = {}

    monkeypatch.setattr(
        "agent.plugin.model_catalog.resolve_exact_model_candidate",
        lambda identity: (
            _candidate()
            if identity.get("candidateId") == "configured-primary"
            else None
        ),
    )

    async def fake_call_llm(messages, **kwargs):
        captured["messages"] = messages
        captured.update(kwargs)
        return {"role": "assistant", "content": "ok", "model": "configured-model"}

    class FakeModelGateway:
        complete = staticmethod(fake_call_llm)

    monkeypatch.setattr(
        agent.plugin,
        "active_plugin_service",
        lambda name: FakeModelGateway() if name == "model" else None,
    )
    result = await model_gateway.call_model_gateway(
        {
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "read", "parameters": {}},
                }
            ],
            "max_tokens": 321,
        },
        {
            "chatId": "chat_selected",
            "modelIdentity": {
                "candidateId": "configured-primary",
                "provider": "openai_compatible",
                "model": "configured-model",
                "baseUrl": "https://models.example/v1",
                "reasoningEffort": "high",
            },
        },
    )
    assert result["content"] == "ok"
    assert captured["session_id"] == "chat_selected"
    assert captured["model_identity"]["candidateId"] == "configured-primary"
    assert captured["tools"][0]["function"]["name"] == "read"
    assert captured["max_tokens"] == 321


def test_responses_protocol_maps_messages_tool_results_and_calls():
    from agent.plugin.plugin_impl.cyrene_extensions.agent_model_gateway_routes import (
        _responses_input_to_messages,
        _responses_output,
        _responses_tools,
    )

    messages = _responses_input_to_messages([
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Use the tool"}],
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "tool result"},
    ])
    assert messages == [
        {"role": "user", "content": "Use the tool"},
        {"role": "tool", "tool_call_id": "call_1", "content": "tool result"},
    ]
    tools = _responses_tools([{
        "type": "function",
        "name": "lookup",
        "description": "Look up data",
        "parameters": {"type": "object"},
    }])
    assert tools[0]["function"]["name"] == "lookup"

    output = _responses_output({
        "content": "",
        "tool_calls": [{
            "id": "call_2",
            "type": "function",
            "function": {"name": "lookup", "arguments": '{"q":"x"}'},
        }],
    })
    assert output == [{
        "type": "function_call",
        "id": "call_2",
        "call_id": "call_2",
        "name": "lookup",
        "arguments": '{"q":"x"}',
        "status": "completed",
    }]


def test_responses_stream_emits_complete_text_and_tool_lifecycles():
    import json

    from agent.plugin.plugin_impl.cyrene_extensions.agent_model_gateway_routes import (
        _responses_output,
        _responses_stream_events,
    )

    payload = {
        "id": "resp_test",
        "object": "response",
        "status": "completed",
        "output": _responses_output({
            "content": "hello",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "lookup", "arguments": '{"q":"x"}'},
            }],
        }),
        "usage": {},
    }
    events = _responses_stream_events(payload)
    event_types = []
    decoded = []
    for event in events:
        for line in event.splitlines():
            if line.startswith("event: "):
                event_types.append(line.removeprefix("event: "))
            elif line.startswith("data: {"):
                decoded.append(json.loads(line.removeprefix("data: ")))

    assert event_types == [
        "response.created",
        "response.output_item.added",
        "response.content_part.added",
        "response.output_text.delta",
        "response.output_text.done",
        "response.content_part.done",
        "response.output_item.done",
        "response.output_item.added",
        "response.function_call_arguments.delta",
        "response.function_call_arguments.done",
        "response.output_item.done",
        "response.completed",
    ]
    assert next(
        item for item in decoded if item["type"] == "response.output_text.delta"
    )["delta"] == "hello"
    assert next(
        item for item in decoded
        if item["type"] == "response.function_call_arguments.done"
    )["arguments"] == '{"q":"x"}'
    assert events[-1] == "data: [DONE]\n\n"


def test_pi_acp_binding_injects_redirected_config_dir_pointing_at_gateway(monkeypatch, tmp_path):
    from cyrene.agent_runtime import model_gateway
    from cyrene.agent_runtime.models import ModelAccess

    monkeypatch.setattr(
        "agent.plugin.model_catalog.resolve_session_model_candidate",
        lambda session_id: _candidate(),
    )
    config_dir = tmp_path / "pi-agent-config"
    monkeypatch.setattr(model_gateway, "_PI_AGENT_CONFIG_ROOT", config_dir)
    monkeypatch.setattr(model_gateway, "_GATEWAY_PORT", 4321)

    env = model_gateway.issue_model_gateway_binding(
        ModelAccess(mode="cyrene_managed", profile_id="primary"),
        {"chat_id": "chat_selected", "run_id": "run_selected", "installation_id": "agent_pi-acp_default", "agent_id": "pi-acp"},
    )
    assert env["OPENAI_API_KEY"]
    expected_dir = config_dir / "configured-model"
    assert env["PI_CODING_AGENT_DIR"] == str(expected_dir)
    models = (expected_dir / "models.json").read_text("utf-8")
    assert models == (
        '{"providers":{"openai":{"baseUrl":"http://127.0.0.1:4321/api/agent-model-gateway/v1",'
        '"models":[{"id":"configured-model","name":"configured-model","api":"openai-responses"}]}}}'
    )
    settings = (expected_dir / "settings.json").read_text("utf-8")
    assert settings == '{"defaultProvider":"openai","defaultModel":"configured-model"}'


def test_pi_acp_binding_uses_configured_model_name_not_entry_id(monkeypatch, tmp_path):
    from cyrene.agent_runtime import model_gateway
    from cyrene.agent_runtime.models import ModelAccess

    candidate = dict(_candidate(), id="deepseek-chat", model="deepseek-v4-flash", name="deepseek-v4-flash")
    monkeypatch.setattr(
        "agent.plugin.model_catalog.resolve_session_model_candidate",
        lambda session_id: candidate,
    )
    config_dir = tmp_path / "pi-agent-config"
    monkeypatch.setattr(model_gateway, "_PI_AGENT_CONFIG_ROOT", config_dir)

    model_gateway.issue_model_gateway_binding(
        ModelAccess(mode="cyrene_managed", profile_id="primary"),
        {"chat_id": "chat_selected", "run_id": "run_selected", "installation_id": "agent_pi-acp_default", "agent_id": "pi-acp"},
    )
    expected_dir = config_dir / "deepseek-v4-flash"
    settings = (expected_dir / "settings.json").read_text("utf-8")
    models = (expected_dir / "models.json").read_text("utf-8")
    assert '"defaultModel":"deepseek-v4-flash"' in settings
    assert "deepseek-v4-flash" in models
    assert "deepseek-chat" not in settings
    assert "gpt-5.4" not in models


def test_pi_acp_binding_falls_back_to_model_when_name_missing(monkeypatch, tmp_path):
    from cyrene.agent_runtime import model_gateway
    from cyrene.agent_runtime.models import ModelAccess

    candidate = dict(_candidate())
    candidate.pop("name", None)
    monkeypatch.setattr(
        "agent.plugin.model_catalog.resolve_session_model_candidate",
        lambda session_id: candidate,
    )
    config_dir = tmp_path / "pi-agent-config"
    monkeypatch.setattr(model_gateway, "_PI_AGENT_CONFIG_ROOT", config_dir)

    model_gateway.issue_model_gateway_binding(
        ModelAccess(mode="cyrene_managed", profile_id="primary"),
        {"chat_id": "chat_selected", "run_id": "run_selected", "installation_id": "agent_pi-acp_default", "agent_id": "pi-acp"},
    )
    settings = (config_dir / "configured-model" / "settings.json").read_text("utf-8")
    assert '"defaultModel":"configured-model"' in settings


def test_pi_acp_binding_is_idempotent(monkeypatch, tmp_path):
    from cyrene.agent_runtime import model_gateway
    from cyrene.agent_runtime.models import ModelAccess

    monkeypatch.setattr(
        "agent.plugin.model_catalog.resolve_session_model_candidate",
        lambda session_id: _candidate(),
    )
    config_dir = tmp_path / "pi-agent-config"
    monkeypatch.setattr(model_gateway, "_PI_AGENT_CONFIG_ROOT", config_dir)

    context = {"chat_id": "chat_selected", "run_id": "run_selected", "installation_id": "agent_pi-acp_default", "agent_id": "pi-acp"}
    first = model_gateway.issue_model_gateway_binding(ModelAccess(mode="cyrene_managed", profile_id="primary"), context)
    second = model_gateway.issue_model_gateway_binding(ModelAccess(mode="cyrene_managed", profile_id="primary"), context)
    assert first == second
    assert first["PI_CODING_AGENT_DIR"] == str(config_dir / "configured-model")


def test_non_pi_agents_do_not_receive_pi_config_dir(monkeypatch, tmp_path):
    from cyrene.agent_runtime import model_gateway
    from cyrene.agent_runtime.models import ModelAccess

    monkeypatch.setattr(
        "agent.plugin.model_catalog.resolve_session_model_candidate",
        lambda session_id: _candidate(),
    )
    monkeypatch.setattr(model_gateway, "_PI_AGENT_CONFIG_ROOT", tmp_path / "unused")

    env = model_gateway.issue_model_gateway_binding(
        ModelAccess(mode="cyrene_managed", profile_id="primary"),
        {"chat_id": "chat_selected", "run_id": "run_selected", "installation_id": "agent_codex-acp_default", "agent_id": "codex-acp"},
    )
    assert "PI_CODING_AGENT_DIR" not in env
    assert "OPENAI_API_KEY" in env
