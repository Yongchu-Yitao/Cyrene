"""Candidate resilience in cyrene.call_llm — fallback, timeout, resolution.

Regression tests for the 2026-06-11 latency incident: a dead LAN endpoint in the
model list added ~120s to every LLM call.  The normalized primary route is the
sole ordered source of truth, with no phantom env candidate prepended.
"""
import json
import socket
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import cyrene.call_llm as cl


@pytest.fixture(autouse=True)
def _clean_cooldowns():
    cl._candidate_cooldowns.clear()
    cl._published_fallback_notices.clear()
    yield
    cl._candidate_cooldowns.clear()
    cl._published_fallback_notices.clear()


def test_deepseek_legacy_disabled_request_keeps_thinking_enabled():
    payload = cl._build_payload(
        [{"role": "user", "content": "ping"}],
        tools=None,
        max_tokens=24,
        stream=False,
        model="deepseek-v4-flash",
        thinking="disabled",
    )

    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "high"


def test_reused_tool_call_ids_have_a_stable_wire_projection():
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "reused",
                "type": "function",
                "function": {"name": "read", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "reused", "content": "first"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "reused",
                "type": "function",
                "function": {"name": "read", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "reused", "content": "second"},
    ]

    first = cl.sanitize_messages_for_llm(messages)
    second = cl.sanitize_messages_for_llm(messages)

    assert first == second
    assert first[0]["tool_calls"][0]["id"] == "reused"
    remapped = first[2]["tool_calls"][0]["id"]
    assert remapped.startswith("call_")
    assert remapped != "reused"
    assert first[3]["tool_call_id"] == remapped
    assert messages[2]["tool_calls"][0]["id"] == "reused"


def test_deepseek_tool_turn_replays_reasoning_content_only_when_required():
    messages = [
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "must be replayed",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "lookup", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "result"},
        {
            "role": "assistant",
            "content": "ordinary answer",
            "reasoning_content": "not needed without a tool call",
        },
    ]

    deepseek_payload = cl._build_payload(
        messages,
        tools=None,
        max_tokens=24,
        stream=False,
        model="deepseek-v4-flash",
        thinking="auto",
    )
    generic_payload = cl._build_payload(
        messages,
        tools=None,
        max_tokens=24,
        stream=False,
        model="gpt-compatible-model",
        thinking="disabled",
    )

    assert deepseek_payload["messages"][0]["reasoning_content"] == "must be replayed"
    assert "reasoning_content" not in deepseek_payload["messages"][2]
    assert all("reasoning_content" not in message for message in generic_payload["messages"])


def test_deepseek_tool_turn_without_reasoning_becomes_recovery_receipt():
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "lookup", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "result"},
    ]

    payload = cl._build_payload(
        messages,
        tools=None,
        max_tokens=24,
        stream=False,
        model="deepseek-v4-flash",
        thinking="auto",
    )

    assert len(payload["messages"]) == 1
    receipt = json.loads(payload["messages"][0]["content"])
    assert payload["messages"][0]["role"] == "system"
    assert receipt["type"] == "deepseek_tool_episode_recovery"
    assert receipt["reason"] == "missing_or_empty_reasoning_content"
    assert receipt["calls"] == [{
        "tool": "lookup",
        "tool_call_id": "call_1",
        "result_available": True,
        "result": "result",
    }]


def test_deepseek_tool_turn_with_null_reasoning_becomes_recovery_receipt():
    messages = [
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": None,
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "lookup", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "result"},
    ]

    payload = cl._build_payload(
        messages,
        tools=None,
        max_tokens=24,
        stream=False,
        model="deepseek-v4-flash",
        thinking="auto",
    )

    assert len(payload["messages"]) == 1
    receipt = json.loads(payload["messages"][0]["content"])
    assert receipt["type"] == "deepseek_tool_episode_recovery"
    assert receipt["reason"] == "missing_or_empty_reasoning_content"


def test_deepseek_hidden_phase1_and_multitool_phase2_replay_reasoning_exactly():
    messages = [
        {"role": "user", "content": "check the weather"},
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "phase1 original reasoning",
            "hidden_from_ui": True,
            "tool_calls": [{
                "id": "use-1",
                "type": "function",
                "function": {"name": "use_tools", "arguments": "{}"},
            }],
        },
        {
            "role": "tool",
            "tool_call_id": "use-1",
            "content": "Execution phase entered.",
            "hidden_from_ui": True,
        },
        {
            "role": "assistant",
            "content": "checking",
            "reasoning_content": "phase2 original reasoning",
            "tool_calls": [
                {
                    "id": "message-1",
                    "type": "function",
                    "function": {"name": "send_message", "arguments": "{}"},
                },
                {
                    "id": "search-1",
                    "type": "function",
                    "function": {"name": "WebSearch", "arguments": "{}"},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "message-1", "content": "sent"},
        {"role": "tool", "tool_call_id": "search-1", "content": "weather"},
    ]

    payload = cl._build_payload(
        messages,
        tools=None,
        max_tokens=24,
        stream=False,
        model="deepseek-v4-flash",
        thinking="auto",
    )

    assistant_turns = [
        message
        for message in payload["messages"]
        if message.get("role") == "assistant" and message.get("tool_calls")
    ]
    assert [message["reasoning_content"] for message in assistant_turns] == [
        "phase1 original reasoning",
        "phase2 original reasoning",
    ]
    assert [
        message["tool_call_id"]
        for message in payload["messages"]
        if message.get("role") == "tool"
    ] == ["use-1", "message-1", "search-1"]


def test_deepseek_noncontiguous_tool_episode_is_recovered_atomically():
    messages = [
        {
            "role": "assistant",
            "content": "checking",
            "reasoning_content": "complete reasoning",
            "tool_calls": [{
                "id": "search-1",
                "type": "function",
                "function": {"name": "WebSearch", "arguments": "{}"},
            }],
        },
        {"role": "system", "content": "intervening observation"},
        {"role": "tool", "tool_call_id": "search-1", "content": "weather"},
        {"role": "user", "content": "continue"},
    ]

    payload = cl._build_payload(
        messages,
        tools=None,
        max_tokens=24,
        stream=False,
        model="deepseek-v4-flash",
        thinking="auto",
    )

    assert not any(message.get("role") == "tool" for message in payload["messages"])
    assert not any(message.get("tool_calls") for message in payload["messages"])
    receipt = json.loads(payload["messages"][0]["content"])
    assert receipt["reason"] == "incomplete_or_noncontiguous_tool_results"
    assert payload["messages"][1]["content"] == "intervening observation"
    assert payload["messages"][-1] == {"role": "user", "content": "continue"}


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        ("", "high"),
        ("low", "high"),
        ("medium", "high"),
        ("high", "high"),
        ("xhigh", "max"),
        ("max", "max"),
    ],
)
def test_deepseek_reasoning_effort_uses_supported_api_values(requested, expected):
    payload = cl._build_payload(
        [{"role": "user", "content": "ping"}],
        tools=None,
        max_tokens=24,
        stream=False,
        model="deepseek-v4-pro",
        thinking="auto",
        reasoning_effort=requested,
    )

    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == expected


def test_generic_model_does_not_receive_deepseek_thinking_extension():
    payload = cl._build_payload(
        [{"role": "user", "content": "ping"}],
        tools=None,
        max_tokens=24,
        stream=False,
        model="gpt-compatible-model",
        thinking="disabled",
    )

    assert "thinking" not in payload
    assert "reasoning_effort" not in payload


def test_kimi_k3_preserves_reasoning_without_sending_unsupported_thinking_field():
    payload = cl._build_payload(
        [
            {"role": "assistant", "content": "first", "reasoning_content": "trace"},
            {"role": "user", "content": "continue"},
        ],
        tools=None,
        max_tokens=24,
        stream=False,
        model="kimi-k3",
        thinking="enabled",
        reasoning_effort="xhigh",
    )

    assert payload["messages"][0]["reasoning_content"] == "trace"
    assert "thinking" not in payload
    assert payload["reasoning_effort"] == "max"


def test_kimi_k26_enables_preserved_thinking_for_tool_loops():
    payload = cl._build_payload(
        [
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "inspect",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "result"},
        ],
        tools=None,
        max_tokens=24,
        stream=False,
        model="kimi-k2.6",
        thinking="auto",
    )

    assert payload["thinking"] == {"type": "enabled", "keep": "all"}
    assert payload["messages"][0]["reasoning_content"] == "inspect"


def test_glm_enables_interleaved_thinking_and_replays_reasoning():
    payload = cl._build_payload(
        [
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "inspect",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "result"},
        ],
        tools=None,
        max_tokens=24,
        stream=False,
        model="glm-5.3",
        thinking="auto",
    )

    assert payload["thinking"] == {"type": "enabled", "clear_thinking": False}
    assert payload["messages"][0]["reasoning_content"] == "inspect"


@pytest.mark.parametrize("provider_preset", ["openrouter", "amd_gpu_cloud"])
@pytest.mark.parametrize("thinking", ["auto", "enabled"])
def test_aggregate_providers_do_not_receive_upstream_private_extensions(
    provider_preset,
    thinking,
):
    payload = cl._build_payload(
        [{"role": "user", "content": "ping"}],
        tools=None,
        max_tokens=24,
        stream=False,
        model="moonshotai/kimi-k2.6",
        thinking=thinking,
        provider_preset=provider_preset,
    )

    assert "thinking" not in payload


def test_minimax_request_splits_reasoning_and_replays_tool_turn_details():
    messages = [
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "inspect first",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "lookup", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "result"},
        {
            "role": "assistant",
            "content": "ordinary answer",
            "reasoning_details": [
                {"type": "reasoning.text", "text": "do not replay"}
            ],
        },
    ]

    payload = cl._build_payload(
        messages,
        tools=None,
        max_tokens=24,
        stream=True,
        model="MiniMax-M3",
        thinking="auto",
    )

    assert payload["reasoning_split"] is True
    assert payload["messages"][0]["reasoning_details"] == [
        {"type": "reasoning.text", "text": "inspect first"}
    ]
    assert "reasoning_content" not in payload["messages"][0]
    assert "reasoning_details" not in payload["messages"][2]


def test_minimax_legacy_think_wrapper_is_removed_from_visible_content():
    message = cl._normalize_minimax_message({
        "role": "assistant",
        "content": "<think>private analysis</think> Public answer",
    })

    assert message["content"] == "Public answer"
    assert message["reasoning_content"] == "private analysis"
    assert message["reasoning_details"] == [
        {"type": "reasoning.text", "text": "private analysis"}
    ]


async def test_minimax_stream_separates_cumulative_reasoning_and_content():
    events = []

    class FakeResponse:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aiter_lines(self):
            yield 'data: {"choices":[{"delta":{"reasoning_details":[{"type":"reasoning.text","text":"plan"}]}}]}'
            yield 'data: {"choices":[{"delta":{"reasoning_details":[{"type":"reasoning.text","text":"plan more"}]}}]}'
            yield 'data: {"choices":[{"delta":{"content":"An"}}]}'
            yield 'data: {"choices":[{"delta":{"content":"Answer"}}]}'
            yield "data: [DONE]"

    class FakeClient:
        def stream(self, *args, **kwargs):
            return FakeResponse()

    async def capture(event):
        events.append(event)

    message = await cl._handle_stream(
        FakeClient(),
        "https://api.minimax.io/v1/chat/completions",
        {"model": "MiniMax-M3", "messages": []},
        {},
        capture,
    )

    assert message["reasoning_content"] == "plan more"
    assert message["content"] == "Answer"
    assert [event["delta"] for event in events if event["type"] == "reply_delta"] == [
        "An", "swer"
    ]


async def test_minimax_stream_filters_legacy_think_tags_across_chunks():
    events = []

    class FakeResponse:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aiter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"<thi"}}]}'
            yield 'data: {"choices":[{"delta":{"content":"nk>secret"}}]}'
            yield 'data: {"choices":[{"delta":{"content":" plan</think>Final"}}]}'
            yield "data: [DONE]"

    class FakeClient:
        def stream(self, *args, **kwargs):
            return FakeResponse()

    async def capture(event):
        events.append(event)

    message = await cl._handle_stream(
        FakeClient(),
        "https://api.minimax.io/v1/chat/completions",
        {"model": "MiniMax-M3", "messages": []},
        {},
        capture,
    )

    assert message["reasoning_content"] == "secret plan"
    assert message["content"] == "Final"
    assert all(
        "<think" not in str(event.get("delta") or event.get("response") or "").lower()
        for event in events
        if event["type"].startswith("reply_")
    )


def test_openai_final_provider_payload_is_strictly_append_only_across_phases():
    tools = [{
        "type": "function",
        "function": {"name": "use_tools", "parameters": {"type": "object"}},
    }]
    phase1_messages = [
        {"role": "system", "content": "stable system"},
        {"role": "user", "content": "inspect"},
        {"role": "user", "content": "decision rules"},
    ]
    phase2_messages = [
        *phase1_messages,
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "use-1",
                "function": {"name": "use_tools", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "use-1", "content": "entered"},
    ]

    phase1 = cl._build_payload(
        phase1_messages,
        tools=tools,
        max_tokens=None,
        stream=True,
        model="deepseek-v4-flash",
        thinking="auto",
        reasoning_effort="high",
    )
    phase2 = cl._build_payload(
        phase2_messages,
        tools=tools,
        max_tokens=None,
        stream=True,
        model="deepseek-v4-flash",
        thinking="auto",
        reasoning_effort="high",
    )

    assert phase2["messages"][:len(phase1["messages"])] == phase1["messages"]
    assert phase2["tools"] == phase1["tools"]
    assert phase2["model"] == phase1["model"]
    assert phase2["thinking"] == phase1["thinking"]
    assert phase2["reasoning_effort"] == phase1["reasoning_effort"]


class _CountingHandler(BaseHTTPRequestHandler):
    """Tiny OpenAI-compatible stub; per-server hit counter + fixed status."""

    def do_POST(self):  # noqa: N802
        self.server.hits += 1
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.server.disconnects_remaining > 0:
            self.server.disconnects_remaining -= 1
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            return
        if self.server.transient_failures_remaining > 0:
            self.server.transient_failures_remaining -= 1
            self.send_response(self.server.transient_status)
            self.end_headers()
            self.wfile.write(b"{}")
            return
        if self.server.status != 200:
            self.send_response(self.server.status)
            self.end_headers()
            self.wfile.write(b"{}")
            return
        body = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "pong"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence
        pass


@pytest.fixture
def stub_server_factory():
    servers = []

    def make(status: int, *, disconnects: int = 0, transient_failures: int = 0, transient_status: int = 503):
        server = HTTPServer(("127.0.0.1", 0), _CountingHandler)
        server.status = status
        server.hits = 0
        server.disconnects_remaining = disconnects
        server.transient_failures_remaining = transient_failures
        server.transient_status = transient_status
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        base = f"http://127.0.0.1:{server.server_port}/v1"
        return server, {
            "id": f"stub-{status}-{server.server_port}",
            "model": "stub-model",
            "base_url": base,
            "api_key": "k",
            "endpoints": [f"{base}/chat/completions"],
        }

    yield make
    for server in servers:
        server.shutdown()
        server.server_close()


async def test_failed_candidate_is_retried_in_configured_order_on_next_call(stub_server_factory, monkeypatch):
    monkeypatch.setattr(cl, "_SERVER_ERROR_RETRY_BASE_DELAY_SECONDS", 0)
    bad_server, bad = stub_server_factory(500)
    good_server, good = stub_server_factory(200)

    msg = await cl.call_llm(
        [{"role": "user", "content": "hi"}],
        candidates=[bad, good],
        publish_events=False, record_usage=False,
    )
    assert msg.get("content") == "pong"
    # A persistent 5xx exhausts the same-endpoint retry budget before rotating.
    expected_bad_hits = 1 + cl.SERVER_ERROR_RETRY_LIMIT
    assert bad_server.hits == expected_bad_hits
    assert cl._candidate_cooling(cl._candidate_key(bad))
    assert not cl._candidate_cooling(cl._candidate_key(good))

    # A later call starts from the configured primary again.  Diagnostic
    # cooldown state must not silently rewrite the UI-defined model order.
    msg = await cl.call_llm(
        [{"role": "user", "content": "hi"}],
        candidates=[bad, good],
        publish_events=False, record_usage=False,
    )
    assert msg.get("content") == "pong"
    assert bad_server.hits == expected_bad_hits * 2
    assert good_server.hits == 2


async def test_final_http_request_is_observed_by_run_cache_diagnostics(
    stub_server_factory,
):
    _server, candidate = stub_server_factory(200)
    observed = []

    class Lease:
        def observe_request(self, model_type, **kwargs):
            observed.append((model_type, kwargs))
            return {
                "model_lease_id": "lease-http",
                "cache_prefix_status": "first_request",
            }

    tools = [{
        "type": "function",
        "function": {"name": "use_tools", "parameters": {"type": "object"}},
    }]
    result = await cl.call_llm(
        [{"role": "user", "content": "inspect"}],
        tools=tools,
        candidates=[candidate],
        candidate_lease=Lease(),
        publish_events=False,
        record_usage=False,
        record_latency=False,
    )

    assert result["content"] == "pong"
    assert len(observed) == 1
    model_type, request = observed[0]
    assert model_type == "primary"
    assert request["identity"]["endpoint"] == candidate["endpoints"][0]
    assert request["message_fingerprints"]
    assert request["tools_fingerprint"]
    assert request["payload_fingerprint"]


async def test_transient_server_error_retries_then_succeeds(stub_server_factory, monkeypatch):
    # 5xx on the first attempts (within the retry budget), then a clean 200.
    server, candidate = stub_server_factory(
        200, transient_failures=cl.SERVER_ERROR_RETRY_LIMIT, transient_status=503
    )
    monkeypatch.setattr(cl, "_SERVER_ERROR_RETRY_BASE_DELAY_SECONDS", 0)

    msg = await cl.call_llm(
        [{"role": "user", "content": "hi"}],
        candidates=[candidate],
        publish_events=False, record_usage=False,
    )

    assert msg.get("content") == "pong"
    assert server.hits == cl.SERVER_ERROR_RETRY_LIMIT + 1
    # A run that ultimately succeeded must not leave the candidate cooling.
    assert not cl._candidate_cooling(cl._candidate_key(candidate))


async def test_retry_reuses_precomputed_request_fingerprints(
    stub_server_factory,
    monkeypatch,
):
    server, candidate = stub_server_factory(
        200, transient_failures=cl.SERVER_ERROR_RETRY_LIMIT, transient_status=503
    )
    monkeypatch.setattr(cl, "_SERVER_ERROR_RETRY_BASE_DELAY_SECONDS", 0)
    original_fingerprint = cl._stable_request_fingerprint
    fingerprint_calls = 0

    def counted_fingerprint(value):
        nonlocal fingerprint_calls
        fingerprint_calls += 1
        return original_fingerprint(value)

    class Lease:
        def __init__(self):
            self.observed = []

        def observe_request(self, model_type, **kwargs):
            self.observed.append((model_type, kwargs))
            return {}

    lease = Lease()
    monkeypatch.setattr(cl, "_stable_request_fingerprint", counted_fingerprint)

    result = await cl.call_llm(
        [{"role": "user", "content": "inspect"}],
        candidates=[candidate],
        candidate_lease=lease,
        publish_events=False,
        record_usage=False,
        record_latency=False,
    )

    assert result["content"] == "pong"
    assert server.hits == cl.SERVER_ERROR_RETRY_LIMIT + 1
    assert fingerprint_calls == 3  # one message, tools, and final payload
    assert len(lease.observed) == server.hits
    assert len({id(item[1]["message_fingerprints"]) for item in lease.observed}) == 1


async def test_client_error_is_not_retried(stub_server_factory, monkeypatch):
    monkeypatch.setattr(cl, "_SERVER_ERROR_RETRY_BASE_DELAY_SECONDS", 0)
    bad_server, bad = stub_server_factory(400)
    good_server, good = stub_server_factory(200)

    msg = await cl.call_llm(
        [{"role": "user", "content": "hi"}],
        candidates=[bad, good],
        publish_events=False, record_usage=False,
    )
    assert msg.get("content") == "pong"
    # 4xx is a real client error: hit once, then rotate — never retried.
    assert bad_server.hits == 1


async def test_all_candidates_cooling_still_tries(stub_server_factory):
    good_server, good = stub_server_factory(200)
    cl._set_candidate_cooldown(cl._candidate_key(good))

    msg = await cl.call_llm(
        [{"role": "user", "content": "hi"}],
        candidates=[good],
        publish_events=False, record_usage=False,
    )
    assert msg.get("content") == "pong"
    # Success clears the cooldown again.
    assert not cl._candidate_cooling(cl._candidate_key(good))


async def test_connection_refused_fails_fast_and_cools_down(stub_server_factory, monkeypatch):
    # A closed local port refuses instantly; the candidate must be cooled down
    # so the next call does not retry it.
    # Retry timing is covered separately; this test only owns candidate
    # rotation/cooldown and must not spend the production backoff budget.
    monkeypatch.setattr(cl, "_NETWORK_RETRY_BASE_DELAY_SECONDS", 0)
    refused = {
        "id": "dead",
        "model": "dead-model",
        "base_url": "http://127.0.0.1:9",
        "endpoints": ["http://127.0.0.1:9/chat/completions"],
        "api_key": "",
    }
    good_server, good = stub_server_factory(200)
    msg = await cl.call_llm(
        [{"role": "user", "content": "hi"}],
        candidates=[refused, good],
        publish_events=False, record_usage=False,
    )
    assert msg.get("content") == "pong"
    assert cl._candidate_cooling(cl._candidate_key(refused))


async def test_transient_network_disconnect_retries_then_succeeds(stub_server_factory, monkeypatch):
    server, candidate = stub_server_factory(200, disconnects=cl.NETWORK_RETRY_LIMIT)
    monkeypatch.setattr(cl, "_NETWORK_RETRY_BASE_DELAY_SECONDS", 0)

    msg = await cl.call_llm(
        [{"role": "user", "content": "hi"}],
        candidates=[candidate],
        publish_events=False,
        record_usage=False,
    )

    assert msg.get("content") == "pong"
    assert server.hits == cl.NETWORK_RETRY_LIMIT + 1
    assert not cl._candidate_cooling(cl._candidate_key(candidate))


async def test_transient_network_disconnect_stops_after_retry_limit(stub_server_factory, monkeypatch):
    server, candidate = stub_server_factory(200, disconnects=cl.NETWORK_RETRY_LIMIT + 1)
    monkeypatch.setattr(cl, "_NETWORK_RETRY_BASE_DELAY_SECONDS", 0)

    with pytest.raises(httpx.RemoteProtocolError):
        await cl.call_llm(
            [{"role": "user", "content": "hi"}],
            candidates=[candidate],
            publish_events=False,
            record_usage=False,
        )

    assert server.hits == cl.NETWORK_RETRY_LIMIT + 1
    assert cl._candidate_cooling(cl._candidate_key(candidate))


async def test_auth_failure_is_not_masked_by_a_later_endpoint_disconnect(
    stub_server_factory, monkeypatch
):
    auth_server, auth_candidate = stub_server_factory(401)
    disconnected_server, disconnected_candidate = stub_server_factory(
        200, disconnects=cl.NETWORK_RETRY_LIMIT + 1
    )
    monkeypatch.setattr(cl, "_NETWORK_RETRY_BASE_DELAY_SECONDS", 0)
    candidate = {
        **auth_candidate,
        "endpoints": [
            auth_candidate["endpoints"][0],
            disconnected_candidate["endpoints"][0],
        ],
    }

    with pytest.raises(httpx.HTTPStatusError) as captured:
        await cl.call_llm(
            [{"role": "user", "content": "hi"}],
            candidates=[candidate],
            publish_events=False,
            record_usage=False,
        )

    assert captured.value.response.status_code == 401
    assert auth_server.hits == 1
    assert disconnected_server.hits == cl.NETWORK_RETRY_LIMIT + 1


def test_workbench_network_error_message_requests_resend():
    from cyrene.workbench.chat import _workbench_chat_run_error_message

    exc = httpx.RemoteProtocolError("Server disconnected without sending a response.")

    assert _workbench_chat_run_error_message(exc, "zh") == (
        f"网络连接异常，已自动重试 {cl.NETWORK_RETRY_LIMIT} 次仍未成功。请重新发送这条消息。"
    )
    assert "Please send this message again." in _workbench_chat_run_error_message(exc, "en")


def test_workbench_model_authentication_error_is_actionable():
    from cyrene.workbench.chat import (
        _workbench_chat_error_metadata,
        _workbench_chat_run_error_message,
    )

    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    response = httpx.Response(401, request=request)
    exc = httpx.HTTPStatusError("401 Authorization Required", request=request, response=response)

    assert _workbench_chat_run_error_message(exc, "zh") == (
        "无法访问模型服务：鉴权失败。请检查 API Key 或登录状态后重试。"
    )
    assert _workbench_chat_error_metadata(exc) == {
        "code": "model_authentication_failed",
        "detail_key": "workbenchChat.error.modelAuthenticationFailed",
    }

    try:
        raise exc
    except httpx.HTTPStatusError as cause:
        wrapped = RuntimeError("all configured model endpoints failed")
        wrapped.__cause__ = cause

    assert _workbench_chat_run_error_message(wrapped, "zh") == (
        "无法访问模型服务：鉴权失败。请检查 API Key 或登录状态后重试。"
    )
    assert _workbench_chat_error_metadata(wrapped) == {
        "code": "model_authentication_failed",
        "detail_key": "workbenchChat.error.modelAuthenticationFailed",
    }


@pytest.mark.parametrize("base_url", [
    "https://api.deepseek.com/v1",
    "https://API.DEEPSEEK.COM:443/v1/",
    "https://api.deepseek.com",
    "https://api.deepseek.com/",
])
def test_official_deepseek_urls_normalize_to_v1_endpoint(base_url):
    assert cl._normalized_llm_endpoints(base_url) == [
        "https://api.deepseek.com/v1/chat/completions",
    ]


@pytest.mark.parametrize("base_url", [
    "https://api.minimaxi.com",
    "https://api.minimaxi.com/v1",
    "https://api.minimax.io",
    "https://api.minimax.io/v1",
])
def test_official_minimax_is_normalized_to_v1_without_root_fallback(base_url):
    assert cl._normalized_llm_endpoints(base_url) == [
        f"https://{urlsplit(base_url).hostname}/v1/chat/completions",
    ]


def test_non_official_provider_keeps_generic_endpoint_order():
    assert cl._normalized_llm_endpoints("https://api.deepseek.com.example") == [
        "https://api.deepseek.com.example/chat/completions",
        "https://api.deepseek.com.example/v1/chat/completions",
    ]


def test_openai_adapter_normalizes_unversioned_deepseek(monkeypatch):
    monkeypatch.setattr(cl, "candidates_for_route", lambda _route: [{
        "id": "deepseek-chat",
        "model": "deepseek-v4-flash",
        "provider": "openai",
        "adapter": "openai",
        "api_key": "test-key",
        "base_url": "https://api.deepseek.com",
    }])

    candidate = cl._resolve_llm_candidates()[0]

    assert candidate["endpoints"] == [
        "https://api.deepseek.com/v1/chat/completions",
    ]


async def test_official_openai_chat_payload_uses_stable_independent_lane_keys(
    monkeypatch,
):
    payloads = []

    async def handler(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [{"message": {"role": "assistant", "content": "OK"}}],
                "usage": {},
            },
    )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        cl, "_get_http_client", lambda _timeout: (client, "test", False)
    )
    candidate = {
        "id": "openai-primary",
        "profile_id": "openai-primary",
        "connection_id": "openai",
        "model": "gpt-5.4",
        "provider": "openai",
        "adapter": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key": "test-key",
        "endpoints": ["https://api.openai.com/v1/chat/completions"],
    }
    tools = [{
        "type": "function",
        "function": {
            "name": "use_tools",
            "description": "handoff",
            "parameters": {"type": "object", "properties": {}},
        },
    }]
    first_messages = [
        {"role": "system", "content": "stable decision prompt"},
        {"role": "user", "content": "first"},
    ]

    try:
        await cl.call_llm(
            first_messages,
            tools=tools,
            candidates=[candidate],
            cache_scope="decision",
            publish_events=False,
            record_usage=False,
        )
        await cl.call_llm(
            [
                *first_messages,
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "next"},
            ],
            tools=tools,
            candidates=[candidate],
            cache_scope="decision",
            publish_events=False,
            record_usage=False,
        )
        await cl.call_llm(
            first_messages,
            tools=tools,
            candidates=[candidate],
            cache_scope="execution",
            publish_events=False,
            record_usage=False,
        )
        await cl.call_llm(
            first_messages,
            tools=tools,
            candidates=[candidate],
            cache_scope="decision",
            cache_epoch="lane-v1:decision:s0:e1",
            publish_events=False,
            record_usage=False,
        )
    finally:
        await client.aclose()

    keys = [payload["prompt_cache_key"] for payload in payloads]
    assert keys[0] == keys[1]
    assert keys[0] != keys[2]
    assert keys[0] != keys[3]
    assert keys[0].startswith("cyrene-v1-decision-")
    assert keys[2].startswith("cyrene-v1-execution-")


async def test_official_openai_responses_payload_receives_lane_cache_key(
    monkeypatch,
):
    payloads = []

    async def handler(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            request=request,
            json={
                "status": "completed",
                "output": [{
                    "type": "message",
                    "content": [{"type": "output_text", "text": "OK"}],
                }],
                "usage": {},
            },
    )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        cl, "_get_http_client", lambda _timeout: (client, "test", False)
    )
    candidate = {
        "id": "responses-primary",
        "model": "gpt-5.4",
        "provider": "openai_responses",
        "adapter": "openai_responses",
        "base_url": "https://api.openai.com/v1",
        "api_key": "test-key",
        "endpoints": ["https://api.openai.com/v1/responses"],
    }

    try:
        response = await cl.call_llm(
            [
                {"role": "system", "content": "stable execution prompt"},
                {"role": "user", "content": "run"},
            ],
            candidates=[candidate],
            cache_scope="execution",
            publish_events=False,
            record_usage=False,
        )
    finally:
        await client.aclose()

    assert response["content"] == "OK"
    assert payloads[0]["prompt_cache_key"].startswith("cyrene-v1-execution-")


def test_prompt_cache_key_field_is_capability_driven_for_compatible_endpoints():
    generic = {
        "id": "generic",
        "model": "model",
        "provider": "openai_compatible",
        "adapter": "openai_compatible",
        "base_url": "https://compatible.example/v1",
    }

    assert cl._candidate_accepts_prompt_cache_key(generic) is False
    assert cl._candidate_accepts_prompt_cache_key({
        **generic,
        "options": {"prompt_cache_key_supported": True},
    }) is True
    assert cl._candidate_accepts_prompt_cache_key({
        **generic,
        "capabilities": ["chat", "prompt_cache_key"],
    }) is True


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.deepseek.com/v1",
        "https://api.minimax.io/v1",
        "https://api.minimaxi.com/v1",
    ],
)
def test_automatic_prefix_cache_providers_never_receive_openai_cache_key(base_url):
    candidate = {
        "id": "automatic-cache",
        "model": "model",
        "provider": "openai_compatible",
        "adapter": "openai_compatible",
        "base_url": base_url,
        "options": {"prompt_cache_key_supported": True},
    }

    assert cl._candidate_accepts_prompt_cache_key(candidate) is False
    assert cl._provider_prompt_cache_route_key(
        candidate,
        model="model",
        cache_scope="decision",
        message_units=[{"role": "system", "content": "stable"}],
        tool_schema=[],
    ).startswith("cyrene-v1-decision-")


async def test_unversioned_deepseek_uses_only_v1(monkeypatch):
    requested_paths = []
    requested_payloads = []

    async def handler(request):
        requested_paths.append(request.url.path)
        requested_payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [{
                    "message": {"role": "assistant", "content": "OK"},
                    "finish_reason": "stop",
                }],
                "usage": {},
            },
    )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        cl, "_get_http_client", lambda _timeout: (client, "test", False)
    )
    candidate = {
        "id": "deepseek",
        "model": "deepseek-v4-flash",
        "provider": "openai",
        "adapter": "openai",
        "base_url": "https://api.deepseek.com",
        "api_key": "test-key",
        "endpoints": cl._normalized_llm_endpoints("https://api.deepseek.com"),
    }

    try:
        response = await cl.call_llm(
            [{"role": "user", "content": "ping"}],
            candidates=[candidate],
            cache_scope="execution",
            publish_events=False,
            record_usage=False,
        )
    finally:
        await client.aclose()

    assert response["content"] == "OK"
    assert requested_paths == ["/v1/chat/completions"]
    assert "prompt_cache_key" not in requested_payloads[0]


def test_saved_deepseek_root_keeps_only_versioned_endpoint(monkeypatch):
    candidate = {
        "id": "deepseek",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
        "endpoints": cl._normalized_llm_endpoints("https://api.deepseek.com"),
    }
    monkeypatch.setattr(cl, "_last_success_map", lambda: {
        "session:chat-1:primary": {
            "candidate_id": "deepseek",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com",
            "endpoint": "https://api.deepseek.com/chat/completions",
        }
    })
    monkeypatch.setattr(cl, "_session_model_preferences", lambda: {})

    prioritized = cl._prioritize_last_success([candidate], "primary", "chat-1")

    assert prioritized[0]["endpoints"] == [
        "https://api.deepseek.com/v1/chat/completions",
    ]


async def test_streaming_http_error_body_is_preserved_for_diagnostics():
    async def handler(request):
        return httpx.Response(
            400,
            request=request,
            json={"error": {"message": "reasoning_content is required"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError) as captured:
            await cl._handle_stream(
                client,
                "https://api.deepseek.com/v1/chat/completions",
                {"model": "deepseek-v4-flash", "messages": []},
                {},
                None,
            )

    detail = cl._format_httpx_error(captured.value)
    assert "status=400" in detail
    assert 'body={"error":{"message":"reasoning_content is required"}}' in detail


def test_resolve_llm_candidates_is_the_primary_route_in_order(monkeypatch):
    """The primary route is the sole source of truth and keeps UI order."""
    monkeypatch.setenv("OPENAI_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.setattr(cl, "candidates_for_route", lambda _route: [
        {"id": "primary", "model": "deepseek-v4-flash", "api_key": "key-flash", "base_url": "https://api.deepseek.com"},
        {"id": "lan", "model": "qwen", "api_key": "", "base_url": "http://10.0.0.1:1234/v1"},
    ])
    candidates = cl._resolve_llm_candidates()
    assert [c["id"] for c in candidates] == ["primary", "lan"]
    assert candidates[0]["api_key"] == "key-flash"


def test_resolve_llm_candidates_allows_keyless_local_endpoint(monkeypatch):
    """A provider that needs no key (local model server) stays keyless and is
    not force-fed an unrelated provider's key."""
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.setattr(cl, "candidates_for_route", lambda _route: [
        {"id": "lan", "model": "qwen", "api_key": "", "base_url": "http://10.0.0.1:1234/v1"},
        {"id": "cloud", "model": "deepseek", "api_key": "cloud-key", "base_url": "https://api.deepseek.com"},
    ])
    candidates = cl._resolve_llm_candidates()
    lan = next(c for c in candidates if c["id"] == "lan")
    assert lan["api_key"] == ""  # different endpoint → no inheritance


def test_graph_candidate_never_inherits_legacy_env_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "stale-env-secret")
    monkeypatch.setattr(cl, "candidates_for_route", lambda _route: [{
        "id": "configured",
        "profile_id": "configured",
        "connection_id": "configured-service",
        "model": "configured-model",
        "api_key": "",
        "base_url": "https://provider.example/v1",
    }])

    assert cl._resolve_llm_candidates()[0]["api_key"] == ""


def test_distinct_services_on_same_endpoint_do_not_share_credentials(monkeypatch):
    monkeypatch.setattr(cl, "candidates_for_route", lambda _route: [
        {
            "id": "first",
            "connection_id": "service-a",
            "model": "model-a",
            "api_key": "service-a-secret",
            "base_url": "https://provider.example/v1",
        },
        {
            "id": "second",
            "connection_id": "service-b",
            "model": "model-b",
            "api_key": "",
            "base_url": "https://provider.example/v1",
        },
    ])

    candidates = cl._resolve_llm_candidates()
    assert candidates[0]["api_key"] == "service-a-secret"
    assert candidates[1]["api_key"] == ""


def test_resolve_llm_candidates_shares_key_within_same_endpoint(monkeypatch):
    """Same-endpoint candidates may inherit the first filled-in key, so the
    user need not paste it onto every row."""
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(cl, "candidates_for_route", lambda _route: [
        {"id": "a", "model": "deepseek-v4-flash", "api_key": "shared", "base_url": "https://api.deepseek.com"},
        {"id": "b", "model": "deepseek-reasoner", "api_key": "", "base_url": "https://api.deepseek.com/v1"},
    ])
    candidates = cl._resolve_llm_candidates()
    assert next(c for c in candidates if c["id"] == "b")["api_key"] == "shared"


def test_resolve_llm_candidates_empty_when_list_empty(monkeypatch):
    """No phantom env candidate: an unconfigured install yields no candidates,
    so the caller can raise a clear 'configure a model' error."""
    monkeypatch.setenv("OPENAI_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.setattr(cl, "candidates_for_route", lambda _route: [])
    assert cl._resolve_llm_candidates() == []


async def test_call_llm_returns_empty_when_no_model_configured(monkeypatch):
    """No candidates → historical empty-string contract (callers degrade), but
    the phantom env candidate that used to 401 is gone."""
    monkeypatch.setattr(cl, "candidates_for_route", lambda _route: [])
    result = await cl.call_llm(
        [{"role": "user", "content": "hi"}],
        publish_events=False, record_usage=False,
    )
    assert result == ""


def test_last_success_affinity_is_scoped_to_conversation_and_exact_endpoint():
    cl._session_model_preference_cache = {}
    cl._last_success_cache = {
        "session:chat_existing:primary": {
            "candidate_id": "backup",
            "model": "backup-model",
            "base_url": "https://backup.example/v1",
            "endpoint": "https://backup.example/v1/chat/completions-alt",
        }
    }
    candidates = [
        {
            "id": "main", "model": "main-model", "base_url": "https://main.example/v1",
            "endpoints": ["https://main.example/v1/chat/completions"],
        },
        {
            "id": "backup", "model": "backup-model", "base_url": "https://backup.example/v1",
            "endpoints": [
                "https://backup.example/v1/chat/completions",
                "https://backup.example/v1/chat/completions-alt",
            ],
        },
    ]

    ordered = cl._prioritize_last_success(candidates, "primary", "chat_existing")
    new_chat_order = cl._prioritize_last_success(candidates, "primary", "chat_new")
    unscoped_order = cl._prioritize_last_success(candidates, "primary")

    assert [item["id"] for item in ordered] == ["main", "backup"]
    assert ordered[1]["endpoints"][0].endswith("chat/completions-alt")
    assert ordered[1]["_configured_rank"] == 1
    assert [item["id"] for item in new_chat_order] == ["main", "backup"]
    assert [item["id"] for item in unscoped_order] == ["main", "backup"]


def test_explicit_session_model_preference_controls_candidate_and_effort(monkeypatch):
    writes = []
    cl._session_model_preference_cache = {}
    cl._last_success_cache = {}
    monkeypatch.setattr(cl, "set_setting", lambda key, value: writes.append((key, value)))
    candidates = [
        {
            "id": "main", "model": "main-model", "base_url": "https://main.example/v1",
            "reasoning_effort": "low", "endpoints": ["https://main.example/v1/chat/completions"],
        },
        {
            "id": "chosen", "model": "chosen-model", "base_url": "https://chosen.example/v1",
            "reasoning_effort": "medium", "endpoints": ["https://chosen.example/v1/chat/completions"],
        },
    ]

    cl.set_session_model_preference("chat_explicit", candidates[1], "high")
    ordered = cl._prioritize_last_success(candidates, "primary", "chat_explicit")

    assert [item["id"] for item in ordered] == ["chosen", "main"]
    assert ordered[0]["reasoning_effort"] == "high"
    assert writes[-1][0] == "llm_session_model_preferences"


def test_successful_endpoint_affinity_is_persisted_only_when_changed(monkeypatch):
    writes = []
    cl._last_success_cache = {}
    monkeypatch.setattr(cl, "set_setting", lambda key, value: writes.append((key, value)))
    candidate = {
        "id": "main", "model": "model", "base_url": "https://model.example/v1"
    }

    cl._remember_success(
        "primary", candidate, "https://model.example/v1/chat/completions", "chat_1"
    )
    cl._remember_success(
        "primary", candidate, "https://model.example/v1/chat/completions", "chat_1"
    )

    assert len(writes) == 1
    assert writes[0][0] == "llm_last_success_endpoints"
    assert writes[0][1]["session:chat_1:primary"]["candidate_id"] == "main"


def test_success_without_session_does_not_create_global_affinity(monkeypatch):
    writes = []
    cl._last_success_cache = {
        "primary": {"candidate_id": "legacy-global"},
    }
    monkeypatch.setattr(cl, "set_setting", lambda key, value: writes.append((key, value)))

    cl._remember_success(
        "primary",
        {"id": "main", "model": "model", "base_url": "https://model.example/v1"},
        "https://model.example/v1/chat/completions",
    )

    assert writes == []
    assert cl._prioritize_last_success([
        {
            "id": "main", "model": "model", "base_url": "https://model.example/v1",
            "endpoints": ["https://model.example/v1/chat/completions"],
        }
    ], "primary")[0]["id"] == "main"


def test_candidate_cooldown_is_scoped_to_conversation():
    candidate = {
        "id": "main",
        "model": "primary-model",
        "base_url": "https://primary.example/v1",
    }

    cl._set_candidate_cooldown(cl._candidate_key(candidate, "chat_existing"))

    assert cl._candidate_cooling(cl._candidate_key(candidate, "chat_existing"))
    assert not cl._candidate_cooling(cl._candidate_key(candidate, "chat_new"))
    assert not cl._candidate_cooling(cl._candidate_key(candidate))


async def test_call_llm_reuses_http_client_within_event_loop(monkeypatch):
    created = 0

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {},
            }

    class FakeClient:
        async def post(self, _endpoint, json=None, headers=None):
            return FakeResponse()

    def factory(*_args, **_kwargs):
        nonlocal created
        created += 1
        return FakeClient()

    monkeypatch.setattr(cl.httpx, "AsyncClient", factory)
    candidate = {
        "id": "main", "model": "model", "base_url": "https://model.example/v1",
        "api_key": "", "endpoints": ["https://model.example/v1/chat/completions"],
    }
    for _ in range(2):
        result = await cl.call_llm(
            [{"role": "user", "content": "hi"}], candidates=[candidate],
            publish_events=False, record_usage=False,
        )
        assert result["content"] == "ok"

    assert created == 1


async def test_primary_failure_publishes_fallback_ui_event(monkeypatch):
    published = []

    class FakeResponse:
        def __init__(self, status_code, model, endpoint):
            self.status_code = status_code
            self._model = model
            self.request = httpx.Request("POST", endpoint)

        def json(self):
            return {
                "choices": [{"message": {"role": "assistant", "content": "backup"}}],
                "usage": {},
            }

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "failed", request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )

    class FakeClient:
        async def post(self, endpoint, json=None, headers=None):
            return FakeResponse(400 if json["model"] == "main" else 200, json["model"], endpoint)

    async def capture(**kwargs):
        published.append(kwargs)

    monkeypatch.setattr(cl.httpx, "AsyncClient", lambda *_args, **_kwargs: FakeClient())
    monkeypatch.setattr(cl, "_publish_model_fallback_event", capture)
    candidates = [
        {"id": "main", "model": "main", "api_key": "", "endpoints": ["https://main/v1/chat/completions"]},
        {"id": "backup", "model": "backup", "api_key": "", "endpoints": ["https://backup/v1/chat/completions"]},
    ]

    result = await cl.call_llm(
        [{"role": "user", "content": "hi"}], candidates=candidates,
        publish_events=False, record_usage=False, session_id="chat_1", round_id="round_1",
    )

    assert result["content"] == "backup"
    assert published == [{
        "session_id": "chat_1", "round_id": "round_1",
        "failed_model": "main", "fallback_model": "backup",
    }]


async def test_retry_count_updates_before_final_model_switch(monkeypatch):
    retries = []
    switches = []

    class FakeResponse:
        def __init__(self, status_code, model, endpoint):
            self.status_code = status_code
            self._model = model
            self.request = httpx.Request("POST", endpoint)

        def json(self):
            return {
                "choices": [{"message": {"role": "assistant", "content": self._model}}],
                "usage": {},
            }

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "failed",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )

    class FakeClient:
        async def post(self, endpoint, json=None, headers=None):
            model = str(json.get("model") or "")
            return FakeResponse(503 if model == "main" else 200, model, endpoint)

    async def capture_retry(**kwargs):
        retries.append(kwargs)

    async def capture_switch(**kwargs):
        switches.append(kwargs)

    monkeypatch.setattr(cl, "_SERVER_ERROR_RETRY_BASE_DELAY_SECONDS", 0)
    monkeypatch.setattr(cl.httpx, "AsyncClient", lambda *_args, **_kwargs: FakeClient())
    monkeypatch.setattr(cl, "_publish_model_retry_event", capture_retry)
    monkeypatch.setattr(cl, "_publish_model_fallback_event", capture_switch)
    candidates = [
        {"id": "main", "model": "main", "api_key": "", "endpoints": ["https://main/v1/chat/completions"]},
        {"id": "backup", "model": "backup", "api_key": "", "endpoints": ["https://backup/v1/chat/completions"]},
    ]

    result = await cl.call_llm(
        [{"role": "user", "content": "hi"}],
        candidates=candidates,
        publish_events=False,
        record_usage=False,
        session_id="chat_retry_switch",
        round_id="round_retry_switch",
    )

    assert result["content"] == "backup"
    assert retries == [
        {
            "session_id": "chat_retry_switch",
            "round_id": "round_retry_switch",
            "model": "main",
            "retry_count": retry_count,
            "retry_limit": cl.SERVER_ERROR_RETRY_LIMIT,
        }
        for retry_count in range(1, cl.SERVER_ERROR_RETRY_LIMIT + 1)
    ]
    assert switches == [{
        "session_id": "chat_retry_switch",
        "round_id": "round_retry_switch",
        "failed_model": "main",
        "fallback_model": "backup",
    }]


def test_retry_policy_uses_fixed_ten_second_intervals():
    assert cl.SERVER_ERROR_RETRY_LIMIT == 5
    assert cl.NETWORK_RETRY_LIMIT == 10
    assert cl._SERVER_ERROR_RETRY_BASE_DELAY_SECONDS == 10.0
    assert cl._NETWORK_RETRY_BASE_DELAY_SECONDS == 10.0


async def test_codex_quota_failure_publishes_actionable_notice_without_cross_family_fallback(
    monkeypatch,
):
    availability_notices = []

    class FakeCodex:
        async def quota_available(self):
            return False

        async def close(self):
            return None

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {"message": {"role": "assistant", "content": "backup"}}
                ],
                "usage": {},
            }

    class FakeClient:
        async def post(self, _endpoint, json=None, headers=None):
            return FakeResponse()

    async def capture(**kwargs):
        availability_notices.append(kwargs)

    import cyrene.model_runtime.codex_provider as codex_provider

    monkeypatch.setattr(codex_provider, "get_codex_provider", lambda: FakeCodex())
    monkeypatch.setattr(cl, "_get_http_client", lambda _timeout: (FakeClient(), "test", True))
    monkeypatch.setattr(cl, "_publish_codex_availability_event", capture)
    monkeypatch.setattr(
        cl,
        "get_setting",
        lambda key, default=None: True if key == "codex_budget_enabled" else default,
    )
    candidates = [
        {
            "id": "codex",
            "model": "gpt-5.6-sol",
            "provider": "codex_oauth",
            "base_url": "codex://oauth",
            "api_key": "",
            "endpoints": ["codex://oauth"],
        },
    ]

    from cyrene.model_runtime.codex_provider import CodexAvailabilityError

    with pytest.raises(CodexAvailabilityError, match="quota is exhausted"):
        await cl.call_llm(
            [{"role": "user", "content": "hi"}],
            candidates=candidates,
            publish_events=False,
            record_usage=False,
            session_id="chat_codex",
            round_id="round_codex",
        )

    assert availability_notices == [
        {
            "session_id": "chat_codex",
            "round_id": "round_codex",
            "model": "gpt-5.6-sol",
            "failure_kind": "quota_exhausted",
        }
    ]


async def test_codex_auth_failure_does_not_arm_cooldown_or_cross_family_fallback(
    monkeypatch,
):
    class FakeCodex:
        async def quota_available(self):
            return True

        async def complete(self, **_kwargs):
            from cyrene.model_runtime.codex_provider import (
                CODEX_AUTHENTICATION_EXPIRED,
                CodexAvailabilityError,
            )

            raise CodexAvailabilityError(
                CODEX_AUTHENTICATION_EXPIRED,
                "Please log in again",
            )

        async def close(self):
            return None

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {"message": {"role": "assistant", "content": "backup"}}
                ],
                "usage": {},
            }

    class FakeClient:
        async def post(self, _endpoint, json=None, headers=None):
            return FakeResponse()

    import cyrene.model_runtime.codex_provider as codex_provider

    async def ignore_notice(**_kwargs):
        return None

    monkeypatch.setattr(
        codex_provider,
        "get_codex_provider",
        lambda: FakeCodex(),
    )
    monkeypatch.setattr(
        cl,
        "_get_http_client",
        lambda _timeout: (FakeClient(), "test", True),
    )
    monkeypatch.setattr(
        cl,
        "_publish_codex_availability_event",
        ignore_notice,
    )
    candidates = [
        {
            "id": "codex",
            "model": "gpt-5.6-sol",
            "provider": "codex_oauth",
            "base_url": "codex://oauth",
            "api_key": "",
            "endpoints": ["codex://oauth"],
        },
    ]

    from cyrene.model_runtime.codex_provider import CodexAvailabilityError

    with pytest.raises(CodexAvailabilityError, match="log in again"):
        await cl.call_llm(
            [{"role": "user", "content": "hi"}],
            candidates=candidates,
            publish_events=False,
            record_usage=False,
            session_id="chat_auth",
            round_id="round_auth",
        )

    assert not cl._candidate_cooling(
        cl._candidate_key(candidates[0], "chat_auth")
    )


async def test_llm_event_identifies_codex_provider(monkeypatch):
    published = []

    from cyrene.observability import debug

    async def capture(event, **kwargs):
        published.append((event, kwargs))

    monkeypatch.setattr(debug, "publish_event", capture)

    await cl._publish_llm_event(
        "main_agent",
        "phase1",
        [{"role": "user", "content": "hi"}],
        None,
        {},
        "gpt-5.6-sol",
        0,
        provider="codex_oauth",
        session_id="chat_codex",
    )

    assert published[0][0]["provider"] == "codex_oauth"


async def test_fallback_ui_event_is_deduplicated_across_calls_in_same_round(monkeypatch):
    published = []

    class FakeResponse:
        def __init__(self, status_code, model, endpoint):
            self.status_code = status_code
            self._model = model
            self.request = httpx.Request("POST", endpoint)

        def json(self):
            return {
                "choices": [{"message": {"role": "assistant", "content": self._model}}],
                "usage": {},
            }

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "failed", request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )

    class FakeClient:
        async def post(self, endpoint, json=None, headers=None):
            return FakeResponse(400 if json["model"] == "main" else 200, json["model"], endpoint)

    async def capture(**kwargs):
        published.append(kwargs)

    monkeypatch.setattr(cl.httpx, "AsyncClient", lambda *_args, **_kwargs: FakeClient())
    monkeypatch.setattr(cl, "_publish_model_fallback_event", capture)
    candidates = [
        {"id": "main", "model": "main", "api_key": "", "endpoints": ["https://main/v1/chat/completions"]},
        {"id": "backup", "model": "backup", "api_key": "", "endpoints": ["https://backup/v1/chat/completions"]},
    ]

    for _ in range(2):
        result = await cl.call_llm(
            [{"role": "user", "content": "hi"}], candidates=candidates,
            publish_events=False, record_usage=False,
            session_id="chat_same", round_id="round_same",
        )
        assert result["content"] == "backup"

    assert published == [{
        "session_id": "chat_same", "round_id": "round_same",
        "failed_model": "main", "fallback_model": "backup",
    }]

    await cl.call_llm(
        [{"role": "user", "content": "next"}], candidates=candidates,
        publish_events=False, record_usage=False,
        session_id="chat_same", round_id="round_next",
    )
    assert len(published) == 2


async def test_oversized_primary_is_skipped_for_larger_fallback(monkeypatch):
    published = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [{"message": {"role": "assistant", "content": "fits"}}],
                "usage": {"prompt_tokens": 500, "completion_tokens": 1, "total_tokens": 501},
            }

    class FakeClient:
        async def post(self, endpoint, json=None, headers=None):
            assert json["model"] == "large"
            return FakeResponse()

    async def capture(**kwargs):
        published.append(kwargs)

    monkeypatch.setattr(cl.httpx, "AsyncClient", lambda *_args, **_kwargs: FakeClient())
    monkeypatch.setattr(cl, "_request_token_estimate", lambda _messages, _tools=None: 500)
    monkeypatch.setattr(cl, "_publish_model_fallback_event", capture)
    candidates = [
        {
            "id": "small", "model": "small", "ctx_limit": 100,
            "api_key": "", "endpoints": ["https://small/v1/chat/completions"],
        },
        {
            "id": "large", "model": "large", "ctx_limit": 1_000,
            "api_key": "", "endpoints": ["https://large/v1/chat/completions"],
        },
    ]

    result = await cl.call_llm(
        [{"role": "user", "content": "hi"}], candidates=candidates,
        publish_events=False, record_usage=False, session_id="chat_ctx", round_id="round_ctx",
    )

    assert result["content"] == "fits"
    assert result["model"] == "large"
    assert not cl._candidate_cooling(cl._candidate_key(candidates[0]))
    assert published == [{
        "session_id": "chat_ctx", "round_id": "round_ctx",
        "failed_model": "small", "fallback_model": "large",
    }]


async def test_all_candidates_over_context_raise_without_cooldown(monkeypatch):
    monkeypatch.setattr(cl, "_request_token_estimate", lambda _messages, _tools=None: 500)
    candidates = [{
        "id": "small", "model": "small", "ctx_limit": 100,
        "api_key": "", "endpoints": ["https://small/v1/chat/completions"],
    }]

    with pytest.raises(ValueError, match="exceeding all candidate context windows"):
        await cl.call_llm(
            [{"role": "user", "content": "hi"}], candidates=candidates,
            publish_events=False, record_usage=False,
        )

    assert not cl._candidate_cooling(cl._candidate_key(candidates[0]))


async def test_last_success_affinity_does_not_override_primary_route(monkeypatch):
    published = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [{"message": {"role": "assistant", "content": "direct"}}],
                "usage": {},
            }

    class FakeClient:
        async def post(self, endpoint, json=None, headers=None):
            assert json["model"] == "main"
            return FakeResponse()

    async def capture(**kwargs):
        published.append(kwargs)

    candidates = [
        {
            "id": "main", "model": "main", "base_url": "https://main/v1",
            "api_key": "", "endpoints": ["https://main/v1/chat/completions"],
        },
        {
            "id": "backup", "model": "backup", "base_url": "https://backup/v1",
            "api_key": "", "endpoints": ["https://backup/v1/chat/completions"],
        },
    ]
    cl._last_success_cache = {
        "session:chat_1:primary": {
            "candidate_id": "backup", "model": "backup",
            "base_url": "https://backup/v1",
            "endpoint": "https://backup/v1/chat/completions",
        }
    }
    monkeypatch.setattr(cl, "_resolve_candidates", lambda _model_type: candidates)
    monkeypatch.setattr(cl, "_get_http_client", lambda _timeout: (FakeClient(), "test", True))
    monkeypatch.setattr(cl, "_publish_model_fallback_event", capture)

    result = await cl.call_llm(
        [{"role": "user", "content": "hi"}],
        publish_events=False, record_usage=False, session_id="chat_1", round_id="round_1",
    )

    assert result["content"] == "direct"
    assert result["model"] == "main"
    assert published == []


async def test_actionable_llm_latency_event_is_persisted(tmp_path):
    from cyrene.runtime.database import (
        get_llm_cache_stats_by_phase,
        record_llm_latency,
    )

    db_path = tmp_path / "latency.db"
    await record_llm_latency(
        str(db_path), call_id="llm_1", session_id="chat_1", round_id="round_1",
        caller="main_agent", phase="phase2", model_type="primary",
        candidate_id="main", model="model", endpoint="https://model/v1/chat/completions",
        candidate_rank=0, endpoint_rank=0, attempt=1, outcome="success", status_code=200,
        queue_wait_ms=4.0, pre_attempt_wait_ms=4.0, request_ms=900.0,
        response_headers_ms=120.0, ttft_ms=300.0,
        first_token_after_headers_ms=180.0, generation_ms=600.0,
        retry_backoff_ms=0.0, total_call_ms=904.0, prompt_tokens=100,
        completion_tokens=60, prompt_cache_hit_tokens=80,
        prompt_cache_miss_tokens=20, output_tokens_per_second=100.0,
        fallback_used=False, connection_pool_key="loop:1:timeout:120",
        model_lease_id="lease-1", request_messages_fingerprint="messages-1",
        request_tools_fingerprint="tools-1",
        request_payload_fingerprint="payload-1",
        previous_payload_fingerprint="payload-0",
        cache_prefix_status="strict_prefix_reuse",
        cache_invalidation_reason="", cache_prefix_message_count=3,
    )

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT call_id, request_ms, response_headers_ms, ttft_ms, "
            "first_token_after_headers_ms, generation_ms, "
            "output_tokens_per_second, connection_pool_key, "
            "prompt_cache_hit_tokens, prompt_cache_miss_tokens, cache_hit_ratio, "
            "model_lease_id, request_messages_fingerprint, "
            "request_tools_fingerprint, request_payload_fingerprint, "
            "previous_payload_fingerprint, cache_prefix_status, "
            "cache_invalidation_reason, cache_prefix_message_count, "
            "error_body, error_body_truncated "
            "FROM llm_latency_events"
        ).fetchone()
    assert row == (
        "llm_1", 900.0, 120.0, 300.0, 180.0, 600.0, 100.0,
        "loop:1:timeout:120", 80, 20, 0.8, "lease-1", "messages-1",
        "tools-1", "payload-1", "payload-0", "strict_prefix_reuse", "", 3,
        "", 0,
    )
    phase_stats = await get_llm_cache_stats_by_phase(str(db_path))
    assert phase_stats == [{
        "phase": "phase2",
        "requests": 1,
        "prompt_tokens": 100,
        "cache_hit_tokens": 80,
        "cache_miss_tokens": 20,
        "cache_hit_ratio": 0.8,
    }]


async def test_4xx_response_body_is_redacted_and_persisted(tmp_path):
    from cyrene.model_runtime.errors import httpx_error_body_for_persistence
    from cyrene.runtime.database import record_llm_latency

    request = httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions")
    response = httpx.Response(
        400,
        request=request,
        json={
            "error": {
                "message": "reasoning_content is required",
                "api_key": "sk-super-secret-value",
            }
        },
    )
    exc = httpx.HTTPStatusError("Bad Request", request=request, response=response)
    body, truncated = httpx_error_body_for_persistence(exc)

    assert body == '{"error":{"message":"reasoning_content is required","api_key":"[REDACTED]"}}'
    assert truncated is False

    db_path = tmp_path / "latency-4xx.db"
    await record_llm_latency(
        str(db_path),
        call_id="llm_400",
        outcome="http_error",
        status_code=400,
        error_type="HTTPStatusError",
        error_body=body,
        error_body_truncated=truncated,
    )
    with sqlite3.connect(db_path) as conn:
        persisted = conn.execute(
            "SELECT status_code, error_type, error_body, error_body_truncated "
            "FROM llm_latency_events"
        ).fetchone()
    assert persisted == (400, "HTTPStatusError", body, 0)
