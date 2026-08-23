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


async def test_deep_search_rejects_disabled_search(monkeypatch):
    from cyrene.tooling.backends import search
    from cyrene.runtime.search_settings import SearchRuntimeSettings

    monkeypatch.setattr(search, "runtime_settings", lambda: SearchRuntimeSettings(False, ()))

    try:
        await search.deep_search("query")
    except search.SearchBackendUnavailable as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError("expected SearchBackendUnavailable")


async def test_deep_search_uses_first_enabled_provider(monkeypatch):
    from cyrene.tooling.backends import search
    from cyrene.runtime.search_settings import SearchRuntimeSettings

    calls = []

    async def run(provider, topic):
        calls.append((provider, topic))
        return "brave result"

    monkeypatch.setattr(
        search,
        "runtime_settings",
        lambda: SearchRuntimeSettings(True, ("brave", "simplexng")),
    )
    monkeypatch.setattr(search, "_run_search_provider", run)

    result = await search.deep_search("query")

    assert result == "brave result"
    assert calls == [("brave", "query")]


async def test_deep_search_falls_back_after_empty_or_unusable_provider(monkeypatch):
    from cyrene.tooling.backends import search
    from cyrene.runtime.search_settings import SearchRuntimeSettings

    calls = []

    async def run(provider, topic):
        calls.append(provider)
        if provider == "simplexng":
            raise search.SearchBackendUnavailable("no usable content")
        return f"deepseek: {topic}"

    monkeypatch.setattr(
        search,
        "runtime_settings",
        lambda: SearchRuntimeSettings(True, ("simplexng", "deepseek")),
    )
    monkeypatch.setattr(search, "_run_search_provider", run)

    result = await search.deep_search("query")

    assert result == "deepseek: query"
    assert calls == ["simplexng", "deepseek"]


async def test_deep_search_reports_all_provider_failures(monkeypatch):
    from cyrene.tooling.backends import search
    from cyrene.runtime.search_settings import SearchRuntimeSettings

    async def run(provider, _topic):
        raise search.SearchBackendUnavailable(f"{provider} unavailable")

    monkeypatch.setattr(
        search,
        "runtime_settings",
        lambda: SearchRuntimeSettings(True, ("tavily", "brave")),
    )
    monkeypatch.setattr(search, "_run_search_provider", run)

    try:
        await search.deep_search("query")
    except search.SearchBackendUnavailable as exc:
        assert "tavily" in str(exc)
        assert "brave" in str(exc)
    else:
        raise AssertionError("expected SearchBackendUnavailable")


async def test_provider_boundary_converts_unexpected_failure_for_fallback(monkeypatch):
    from cyrene.tooling.backends import search

    async def broken_simplexng(_topic):
        raise ValueError("bad provider response")

    monkeypatch.setattr(search, "_deep_search_simplexng", broken_simplexng)

    try:
        await search._run_search_provider("simplexng", "query")
    except search.SearchBackendUnavailable as exc:
        assert str(exc) == "simplexng failed (ValueError)."
        assert "bad provider response" not in str(exc)
    else:
        raise AssertionError("expected SearchBackendUnavailable")


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
