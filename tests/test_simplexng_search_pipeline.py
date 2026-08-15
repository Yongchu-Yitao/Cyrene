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
