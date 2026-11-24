"""Regression coverage for Go routing headers and protocol-specific URLs."""

import json

import httpx
import pytest

from cyrene.core.plugin import PluginContext
from cyrene.model.protocol_adapters import discovery_request, protocol_endpoints
from cyrene.plugins.builtin.cyrene_model import _shared
from cyrene.plugins.builtin.cyrene_model.opencode_go import OPENCODE_GO_PROVIDER
from cyrene.plugins.builtin.cyrene_model.openai_compatible import OPENAI_COMPATIBLE_PROVIDER

BASE = "https://opencode.ai/inference/go/openai/v1"


@pytest.mark.asyncio
async def test_go_session_header_is_stable_and_probes_have_independent_ids():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={
            "id": "test", "choices": [{"message": {"role": "assistant", "content": "OK"},
                                       "finish_reason": "stop"}],
        })

    async def call(data, provider=OPENCODE_GO_PROVIDER):
        context = PluginContext(data={
            **data,
            "model_connection": {"base_url": BASE, "api_key": "test-key"},
            "http_transport": httpx.MockTransport(respond),
        })
        result = await _shared.complete_model({
            "model": "deepseek-v4.1-flash",
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "max_tokens": 8,
        }, context, provider)
        assert result["content"] == "OK"

    await call({"session_id": "会话一", "run_id": "turn-1"})
    await call({"session_id": "会话一", "run_id": "turn-2"})
    await call({"run_context": {"session_id": "会话一"}})
    await call({"session_id": "conversation-two"})
    await call({"caller": "settings_model_test"})
    await call({"caller": "settings_model_test"})
    await call({}, OPENAI_COMPATIBLE_PROVIDER)
    ids = [request.headers.get("x-opencode-session") for request in requests]
    assert ids[0] == ids[1] == ids[2]
    assert len(set(ids[2:6])) == 4
    assert all(value.startswith("ses_cyrene_") and value.isascii() for value in ids[:6])
    assert ids[6] is None
    assert all(request.headers["authorization"] == "Bearer test-key" for request in requests)
    assert json.loads(requests[0].content)["model"] == "deepseek-v4.1-flash"


@pytest.mark.asyncio
@pytest.mark.parametrize("model,suffix", [
    ("minimax-m3", "/anthropic/v1/messages"),
    ("gpt-5.6-luna", "/openai/v1/responses"),
])
async def test_go_native_protocols_receive_session_header(monkeypatch, model, suffix):
    captured = []

    async def capture(**kwargs):
        captured.append(kwargs)
        return {"content": "OK"}

    monkeypatch.setattr(_shared, "_complete_stream_endpoint", capture)
    context = PluginContext(tree_id="conversation-tree", data={
        "model_connection": {"base_url": BASE, "api_key": "test-key"},
    })
    for _ in range(2):
        await _shared._complete_model_wire({
            "model": model, "messages": [{"role": "user", "content": "OK"}],
        }, context, OPENCODE_GO_PROVIDER)
    assert captured[0]["endpoint"] == "https://opencode.ai/inference/go" + suffix
    first, second = [item["request"].headers["x-opencode-session"] for item in captured]
    assert first == second
    assert first.startswith("ses_cyrene_")


def test_go_default_and_discovery_use_subscription_endpoint():
    assert OPENCODE_GO_PROVIDER.default_base_url == BASE
    assert discovery_request("openai", BASE, "test-key", provider_preset="opencode_go") == (
        "https://opencode.ai/inference/go/v1/models", {"Authorization": "Bearer test-key"},
    )
    assert protocol_endpoints("openai", BASE, "deepseek-v4.1-flash") == [BASE + "/chat/completions"]
    assert protocol_endpoints("anthropic", "https://custom.example/v1", "minimax-m3") == [
        "https://custom.example/v1/messages",
    ]
