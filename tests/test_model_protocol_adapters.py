from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from cyrene.model.error_details import classify_model_error

from cyrene.model.protocol_adapters import (
    ModelStreamError,
    PreparedRequest,
    discovery_request,
    handle_stream,
    next_discovery_page,
    parse_discovery_response,
    parse_response,
    prepare_request,
    protocol_endpoints,
    runtime_adapter_for_provider,
)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "weather",
            "description": "Read the weather",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }
]


def test_protocol_endpoints_use_native_provider_routes() -> None:
    base = "https://provider.example/v1/"

    assert protocol_endpoints("openai_chat", base, "gpt-5") == [
        "https://provider.example/v1/chat/completions"
    ]
    assert protocol_endpoints("openai_responses", base, "gpt-5") == [
        "https://provider.example/v1/responses"
    ]
    assert protocol_endpoints("anthropic", base, "claude-sonnet-4-5") == [
        "https://provider.example/v1/messages"
    ]
    assert protocol_endpoints(
        "gemini", "https://generativelanguage.googleapis.com/v1beta", "models/gemini-2.5-flash"
    ) == [
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    ]


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("kimi-k3", "openai"),
        ("glm-5.3", "openai"),
        ("deepseek-v4-flash", "openai"),
        ("minimax-m3", "anthropic"),
        ("qwen3.8-max", "anthropic"),
        ("gpt-5.6-luna", "openai_responses"),
        ("grok-4.5", "openai_responses"),
        ("muse-spark-1.2-contributor", "openai_responses"),
    ],
)
def test_opencode_go_selects_each_models_documented_protocol(
    model: str,
    expected: str,
) -> None:
    assert runtime_adapter_for_provider(
        "openai",
        model,
        provider_preset="opencode_go",
    ) == expected


def test_other_provider_presets_keep_the_configured_adapter() -> None:
    assert runtime_adapter_for_provider(
        "gemini",
        "gemini-3.7-flash",
        provider_preset="gemini",
    ) == "gemini"


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://api.deepseek.com", "https://api.deepseek.com/v1/chat/completions"),
        ("https://api.deepseek.com/v1", "https://api.deepseek.com/v1/chat/completions"),
        ("https://api.moonshot.cn", "https://api.moonshot.cn/v1/chat/completions"),
        ("https://api.moonshot.cn/v1", "https://api.moonshot.cn/v1/chat/completions"),
        ("https://api.minimaxi.com", "https://api.minimaxi.com/v1/chat/completions"),
        ("https://api.minimaxi.com/v1", "https://api.minimaxi.com/v1/chat/completions"),
        ("https://api.minimax.io", "https://api.minimax.io/v1/chat/completions"),
        ("https://api.minimax.io/v1", "https://api.minimax.io/v1/chat/completions"),
    ],
)
def test_official_openai_compatible_providers_use_only_v1_chat_endpoint(
    base_url: str,
    expected: str,
) -> None:
    assert protocol_endpoints("openai", base_url, "model") == [expected]


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://api.deepseek.com", "https://api.deepseek.com/v1/models"),
        ("https://api.moonshot.cn", "https://api.moonshot.cn/v1/models"),
        ("https://api.minimaxi.com", "https://api.minimaxi.com/v1/models"),
        ("https://api.minimax.io", "https://api.minimax.io/v1/models"),
    ],
)
def test_official_openai_compatible_discovery_uses_v1(
    base_url: str,
    expected: str,
) -> None:
    assert discovery_request("openai", base_url, "key") == (
        expected,
        {"Authorization": "Bearer key"},
    )


def test_discovery_uses_provider_native_auth_and_normalizes_gemini_models() -> None:
    assert discovery_request("anthropic", "https://api.anthropic.com/v1/", "ant-key") == (
        "https://api.anthropic.com/v1/models",
        {"x-api-key": "ant-key", "anthropic-version": "2023-06-01"},
    )
    assert discovery_request("gemini", "https://generativelanguage.googleapis.com/v1beta", "gem-key") == (
        "https://generativelanguage.googleapis.com/v1beta/models",
        {"x-goog-api-key": "gem-key"},
    )
    assert parse_discovery_response(
        "gemini",
        {
            "models": [
                {
                    "name": "models/gemini-2.5-flash",
                    "displayName": "Gemini 2.5 Flash",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/text-embedding-004",
                    "supportedGenerationMethods": ["embedContent"],
                },
            ]
        },
    ) == [
        {
            "id": "gemini-2.5-flash",
            "model": "gemini-2.5-flash",
            "name": "Gemini 2.5 Flash",
            "capabilities": ["chat", "vision", "tools", "reasoning"],
        }
    ]


def test_minimax_openai_discovery_uses_models_endpoint_and_bearer_auth() -> None:
    assert discovery_request("openai", "https://api.minimaxi.com/v1", "mini-key") == (
        "https://api.minimaxi.com/v1/models",
        {"Authorization": "Bearer mini-key"},
    )
    assert parse_discovery_response(
        "openai",
        {"object": "list", "data": [{"id": "MiniMax-M2.7", "owned_by": "minimax"}]},
    ) == [{
        "id": "MiniMax-M2.7",
        "model": "MiniMax-M2.7",
        "name": "MiniMax-M2.7",
        "capabilities": ["chat"],
    }]

    with pytest.raises(ValueError, match="expected a model array"):
        parse_discovery_response("openai", {"error": "upstream proxy page"})


def test_aliyun_bailian_discovery_uses_catalog_api_and_normalizes_models() -> None:
    endpoint, headers = discovery_request(
        "openai",
        "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "bailian-key",
        provider_preset="aliyun_bailian",
    )

    assert endpoint == (
        "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1/models"
        "?page_no=1&page_size=100"
    )
    assert headers == {"Authorization": "Bearer bailian-key"}

    payload = {
        "output": {
            "total": 101,
            "page_no": 1,
            "page_size": 100,
            "models": [
                {
                    "model": "qwen3-max",
                    "name": "Qwen3 Max",
                    "description": "General-purpose reasoning model",
                    "capabilities": ["TG", "Reasoning"],
                    "features": ["function-calling", "structured-outputs"],
                    "inference_metadata": {
                        "request_modality": ["Text"],
                        "response_modality": ["Text"],
                    },
                    "model_info": {"context_window": 131_072},
                },
                {
                    "model": "qwen-vl-plus",
                    "name": "Qwen VL Plus",
                    "capabilities": ["VU"],
                    "features": ["function-calling"],
                    "inference_metadata": {
                        "request_modality": ["Text", "Image"],
                        "response_modality": ["Text"],
                    },
                    "model_info": {"context_window": 32_768},
                },
                {
                    "model": "qwen-image-max",
                    "name": "Qwen Image Max",
                    "capabilities": ["IG"],
                    "inference_metadata": {
                        "request_modality": ["Text"],
                        "response_modality": ["Image"],
                    },
                },
            ],
        }
    }

    assert parse_discovery_response(
        "openai",
        payload,
        provider_preset="aliyun_bailian",
    ) == [
        {
            "id": "qwen3-max",
            "model": "qwen3-max",
            "name": "Qwen3 Max",
            "capabilities": ["chat", "tools", "reasoning"],
            "description": "General-purpose reasoning model",
            "context_limit": 131_072,
        },
        {
            "id": "qwen-vl-plus",
            "model": "qwen-vl-plus",
            "name": "Qwen VL Plus",
            "capabilities": ["chat", "vision", "tools"],
            "context_limit": 32_768,
        },
    ]
    assert next_discovery_page(
        endpoint,
        payload,
        provider_preset="aliyun_bailian",
    ) == (
        "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1/models"
        "?page_no=2&page_size=100"
    )

    with pytest.raises(
        ValueError,
        match="Alibaba Cloud Model Studio model discovery failed: InvalidApiKey",
    ):
        parse_discovery_response(
            "openai",
            {
                "success": False,
                "code": "InvalidApiKey",
                "message": "InvalidApiKey",
            },
            provider_preset="aliyun_bailian",
        )


def test_openrouter_discovery_uses_catalog_metadata() -> None:
    assert parse_discovery_response(
        "openai",
        {
            "data": [{
                "id": "vendor/model",
                "name": "Vendor Model",
                "context_length": 262_144,
                "architecture": {"input_modalities": ["text", "image"]},
                "supported_parameters": ["tools", "reasoning"],
            }],
        },
    ) == [{
        "id": "vendor/model",
        "model": "vendor/model",
        "name": "Vendor Model",
        "capabilities": ["chat", "vision", "tools", "reasoning"],
        "context_limit": 262_144,
    }]


def test_prepare_anthropic_request_converts_system_images_tools_and_auth() -> None:
    request = prepare_request(
        "anthropic",
        api_key="ant-key",
        model="claude-sonnet-4-5",
        messages=[
            {"role": "system", "content": "Be concise."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is this?"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}},
                ],
            },
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "toolu_weather",
                        "type": "function",
                        "function": {"name": "weather", "arguments": '{"city":"Paris"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "toolu_weather", "content": '{"temp":21}'},
        ],
        tools=TOOLS,
        max_tokens=512,
        stream=True,
        response_format=None,
    )

    assert request.headers == {
        "Content-Type": "application/json",
        "x-api-key": "ant-key",
        "anthropic-version": "2023-06-01",
    }
    assert request.payload["system"] == [{
        "type": "text",
        "text": "Be concise.",
        "cache_control": {"type": "ephemeral"},
    }]
    assert request.payload["max_tokens"] == 512
    assert request.payload["stream"] is True
    assert "cache_control" not in request.payload
    assert request.payload["messages"][0] == {
        "role": "user",
        "content": [
            {"type": "text", "text": "What is this?"},
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": "aGVsbG8="},
            },
        ],
    }
    assert request.payload["messages"][1]["content"][0] == {
        "type": "tool_use",
        "id": "toolu_weather",
        "name": "weather",
        "input": {"city": "Paris"},
    }
    assert request.payload["messages"][2] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_weather",
                "content": '{"temp":21}',
                "cache_control": {"type": "ephemeral"},
            }
        ],
    }
    assert request.payload["tools"] == [
        {
            "name": "weather",
            "description": "Read the weather",
            "input_schema": TOOLS[0]["function"]["parameters"],
            "cache_control": {"type": "ephemeral"},
        }
    ]


def test_prepare_openai_responses_request_converts_function_history_and_options() -> None:
    request = prepare_request(
        "openai_responses",
        api_key="oa-key",
        model="gpt-5",
        messages=[
            {"role": "developer", "content": "Be precise."},
            {"role": "user", "content": "weather?"},
            {
                "role": "assistant",
                "content": "Checking.",
                "tool_calls": [
                    {
                        "id": "call_weather",
                        "type": "function",
                        "function": {"name": "weather", "arguments": '{"city":"Paris"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_weather", "content": '{"temp":21}'},
        ],
        tools=TOOLS,
        max_tokens=1000,
        stream=False,
        response_format={"type": "json_schema", "name": "answer", "schema": {"type": "object"}},
        reasoning_effort="medium",
    )

    assert request.headers == {
        "Content-Type": "application/json",
        "Authorization": "Bearer oa-key",
    }
    assert request.payload["max_output_tokens"] == 1000
    assert request.payload["reasoning"] == {"effort": "medium"}
    assert request.payload["text"] == {
        "format": {"type": "json_schema", "name": "answer", "schema": {"type": "object"}}
    }
    assert request.payload["input"][2] == {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "input_text", "text": "Checking."}],
    }
    assert request.payload["input"][-2:] == [
        {
            "type": "function_call",
            "call_id": "call_weather",
            "name": "weather",
            "arguments": '{"city":"Paris"}',
        },
        {"type": "function_call_output", "call_id": "call_weather", "output": '{"temp":21}'},
    ]
    assert request.payload["tools"] == [
        {
            "type": "function",
            "name": "weather",
            "description": "Read the weather",
            "parameters": {
                **TOOLS[0]["function"]["parameters"],
                "additionalProperties": False,
            },
            "strict": True,
        }
    ]


def test_openai_responses_strict_schema_preserves_optional_plugin_fields() -> None:
    tools = [{
        "type": "function",
        "function": {
            "name": "write",
            "description": "Write content",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string", "maxLength": 8000},
                    "mode": {
                        "type": "string",
                        "enum": ["overwrite", "append"],
                        "default": "overwrite",
                    },
                },
                "required": ["path", "content"],
            },
        },
    }]

    request = prepare_request(
        "openai_responses",
        api_key="oa-key",
        model="gpt-5",
        messages=[{"role": "user", "content": "write"}],
        tools=tools,
        max_tokens=None,
        stream=True,
        response_format=None,
    )

    function = request.payload["tools"][0]
    assert function["strict"] is True
    assert function["parameters"]["required"] == ["path", "content", "mode"]
    assert function["parameters"]["additionalProperties"] is False
    assert function["parameters"]["properties"]["mode"] == {
        "anyOf": [
            {"type": "string", "enum": ["overwrite", "append"]},
            {"type": "null"},
        ]
    }


def test_openai_responses_omits_strict_for_freeform_object_schemas() -> None:
    tools = [{
        "type": "function",
        "function": {
            "name": "send_payload",
            "description": "Send an arbitrary payload",
            "parameters": {
                "type": "object",
                "properties": {"payload": {"type": "object"}},
                "required": ["payload"],
            },
        },
    }]

    request = prepare_request(
        "openai_responses",
        api_key="oa-key",
        model="gpt-5",
        messages=[{"role": "user", "content": "send"}],
        tools=tools,
        max_tokens=None,
        stream=True,
        response_format=None,
    )

    function = request.payload["tools"][0]
    assert "strict" not in function
    assert function["parameters"] == tools[0]["function"]["parameters"]


def test_prepare_gemini_request_converts_roles_images_tools_and_schema() -> None:
    request = prepare_request(
        "gemini",
        api_key="gem-key",
        model="gemini-2.5-flash",
        messages=[
            {"role": "system", "content": "Be concise."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is this?"},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,aGVsbG8="}},
                ],
            },
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_weather",
                        "type": "function",
                        "function": {"name": "weather", "arguments": '{"city":"Paris"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_weather", "content": '{"temp":21}'},
        ],
        tools=TOOLS,
        max_tokens=256,
        stream=False,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "answer", "schema": {"type": "object", "properties": {}}},
        },
    )

    assert request.headers == {
        "Content-Type": "application/json",
        "x-goog-api-key": "gem-key",
    }
    assert request.payload["systemInstruction"] == {"parts": [{"text": "Be concise."}]}
    assert request.payload["contents"][0] == {
        "role": "user",
        "parts": [
            {"text": "What is this?"},
            {"inlineData": {"mimeType": "image/jpeg", "data": "aGVsbG8="}},
        ],
    }
    assert request.payload["contents"][1] == {
        "role": "model",
        "parts": [{"functionCall": {"name": "weather", "args": {"city": "Paris"}}}],
    }
    assert request.payload["contents"][2] == {
        "role": "user",
        "parts": [{"functionResponse": {"name": "weather", "response": {"temp": 21}}}],
    }
    assert request.payload["generationConfig"] == {
        "maxOutputTokens": 256,
        "responseMimeType": "application/json",
        "responseSchema": {"type": "object", "properties": {}},
    }
    assert request.payload["tools"] == [
        {
            "functionDeclarations": [
                {
                    "name": "weather",
                    "description": "Read the weather",
                    "parameters": TOOLS[0]["function"]["parameters"],
                }
            ]
        }
    ]


@pytest.mark.parametrize(
    ("adapter", "expected"),
    [
        ("anthropic", {"type": "any"}),
        ("openai_responses", "required"),
        ("gemini", "ANY"),
    ],
)
def test_native_adapters_map_required_tool_choice(adapter, expected) -> None:
    request = prepare_request(
        adapter,
        api_key="key",
        model="model",
        messages=[{"role": "user", "content": "Choose one control action."}],
        tools=TOOLS,
        max_tokens=64,
        stream=False,
        response_format=None,
        tool_choice="required",
    )

    if adapter == "gemini":
        actual = request.payload["toolConfig"]["functionCallingConfig"]["mode"]
    else:
        actual = request.payload["tool_choice"]
    assert actual == expected


@pytest.mark.parametrize(
    ("adapter", "wire", "expected"),
    [
        (
            "anthropic",
            {
                "content": [
                    {"type": "text", "text": "Sunny."},
                    {"type": "tool_use", "id": "toolu_1", "name": "weather", "input": {"city": "Paris"}},
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 10, "output_tokens": 4},
            },
            {
                "content": "Sunny.",
                "finish_reason": "tool_use",
                "tool_id": "toolu_1",
                "tool_name": "weather",
                "tool_arguments": '{"city": "Paris"}',
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            },
        ),
        (
            "openai_responses",
            {
                "status": "completed",
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": "Sunny."}]},
                    {"type": "function_call", "call_id": "call_1", "name": "weather", "arguments": '{"city":"Paris"}'},
                ],
                "usage": {"input_tokens": 11, "output_tokens": 5, "total_tokens": 16},
            },
            {
                "content": "Sunny.",
                "finish_reason": "completed",
                "tool_id": "call_1",
                "tool_name": "weather",
                "tool_arguments": '{"city":"Paris"}',
                "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16},
            },
        ),
        (
            "gemini",
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "Sunny."},
                                {"functionCall": {"name": "weather", "args": {"city": "Paris"}}},
                            ]
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 6, "totalTokenCount": 18},
            },
            {
                "content": "Sunny.",
                "finish_reason": "stop",
                "tool_id": None,
                "tool_name": "weather",
                "tool_arguments": '{"city": "Paris"}',
                "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
            },
        ),
    ],
)
def test_parse_nonstream_responses_normalizes_text_tools_finish_and_usage(
    adapter: str, wire: dict[str, object], expected: dict[str, object]
) -> None:
    parsed = parse_response(adapter, wire)

    assert parsed["role"] == "assistant"
    assert parsed["content"] == expected["content"]
    assert parsed["finish_reason"] == expected["finish_reason"]
    assert parsed["usage"] == expected["usage"]
    call = parsed["tool_calls"][0]
    if expected["tool_id"] is not None:
        assert call["id"] == expected["tool_id"]
    else:
        assert call["id"].startswith("call_gemini_")
    assert call["function"] == {
        "name": expected["tool_name"],
        "arguments": expected["tool_arguments"],
    }


def test_gemini_thought_parts_are_reasoning_not_visible_answer_text() -> None:
    parsed = parse_response(
        "gemini",
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "internal analysis", "thought": True},
                            {"text": "Visible answer."},
                        ]
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 3},
        },
    )

    assert parsed["content"] == "Visible answer."
    assert parsed["reasoning_content"] == "internal analysis"


def test_parse_response_normalizes_provider_cache_usage() -> None:
    anthropic = parse_response(
        "anthropic",
        {
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 120,
                "output_tokens": 12,
                "cache_read_input_tokens": 90,
                "cache_creation_input_tokens": 30,
            },
        },
    )
    gemini = parse_response(
        "gemini",
        {
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
            "usageMetadata": {
                "promptTokenCount": 50,
                "candidatesTokenCount": 5,
                "cachedContentTokenCount": 20,
            },
        },
    )

    assert anthropic["usage"] == {
        "prompt_tokens": 240,
        "completion_tokens": 12,
        "total_tokens": 252,
        "prompt_cache_hit_tokens": 90,
        "prompt_cache_miss_tokens": 150,
    }
    assert gemini["usage"]["prompt_cache_hit_tokens"] == 20
    assert gemini["usage"]["prompt_cache_miss_tokens"] == 30


def _sse_response(events: list[dict[str, object]]) -> httpx.Response:
    body = "".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


@pytest.mark.asyncio
async def test_openai_chat_sse_accumulates_content_reasoning_tools_and_usage() -> None:
    events = [
        {
            "id": "chatcmpl_1",
            "model": "MiniMax-M2.1",
            "choices": [{"index": 0, "delta": {"content": "Hel"}}],
        },
        {
            "id": "chatcmpl_1",
            "choices": [{
                "index": 0,
                "delta": {
                    "content": "lo",
                    "reasoning_content": "Think",
                    "tool_calls": [{
                        "index": 0,
                        "id": "call_1",
                        "function": {"name": "weather", "arguments": '{"city"'},
                    }],
                },
            }],
        },
        {
            "id": "chatcmpl_1",
            "choices": [{
                "index": 0,
                "delta": {"tool_calls": [{
                    "index": 0,
                    "function": {"arguments": ':"Paris"}'},
                }]},
                "finish_reason": "tool_calls",
            }],
        },
        {
            "id": "chatcmpl_1",
            "choices": [],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 8,
                "total_tokens": 108,
                "prompt_tokens_details": {"cached_tokens": 80},
            },
        },
    ]
    callbacks: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True
        return _sse_response(events)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        parsed = await handle_stream(
            "openai_compatible",
            client,
            "https://api.minimaxi.com/v1/chat/completions",
            PreparedRequest({"stream": True}, {"Authorization": "Bearer key"}),
            lambda event: _record_event(callbacks, event),
        )

    assert callbacks == [
        {"type": "reply_start"},
        {"type": "reply_delta", "delta": "Hel"},
        {"type": "reply_delta", "delta": "lo"},
        {"type": "reasoning_start"},
        {"type": "reasoning_delta", "delta": "Think"},
        {"type": "reasoning_done", "response": "Think"},
        {"type": "reply_done", "response": "Hello"},
    ]
    diagnostics = parsed.pop("stream_diagnostics")
    assert diagnostics == {
        "adapter": "openai_compatible",
        "line_count": 9,
        "data_chunk_count": 4,
        "event_count": 4,
        "invalid_json_line_count": 0,
        "saw_done_marker": True,
        "http_status": 200,
        "terminal_event_seen": False,
        "last_event_type": "",
        "termination_reason": "provider_finish_reason",
        "finish_reason": "tool_calls",
        "stream_completed": True,
        "tool_calls": [{
            "index": "0",
            "name": "weather",
            "arguments_length": 16,
            "arguments_sha256": hashlib.sha256(
                b'{"city":"Paris"}'
            ).hexdigest(),
            "arguments_validation": "valid_object",
        }],
    }
    assert parsed == {
        "role": "assistant",
        "content": "Hello",
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 8,
            "total_tokens": 108,
            "prompt_cache_hit_tokens": 80,
            "prompt_cache_miss_tokens": 20,
        },
        "reasoning_content": "Think",
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "weather", "arguments": '{"city":"Paris"}'},
        }],
        "finish_reason": "tool_calls",
        "response_id": "chatcmpl_1",
        "model": "MiniMax-M2.1",
    }


@pytest.mark.asyncio
async def test_stream_rejects_clean_eof_without_provider_terminal_signal() -> None:
    body = b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ModelStreamError) as captured:
            await handle_stream(
                "openai_compatible",
                client,
                "https://provider.test/v1/chat/completions",
                PreparedRequest({"stream": True}, {}),
                None,
            )

    assert captured.value.kind == "upstream_incomplete"
    assert classify_model_error(captured.value).code == "model_response_incomplete"
    assert captured.value.diagnostics["http_status"] == 200
    assert captured.value.diagnostics["event_count"] == 1
    assert captured.value.diagnostics["stream_completed"] is False
    assert captured.value.diagnostics["termination_reason"] == "eof_without_terminal_event"


@pytest.mark.asyncio
async def test_stream_distinguishes_local_protocol_decode_failure() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b"data: {not-json}\n\n",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ModelStreamError) as captured:
            await handle_stream(
                "openai_compatible",
                client,
                "https://provider.test/v1/chat/completions",
                PreparedRequest({"stream": True}, {}),
                None,
            )

    assert captured.value.kind == "protocol_invalid_json"
    assert captured.value.diagnostics["termination_reason"] == "invalid_sse_json"
    assert captured.value.diagnostics["invalid_json_line_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["tool_calls", "length", "max_tokens", "max_output_tokens"])
async def test_stream_rejects_malformed_tool_arguments_with_safe_diagnostics(finish) -> None:
    events = [{
        "choices": [{
            "delta": {
                "tool_calls": [{
                    "index": 0,
                    "id": "call_1",
                    "function": {"name": "weather", "arguments": '{"city"'},
                }],
            },
            "finish_reason": finish,
        }],
    }]

    async def handler(_request: httpx.Request) -> httpx.Response:
        return _sse_response(events)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ModelStreamError) as captured:
            await handle_stream(
                "openai_compatible",
                client,
                "https://provider.test/v1/chat/completions",
                PreparedRequest({"stream": True}, {}),
                None,
            )

    assert captured.value.kind == "invalid_tool_arguments"
    diagnostics = captured.value.diagnostics
    assert diagnostics["finish_reason"] == ("tool_calls" if finish == "tool_calls" else "length")
    assert diagnostics["termination_reason"] == (
        "invalid_tool_arguments" if finish == "tool_calls" else "output_limit_with_invalid_tool_arguments"
    )
    assert classify_model_error(captured.value).code == (
        "model_response_invalid" if finish == "tool_calls" else "model_output_truncated"
    )
    assert diagnostics["tool_calls"] == [{
        "index": "0",
        "name": "weather",
        "arguments_length": 7,
        "arguments_sha256": hashlib.sha256(b'{"city"').hexdigest(),
        "arguments_validation": "invalid_json",
    }]
    assert '{"city"' not in json.dumps(diagnostics)


@pytest.mark.asyncio
async def test_developer_protocol_trace_is_explicit_and_observational() -> None:
    events = [{"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}]
    trace: list[dict[str, object]] = []

    async def handler(_request: httpx.Request) -> httpx.Response:
        return _sse_response(events)

    async def record(event: dict[str, object]) -> None:
        trace.append(event)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await handle_stream(
            "openai_compatible",
            client,
            "https://provider.test/v1/chat/completions",
            PreparedRequest({"stream": True}, {}),
            None,
            protocol_trace=record,
        )

    assert trace[0] == {
        "type": "response_start",
        "adapter": "openai_compatible",
        "status_code": 200,
    }
    assert any(item.get("type") == "response_line" for item in trace)
    assert trace[-1]["type"] == "response_end"


async def _record_event(
    target: list[dict[str, object]],
    event: dict[str, object],
) -> None:
    target.append(event)


@pytest.mark.asyncio
async def test_anthropic_sse_accumulates_text_tool_arguments_usage_and_finish() -> None:
    events = [
        {
            "type": "message_start",
            "message": {
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 0,
                    "cache_read_input_tokens": 40,
                    "cache_creation_input_tokens": 5,
                }
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "weather", "input": {}}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"city"'}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": ':"Paris"}'}},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 7}},
    ]
    callbacks: list[dict[str, object]] = []

    async def record_callback(event: dict[str, object]) -> None:
        callbacks.append(event)

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "ant-key"
        return _sse_response(events)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        parsed = await handle_stream(
            "anthropic",
            client,
            "https://api.anthropic.test/v1/messages",
            PreparedRequest({"stream": True}, {"x-api-key": "ant-key"}),
            record_callback,
        )

    parsed.pop("stream_diagnostics")
    assert callbacks == [
        {"type": "reply_start"},
        {"type": "reply_delta", "delta": "Hello"},
        {"type": "reply_done", "response": "Hello"},
    ]
    assert parsed == {
        "role": "assistant",
        "content": "Hello",
        "usage": {
            "prompt_tokens": 55,
            "completion_tokens": 7,
            "total_tokens": 62,
            "prompt_cache_hit_tokens": 40,
            "prompt_cache_miss_tokens": 15,
        },
        "tool_calls": [
            {
                "id": "toolu_1",
                "type": "function",
                "function": {"name": "weather", "arguments": '{"city":"Paris"}'},
            }
        ],
        "finish_reason": "tool_use",
    }


@pytest.mark.asyncio
async def test_anthropic_sse_accepts_complete_usage_in_message_delta() -> None:
    events = [
        {
            "type": "message_start",
            "message": {"usage": {"output_tokens": 0}},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hello"},
        },
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {
                "input_tokens": 15,
                "output_tokens": 7,
                "cache_read_input_tokens": 40,
                "cache_creation_input_tokens": 5,
            },
        },
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        return _sse_response(events)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        parsed = await handle_stream(
            "anthropic",
            client,
            "https://api.minimax.test/anthropic/v1/messages",
            PreparedRequest({"stream": True}, {"x-api-key": "key"}),
            None,
        )

    parsed.pop("stream_diagnostics")
    assert parsed["usage"] == {
        "prompt_tokens": 60,
        "completion_tokens": 7,
        "total_tokens": 67,
        "prompt_cache_hit_tokens": 40,
        "prompt_cache_miss_tokens": 20,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "finalization_events",
    [
        pytest.param(
            [{
                "type": "response.function_call_arguments.done",
                "output_index": 1,
                "item_id": "fc_1",
                "arguments": '{"city":"Paris"}',
            }],
            id="arguments-done",
        ),
        pytest.param(
            [{
                "type": "response.output_item.done",
                "output_index": 1,
                "item": {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "weather",
                    "arguments": '{"city":"Paris"}',
                },
            }],
            id="output-item-done",
        ),
        pytest.param(
            [],
            id="response-completed",
        ),
    ],
)
async def test_openai_responses_sse_uses_authoritative_tool_arguments(
    finalization_events: list[dict[str, object]],
) -> None:
    events = [
        {"type": "response.output_text.delta", "output_index": 0, "delta": "Hel"},
        {"type": "response.output_text.delta", "output_index": 0, "delta": "lo"},
        {
            "type": "response.output_item.added",
            "output_index": 1,
            "item": {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "weather", "arguments": ""},
        },
        {"type": "response.function_call_arguments.delta", "output_index": 1, "item_id": "fc_1", "delta": '{"city":'},
        {"type": "response.function_call_arguments.delta", "output_index": 1, "item_id": "fc_1", "delta": '"Par'},
        *finalization_events,
        {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "usage": {"input_tokens": 20, "output_tokens": 8, "total_tokens": 28},
                "output": (
                    [{
                        "type": "function_call",
                        "id": "fc_1",
                        "call_id": "call_1",
                        "name": "weather",
                        "arguments": '{"city":"Paris"}',
                    }]
                    if not finalization_events
                    else []
                ),
            },
        },
    ]

    async def handler(_request: httpx.Request) -> httpx.Response:
        return _sse_response(events)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        parsed = await handle_stream(
            "openai_responses",
            client,
            "https://api.openai.test/v1/responses",
            PreparedRequest({"stream": True}, {"Authorization": "Bearer oa-key"}),
            None,
        )

    parsed.pop("stream_diagnostics")
    assert parsed == {
        "role": "assistant",
        "content": "Hello",
        "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "weather", "arguments": '{"city":"Paris"}'},
            }
        ],
        "finish_reason": "completed",
    }


@pytest.mark.asyncio
async def test_gemini_sse_requests_stream_route_and_accumulates_chunks() -> None:
    events = [
        {"candidates": [{"content": {"parts": [{"text": "Hel"}]}}]},
        {
            "candidates": [{"content": {"parts": [{"text": "lo"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 6, "candidatesTokenCount": 2, "totalTokenCount": 8},
        },
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == (
            "https://generativelanguage.googleapis.com/v1beta/"
            "models/gemini-2.5-flash:streamGenerateContent?alt=sse"
        )
        assert request.headers["x-goog-api-key"] == "gem-key"
        return _sse_response(events)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        parsed = await handle_stream(
            "gemini",
            client,
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
            PreparedRequest({"contents": [], "stream": True}, {"x-goog-api-key": "gem-key"}),
            None,
        )

    parsed.pop("stream_diagnostics")
    assert parsed == {
        "role": "assistant",
        "content": "Hello",
        "usage": {"prompt_tokens": 6, "completion_tokens": 2, "total_tokens": 8},
        "finish_reason": "stop",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("reason,arguments,code", [
    ("max_output_tokens", '{"city":', "model_output_truncated"),
    ("max_output_tokens", '{"city":"Paris"}', None),
    ("max_output_tokens", None, "model_output_truncated"),
    ("content_filter", None, "model_response_incomplete"),
    (None, None, "model_response_incomplete"),
])
async def test_responses_incomplete_terminal_event(reason, arguments, code):
    output = [] if arguments is None else [{
        "type": "function_call", "id": "item_1", "call_id": "call_1",
        "name": "weather", "arguments": arguments,
    }]
    events = [{"type": "response.incomplete", "response": {
        "id": "resp_1", "status": "incomplete", "output": output,
        "incomplete_details": {"reason": reason},
        "usage": {"input_tokens": 20, "output_tokens": 4096},
    }}]
    async def handler(_request):
        return _sse_response(events)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        call = handle_stream("openai_responses", client, "https://provider.test/v1/responses",
                             PreparedRequest({"stream": True}, {}), None)
        if code:
            with pytest.raises(ModelStreamError) as captured:
                await call
            assert classify_model_error(captured.value).code == code
            assert captured.value.diagnostics["terminal_event_seen"] is True
            assert captured.value.diagnostics["termination_reason"] != "eof_without_terminal_event"
        else:
            result = await call
            assert result["finish_reason"] == "length"
            assert result["tool_calls"][0]["function"]["arguments"] == arguments


class _DisconnectAfterEvents(httpx.AsyncByteStream):
    def __init__(self, events, done=False):
        self.body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        if done:
            self.body += "data: [DONE]\n\n"

    async def __aiter__(self):
        yield self.body.encode()
        raise httpx.ReadError("connection closed after events")


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter,events,done", [
    ("openai_compatible", [{"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}], False),
    ("openai_compatible", [{"choices": [{"delta": {"content": "ok"}}]}], True),
    ("anthropic", [
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok"}},
        {"type": "message_stop"},
    ], False),
    ("openai_responses", [
        {"type": "response.output_text.delta", "delta": "ok"},
        {"type": "response.completed", "response": {"status": "completed", "output": []}},
    ], False),
])
async def test_successful_terminal_survives_tail_disconnect(adapter, events, done):
    async def handler(_request):
        return httpx.Response(200, stream=_DisconnectAfterEvents(events, done),
                              headers={"content-type": "text/event-stream"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await handle_stream(adapter, client, "https://provider.test/stream",
                                     PreparedRequest({"stream": True}, {}), None)
    assert result["content"] == "ok"
    assert result["stream_diagnostics"]["stream_completed"] is True


@pytest.mark.asyncio
async def test_tail_disconnect_preserves_usage_after_finish_reason():
    events = [
        {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}},
    ]
    async def handler(_request):
        return httpx.Response(200, stream=_DisconnectAfterEvents(events),
                              headers={"content-type": "text/event-stream"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await handle_stream("openai_compatible", client, "https://provider.test/stream",
                                     PreparedRequest({"stream": True}, {}), None)
    assert result["usage"]["completion_tokens"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("finish,arguments,expected", [
    (None, None, "model_connection_failed"),
    ("length", '{"path":', "model_output_truncated"),
    ("tool_calls", '{"path":', "model_response_invalid"),
])
async def test_tail_disconnect_does_not_accept_unfinished_response(finish, arguments, expected):
    delta = {"content": "partial"}
    if arguments is not None:
        delta["tool_calls"] = [{"index": 0, "id": "call_1", "function": {
            "name": "Write", "arguments": arguments,
        }}]
    events = [{"choices": [{"delta": delta, "finish_reason": finish}]}]
    async def handler(_request):
        return httpx.Response(200, stream=_DisconnectAfterEvents(events),
                              headers={"content-type": "text/event-stream"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ModelStreamError) as captured:
            await handle_stream("openai_compatible", client, "https://provider.test/stream",
                                PreparedRequest({"stream": True}, {}), None)
    assert classify_model_error(captured.value).code == expected


class _CloseFailureStream(httpx.AsyncByteStream):
    def __init__(self, events):
        self.body = "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()
        self.close_attempts = 0

    async def __aiter__(self):
        yield self.body

    async def aclose(self):
        self.close_attempts += 1
        raise httpx.ReadError("cleanup failed")


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments,expected", [
    ('{"city":"Paris"}', None),
    ('{"city":', "model_output_truncated"),
])
async def test_cleanup_failure_preserves_terminal_response_or_validation_error(arguments, expected):
    stream = _CloseFailureStream([{"type": "response.incomplete", "response": {
        "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
        "output": [{"type": "function_call", "id": "item_1", "call_id": "call_1",
                    "name": "weather", "arguments": arguments}],
    }}])
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _r: httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})
    )) as client:
        call = handle_stream("openai_responses", client, "https://provider.test/stream",
                             PreparedRequest({"stream": True}, {}), None)
        if expected:
            with pytest.raises(ModelStreamError) as captured:
                await call
            assert classify_model_error(captured.value).code == expected
        else:
            result = await call
            assert result["tool_calls"][0]["function"]["arguments"] == arguments
    assert stream.close_attempts >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize("code,expected", [
    ("server_error", "model_service_unavailable"),
    ("rate_limit_exceeded", "model_rate_limited"),
    ("insufficient_quota", "model_quota_exhausted"),
    ("invalid_api_key", "model_authentication_failed"),
    ("context_length_exceeded", "model_request_too_large"),
    ("invalid_prompt", "model_request_invalid"),
    ("model_not_found", "model_unavailable"),
    ("private-unknown-code", "model_call_failed"),
])
async def test_responses_failed_preserves_cause_even_when_cleanup_fails(code, expected):
    from cyrene.model.error_details import ModelCallError
    stream = _CloseFailureStream([{"type": "response.failed", "response": {
        "status": "failed", "error": {"code": code, "message": "private-upstream-text"},
    }}])
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _r: httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})
    )) as client:
        with pytest.raises(ModelStreamError) as captured:
            await handle_stream("openai_responses", client, "https://provider.test/stream",
                                PreparedRequest({"stream": True}, {}), None)
    error = captured.value
    assert error.kind == "provider_failed"
    assert error.diagnostics["termination_reason"] == "provider_response_failed"
    details = ModelCallError(classify_model_error(error), diagnostics=error.diagnostics).as_error_details()
    assert details["code"] == expected
    assert "private-" not in json.dumps(details)
    assert details["stream_diagnostics"]["provider_error_code"] == (
        "unknown" if code == "private-unknown-code" else code
    )


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_hide_callback_cancellation():
    import asyncio
    stream = _CloseFailureStream([{"choices": [{"delta": {"content": "partial"}}]}])
    async def cancel(_event):
        raise asyncio.CancelledError()
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _r: httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})
    )) as client:
        with pytest.raises(asyncio.CancelledError):
            await handle_stream("openai_compatible", client, "https://provider.test/stream",
                                PreparedRequest({"stream": True}, {}), cancel)
    assert stream.close_attempts >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 429, 503])
async def test_cleanup_preserves_success_and_http_failure(status):
    stream = _CloseFailureStream([{"type": "response.completed", "response": {
        "status": "completed", "output": [], "usage": {"input_tokens": 10, "output_tokens": 2},
    }}])
    emitted = []
    async def callback(event):
        emitted.append(event)
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _r: httpx.Response(status, stream=stream, headers={"content-type": "text/event-stream"})
    )) as client:
        call = handle_stream("openai_responses", client, "https://provider.test/stream",
                             PreparedRequest({"stream": True}, {}), callback)
        if status == 200:
            result = await call
            assert result["stream_diagnostics"]["stream_completed"] is True
            assert result["usage"]["completion_tokens"] == 2
            assert sum(e["type"] == "reply_done" for e in emitted) == 1
        else:
            with pytest.raises(httpx.HTTPStatusError) as captured:
                await call
            assert captured.value.response.status_code == status
            assert not emitted
    assert stream.close_attempts >= 1
