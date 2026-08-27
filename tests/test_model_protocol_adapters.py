from __future__ import annotations

import json

import httpx
import pytest

from cyrene.model_runtime.protocol_adapters import (
    PreparedRequest,
    discovery_request,
    handle_stream,
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
    assert request.payload["system"] == "Be concise."
    assert request.payload["max_tokens"] == 512
    assert request.payload["stream"] is True
    assert request.payload["cache_control"] == {"type": "ephemeral"}
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
            {"type": "tool_result", "tool_use_id": "toolu_weather", "content": '{"temp":21}'}
        ],
    }
    assert request.payload["tools"] == [
        {
            "name": "weather",
            "description": "Read the weather",
            "input_schema": TOOLS[0]["function"]["parameters"],
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
            "parameters": TOOLS[0]["function"]["parameters"],
        }
    ]


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
async def test_openai_responses_sse_uses_named_events_and_accumulates_tool_arguments() -> None:
    events = [
        {"type": "response.output_text.delta", "output_index": 0, "delta": "Hel"},
        {"type": "response.output_text.delta", "output_index": 0, "delta": "lo"},
        {
            "type": "response.output_item.added",
            "output_index": 1,
            "item": {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "weather", "arguments": ""},
        },
        {"type": "response.function_call_arguments.delta", "output_index": 1, "item_id": "fc_1", "delta": '{"city"'},
        {"type": "response.function_call_arguments.delta", "output_index": 1, "item_id": "fc_1", "delta": ':"Paris"}'},
        {
            "type": "response.completed",
            "response": {"status": "completed", "usage": {"input_tokens": 20, "output_tokens": 8, "total_tokens": 28}},
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

    assert parsed == {
        "role": "assistant",
        "content": "Hello",
        "usage": {"prompt_tokens": 6, "completion_tokens": 2, "total_tokens": 8},
        "finish_reason": "stop",
    }
