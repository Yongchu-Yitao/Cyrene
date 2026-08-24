import asyncio

import pytest


async def test_simplexng_fetches_pages_and_returns_evidence(monkeypatch):
    from cyrene.tooling.backends import search

    results = [
        {
            "title": "Source",
            "url": "https://example.test/source",
            "snippet": "Snippet",
            "query": "topic",
        }
    ]

    async def fake_search(_query):
        return [dict(item) for item in results]

    async def fake_fetch(_url, session=None, **_kwargs):
        await asyncio.sleep(0)
        return "Fetched body"

    monkeypatch.setattr(search, "_search_simplexng", fake_search)
    monkeypatch.setattr(search, "_fetch_url", fake_fetch)

    result = await search._deep_search_simplexng("topic")
    assert "No internal answer synthesis was performed" in result
    assert "Source evidence:" in result
    assert "URL: https://example.test/source" in result
    assert "Excerpt: Fetched body" in result
    assert "Do not call WebFetch" in result


async def test_simplexng_preview_fetches_only_first_three_pages(monkeypatch):
    from cyrene.tooling.backends import search

    async def fake_search(_query):
        return [
            {
                "title": f"Weather source {index}",
                "url": f"https://example.test/weather-{index}",
                "snippet": f"Search snippet {index}",
                "query": "Guangzhou weather",
            }
            for index in range(1, 5)
        ]

    fetches = []

    async def fake_fetch(url, _client):
        fetches.append(url)
        return f"Fetched preview for {url}"

    monkeypatch.setattr(search, "_search_simplexng", fake_search)
    monkeypatch.setattr(search, "_fetch_preview_url", fake_fetch)

    result = await search._deep_search_simplexng(
        "Guangzhou weather",
        detail="preview",
    )

    assert fetches == [
        "https://example.test/weather-1",
        "https://example.test/weather-2",
        "https://example.test/weather-3",
    ]
    assert "fetched the first three result pages" in result
    assert "[1] Weather source 1" in result
    assert "[2] Weather source 2" in result
    assert "[3] Weather source 3" in result
    assert "Weather source 4" not in result
    assert "Preview: Fetched preview" in result


async def test_preview_gives_remaining_page_five_seconds_after_first_success(monkeypatch):
    from cyrene.tooling.backends import search

    assert search._PREVIEW_REMAINING_TIMEOUT == 5.0
    results = [
        {"url": "https://example.test/fast"},
        {"url": "https://example.test/slow-1"},
        {"url": "https://example.test/slow-2"},
    ]
    slow_cancelled = asyncio.Event()

    async def staged_fetch(url, _client):
        if url.endswith("/fast"):
            return "fast page"
        try:
            await asyncio.sleep(60)
        finally:
            slow_cancelled.set()

    monkeypatch.setattr(search, "_fetch_preview_url", staged_fetch)

    output = await search._fetch_preview_pages(results, remaining_timeout=0.01)

    assert output == ["fast page", "", ""]
    assert slow_cancelled.is_set()


async def test_simplexng_pipeline_makes_no_internal_model_calls(monkeypatch):
    from cyrene.tooling.backends import search

    async def fake_search(_query):
        return [{
            "title": "Source",
            "url": "https://example.test/source",
            "snippet": "Relevant snippet",
            "query": "topic",
        }]

    async def fake_fetch(_url, session=None, **_kwargs):
        return "Fetched evidence"

    monkeypatch.setattr(search, "_search_simplexng", fake_search)
    monkeypatch.setattr(search, "_fetch_url", fake_fetch)

    result = await search._deep_search_simplexng("topic")

    assert "Source evidence:" in result
    assert "Fetched evidence" in result


class _FakeSearchResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSearchSession:
    def __init__(self, payload):
        self._payload = payload
        self.trust_env = True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def get(self, *_args, **_kwargs):
        return _FakeSearchResponse(self._payload)


async def test_simplexng_engine_outage_is_not_reported_as_zero_results(monkeypatch):
    from cyrene.tooling.backends import search

    payload = {
        "results": [],
        "unresponsive_engines": [
            ["google", "HTTP connection error"],
            ["duckduckgo", "HTTP connection error"],
        ],
    }
    monkeypatch.setattr(search, "_get_simplexng_url", lambda: "http://127.0.0.1:8888")
    monkeypatch.setattr(search.requests, "Session", lambda: _FakeSearchSession(payload))

    with pytest.raises(search.SearchBackendUnavailable, match="duckduckgo, google"):
        await search._deep_search_simplexng("广州天气")


async def test_all_simplexng_engine_failures_continue_to_next_provider(monkeypatch):
    from cyrene.runtime.search_settings import SearchRuntimeSettings
    from cyrene.tooling.backends import search

    payload = {
        "results": [],
        "unresponsive_engines": [
            [engine, "HTTP connection error"]
            for engine in (
                "bing",
                "brave",
                "duckduckgo",
                "google",
                "qwant",
                "startpage",
            )
        ],
    }
    monkeypatch.setattr(search, "_get_simplexng_url", lambda: "http://127.0.0.1:8888")
    monkeypatch.setattr(search.requests, "Session", lambda: _FakeSearchSession(payload))
    monkeypatch.setattr(
        search,
        "runtime_settings",
        lambda: SearchRuntimeSettings(True, ("simplexng", "deepseek")),
    )

    async def deepseek_result(_topic):
        return "DeepSeek fallback result"

    monkeypatch.setattr(search, "_deep_search_deepseek", deepseek_result)

    assert await search.deep_search("广州天气") == "DeepSeek fallback result"


async def test_simplexng_genuine_empty_result_remains_empty(monkeypatch):
    from cyrene.tooling.backends import search

    payload = {"results": [], "unresponsive_engines": []}
    monkeypatch.setattr(search, "_get_simplexng_url", lambda: "http://127.0.0.1:8888")
    monkeypatch.setattr(search.requests, "Session", lambda: _FakeSearchSession(payload))

    with pytest.raises(search.SearchBackendUnavailable, match="no usable search results"):
        await search._deep_search_simplexng("不存在的内容")


async def test_simplexng_result_without_snippet_or_page_content_is_unusable(monkeypatch):
    from cyrene.tooling.backends import search

    async def fake_search(_query):
        return [{
            "title": "Empty source",
            "url": "https://example.test/empty",
            "snippet": "",
            "query": "topic",
        }]

    async def fake_fetch(_url, session=None, **_kwargs):
        return ""

    monkeypatch.setattr(search, "_search_simplexng", fake_search)
    monkeypatch.setattr(search, "_fetch_url", fake_fetch)

    with pytest.raises(search.SearchBackendUnavailable, match="without usable content"):
        await search._deep_search_simplexng("topic")


class _FakeProviderSession:
    def __init__(self, payload, calls):
        self.payload = payload
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        return _FakeSearchResponse(self.payload)

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        return _FakeSearchResponse(self.payload)


def test_proxied_session_does_not_allow_system_proxy_override(monkeypatch):
    from cyrene.tooling.backends import search
    from cyrene.tooling.backends import searxng_manager

    configured = "http://proxy.example.test:8080"
    monkeypatch.setattr(
        searxng_manager,
        "get_effective_search_proxy",
        lambda: configured,
    )

    session = search._proxied_session()

    assert session.trust_env is False
    assert session.proxies == {"http": configured, "https": configured}


async def test_tavily_uses_bearer_auth_and_normalizes_results(monkeypatch):
    from cyrene.tooling.backends import search

    calls = []
    payload = {
        "results": [{
            "title": "Tavily source",
            "url": "https://example.test/tavily",
            "content": "Tavily evidence",
        }],
    }
    monkeypatch.setattr(search, "provider_api_key", lambda provider: "tvly-secret")
    monkeypatch.setattr(
        search,
        "_proxied_session",
        lambda: _FakeProviderSession(payload, calls),
    )

    result = await search._search_tavily("topic")

    method, url, request = calls[0]
    assert (method, url) == ("post", "https://api.tavily.com/search")
    assert request["headers"]["Authorization"] == "Bearer tvly-secret"
    assert "api_key" not in request["json"]
    assert "Tavily evidence" in result


async def test_brave_uses_subscription_token_and_normalizes_results(monkeypatch):
    from cyrene.tooling.backends import search

    calls = []
    payload = {
        "web": {
            "results": [{
                "title": "Brave source",
                "url": "https://example.test/brave",
                "description": "Brave evidence",
            }],
        },
    }
    monkeypatch.setattr(search, "provider_api_key", lambda provider: "brave-secret")
    monkeypatch.setattr(
        search,
        "_proxied_session",
        lambda: _FakeProviderSession(payload, calls),
    )

    result = await search._search_brave("topic")

    method, url, request = calls[0]
    assert (method, url) == (
        "get",
        "https://api.search.brave.com/res/v1/web/search",
    )
    assert request["headers"]["X-Subscription-Token"] == "brave-secret"
    assert "Brave evidence" in result


class _FakeResponse:
    def __init__(self, content: bytes, encoding: str, apparent: str = "utf-8"):
        self.content = content
        self.encoding = encoding
        self.apparent_encoding = apparent
        self.status_code = 200

    def raise_for_status(self):
        pass


class _FakeSession:
    def __init__(self, response):
        self._response = response

    def get(self, _url, timeout=None):
        return self._response

    def close(self):
        pass


async def test_fetch_url_decodes_chinese_page_without_charset_declaration(monkeypatch):
    from cyrene.tooling.backends import search

    # 中文站点头部无 charset 时 requests 默认 ISO-8859-1,UTF-8 页面会整体乱码
    body = "广州天气 雷阵雨 26℃".encode("utf-8")
    monkeypatch.setattr(
        search,
        "_proxied_session",
        lambda: _FakeSession(_FakeResponse(body, encoding="ISO-8859-1")),
    )

    text = await search._fetch_url("https://example.test/weather")

    assert text == "广州天气 雷阵雨 26℃"
    assert "å¹¿å·ž" not in text


async def test_fetch_url_respects_declared_non_utf8_charset(monkeypatch):
    from cyrene.tooling.backends import search

    body = "广州天气".encode("gbk")
    monkeypatch.setattr(
        search,
        "_proxied_session",
        lambda: _FakeSession(_FakeResponse(body, encoding="gbk")),
    )

    text = await search._fetch_url("https://example.test/weather")

    assert text == "广州天气"


def test_web_search_contract_exposes_preview_and_content_detail_modes():
    from cyrene.tooling.native_definitions import get_native_tool_def

    tool = get_native_tool_def("WebSearch")["function"]
    description = tool["description"]
    properties = tool["parameters"]["properties"]

    assert "filter them for relevance" not in description
    assert 'detail="preview"' in description
    assert 'detail="content"' in description
    assert properties["detail"]["enum"] == ["preview", "content"]
    assert properties["detail"]["default"] == "preview"


def test_self_contained_result_preserves_evidence_order():
    from cyrene.tooling.backends.search import _self_contained_search_result

    sources = [
        {"title": "First", "url": "https://example.test/first", "snippet": "one"},
        {"title": "Second", "url": "https://example.test/second", "snippet": "two"},
    ]
    result = _self_contained_search_result(
        sources,
        ["First fetched body", "Second fetched body"],
    )

    assert "No internal answer synthesis was performed" in result
    assert result.index("[1] First") < result.index("[2] Second")
    assert "Excerpt: First fetched body" in result
    assert "Excerpt: Second fetched body" in result
