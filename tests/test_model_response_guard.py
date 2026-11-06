"""New provider-plugin boundary rejects textual DSML before delivery/execution."""

from contextlib import asynccontextmanager
import json

import httpx
import pytest

from cyrene.core.plugin import PluginContext
from cyrene.model.error_details import ModelCallError
from cyrene.plugins.builtin.cyrene_model import _shared
from cyrene.plugins.builtin.cyrene_model.deepseek import DEEPSEEK_PLUGIN
from cyrene.plugins.builtin.cyrene_model.response_guard import ResponseStreamGuard


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", [
    "<｜｜DSML｜｜invoke name=\"task_update\">",
    "</｜｜DSML｜｜ parameter>",
    "</||DSML||calls>",
    "<||DSM",  # truncated response
])
async def test_every_chunk_boundary_blocks_markers_and_cancels_attempt(marker):
    for boundary in range(len(marker) + 1):
        events = []

        async def emit(event):
            events.append(event)

        guard = ResponseStreamGuard(emit)
        with pytest.raises(ModelCallError) as caught:
            await guard({"type": "reply_start"})
            await guard({"type": "reply_delta", "delta": "history " + marker[:boundary]})
            await guard({"type": "reply_delta", "delta": marker[boundary:]})
            await guard({"type": "reply_done", "response": "history " + marker})
        assert caught.value.details.code == "model_response_invalid"
        assert caught.value.details.retry_scope == "immediate"
        assert "".join(e.get("delta", "") for e in events) in {"", "history "}
        assert events[-1]["type"] == "stream_cancelled"
        assert not any(e["type"] == "reply_done" for e in events)


@pytest.mark.asyncio
async def test_authoritative_done_text_is_checked_without_deltas():
    events = []

    async def emit(event):
        events.append(event)

    with pytest.raises(ModelCallError):
        await ResponseStreamGuard(emit)({
            "type": "reply_done", "response": "</||DSML||calls>",
        })
    assert events == [{"type": "stream_cancelled"}]


@pytest.mark.asyncio
async def test_normal_text_and_reset_preserve_stream_contract():
    events = []

    async def emit(event):
        events.append(event)

    guard = ResponseStreamGuard(emit)
    await guard({"type": "reply_delta", "delta": "<||D"})
    await guard({"type": "reply_start", "reset": True})
    text = "DSML is a format; a < b; <div>你好</div>; trailing <"
    for char in text:
        await guard({"type": "reply_delta", "delta": char})
    await guard({"type": "reply_done", "response": text})
    assert "".join(e.get("delta", "") for e in events) == text
    assert events[-1] == {"type": "reply_done", "response": text}


@pytest.mark.asyncio
@pytest.mark.parametrize("with_callback", [True, False])
@pytest.mark.parametrize("bad", [True, False])
async def test_deepseek_plugin_sse_boundary(monkeypatch, with_callback, bad):
    # The observed incident: history echo + orphan DSML closer, finish=stop,
    # no structured calls. Also check genuine calls remain unmodified.
    text = ('[Historical tool calls; completed records, results discarded]\n'
            '[{"name":"task_update"}]</｜｜DSML｜｜ parameter>')
    call = {"index": 0, "id": "call_1", "type": "function", "function": {
        "name": "task_update", "arguments": '{"task_id":"existing"}',
    }}
    deltas = [{"content": char} for char in text] if bad else [{"tool_calls": [call]}]
    chunks = [{"choices": [{"delta": delta}]} for delta in deltas]
    chunks.append({"choices": [{"delta": {}, "finish_reason": "stop" if bad else "tool_calls"}]})
    body = "".join("data: " + json.dumps(e) + "\n\n" for e in chunks) + "data: [DONE]\n\n"

    @asynccontextmanager
    async def client(*args, **kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=body,
                headers={"content-type": "text/event-stream"}),
        )) as value:
            yield value

    monkeypatch.setattr(_shared, "_model_client", client)
    events = []

    async def emit(event):
        events.append(event)

    context = PluginContext(
        data={"model_connection": {"api_key": "test", "base_url": "https://provider.test/v1"}},
        services={"model_stream": emit} if with_callback else {},
    )
    args = {"messages": [{"role": "user", "content": "continue"}]}
    if bad:
        with pytest.raises(ModelCallError) as caught:
            await DEEPSEEK_PLUGIN.handler(args, context)
        assert caught.value.details.code == "model_response_invalid"
        assert caught.value.details.retryable
        assert caught.value.details.retry_scope == "immediate"
        assert "DSML" not in json.dumps(events, ensure_ascii=False)
        assert not any(e["type"] == "reply_done" for e in events)
        if with_callback:
            assert any(e["type"] == "stream_cancelled" for e in events)
    else:
        result = await DEEPSEEK_PLUGIN.handler(args, context)
        assert result["tool_calls"][0]["function"] == call["function"]
        assert result["content"] == ""
