import httpx
import pytest

from cyrene.plugins.builtin.cyrene_model._shared import _IPv4FallbackTransport
from cyrene.plugins.builtin.cyrene_model._shared import _openai_payload
from cyrene.plugins.builtin.cyrene_model.openai_compatible import (
    OPENAI_COMPATIBLE_PROVIDER,
)
from cyrene.plugins.builtin.cyrene_model.aliyun_bailian import (
    ALIYUN_BAILIAN_PROVIDER,
)
from cyrene.plugins.builtin.cyrene_model.local_onnx import LOCAL_ONNX_PLUGIN
from cyrene.plugins.builtin.cyrene_model.minimax import MINIMAX_PLUGIN


def test_model_plugins_have_no_fixed_wall_clock_timeout() -> None:
    assert MINIMAX_PLUGIN.timeout_seconds is None
    assert LOCAL_ONNX_PLUGIN.timeout_seconds is None


def test_openai_compatible_stream_requests_usage() -> None:
    assert OPENAI_COMPATIBLE_PROVIDER.include_stream_usage is True

    payload = _openai_payload(
        {"messages": [{"role": "user", "content": "hello"}]},
        OPENAI_COMPATIBLE_PROVIDER,
        "local-model",
    )

    assert payload["stream"] is True
    assert payload["stream_options"] == {"include_usage": True}


def test_aliyun_bailian_stream_requests_usage_and_reasoning_effort() -> None:
    payload = _openai_payload(
        {
            "messages": [{"role": "user", "content": "hello"}],
            "reasoning_effort": "high",
        },
        ALIYUN_BAILIAN_PROVIDER,
        "qwen3-max",
    )

    assert payload["stream_options"] == {"include_usage": True}
    assert payload["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_direct_transport_retries_connect_errors_over_ipv4() -> None:
    calls: list[str] = []

    def fail_preferred(request: httpx.Request) -> httpx.Response:
        calls.append("preferred")
        raise httpx.ConnectError("TLS connection reset", request=request)

    def succeed_ipv4(request: httpx.Request) -> httpx.Response:
        calls.append("ipv4")
        return httpx.Response(200, json={"ok": True}, request=request)

    transport = _IPv4FallbackTransport(
        primary=httpx.MockTransport(fail_preferred),
        ipv4=httpx.MockTransport(succeed_ipv4),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.get("https://dashscope.aliyuncs.com/api/v1/models")

    assert response.json() == {"ok": True}
    assert calls == ["preferred", "ipv4"]


@pytest.mark.asyncio
async def test_direct_transport_does_not_replay_http_responses() -> None:
    calls: list[str] = []

    def reject(request: httpx.Request) -> httpx.Response:
        calls.append("preferred")
        return httpx.Response(401, request=request)

    def unexpected_ipv4(request: httpx.Request) -> httpx.Response:
        calls.append("ipv4")
        return httpx.Response(200, request=request)

    transport = _IPv4FallbackTransport(
        primary=httpx.MockTransport(reject),
        ipv4=httpx.MockTransport(unexpected_ipv4),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.get("https://dashscope.aliyuncs.com/api/v1/models")

    assert response.status_code == 401
    assert calls == ["preferred"]
