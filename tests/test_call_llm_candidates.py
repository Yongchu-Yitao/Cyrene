"""Candidate resilience in cyrene.call_llm — cooldown, connect timeout, resolution.

Regression tests for the 2026-06-11 latency incident: a dead LAN endpoint in the
model list added ~120s to every LLM call. Also pins the candidate model: the
model list is the sole ordered source of truth, with no phantom env candidate
prepended (that duplicate 401'd on every call when its key was empty).
"""
import json
import socket
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

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


async def test_failed_candidate_gets_cooldown_and_is_skipped(stub_server_factory, monkeypatch):
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

    # Second call: the failed candidate is cooling and must be skipped entirely.
    msg = await cl.call_llm(
        [{"role": "user", "content": "hi"}],
        candidates=[bad, good],
        publish_events=False, record_usage=False,
    )
    assert msg.get("content") == "pong"
    assert bad_server.hits == expected_bad_hits  # unchanged — skipped
    assert good_server.hits == 2


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


async def test_connection_refused_fails_fast_and_cools_down(stub_server_factory):
    # A closed local port refuses instantly; the candidate must be cooled down
    # so the next call does not retry it.
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


def test_workbench_network_error_message_requests_resend():
    from cyrene.workbench.chat import _workbench_chat_run_error_message

    exc = httpx.RemoteProtocolError("Server disconnected without sending a response.")

    assert _workbench_chat_run_error_message(exc, "zh") == (
        f"网络连接异常，已自动重试 {cl.NETWORK_RETRY_LIMIT} 次仍未成功。请重新发送这条消息。"
    )
    assert "Please send this message again." in _workbench_chat_run_error_message(exc, "en")


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.deepseek.com",
        "https://api.deepseek.com/",
        "https://api.deepseek.com/v1",
        "https://API.DEEPSEEK.COM:443/v1/",
    ],
)
def test_official_deepseek_prefers_versioned_chat_completions(base_url):
    assert cl._normalized_llm_endpoints(base_url) == [
        "https://api.deepseek.com/v1/chat/completions",
        "https://api.deepseek.com/chat/completions",
    ]


def test_non_official_provider_keeps_generic_endpoint_order():
    assert cl._normalized_llm_endpoints("https://api.deepseek.com.example") == [
        "https://api.deepseek.com.example/chat/completions",
        "https://api.deepseek.com.example/v1/chat/completions",
    ]


def test_stale_deepseek_root_affinity_does_not_override_versioned_priority(monkeypatch):
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

    assert prioritized[0]["endpoints"][0] == (
        "https://api.deepseek.com/v1/chat/completions"
    )


def test_resolve_llm_candidates_is_the_model_list_in_order(monkeypatch):
    """The model list is the sole source of truth — no phantom env candidate
    prepended, entries kept in their configured order."""
    monkeypatch.setenv("OPENAI_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.setattr(cl, "get_models", lambda: [
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
    monkeypatch.setattr(cl, "get_models", lambda: [
        {"id": "lan", "model": "qwen", "api_key": "", "base_url": "http://10.0.0.1:1234/v1"},
        {"id": "cloud", "model": "deepseek", "api_key": "cloud-key", "base_url": "https://api.deepseek.com"},
    ])
    candidates = cl._resolve_llm_candidates()
    lan = next(c for c in candidates if c["id"] == "lan")
    assert lan["api_key"] == ""  # different endpoint → no inheritance


def test_resolve_llm_candidates_shares_key_within_same_endpoint(monkeypatch):
    """Same-endpoint candidates may inherit the first filled-in key, so the
    user need not paste it onto every row."""
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(cl, "get_models", lambda: [
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
    monkeypatch.setattr(cl, "get_models", lambda: [])
    assert cl._resolve_llm_candidates() == []


async def test_call_llm_returns_empty_when_no_model_configured(monkeypatch):
    """No candidates → historical empty-string contract (callers degrade), but
    the phantom env candidate that used to 401 is gone."""
    monkeypatch.setattr(cl, "get_models", lambda: [])
    monkeypatch.setattr(cl, "get_vision_models", lambda: [])
    monkeypatch.setattr(cl, "get_secondary_model", lambda: {"model": ""})
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

    assert [item["id"] for item in ordered] == ["backup", "main"]
    assert ordered[0]["endpoints"][0].endswith("chat/completions-alt")
    assert ordered[0]["_configured_rank"] == 1
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


async def test_codex_quota_failure_publishes_actionable_notice_before_fallback(
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
    candidates = [
        {
            "id": "codex",
            "model": "gpt-5.6-sol",
            "provider": "codex_oauth",
            "base_url": "codex://oauth",
            "api_key": "",
            "endpoints": ["codex://oauth"],
        },
        {
            "id": "backup",
            "model": "backup",
            "provider": "openai_compatible",
            "base_url": "https://backup/v1",
            "api_key": "",
            "endpoints": ["https://backup/v1/chat/completions"],
        },
    ]

    result = await cl.call_llm(
        [{"role": "user", "content": "hi"}],
        candidates=candidates,
        publish_events=False,
        record_usage=False,
        session_id="chat_codex",
        round_id="round_codex",
    )

    assert result["content"] == "backup"
    assert availability_notices == [
        {
            "session_id": "chat_codex",
            "round_id": "round_codex",
            "model": "gpt-5.6-sol",
            "failure_kind": "quota_exhausted",
        }
    ]


async def test_codex_auth_failure_falls_back_without_arming_cooldown(
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
        {
            "id": "backup",
            "model": "backup",
            "provider": "openai_compatible",
            "base_url": "https://backup/v1",
            "api_key": "",
            "endpoints": ["https://backup/v1/chat/completions"],
        },
    ]

    result = await cl.call_llm(
        [{"role": "user", "content": "hi"}],
        candidates=candidates,
        publish_events=False,
        record_usage=False,
        session_id="chat_auth",
        round_id="round_auth",
    )

    assert result["content"] == "backup"
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


async def test_last_success_affinity_does_not_publish_fallback_ui_event(monkeypatch):
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
            assert json["model"] == "backup"
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
    assert result["model"] == "backup"
    assert published == []


async def test_actionable_llm_latency_event_is_persisted(tmp_path):
    from cyrene.runtime.database import record_llm_latency

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
        completion_tokens=60, output_tokens_per_second=100.0,
        fallback_used=False, connection_pool_key="loop:1:timeout:120",
    )

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT call_id, request_ms, response_headers_ms, ttft_ms, "
            "first_token_after_headers_ms, generation_ms, "
            "output_tokens_per_second, connection_pool_key FROM llm_latency_events"
        ).fetchone()
    assert row == (
        "llm_1", 900.0, 120.0, 300.0, 180.0, 600.0, 100.0,
        "loop:1:timeout:120",
    )
