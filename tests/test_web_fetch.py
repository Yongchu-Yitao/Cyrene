from cyrene.plugins.builtin.cyrene_content.web_fetch import _extract_response_text


def test_extract_response_text_removes_html_and_hidden_content():
    html = """<!doctype html>
    <html><head><title>Example &amp; Docs</title><style>.hidden { color: red }</style></head>
    <body><main><h1>Install</h1><p>Use <strong>mise</strong> today.</p>
    <script>window.secret = 'do not include'</script>
    <ul><li>Python</li><li>bun</li></ul></main></body></html>"""

    result = _extract_response_text(html, "text/html; charset=utf-8")

    assert "Example & Docs" in result
    assert "Install" in result
    assert "Use mise today." in result
    assert "Python\nbun" in result
    assert "<html" not in result
    assert "window.secret" not in result
    assert ".hidden" not in result


def test_extract_response_text_preserves_non_html_response():
    body = '{"html":"<strong>data, not markup</strong>"}'

    assert _extract_response_text(body, "application/json") == body


def test_extract_response_text_detects_html_when_content_type_is_generic():
    body = "  <!DOCTYPE html><html><body><p>Readable text</p></body></html>"

    assert _extract_response_text(body, "application/octet-stream") == "Readable text"


def test_extract_response_text_preserves_and_resolves_http_links():
    body = """<html><body>
    <p><a href="/docs/start#install">文档</a></p>
    <p><a href="https://example.org/reference">Reference</a></p>
    <p><a href="mailto:help@example.org">Email us</a></p>
    </body></html>"""

    result = _extract_response_text(body, "text/html", "https://example.com/guide/page")

    assert "文档 (https://example.com/docs/start)" in result
    assert "Reference (https://example.org/reference)" in result
    assert "Email us" in result
    assert "mailto:" not in result


def test_extract_response_text_limits_unique_link_urls():
    body = """<html><body>
    <a href="/one">One</a>
    <a href="/one">One again</a>
    <a href="/two">Two</a>
    </body></html>"""

    result = _extract_response_text(
        body, "text/html", "https://example.com/base", max_links=1
    )

    assert "One (https://example.com/one)" in result
    assert result.count("https://example.com/one") == 1
    assert "https://example.com/two" not in result


async def test_webfetch_prefers_enabled_deepseek_and_preserves_markdown(monkeypatch):
    from cyrene.core.plugin import PluginContext
    from cyrene.plugins.builtin.cyrene_content import web_fetch
    from cyrene.plugins.builtin.cyrene_content.search_settings import SearchRuntimeSettings

    calls = []
    credential = object()
    monkeypatch.setattr(web_fetch, "runtime_settings", lambda name: SearchRuntimeSettings(True, ("deepseek",)))
    monkeypatch.setattr(web_fetch, "find_official_deepseek_search_candidate", lambda: credential)

    async def fetch(url, candidate):
        calls.append((url, candidate))
        return "# Article\n\n[Reference](https://example.org)"

    monkeypatch.setattr(web_fetch, "fetch_with_deepseek", fetch)
    def unexpected_client(**_kwargs):
        raise AssertionError("Direct retrieval must not run after successful Fetch")
    monkeypatch.setattr(web_fetch.httpx, "AsyncClient", unexpected_client)
    result = await web_fetch._tool_webfetch({"url": "https://example.com"}, PluginContext())
    assert result == "# Article\n\n[Reference](https://example.org)"
    assert calls == [("https://example.com", credential)]


async def test_webfetch_falls_back_to_direct_html_after_deepseek_failure(monkeypatch):
    from cyrene.core.plugin import PluginContext
    from cyrene.plugins.builtin.cyrene_content import web_fetch
    from cyrene.plugins.builtin.cyrene_content.search_settings import SearchRuntimeSettings
    import httpx

    monkeypatch.setattr(web_fetch, "runtime_settings", lambda name: SearchRuntimeSettings(True, ("deepseek",)))
    monkeypatch.setattr(web_fetch, "find_official_deepseek_search_candidate", lambda: object())
    calls = []

    async def fail(url, candidate):
        calls.append("deepseek")
        raise web_fetch.DeepSeekWebSearchError("DeepSeek fetch returned no page content", error_code="no_results")

    def direct(request):
        calls.append("direct")
        assert "authorization" not in request.headers
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<h1>Fallback</h1><p>Evidence</p>")

    original_client = httpx.AsyncClient
    client_options = []

    def client(**kwargs):
        client_options.append(kwargs)
        return original_client(transport=httpx.MockTransport(direct), **{k: v for k, v in kwargs.items() if k != "proxy"})

    monkeypatch.setattr(web_fetch, "get_effective_search_proxy", lambda: "http://proxy.test:8080")
    monkeypatch.setattr(web_fetch, "fetch_with_deepseek", fail)
    monkeypatch.setattr(web_fetch.httpx, "AsyncClient", client)
    result = await web_fetch._tool_webfetch({"url": "https://example.com"}, PluginContext())
    assert result == "Fallback\nEvidence"
    assert calls == ["deepseek", "direct"]
    assert client_options[0]["proxy"] == "http://proxy.test:8080"
    assert client_options[0]["trust_env"] is False


async def test_webfetch_skips_deepseek_when_disabled_or_unconfigured(monkeypatch):
    from cyrene.core.plugin import PluginContext
    from cyrene.plugins.builtin.cyrene_content import web_fetch
    from cyrene.plugins.builtin.cyrene_content.search_settings import SearchRuntimeSettings
    import httpx

    original_client = httpx.AsyncClient
    requests = []

    def direct(request):
        requests.append(request)
        return httpx.Response(200, text="Direct content")

    async def unexpected_fetch(*_args):
        raise AssertionError("Disabled or unconfigured DeepSeek must not be called")

    monkeypatch.setattr(web_fetch, "get_effective_search_proxy", lambda: "")
    monkeypatch.setattr(web_fetch, "fetch_with_deepseek", unexpected_fetch)
    monkeypatch.setattr(web_fetch.httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(direct), **kwargs))
    for settings, credential in [
        (SearchRuntimeSettings(True, ("simplexng",)), object()),
        (SearchRuntimeSettings(False, ("deepseek",)), object()),
        (SearchRuntimeSettings(True, ("deepseek",)), None),
    ]:
        monkeypatch.setattr(web_fetch, "runtime_settings", lambda name: settings)
        monkeypatch.setattr(web_fetch, "find_official_deepseek_search_candidate", lambda: credential)
        assert await web_fetch._tool_webfetch({"url": "https://example.com"}, PluginContext()) == "Direct content"
    assert len(requests) == 3


async def test_local_webfetch_bypasses_remote_api_and_proxy(monkeypatch):
    from cyrene.core.plugin import PluginContext
    from cyrene.plugins.builtin.cyrene_content import web_fetch
    from cyrene.plugins.builtin.cyrene_content.search_settings import SearchRuntimeSettings
    import httpx

    monkeypatch.setattr(web_fetch, "runtime_settings", lambda name: SearchRuntimeSettings(True, ("deepseek",)))
    def unexpected():
        raise AssertionError("Local URL must not resolve credentials or proxy")
    monkeypatch.setattr(web_fetch, "find_official_deepseek_search_candidate", unexpected)
    monkeypatch.setattr(web_fetch, "get_effective_search_proxy", unexpected)
    original_client = httpx.AsyncClient
    client_options = []

    def client(**kwargs):
        client_options.append(kwargs)
        return original_client(transport=httpx.MockTransport(lambda req: httpx.Response(200, text="Local content")), **kwargs)

    monkeypatch.setattr(web_fetch.httpx, "AsyncClient", client)
    for url in ("http://127.0.0.1:4242/", "http://localhost/", "http://192.168.1.2/", "http://[::1]/", "http://cyrene.local/"):
        assert await web_fetch._tool_webfetch({"url": url}, PluginContext()) == "Local content"
    assert all(option["proxy"] is None for option in client_options)
