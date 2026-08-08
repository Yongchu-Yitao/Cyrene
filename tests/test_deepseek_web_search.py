"""Tests for DeepSeek Responses API web search and SimpleXNG fallback routing."""

from __future__ import annotations


def _official_model(**overrides):
    model = {
        "id": "deepseek-official",
        "model": "deepseek-v4-flash",
        "provider": "openai_compatible",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-test-secret",
    }
    model.update(overrides)
    return model


def _native_response():
    return {
        "output": [
            {"type": "web_search_call", "id": "search_1", "status": "completed"},
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "The searched answer.",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "title": "Example [source]",
                                "url": "https://example.com/source",
                            },
                            {
                                "type": "url_citation",
                                "url_citation": {
                                    "title": "Duplicate",
                                    "url": "https://example.com/source",
                                },
                            },
                            {
                                "type": "url_citation",
                                "title": "Unsafe",
                                "url": "javascript:alert(1)",
                            },
                        ],
                    }
                ],
            },
        ],
        "usage": {
            "input_tokens": 120,
            "output_tokens": 30,
            "total_tokens": 150,
            "input_tokens_details": {"cached_tokens": 20},
        },
    }


def test_candidate_requires_exact_official_endpoint_and_supported_v4_model():
    from cyrene.tooling.backends import deepseek_web_search as dws

    rejected = [
        _official_model(base_url="http://api.deepseek.com/v1"),
        _official_model(base_url="https://api.deepseek.com.evil.test/v1"),
        _official_model(base_url="https://api.deepseek.com/anthropic"),
        _official_model(base_url="https://user@api.deepseek.com/v1"),
        _official_model(model="deepseek-chat"),
        _official_model(provider="codex_oauth"),
        _official_model(api_key=""),
    ]

    for candidate in rejected:
        assert dws.find_official_deepseek_search_candidate([candidate]) is None

    selected = dws.find_official_deepseek_search_candidate(
        [_official_model(base_url="https://api.deepseek.com")]
    )
    assert selected is not None
    assert selected.configured_model == "deepseek-v4-flash"
    assert selected.search_model == "deepseek-v4-flash"


def test_candidate_uses_flash_search_worker_for_official_pro_and_inherits_key():
    from cyrene.tooling.backends import deepseek_web_search as dws

    selected = dws.find_official_deepseek_search_candidate(
        [
            _official_model(
                id="pro",
                model="deepseek-v4-pro[1m]",
                api_key="",
                base_url="https://api.deepseek.com",
            ),
            _official_model(id="flash", api_key='"shared-key"'),
        ]
    )

    assert selected is not None
    assert selected.candidate_id == "pro"
    assert selected.configured_model == "deepseek-v4-pro"
    assert selected.search_model == "deepseek-v4-flash"
    assert selected.api_key == "shared-key"


def test_candidate_reads_preserved_custom_models(monkeypatch):
    from cyrene.tooling.backends import deepseek_web_search as dws

    monkeypatch.setattr(dws, "get_custom_models", lambda: [_official_model()])

    selected = dws.find_official_deepseek_search_candidate()
    assert selected is not None
    assert selected.candidate_id == "deepseek-official"


async def test_native_search_sends_forced_web_search_and_parses_sources(monkeypatch):
    from cyrene.tooling.backends import deepseek_web_search as dws

    requests = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return _native_response()

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, endpoint, *, json, headers):
            requests.append((endpoint, json, headers, self.kwargs))
            return FakeResponse()

    monkeypatch.setattr(dws.httpx, "AsyncClient", FakeClient)
    candidate = dws.find_official_deepseek_search_candidate([_official_model()])
    assert candidate is not None

    result = await dws.search_with_deepseek("latest facts", candidate)

    assert result.text == (
        "The searched answer.\n\nSources:\n"
        "- [Example \\[source\\]](https://example.com/source)"
    )
    assert result.usage == {
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "total_tokens": 150,
        "prompt_cache_hit_tokens": 20,
        "prompt_cache_miss_tokens": 100,
    }
    endpoint, payload, headers, client_kwargs = requests[0]
    assert endpoint == "https://api.deepseek.com/responses"
    assert payload["model"] == "deepseek-v4-flash"
    assert payload["input"] == "latest facts"
    assert payload["tools"] == [{"type": "web_search"}]
    assert payload["tool_choice"] == {"type": "web_search"}
    assert headers["Authorization"] == "Bearer sk-test-secret"
    assert client_kwargs["follow_redirects"] is False


async def test_native_search_errors_are_safe_and_do_not_include_api_key(monkeypatch):
    from cyrene.tooling.backends import deepseek_web_search as dws

    class FakeResponse:
        status_code = 401

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(dws.httpx, "AsyncClient", FakeClient)
    candidate = dws.find_official_deepseek_search_candidate([_official_model()])
    assert candidate is not None

    try:
        await dws.search_with_deepseek("query", candidate)
    except dws.DeepSeekWebSearchError as exc:
        assert str(exc) == "DeepSeek web_search returned HTTP 401"
        assert candidate.api_key not in str(exc)
    else:
        raise AssertionError("expected DeepSeekWebSearchError")


async def test_deep_search_prefers_native_and_records_usage(monkeypatch):
    from cyrene.runtime import database
    from cyrene.tooling.backends import deepseek_web_search as dws
    from cyrene.tooling.backends import search

    candidate = dws.DeepSeekSearchCandidate("deepseek", "deepseek-v4-pro", "key")
    native = dws.DeepSeekWebSearchResult(
        text="native result",
        usage={
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "prompt_cache_hit_tokens": 2,
            "prompt_cache_miss_tokens": 8,
        },
        duration_ms=321,
    )
    recorded = []

    monkeypatch.setattr(dws, "find_official_deepseek_search_candidate", lambda: candidate)
    monkeypatch.setattr(dws, "search_with_deepseek", lambda *_args: _async_value(native))
    monkeypatch.setattr(
        search,
        "_deep_search_simplexng",
        lambda *_args: _async_failure(AssertionError("must not use SimpleXNG")),
    )

    async def record(db_path, **kwargs):
        recorded.append((db_path, kwargs))

    monkeypatch.setattr(database, "record_token_usage", record)

    result = await search.deep_search(
        "query",
        db_path="runtime.db",
        session_id="chat-1",
        round_id="round-1",
    )

    assert result == "native result"
    assert recorded == [
        (
            "runtime.db",
            {
                "model": "deepseek-v4-flash",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "cache_hit_tokens": 2,
                "cache_miss_tokens": 8,
                "duration_ms": 321,
                "round_id": "round-1",
                "session_id": "chat-1",
                "caller": "search",
            },
        )
    ]


async def test_deep_search_falls_back_when_native_search_fails(monkeypatch):
    from cyrene.tooling.backends import deepseek_web_search as dws
    from cyrene.tooling.backends import search

    candidate = dws.DeepSeekSearchCandidate("deepseek", "deepseek-v4-flash", "key")
    monkeypatch.setattr(dws, "find_official_deepseek_search_candidate", lambda: candidate)
    monkeypatch.setattr(
        dws,
        "search_with_deepseek",
        lambda *_args: _async_failure(dws.DeepSeekWebSearchError("safe failure")),
    )
    monkeypatch.setattr(
        search,
        "_deep_search_simplexng",
        lambda topic: _async_value(f"simplexng: {topic}"),
    )

    assert await search.deep_search("query") == "simplexng: query"


async def test_deep_search_uses_simplexng_without_official_candidate(monkeypatch):
    from cyrene.tooling.backends import deepseek_web_search as dws
    from cyrene.tooling.backends import search

    monkeypatch.setattr(dws, "find_official_deepseek_search_candidate", lambda: None)
    monkeypatch.setattr(
        search,
        "_deep_search_simplexng",
        lambda topic: _async_value(f"simplexng: {topic}"),
    )

    assert await search.deep_search("query") == "simplexng: query"


async def test_tool_passes_run_context_to_search(monkeypatch):
    from cyrene.agent.context import bind_run_context
    from cyrene.tool_impl.core import web_search

    captured = []

    async def fake_search(query, **kwargs):
        captured.append((query, kwargs))
        return "ok"

    monkeypatch.setattr(web_search, "deep_search", fake_search)
    with bind_run_context(session_id="session-context", round_id="round-context"):
        result = await web_search._tool_websearch(
            {"query": "facts"}, None, 123, "runtime.db", None
        )

    assert result == "ok"
    assert captured == [
        (
            "facts",
            {
                "db_path": "runtime.db",
                "session_id": "session-context",
                "round_id": "round-context",
            },
        )
    ]


async def _async_value(value):
    return value


async def _async_failure(exc):
    raise exc
