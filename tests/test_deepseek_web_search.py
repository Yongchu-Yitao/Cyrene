"""Standalone DeepSeek Search/Fetch contracts and provider fallback routing."""

from __future__ import annotations

import json

import httpx
import pytest

from cyrene.plugins.builtin.cyrene_content import deepseek_web_search as dws


def _official_model(**overrides):
    model = {
        "id": "deepseek-official",
        "model": "deepseek-flash",
        "provider": "openai_compatible",
        "adapter": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-test-secret",
        "options": {"provider_preset": "deepseek"},
    }
    model.update(overrides)
    return model


def _search_response():
    return {"results": [{
        "title": "Example source",
        "url": "https://example.com/source",
        "snippet": "Short snippet.",
        "summary": "Query-specific summary.",
        "content": "# Full page\n\nPage evidence.",
        "date": "2026-10-17T00:00:00Z",
        "site_name": "Example",
        "rerank_score": 2.4,
        "authority": 3,
    }]}


@pytest.fixture
def candidate():
    return dws.find_official_deepseek_search_candidate([_official_model()])


@pytest.fixture
def api(monkeypatch):
    requests = []
    options = []
    state = {"status": 200, "json": _search_response()}
    original_client = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        if "error" in state:
            raise state["error"]
        if "body" in state:
            return httpx.Response(state["status"], content=state["body"])
        return httpx.Response(state["status"], json=state["json"])

    def client(**kwargs):
        options.append(kwargs)
        return original_client(transport=httpx.MockTransport(respond), **{key: value for key, value in kwargs.items() if key != "proxy"})

    monkeypatch.setattr(dws.httpx, "AsyncClient", client)
    monkeypatch.setattr(dws, "get_effective_search_proxy", lambda: "")
    return state, requests, options


@pytest.mark.parametrize("overrides", [
    {"base_url": "http://api.deepseek.com/v1"},
    {"base_url": "https://api.deepseek.com.evil.test/v1"},
    {"base_url": "https://api.deepseek.com/anthropic"},
    {"base_url": "https://user@api.deepseek.com/v1"},
    {"base_url": "https://api.deepseek.com:444/v1"},
    {"base_url": "https://api.deepseek.com/v1?key=secret"},
    {"options": {"provider_preset": "openai"}},
    {"api_key": ""},
    {"enabled": False},
])
def test_candidate_requires_enabled_official_deepseek_credentials(overrides):
    assert dws.find_official_deepseek_search_candidate([_official_model(**overrides)]) is None


@pytest.mark.parametrize("model", ["deepseek-flash", "deepseek-v4-flash", "deepseek-v4-pro[1m]", "deepseek-chat", "future-model"])
def test_candidate_is_independent_of_chat_model_and_adapter(model):
    selected = dws.find_official_deepseek_search_candidate([
        _official_model(model=model, adapter="openai_responses", base_url="https://api.deepseek.com")
    ])
    assert selected is not None
    assert selected.configured_model == model.split("[", 1)[0]
    assert selected.api_key not in repr(selected)


def test_candidate_does_not_borrow_a_key_from_another_profile():
    selected = dws.find_official_deepseek_search_candidate([
        _official_model(id="pro", model="deepseek-v4-pro[1m]", api_key=""),
        _official_model(id="flash", api_key='"shared-key"'),
    ])
    assert selected.candidate_id == "flash"
    assert selected.api_key == "shared-key"


def test_candidate_reads_the_canonical_model_graph(monkeypatch):
    from types import SimpleNamespace
    import cyrene.core.plugin

    monkeypatch.setattr(dws, "model_plugin_catalog", lambda: [{"id": "deepseek"}])
    configuration = {"profiles": [{"id": "profile-deepseek"}]}
    service = SimpleNamespace(
        get_model_configuration=lambda: configuration,
        candidate_for_profile=lambda _profile_id, _configuration: _official_model(),
    )
    monkeypatch.setattr(cyrene.core.plugin, "application_plugin_service", lambda name: service if name == "model_configuration" else None)
    assert dws.find_official_deepseek_search_candidate().candidate_id == "deepseek-official"


@pytest.mark.parametrize("detail, expected", [("preview", "Query-specific summary."), ("content", "Page evidence.")])
async def test_search_posts_standalone_contract_and_returns_evidence(api, candidate, detail, expected):
    state, requests, options = api
    result = await dws.search_with_deepseek('DeepSeek site:example.com after:2026', candidate, detail=detail, max_results=3)
    request = requests[0]
    assert str(request.url) == "https://api.deepseek.com/v1/tools/search"
    assert json.loads(request.content) == {
        "query": 'DeepSeek site:example.com after:2026', "count": 3, "need_content": detail == "content",
    }
    assert request.headers["Authorization"] == "Bearer sk-test-secret"
    assert options[0]["follow_redirects"] is False
    assert options[0]["trust_env"] is False
    assert expected in result.text
    if detail == "preview":
        assert "Page evidence." not in result.text
    assert "https://example.com/source" in result.text
    assert "authority: 3" in result.text
    assert "rerank_score: 2.4" in result.text
    assert "date: 2026-10-17" in result.text
    assert result.result_count == 1
    assert result.duration_ms >= 0


async def test_search_deduplicates_and_skips_unsafe_or_unusable_results(api, candidate):
    state, _, _ = api
    first = _search_response()["results"][0]
    state["json"] = {"results": [
        None, {**first, "url": "javascript:alert(1)"},
        {**first, "url": "https://user:password@example.com/"},
        {**first, "url": "https://example.com:bad/"},
        first, first, {**first, "url": "https://example.com/two", "content": "", "summary": ""},
    ]}
    result = await dws.search_with_deepseek("query", candidate)
    assert result.result_count == 2
    assert result.text.count("URL: https://example.com/source") == 1
    assert "Short snippet." in result.text
    assert "javascript:" not in result.text
    assert "password" not in result.text


@pytest.mark.parametrize("payload, code", [
    ({"results": []}, "no_results"),
    ({"results": [{"url": "https://example.com"}]}, "no_results"),
    ({"results": None}, "provider_request_failed"),
    ({"output": []}, "provider_request_failed"),
    ([], "provider_request_failed"),
])
async def test_empty_or_invalid_responses_fail_for_provider_fallback(api, candidate, payload, code):
    api[0]["json"] = payload
    with pytest.raises(dws.DeepSeekWebSearchError) as raised:
        await dws.search_with_deepseek("query", candidate)
    assert raised.value.error_code == code


@pytest.mark.parametrize("query", ["", "   ", "字" * 129])
async def test_search_validates_query_before_network_call(api, candidate, query):
    with pytest.raises(dws.DeepSeekWebSearchError) as raised:
        await dws.search_with_deepseek(query, candidate)
    assert raised.value.error_code == "invalid_arguments"
    assert not api[1]


@pytest.mark.parametrize("status", [301, 400, 401, 402, 403, 408, 429, 500, 504])
async def test_http_errors_preserve_status_without_response_body_or_key(api, candidate, status):
    api[0].update(status=status, json={"error": {"message": candidate.api_key}})
    with pytest.raises(dws.DeepSeekWebSearchError) as raised:
        await dws.search_with_deepseek("query", candidate)
    assert raised.value.status_code == status
    assert str(raised.value) == f"DeepSeek search returned HTTP {status}"
    assert candidate.api_key not in str(raised.value)


async def test_invalid_json_and_transport_errors_are_safe(api, candidate):
    state = api[0]
    state["body"] = b"not json"
    with pytest.raises(dws.DeepSeekWebSearchError, match="invalid JSON"):
        await dws.search_with_deepseek("query", candidate)
    state["error"] = httpx.ConnectError(candidate.api_key)
    with pytest.raises(dws.DeepSeekWebSearchError) as raised:
        await dws.search_with_deepseek("query", candidate)
    assert str(raised.value) == "DeepSeek search transport failed (ConnectError)"


async def test_fetch_posts_url_and_preserves_markdown(api, candidate):
    api[0]["json"] = {"title": "Example", "content": "# Article\n\n[Reference](https://example.org)"}
    result = await dws.fetch_with_deepseek("https://example.com/page", candidate)
    assert str(api[1][0].url) == "https://api.deepseek.com/v1/tools/fetch"
    assert json.loads(api[1][0].content) == {"url": "https://example.com/page"}
    assert result == "Example\nURL: https://example.com/page\n\n# Article\n\n[Reference](https://example.org)"


@pytest.mark.parametrize("url", ["file:///tmp/file", "https://user:password@example.com", "https://example.com:bad", "https://example.com/" + "x" * 8192])
async def test_fetch_validates_url_before_network_call(api, candidate, url):
    with pytest.raises(dws.DeepSeekWebSearchError) as raised:
        await dws.fetch_with_deepseek(url, candidate)
    assert raised.value.error_code == "invalid_arguments"
    assert not api[1]


@pytest.mark.parametrize("content", ["", " ", None, []])
async def test_fetch_requires_usable_content(api, candidate, content):
    api[0]["json"] = {"title": "Example", "content": content}
    with pytest.raises(dws.DeepSeekWebSearchError) as raised:
        await dws.fetch_with_deepseek("https://example.com", candidate)
    assert raised.value.error_code == "no_results"


async def test_backend_passes_detail_and_count_to_deepseek(monkeypatch):
    from cyrene.plugins.builtin.cyrene_content import search_backend as search
    calls = []

    async def run(topic, **kwargs):
        calls.append((topic, kwargs))
        return "Evidence"

    monkeypatch.setattr(search, "_deep_search_deepseek", run)
    assert await search._run_search_provider("deepseek", "query", detail="preview", max_results=2) == "Evidence"
    assert calls == [("query", {"detail": "preview", "max_results": 2})]


async def test_backend_consumes_standalone_result_without_model_usage(api, candidate, monkeypatch):
    from cyrene.plugins.builtin.cyrene_content import search_backend as search

    monkeypatch.setattr(search, "find_official_deepseek_search_candidate", lambda: candidate)
    result = await search._run_search_provider("deepseek", "query", detail="preview", max_results=2)
    assert "Query-specific summary." in result
    assert json.loads(api[1][0].content) == {"query": "query", "count": 2, "need_content": False}


@pytest.mark.parametrize("status, code, scope, retryable", [
    (400, "invalid_arguments", "different_arguments", True),
    (401, "credentials_invalid", "after_config_change", False),
    (402, "insufficient_balance", "after_config_change", False),
    (429, "rate_limited", "after_delay", True),
    (500, "provider_request_failed", "after_delay", True),
])
async def test_backend_classifies_api_failures(monkeypatch, status, code, scope, retryable):
    from cyrene.plugins.builtin.cyrene_content import search_backend as search

    async def fail(*_args, **_kwargs):
        raise dws.DeepSeekWebSearchError(f"DeepSeek search returned HTTP {status}", status_code=status)

    monkeypatch.setattr(search, "_deep_search_deepseek", fail)
    with pytest.raises(search.SearchBackendUnavailable) as raised:
        await search._run_search_provider("deepseek", "query")
    assert raised.value.error_code == code
    assert raised.value.retry_scope == scope
    assert raised.value.retryable is retryable


async def test_deep_search_rejects_disabled_search(monkeypatch):
    from cyrene.plugins.builtin.cyrene_content import search_backend as search
    from cyrene.plugins.builtin.cyrene_content.search_settings import SearchRuntimeSettings

    monkeypatch.setattr(search, "runtime_settings", lambda: SearchRuntimeSettings(False, ()))

    try:
        await search.deep_search("query")
    except search.SearchBackendUnavailable as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError("expected SearchBackendUnavailable")


async def test_deep_search_uses_first_enabled_provider(monkeypatch):
    from cyrene.plugins.builtin.cyrene_content import search_backend as search
    from cyrene.plugins.builtin.cyrene_content.search_settings import SearchRuntimeSettings

    calls = []

    async def run(provider, topic, **_kwargs):
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
    from cyrene.plugins.builtin.cyrene_content import search_backend as search
    from cyrene.plugins.builtin.cyrene_content.search_settings import SearchRuntimeSettings

    calls = []

    async def run(provider, topic, **_kwargs):
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
    from cyrene.plugins.builtin.cyrene_content import search_backend as search
    from cyrene.plugins.builtin.cyrene_content.search_settings import SearchRuntimeSettings

    async def run(provider, _topic, **_kwargs):
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
    from cyrene.plugins.builtin.cyrene_content import search_backend as search

    async def broken_simplexng(_topic, **_kwargs):
        raise ValueError("bad provider response")

    monkeypatch.setattr(search, "_deep_search_simplexng", broken_simplexng)

    try:
        await search._run_search_provider("simplexng", "query")
    except search.SearchBackendUnavailable as exc:
        assert str(exc) == "simplexng failed (ValueError)."
        assert "bad provider response" not in str(exc)
    else:
        raise AssertionError("expected SearchBackendUnavailable")


async def test_tool_passes_run_context_to_search():
    from cyrene.core.plugin import PluginContext
    from cyrene.plugins.builtin.cyrene_content import web_search

    captured = []

    async def fake_search(query, **kwargs):
        captured.append((query, kwargs))
        return "ok"

    class SearchService:
        search = staticmethod(fake_search)

    result = await web_search._tool_websearch(
        {"query": "facts", "detail": "preview", "max_results": 3},
        PluginContext(
            data={
                "db_path": "runtime.db",
                "run_context": {
                    "session_id": "session-context",
                    "round_id": "round-context",
                },
            },
            services={"web_search": SearchService()},
        ),
    )

    assert result == "ok"
    assert captured == [
        (
            "facts",
            {
                "db_path": "runtime.db",
                "session_id": "session-context",
                "round_id": "round-context",
                "detail": "preview",
                "max_results": 3,
            },
        )
    ]


async def _async_value(value):
    return value


async def test_search_and_fetch_use_search_proxy(api, candidate, monkeypatch):
    monkeypatch.setattr(dws, "get_effective_search_proxy", lambda: "http://127.0.0.1:8899")
    await dws.search_with_deepseek("query", candidate)
    api[0]["json"] = {"content": "Page content"}
    await dws.fetch_with_deepseek("https://example.com", candidate)
    assert [options["proxy"] for options in api[2]] == ["http://127.0.0.1:8899"] * 2


async def test_search_honors_requested_result_limit_and_unicode_boundary(api, candidate):
    first = _search_response()["results"][0]
    api[0]["json"] = {"results": [first, {**first, "url": "https://example.com/two"}]}
    result = await dws.search_with_deepseek("字" * 128, candidate, max_results=1)
    assert result.result_count == 1
    assert "https://example.com/two" not in result.text


async def test_backend_empty_search_falls_back_without_affecting_health(monkeypatch):
    from cyrene.plugins.builtin.cyrene_content import search_backend as search
    from cyrene.plugins.builtin.cyrene_content.search_settings import SearchRuntimeSettings

    async def empty(*_args, **_kwargs):
        raise dws.DeepSeekWebSearchError("DeepSeek search returned no usable results", error_code="no_results")

    async def fallback(*_args, **_kwargs):
        return "Local evidence"

    monkeypatch.setattr(search, "_deep_search_deepseek", empty)
    monkeypatch.setattr(search, "_deep_search_simplexng", fallback)
    monkeypatch.setattr(search, "runtime_settings", lambda: SearchRuntimeSettings(True, ("deepseek", "simplexng")))
    health = search.ProviderHealthRegistry()
    assert await search.deep_search("query", provider_health=health) == "Local evidence"
    assert health.snapshots(("deepseek",))[0]["state"] == "closed"


async def test_invalid_query_keeps_argument_retry_policy_at_tool_boundary(monkeypatch):
    from cyrene.plugins.builtin.cyrene_content import search_backend as search
    from cyrene.plugins.builtin.cyrene_content.search_settings import SearchRuntimeSettings

    async def invalid(*_args, **_kwargs):
        raise dws.DeepSeekWebSearchError("DeepSeek search query must contain 1 to 128 characters", error_code="invalid_arguments")

    monkeypatch.setattr(search, "_deep_search_deepseek", invalid)
    monkeypatch.setattr(search, "runtime_settings", lambda: SearchRuntimeSettings(True, ("deepseek",)))
    with pytest.raises(search.SearchBackendUnavailable) as raised:
        await search.deep_search("字" * 129)
    assert raised.value.error_code == "search_invalid_arguments"
    assert raised.value.retry_scope == "different_arguments"
    assert raised.value.circuit_scope == "none"
