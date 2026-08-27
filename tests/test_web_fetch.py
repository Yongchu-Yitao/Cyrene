from agent.plugin.plugin_impl.cyrene_content.web_fetch import _extract_response_text


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
