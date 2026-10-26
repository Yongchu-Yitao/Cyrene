"""Full tool diagnostics belong to stream outcomes, not argument fragments."""

import asyncio
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from cyrene.model import protocol_adapters as adapters


def sse(event):
    return ("data: " + json.dumps(event) + "\n\n").encode()


def tool_delta(arguments, *, index=0, name="Write", finish=None):
    return {"choices": [{
        "delta": {"tool_calls": [{
            "index": index, "id": f"call_{index}",
            "function": {"name": name, "arguments": arguments},
        }]},
        "finish_reason": finish,
    }]}


class Stream(httpx.AsyncByteStream):
    def __init__(self, parts, *, before_part=None, close_error=False):
        self.parts = parts
        self.before_part = before_part
        self.close_error = close_error
        self.closed = False

    async def __aiter__(self):
        for part in self.parts:
            if self.before_part:
                self.before_part()
            if isinstance(part, BaseException):
                raise part
            yield part

    async def aclose(self):
        self.closed = True
        if self.close_error:
            raise httpx.ReadError("cleanup failed")


async def consume(stream, *, adapter="openai_compatible", callback=None, trace=None):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _request: httpx.Response(
            200, stream=stream, headers={"content-type": "text/event-stream"},
        ),
    )) as client:
        return await adapters.handle_stream(
            adapter, client, "https://provider.test/generateContent",
            adapters.PreparedRequest({"stream": True}, {}), callback,
            protocol_trace=trace,
        )


@pytest.fixture
def observed_diagnostics(monkeypatch):
    snapshots, hashed = [], []
    original = adapters._tool_stream_diagnostics
    real_sha256 = hashlib.sha256

    def observe(calls):
        snapshots.append(deepcopy(calls))
        return original(calls)

    def sha256(value):
        hashed.append(value)
        return real_sha256(value)

    monkeypatch.setattr(adapters, "_tool_stream_diagnostics", observe)
    monkeypatch.setattr(adapters, "hashlib", SimpleNamespace(sha256=sha256))
    return snapshots, hashed


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ["openai_compatible", "anthropic", "openai_responses", "gemini"])
async def test_success_hashes_final_arguments_once_and_reuses_trace_snapshot(adapter, observed_diagnostics):
    snapshots, hashed = observed_diagnostics
    arguments = json.dumps({"content": "你好🌍" * 1000}, ensure_ascii=False)
    fragments = [arguments[offset:offset + 97] for offset in range(0, len(arguments), 97)]
    if adapter == "openai_compatible":
        events = [tool_delta(fragment, name="Write" if i == 0 else "")
                  for i, fragment in enumerate(fragments)]
        events += [
            {"choices": [{"delta": {"content": "ok"}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 9}},
        ]
    elif adapter == "anthropic":
        events = [{"type": "content_block_start", "index": 0,
                   "content_block": {"type": "tool_use", "id": "c", "name": "Write"}}]
        events += [{"type": "content_block_delta", "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": fragment}}
                   for fragment in fragments]
        events += [{"type": "message_delta", "delta": {"stop_reason": "tool_use"},
                    "usage": {"output_tokens": 9}}, {"type": "message_stop"}]
    elif adapter == "openai_responses":
        events = [{"type": "response.output_item.added", "output_index": 0,
                   "item": {"type": "function_call", "id": "c", "call_id": "c", "name": "Write"}}]
        events += [{"type": "response.function_call_arguments.delta", "output_index": 0,
                    "delta": fragment} for fragment in fragments]
        # The final provider snapshot may replace all accumulated arguments.
        arguments = json.dumps({"content": "最终版本"}, ensure_ascii=False)
        events += [{"type": "response.completed", "response": {
            "status": "completed", "output": [{"type": "function_call", "id": "c", "call_id": "c",
            "name": "Write", "arguments": arguments}], "usage": {"output_tokens": 9},
        }}]
    else:
        events = [{"candidates": [{"content": {"parts": [{"functionCall": {
            "name": "Write", "args": json.loads(arguments),
        }}]}}]}]
        events += [{"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
                    "usageMetadata": {"candidatesTokenCount": 9}}]

    def before_part():
        assert snapshots == [], "Full diagnostics ran before the stream outcome"

    traces = []
    async def trace(event):
        if event["type"] == "response_end":
            traces.append(deepcopy(event["diagnostics"]))
            # A trace observer must not mutate the cached final diagnostics.
            event["diagnostics"]["tool_calls"][0]["arguments_sha256"] = "tampered"

    stream = Stream([*(sse(event) for event in events), b"data: [DONE]\n\n"], before_part=before_part)
    result = await consume(stream, adapter=adapter, trace=trace)
    assert stream.closed
    assert len(snapshots) == 1
    assert hashed == [arguments.encode()]
    assert result["tool_calls"][0]["function"]["arguments"] == arguments
    diagnostic = result["stream_diagnostics"]
    assert diagnostic["tool_calls"][0]["arguments_sha256"] == hashlib.sha256(arguments.encode()).hexdigest()
    assert diagnostic["tool_calls"][0]["arguments_validation"] == "valid_object"
    assert traces == [diagnostic]
    assert result["usage"]["completion_tokens"] == 9


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,kind", [
    ("disconnect", "transport_interrupted"),
    ("timeout", "transport_interrupted"),
    ("bad_json", "protocol_invalid_json"),
    ("bad_event", "protocol_invalid_event"),
    ("bad_utf8", "protocol_invalid_utf8"),
    ("eof", "upstream_incomplete"),
    ("eof_close_error", "transport_interrupted"),
    ("invalid_arguments", "invalid_tool_arguments"),
])
async def test_failure_preserves_partial_tool_snapshot_once(failure, kind, observed_diagnostics):
    snapshots, hashed = observed_diagnostics
    arguments = '{"content":"private unfinished'
    parts = [sse(tool_delta(arguments))]
    parts += {
        "disconnect": [httpx.ReadError("lost connection")],
        "timeout": [httpx.ReadTimeout("timeout")],
        "bad_json": [b"data: {broken\n\n"],
        "bad_event": [b"data: []\n\n"],
        "bad_utf8": [b"\xff\n"],
        "eof": [],
        "eof_close_error": [],
        "invalid_arguments": [sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}), b"data: [DONE]\n\n"],
    }[failure]
    # HTTPX also closes at EOF inside aiter_bytes. A failure there is a
    # transport error; a clean EOF without a finish marker is incomplete.
    stream = Stream(parts, close_error=failure != "eof")
    with pytest.raises(adapters.ModelStreamError) as captured:
        await consume(stream)
    assert stream.closed
    assert captured.value.kind == kind
    assert len(snapshots) == 1
    assert hashed == [arguments.encode()]
    assert captured.value.diagnostics["tool_calls"] == [{
        "index": "0", "name": "Write", "arguments_length": len(arguments),
        "arguments_sha256": hashlib.sha256(arguments.encode()).hexdigest(),
        "arguments_validation": "invalid_json",
    }]
    assert "private unfinished" not in json.dumps(captured.value.diagnostics)


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal,kind", [("failed", "provider_failed"), ("incomplete", "upstream_incomplete")])
async def test_decoder_failure_does_not_recompute_snapshot(terminal, kind, observed_diagnostics):
    snapshots, hashed = observed_diagnostics
    arguments = '{"content":'
    events = [
        {"type": "response.output_item.added", "output_index": 0,
         "item": {"type": "function_call", "id": "c", "name": "Write", "arguments": arguments}},
        {"type": f"response.{terminal}", "response": {
            "status": terminal, "error": {"code": "server_error"},
            "incomplete_details": {"reason": "content_filter"},
        }},
    ]
    with pytest.raises(adapters.ModelStreamError) as captured:
        await consume(Stream([sse(event) for event in events], close_error=True), adapter="openai_responses")
    assert captured.value.kind == kind
    assert len(snapshots) == 1
    assert hashed == [arguments.encode()]
    assert captured.value.diagnostics["tool_calls"][0]["arguments_length"] == len(arguments)


@pytest.mark.asyncio
async def test_multiple_interleaved_tools_are_sorted_and_each_hashed_once(observed_diagnostics):
    snapshots, hashed = observed_diagnostics
    events = [tool_delta('{"a":', index=2), tool_delta('{"b":', index=0),
              tool_delta('2}', index=2, name=""), tool_delta('1}', index=0, name="", finish="tool_calls")]
    result = await consume(Stream([*(sse(event) for event in events), b"data: [DONE]\n\n"]))
    assert len(snapshots) == 1
    assert hashed == [b'{"b":1}', b'{"a":2}']
    assert [call["index"] for call in result["stream_diagnostics"]["tool_calls"]] == ["0", "2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments,validation", [
    ('{"content":"complete"}', "valid_object"),
    ('{"content":', "invalid_json"),
    ('["not an object"]', "not_object"),
    ("", "valid_object"),
])
async def test_output_limit_still_validates_arguments_once(arguments, validation, observed_diagnostics):
    snapshots, hashed = observed_diagnostics
    stream = Stream([sse(tool_delta(arguments, finish="length")), b"data: [DONE]\n\n"])
    if validation == "valid_object":
        result = await consume(stream)
        diagnostic = result["stream_diagnostics"]
        assert result["finish_reason"] == "length"
    else:
        with pytest.raises(adapters.ModelStreamError) as captured:
            await consume(stream)
        assert captured.value.kind == "invalid_tool_arguments"
        diagnostic = captured.value.diagnostics
        assert diagnostic["termination_reason"] == "output_limit_with_invalid_tool_arguments"
    assert diagnostic["tool_calls"][0]["arguments_validation"] == validation
    assert len(snapshots) == 1
    assert hashed == [arguments.encode()]


@pytest.mark.asyncio
async def test_text_only_stream_never_hashes_tool_arguments(observed_diagnostics):
    snapshots, hashed = observed_diagnostics
    result = await consume(Stream([
        sse({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}),
        b"data: [DONE]\n\n",
    ]))
    assert result["content"] == "ok"
    assert result["stream_diagnostics"]["tool_calls"] == []
    assert snapshots == []
    assert hashed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("callback_failure", [False, True])
async def test_cancellation_does_not_generate_diagnostics_or_get_masked(callback_failure, observed_diagnostics):
    snapshots, hashed = observed_diagnostics
    parts = [sse(tool_delta('{"content":'))]
    parts += [sse({"choices": [{"delta": {"content": "partial"}}]})] if callback_failure else [asyncio.CancelledError()]
    async def callback(_event):
        raise asyncio.CancelledError()
    stream = Stream(parts, close_error=True)
    with pytest.raises(asyncio.CancelledError):
        await consume(stream, callback=callback if callback_failure else None)
    assert stream.closed
    assert snapshots == []
    assert hashed == []


@pytest.mark.asyncio
async def test_diagnostic_error_cannot_mask_transport_error(monkeypatch):
    def fail(_calls):
        raise ValueError("diagnostic failure")
    monkeypatch.setattr(adapters, "_tool_stream_diagnostics", fail)
    with pytest.raises(adapters.ModelStreamError) as captured:
        await consume(Stream([sse(tool_delta('{"content":')), httpx.ReadError("lost")]))
    assert captured.value.kind == "transport_interrupted"
