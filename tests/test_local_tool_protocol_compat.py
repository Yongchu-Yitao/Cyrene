import json

import httpx
import pytest

from cyrene.call_llm import (
    _accumulate_tool_call_deltas,
    _finalize_tool_call_fragments,
    _handle_stream,
    _normalize_tool_call_protocol,
)
from cyrene.model_runtime.messages import parse_tool_arguments


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "Read",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "quit",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def test_structured_call_accepts_object_arguments_and_generates_id():
    normalized = _normalize_tool_call_protocol(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "read", "arguments": {"path": "a.txt"}}},
            ],
        },
        TOOLS,
    )

    call = normalized["tool_calls"][0]
    assert call["id"].startswith("call_compat_")
    assert call["function"]["name"] == "Read"
    assert json.loads(call["function"]["arguments"]) == {"path": "a.txt"}


def test_legacy_function_call_is_promoted_to_tool_calls():
    normalized = _normalize_tool_call_protocol(
        {
            "role": "assistant",
            "content": "",
            "function_call": {
                "name": "Read",
                "arguments": "{'path': 'legacy.txt'}",
            },
        },
        TOOLS,
    )

    assert "function_call" not in normalized
    assert json.loads(
        normalized["tool_calls"][0]["function"]["arguments"]
    ) == {"path": "legacy.txt"}


def test_hermes_json_tool_call_block_is_executable():
    normalized = _normalize_tool_call_protocol(
        {
            "role": "assistant",
            "content": (
                "Checking now.\n"
                "<tool_call>"
                '{"name":"Read","arguments":{"path":"notes.md"}}'
                "</tool_call>"
            ),
        },
        TOOLS,
    )

    assert normalized["content"] == "Checking now."
    assert normalized["tool_calls"][0]["function"]["name"] == "Read"
    assert json.loads(
        normalized["tool_calls"][0]["function"]["arguments"]
    ) == {"path": "notes.md"}


def test_qwen_xml_tool_call_block_is_executable():
    normalized = _normalize_tool_call_protocol(
        {
            "role": "assistant",
            "content": (
                "<tool_call><function=Read>"
                "<parameter=path>notes.md</parameter>"
                "</function></tool_call>"
            ),
        },
        TOOLS,
    )

    assert normalized["content"] == ""
    assert json.loads(
        normalized["tool_calls"][0]["function"]["arguments"]
    ) == {"path": "notes.md"}


def test_bare_json_action_is_recognized_only_for_available_tool():
    normalized = _normalize_tool_call_protocol(
        {
            "role": "assistant",
            "content": '{"name":"Read","arguments":{"path":"bare.txt"}}',
        },
        TOOLS,
    )
    ordinary = _normalize_tool_call_protocol(
        {
            "role": "assistant",
            "content": '{"name":"not_a_tool","arguments":{"path":"bare.txt"}}',
        },
        TOOLS,
    )

    assert normalized["content"] == ""
    assert normalized["tool_calls"][0]["function"]["name"] == "Read"
    assert "tool_calls" not in ordinary


def test_argument_parser_accepts_fences_and_trailing_commas():
    assert parse_tool_arguments(
        '```json\n{"path":"fenced.txt",}\n```'
    ) == {"path": "fenced.txt"}


def test_streamed_object_arguments_are_assembled():
    fragments = {}
    _accumulate_tool_call_deltas(
        [{
            "index": 0,
            "function": {
                "name": "Read",
                "arguments": {"path": "streamed.txt"},
            },
        }],
        fragments,
    )

    calls = _finalize_tool_call_fragments(fragments)
    assert json.loads(calls[0]["function"]["arguments"]) == {
        "path": "streamed.txt",
    }


def test_streamed_single_tool_call_object_is_assembled():
    fragments = {}
    _accumulate_tool_call_deltas(
        {
            "index": 0,
            "function": {
                "name": "Read",
                "arguments": '{"path":"single.txt"}',
            },
        },
        fragments,
    )

    calls = _finalize_tool_call_fragments(fragments)
    assert json.loads(calls[0]["function"]["arguments"]) == {
        "path": "single.txt",
    }


@pytest.mark.asyncio
async def test_legacy_stream_function_call_is_assembled():
    stream_body = "\n\n".join([
        'data: {"choices":[{"delta":{"function_call":{"name":"Read","arguments":""}}}]}',
        'data: {"choices":[{"delta":{"function_call":{"arguments":"{\\"path\\":"}}}]}',
        'data: {"choices":[{"delta":{"function_call":{"arguments":"\\"legacy-stream.txt\\"}"}}}]}',
        "data: [DONE]",
    ])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=stream_body)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        message = await _handle_stream(
            client,
            "https://local.test/v1/chat/completions",
            {"messages": []},
            {},
            None,
        )

    call = message["tool_calls"][0]
    assert call["function"]["name"] == "Read"
    assert json.loads(call["function"]["arguments"]) == {
        "path": "legacy-stream.txt",
    }
