"""End-to-end contracts for external Agents using Cyrene model settings."""

from __future__ import annotations

import pytest


def _candidate() -> dict:
    return {
        "id": "configured-primary",
        "provider": "openai_compatible",
        "model": "configured-model",
        "base_url": "https://models.example/v1",
        "api_key": "test-only",
        "reasoning_effort": "high",
    }


def test_gateway_binding_is_scoped_to_selected_chat_model_and_revocable(monkeypatch):
    from cyrene.agent_runtime import model_gateway
    from cyrene.agent_runtime.models import ModelAccess

    monkeypatch.setattr(
        "cyrene.model_runtime.client.resolve_session_model_candidate",
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
        "cyrene.model_runtime.client.resolve_session_model_candidate",
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
        "cyrene.model_runtime.client.resolve_session_model_candidate",
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
    from cyrene.agent_runtime import model_gateway

    captured = {}

    monkeypatch.setattr(
        "cyrene.model_runtime.client.resolve_exact_model_candidate",
        lambda identity: _candidate() if identity.get("candidateId") == "configured-primary" else None,
    )

    async def fake_call_llm(messages, **kwargs):
        captured["messages"] = messages
        captured.update(kwargs)
        return {"role": "assistant", "content": "ok", "model": "configured-model"}

    monkeypatch.setattr(model_gateway, "call_llm", fake_call_llm)
    result = await model_gateway.call_model_gateway(
        {
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [{"type": "function", "function": {"name": "read", "parameters": {}}}],
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
    assert captured["candidates"][0]["id"] == "configured-primary"
    assert captured["tools"][0]["function"]["name"] == "read"
    assert captured["max_tokens"] == 321


def test_responses_protocol_maps_messages_tool_results_and_calls():
    from route.agent_model_gateway import (
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
