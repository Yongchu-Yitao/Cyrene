import asyncio


async def test_simplexng_overlaps_fetch_and_filter_without_changing_output(monkeypatch):
    from cyrene.tooling.backends import search

    filter_started = asyncio.Event()
    fetch_saw_filter = []
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

    async def fake_filter(raw_results, _topic):
        filter_started.set()
        await asyncio.sleep(0)
        return raw_results

    async def fake_fetch(_url):
        await asyncio.wait_for(filter_started.wait(), timeout=0.5)
        fetch_saw_filter.append(True)
        return "Fetched body"

    monkeypatch.setattr(search, "_search_simplexng", fake_search)
    monkeypatch.setattr(search, "_filter_results", fake_filter)
    monkeypatch.setattr(search, "_fetch_url", fake_fetch)

    result = await search._deep_search_simplexng("topic")
    assert "No internal answer synthesis was performed" in result
    assert "Filtered source evidence:" in result
    assert "URL: https://example.test/source" in result
    assert "Excerpt: Fetched body" in result
    assert "Do not call WebFetch" in result
    assert fetch_saw_filter == [True]


async def test_simplexng_pipeline_uses_only_one_internal_model_call(monkeypatch):
    from cyrene.tooling.backends import search

    model_prompts = []

    async def fake_search(_query):
        return [{
            "title": "Source",
            "url": "https://example.test/source",
            "snippet": "Relevant snippet",
            "query": "topic",
        }]

    async def fake_fetch(_url):
        return "Fetched evidence"

    async def fake_llm(messages):
        model_prompts.append(messages)
        return "KEEP: 1\nDISCARD:"

    monkeypatch.setattr(search, "_search_simplexng", fake_search)
    monkeypatch.setattr(search, "_fetch_url", fake_fetch)
    monkeypatch.setattr(search, "_call_llm", fake_llm)

    result = await search._deep_search_simplexng("topic")

    assert len(model_prompts) == 1
    assert "search result filter" in model_prompts[0][0]["content"]
    assert "Filtered source evidence:" in result
    assert "Fetched evidence" in result


def test_web_search_contract_declares_self_contained_fetched_evidence():
    from cyrene.tooling.native_definitions import get_native_tool_def

    description = get_native_tool_def("WebSearch")["function"]["description"]

    assert "filter them for relevance" in description
    assert "Synthesize the answer from this evidence" in description
    assert "detailed fetched excerpts" in description
    assert "do not call WebFetch" in description


def test_self_contained_result_preserves_filtered_evidence_order():
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
