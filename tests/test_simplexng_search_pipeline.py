import asyncio


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

    async def fake_fetch(_url, session=None):
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


async def test_simplexng_pipeline_makes_no_internal_model_calls(monkeypatch):
    from cyrene.tooling.backends import search

    async def fake_search(_query):
        return [{
            "title": "Source",
            "url": "https://example.test/source",
            "snippet": "Relevant snippet",
            "query": "topic",
        }]

    async def fake_fetch(_url, session=None):
        return "Fetched evidence"

    monkeypatch.setattr(search, "_search_simplexng", fake_search)
    monkeypatch.setattr(search, "_fetch_url", fake_fetch)

    result = await search._deep_search_simplexng("topic")

    assert "Source evidence:" in result
    assert "Fetched evidence" in result


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


def test_web_search_contract_declares_self_contained_fetched_evidence():
    from cyrene.tooling.native_definitions import get_native_tool_def

    description = get_native_tool_def("WebSearch")["function"]["description"]

    assert "filter them for relevance" not in description
    assert "Synthesize the answer from this evidence" in description
    assert "detailed fetched excerpts" in description
    assert "do not call WebFetch" in description


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
