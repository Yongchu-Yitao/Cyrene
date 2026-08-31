"""M1 tests for the persistent browser session (src/cyrene/plugins/builtin/cyrene_browser/runtime.py).

Covers the live-view foundation without launching a real browser:
  - navigate() drives the shared page and returns extracted text
  - every action emits a structured metadata-only ``browser_frame`` SSE event
  - click/type refuse to run before navigate (regression: the old
    ``_current_page`` global was never assigned, so these were dead code)
  - navigate falls back to httpx when Playwright is unavailable
"""

import asyncio
import base64
import importlib.util
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cyrene.core.plugin import PluginContext

# Patch missing optional deps before any cyrene import (mirrors test_runtime_fixes).
try:
    _pil_missing = importlib.util.find_spec("PIL") is None
except ValueError:
    _pil_missing = "PIL" not in sys.modules
if _pil_missing:
    sys.modules.setdefault("PIL", MagicMock())
sys.modules.setdefault("pypdf", MagicMock())

_VALID_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class _FakePage:
    """Minimal stand-in for a Playwright Page."""

    def __init__(self, url: str = "https://example.com/") -> None:
        self.url = url

    async def goto(self, url, **_kw):
        self.url = url
        return MagicMock(status=200)

    async def title(self):
        return "Example"

    async def content(self):
        return (
            "<html><head><title>Example</title></head>"
            "<body><h1>Hello</h1><p>World</p>"
            "<a href='/watch/123'>Readable video</a></body></html>"
        )

    async def screenshot(self, **kw):
        path = kw.get("path")
        if path:
            Path(path).write_bytes(_VALID_PNG)
        return _VALID_PNG

    async def wait_for_load_state(self, *_a, **_k):
        return None


def _capture_publish(monkeypatch):
    """Patch debug.publish_event and return the list it appends events to."""
    from cyrene.observability import debug

    captured: list[dict] = []

    async def fake_publish(event):
        captured.append(event)

    monkeypatch.setattr(debug, "publish_event", fake_publish)
    return captured


async def test_session_navigate_returns_text_and_emits_frame(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    captured = _capture_publish(monkeypatch)
    session = browser._BrowserSession()
    session._page = _FakePage()

    async def _noop(**_kw):
        return None

    monkeypatch.setattr(session, "_ensure_started", _noop)

    result = await session.navigate("https://example.com/page")

    assert result["title"] == "Example"
    assert "Hello" in result["text"] and "World" in result["text"]
    assert result["links"] == [
        {"text": "Readable video", "url": "https://example.com/watch/123"}
    ]
    assert session._page.url == "https://example.com/page"

    frames = [e for e in captured if e.get("type") == "browser_frame"]
    assert len(frames) == 1
    assert frames[0]["action"] == "navigate"
    assert "image" not in frames[0]


async def test_session_navigate_returns_immediately_clickable_link_refs(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    session = browser._BrowserSession()
    page = _FakePage()

    async def evaluate(script, args):
        assert "data-cyrene-ref" in script
        assert args == [120, 200]
        return [
            {
                "ref": "e1",
                "text": "Target video",
                "url": "https://example.com/video/1",
            }
        ]

    page.evaluate = evaluate
    session._page = page

    async def _noop(**_kw):
        return None

    monkeypatch.setattr(session, "_ensure_started", _noop)

    result = await session.navigate("https://example.com/search")

    assert result["links"] == [
        {"ref": "e1", "text": "Target video", "url": "https://example.com/video/1"}
    ]


async def test_emit_frame_normalizes_box_and_target(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    captured = _capture_publish(monkeypatch)
    session = browser._BrowserSession()
    session._page = _FakePage("https://site/login")

    await session._emit_frame(
        "click", target="#submit", box={"x": 10, "y": 20, "width": 30, "height": 40}
    )

    ev = captured[-1]
    assert ev["type"] == "browser_frame"
    assert ev["action"] == "click"
    assert ev["target"] == "#submit"
    assert ev["box"] == {"x": 10, "y": 20, "w": 30, "h": 40}
    assert ev["url"] == "https://site/login"


async def test_emit_frame_is_best_effort(monkeypatch):
    """A metadata publish failure must not raise out of _emit_frame."""
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser
    from cyrene.observability import debug

    session = browser._BrowserSession()
    session._page = _FakePage()

    async def broken_publish(_event):
        raise RuntimeError("boom")

    monkeypatch.setattr(debug, "publish_event", broken_publish)
    # Should swallow the error rather than propagate.
    await session._emit_frame("navigate")


async def test_click_requires_navigate_first(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    monkeypatch.setattr(browser, "_PLAYWRIGHT_AVAILABLE", True)
    monkeypatch.setattr(browser, "_session", None)

    result = await browser.click("#x")

    assert result["ok"] is False
    assert "navigate" in result["error"].lower()


async def test_type_requires_navigate_first(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    monkeypatch.setattr(browser, "_PLAYWRIGHT_AVAILABLE", True)
    monkeypatch.setattr(browser, "_session", None)

    result = await browser.type_text("#x", "hello")

    assert result["ok"] is False
    assert "navigate" in result["error"].lower()


async def test_navigate_falls_back_to_httpx_without_playwright(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    monkeypatch.setattr(browser, "_PLAYWRIGHT_AVAILABLE", False)

    called: dict = {}

    async def fake_httpx(url, **_kw):
        called["url"] = url
        return {"url": url, "status": 200, "title": "x", "text": "y", "error": None}

    monkeypatch.setattr(browser, "_httpx_navigate", fake_httpx)

    result = await browser.navigate("https://ex.com")

    assert called["url"] == "https://ex.com"
    assert result["status"] == 200


def test_html_links_resolve_text_and_image_links_and_skip_non_http():
    from cyrene.plugins.builtin.cyrene_browser.runtime import _html_links

    links = _html_links(
        """
        <a href="/video/BV123">Video title</a>
        <a href="/video/BV456"><img alt="Image-only title"></a>
        <a href="javascript:void(0)">Not a URL</a>
        <a href="/video/BV123">Video title</a>
        """,
        "https://www.bilibili.com/search",
    )

    assert links == [
        {"text": "Video title", "url": "https://www.bilibili.com/video/BV123"},
        {"text": "Image-only title", "url": "https://www.bilibili.com/video/BV456"},
    ]


def test_browser_navigate_link_output_is_before_page_text():
    from cyrene.plugins.builtin.cyrene_browser.browser_output import page_link_lines

    lines = page_link_lines(
        {"links": [{"text": "Target result", "url": "https://example.com/video/1"}]},
        PluginContext(data={"language": "en"}),
    )

    assert lines == [
        "Text links on this page:\n- 'Target result' -> https://example.com/video/1"
    ]

    lines_with_ref = page_link_lines(
        {"links": [{"ref": "e7", "text": "Clickable result", "url": "https://example.com/video/7"}]},
        PluginContext(data={"language": "en"}),
    )
    assert lines_with_ref == [
        "Text links on this page:\n- [e7] 'Clickable result' -> https://example.com/video/7"
    ]


async def test_navigate_normalizes_bare_domain_before_validation(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    monkeypatch.setattr(browser, "_PLAYWRIGHT_AVAILABLE", False)

    called: dict = {}

    async def fake_httpx(url, **_kw):
        called["url"] = url
        return {"url": url, "status": 200, "title": "x", "text": "y", "error": None}

    monkeypatch.setattr(browser, "_httpx_navigate", fake_httpx)

    result = await browser.navigate("example.com/page")

    assert called["url"] == "https://example.com/page"
    assert result["url"] == "https://example.com/page"


def test_generic_access_gate_signal_requires_paired_markers_and_allows_one_retry():
    from cyrene.plugins.builtin.cyrene_browser.runtime import _browser_page_signal

    signal = _browser_page_signal(
        "https://example.com/article/abc",
        "Example",
        "当前内容暂时无法浏览 请打开 App 扫码查看",
    )

    assert signal["kind"] == "access_gate"
    assert signal["requires_user_takeover"] is False
    assert signal["retry_allowed"] is True
    assert signal["max_retries"] == 1

    normal = _browser_page_signal("https://example.com/article/abc", "Example", "扫码查看其他内容")
    assert normal["kind"] == "normal"
    assert normal["retry_allowed"] is True


def test_normalize_electron_page_signal_for_python_tools():
    from cyrene.plugins.builtin.cyrene_browser.runtime import _normalize_browser_result

    result = _normalize_browser_result({"pageSignal": {"kind": "access_gate"}})

    assert result["page_signal"] == {"kind": "access_gate"}
    assert "pageSignal" not in result


async def test_browser_click_ref_surfaces_bounded_access_gate_recovery(monkeypatch):
    import importlib

    module = importlib.import_module("cyrene.plugins.builtin.cyrene_browser.browser_click_ref")

    async def fake_click_ref(_ref):
        return {
            "ok": True,
            "url": "https://example.com/article/abc",
            "title": "Example",
            "page_signal": {
                "kind": "access_gate",
                "requires_user_takeover": False,
                "retry_allowed": True,
                "max_retries": 1,
                "cooldown_ms": 10000,
                "message": "页面内容暂不可用。",
            },
            "text": "当前笔记暂时无法浏览",
        }

    monkeypatch.setattr("cyrene.plugins.builtin.cyrene_browser.runtime.click_ref", fake_click_ref)
    result = await module._tool_browser_click_ref(
        {"ref": "e1"}, PluginContext(data={"language": "en"})
    )

    assert "PAGE_SIGNAL: access_gate" in result
    assert "make at most one recovery attempt" in result
    assert "browser_request_takeover" in result


def test_click_debounce_is_short_lived_and_session_scoped():
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    session = browser._BrowserSession()
    assert session._click_debounced() is False
    session._last_agent_click_completed_at = time.monotonic()
    assert session._click_debounced() is True
    session._last_agent_click_completed_at -= 1
    assert session._click_debounced() is False


async def test_screenshot_normalizes_bare_domain(monkeypatch, tmp_path):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    monkeypatch.setattr(browser, "_PLAYWRIGHT_AVAILABLE", True)

    captured: dict = {}

    class _FakeSession:
        async def navigate(self, url):
            captured["url"] = url
            return {"url": url, "status": 200, "title": "x", "text": "", "error": None}

        async def screenshot_path(self, full_page=True):
            path = tmp_path / "fake.png"
            path.write_bytes(_VALID_PNG)
            return str(path)

        async def page(self):
            return _FakePage("https://example.com/")

    async def fake_get_session():
        return _FakeSession()

    monkeypatch.setattr(browser, "get_session", fake_get_session)

    result = await browser.screenshot("example.com")

    assert result["ok"] is True
    assert captured["url"] == "https://example.com"


def test_default_browser_user_agent_is_modern_chrome(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    def fake_cfg(key, default):
        return default

    monkeypatch.setattr(browser, "_cfg", fake_cfg)

    ua = browser._browser_user_agent("147.0.7727.15")

    assert "Chrome/147.0.7727.15" in ua
    assert "HeadlessChrome" not in ua


def test_browser_runtime_error_filters_install_commands(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser
    from cyrene import localization

    monkeypatch.setattr(localization, "app_language", lambda explicit=None: "en")

    message = browser.browser_runtime_unavailable_message(
        "BrowserType.launch: Executable doesn't exist\n"
        "Please run the following command to download browsers:\n"
        "    playwright install chromium\n"
        "pip install playwright"
    )

    assert "Cyrene browser runtime is unavailable" in message
    assert "playwright install" not in message
    assert "pip install" not in message


def test_browser_live_frames_do_not_ride_sse_as_base64():
    root = Path(__file__).resolve().parent.parent
    browser_source = (root / "src" / "cyrene" / "plugins" / "builtin" / "cyrene_browser" / "runtime.py").read_text(encoding="utf-8")
    routes_source = (root / "src" / "cyrene" / "plugins" / "builtin" / "cyrene_browser" / "routes.py").read_text(encoding="utf-8")
    view_source = (root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "shared" / "browser" / "viewport.jsx").read_text(encoding="utf-8")

    emit_frame_body = browser_source.split("async def _emit_frame", 1)[1].split("# -- Screencast", 1)[0]
    assert "page.screenshot" not in emit_frame_body
    assert "base64.b64encode" not in emit_frame_body
    assert '"image"' not in emit_frame_body

    assert "await websocket.send_bytes(data)" in routes_source
    assert '"data:image/jpeg;base64,"' not in view_source
    assert 'ws.binaryType = "arraybuffer"' in view_source


async def test_launch_context_uses_desktop_ua_and_locale(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    captured: dict = {}
    original_cfg = browser._cfg

    def fake_cfg(key, default):
        if key == "CYRENE_BROWSER_LOCALE":
            return "zh-CN"
        if key == "CYRENE_BROWSER_ACCEPT_LANGUAGE":
            return "zh-CN,zh;q=0.9,en;q=0.8"
        return original_cfg(key, default)

    monkeypatch.setattr(browser, "_cfg", fake_cfg)

    class _FakeChromium:
        executable_path = ""

        async def launch_persistent_context(self, profile_dir, **kwargs):
            captured["profile_dir"] = profile_dir
            captured.update(kwargs)
            return MagicMock(pages=[_FakePage()])

    class _FakePlaywright:
        chromium = _FakeChromium()

    session = browser._BrowserSession()
    session._pw = _FakePlaywright()

    async def fake_detect_version(_chromium):
        return "147.0.7727.15"

    monkeypatch.setattr(browser, "_detect_chromium_version", fake_detect_version)

    context = await session._launch_persistent_context(headless=True)

    assert context.pages
    assert captured["headless"] is True
    assert captured["locale"] == "zh-CN"
    assert captured["extra_http_headers"]["Accept-Language"].startswith("zh-CN")
    assert "Chrome/147.0.7727.15" in captured["user_agent"]
    assert "HeadlessChrome" not in captured["user_agent"]


async def test_click_delegates_to_session(monkeypatch):
    """When a page is open, browser.click drives the session and emits a frame."""
    pytest.importorskip("playwright")
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    captured = _capture_publish(monkeypatch)
    monkeypatch.setattr(browser, "_PLAYWRIGHT_AVAILABLE", True)

    session = browser._BrowserSession()
    session._page = _FakePage("https://site/")

    # Stub the heavy Playwright bits: locator + expect.
    fake_locator = MagicMock()

    async def _box():
        return {"x": 1, "y": 2, "width": 3, "height": 4}

    async def _click():
        return None

    fake_locator.bounding_box = _box
    fake_locator.click = _click
    session._page.locator = lambda _sel: fake_locator

    import playwright.async_api as _pw  # noqa: F401  (present in this env)

    async def _expect_visible(*_a, **_k):
        return None

    fake_expect = MagicMock()
    fake_expect.return_value.to_be_visible = _expect_visible
    monkeypatch.setattr("playwright.async_api.expect", fake_expect)

    monkeypatch.setattr(browser, "_session", session)

    result = await browser.click("#go")

    assert result["ok"] is True
    frames = [e for e in captured if e.get("type") == "browser_frame"]
    assert frames and frames[-1]["action"] == "click"
    assert frames[-1]["box"] == {"x": 1, "y": 2, "w": 3, "h": 4}
    assert "image" not in frames[-1]


# --- M2: screencast fan-out -------------------------------------------------


class _FakeCDP:
    def __init__(self) -> None:
        self.sent: list = []
        self.handlers: dict = {}
        self.detached = False

    def on(self, event, handler):
        self.handlers[event] = handler

    async def send(self, method, params=None):
        self.sent.append((method, params))

    async def detach(self):
        self.detached = True


class _FakeContext:
    def __init__(self, cdp):
        self._cdp = cdp

    async def new_cdp_session(self, _page):
        return self._cdp


async def test_screencast_start_stop_bookkeeping(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    session = browser._BrowserSession()
    session._page = _FakePage()
    cdp = _FakeCDP()
    session._context = _FakeContext(cdp)

    async def _noop(**_kw):
        return None

    monkeypatch.setattr(session, "_ensure_started", _noop)

    q1, q2 = asyncio.Queue(), asyncio.Queue()

    await session.start_screencast(q1)
    assert session._screencasting is True
    assert "Page.startScreencast" in [m for m, _ in cdp.sent]
    assert "Page.screencastFrame" in cdp.handlers

    # A second subscriber must not restart the screencast.
    cdp.sent.clear()
    await session.start_screencast(q2)
    assert "Page.startScreencast" not in [m for m, _ in cdp.sent]
    assert session._frame_subs == {q1, q2}

    # Dropping one keeps casting; dropping the last tears it down.
    await session.stop_screencast(q1)
    assert session._screencasting is True
    await session.stop_screencast(q2)
    assert session._screencasting is False
    assert cdp.detached is True
    assert "Page.stopScreencast" in [m for m, _ in cdp.sent]


async def test_screencast_frame_fans_out_and_acks():
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    session = browser._BrowserSession()
    session._page = _FakePage("https://x/")
    cdp = _FakeCDP()
    session._cdp = cdp
    q1, q2 = asyncio.Queue(), asyncio.Queue()
    session._frame_subs = {q1, q2}

    encoded = "anBlZw=="
    session._on_screencast_frame({"data": encoded, "sessionId": "s1"})
    await asyncio.sleep(0)  # let the ack task run

    f1, f2 = q1.get_nowait(), q2.get_nowait()
    assert f1["data"] == b"jpeg" and f1["url"] == "https://x/"
    assert f1["content_type"] == "image/jpeg"
    assert f2["data"] == b"jpeg"
    assert ("Page.screencastFrameAck", {"sessionId": "s1"}) in cdp.sent


async def test_screencast_skips_decode_and_acks_when_queue_full(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    session = browser._BrowserSession()
    session._page = _FakePage()
    cdp = _FakeCDP()
    session._cdp = cdp
    q = asyncio.Queue(maxsize=1)
    q.put_nowait({"data": "old", "url": ""})
    session._frame_subs = {q}
    decode = MagicMock(side_effect=AssertionError("discarded frame was decoded"))
    monkeypatch.setattr(browser.base64, "b64decode", decode)

    session._on_screencast_frame({"data": "new", "sessionId": "s1"})
    await asyncio.sleep(0)

    decode.assert_not_called()
    assert q.qsize() == 1
    assert q.get_nowait()["data"] == "old"
    assert ("Page.screencastFrameAck", {"sessionId": "s1"}) in cdp.sent


# --- M3: native-window login takeover --------------------------------------


async def test_browser_request_takeover_pauses_with_takeover_meta(monkeypatch):
    import json
    from types import SimpleNamespace

    from cyrene.core.plugin import PluginContext
    from cyrene.plugins.builtin.cyrene_browser import browser_request_takeover as _tools
    from cyrene.plugins.builtin.cyrene_browser import runtime as _browser

    events = []
    switched = []

    class _FakeSession:
        async def current_url(self):
            return "https://example.com/login"

        async def switch_to_headed(self, url=""):
            switched.append(url)

    async def fake_get_session():
        return _FakeSession()

    monkeypatch.setattr(_browser, "electron_browser_available", lambda: False)
    monkeypatch.setattr(_browser, "get_session", fake_get_session)
    monkeypatch.setattr(
        _tools,
        "require_plugin_execution",
        lambda: SimpleNamespace(call=SimpleNamespace(id="call_takeover")),
    )
    context = PluginContext(
        data={
            "run_context": {
                "agent_id": "main",
                "round_id": "round_1",
                "session_id": "chat_1",
            }
        },
        services={"runtime_events": events.append},
    )
    result = await _tools._tool_browser_request_takeover(
        {"reason": "Please log in to Gmail"},
        context,
    )

    payload = json.loads(result)
    assert payload["status"] == "awaiting_user"
    assert payload["question_id"] == "question_call_takeover"
    assert switched == ["https://example.com/login"]
    takeover_events = [e for e in events if e.get("type") == "browser_takeover_request"]
    assert takeover_events[-1]["question_id"] == "question_call_takeover"


async def test_browser_request_takeover_rejects_non_main_agent(monkeypatch):
    from cyrene.core.plugin import PluginContext
    from cyrene.plugins.builtin.cyrene_browser import browser_request_takeover as _tools

    result = await _tools._tool_browser_request_takeover(
        {"reason": "x"},
        PluginContext(
            data={
                "language": "en",
                "run_context": {"agent_id": "alice", "round_id": "round_1"},
            }
        ),
    )
    assert "main agent" in result.lower()


# ---------------------------------------------------------------------------
# SSRF protection tests (#86)
# ---------------------------------------------------------------------------


def test_check_url_blocks_non_http_schemes(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser.runtime import _check_url, SSRFBlockedError
    from cyrene import localization

    monkeypatch.setattr(localization, "app_language", lambda explicit=None: "en")

    for bad in ("file:///etc/passwd", "ftp://ftp.example.com/file", "data:text/html,hi"):
        with pytest.raises(SSRFBlockedError, match="scheme"):
            _check_url(bad)


def test_check_url_blocks_loopback():
    from cyrene.plugins.builtin.cyrene_browser.runtime import _check_url, SSRFBlockedError

    with pytest.raises(SSRFBlockedError):
        _check_url("http://127.0.0.1/admin")
    with pytest.raises(SSRFBlockedError):
        _check_url("http://127.1.2.3:8080/")


def test_check_url_blocks_localhost_by_name():
    from cyrene.plugins.builtin.cyrene_browser.runtime import _check_url, SSRFBlockedError

    with pytest.raises(SSRFBlockedError):
        _check_url("http://localhost/secret")


def test_check_url_blocks_private_ranges():
    from cyrene.plugins.builtin.cyrene_browser.runtime import _check_url, SSRFBlockedError

    for url in (
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://172.31.255.255/",
    ):
        with pytest.raises(SSRFBlockedError):
            _check_url(url)


def test_check_url_blocks_cloud_metadata():
    from cyrene.plugins.builtin.cyrene_browser.runtime import _check_url, SSRFBlockedError

    with pytest.raises(SSRFBlockedError):
        _check_url("http://169.254.169.254/latest/meta-data/")


def test_check_url_allows_public_urls():
    from cyrene.plugins.builtin.cyrene_browser.runtime import _check_url

    # Should not raise
    _check_url("https://example.com/page")
    _check_url("http://www.google.com/")
    _check_url("https://api.github.com/repos")


async def test_redirect_hook_allows_relative_location():
    import httpx

    from cyrene.plugins.builtin.cyrene_browser.runtime import _ssrf_redirect_hook

    response = httpx.Response(
        302,
        headers={"location": "/login"},
        request=httpx.Request("GET", "https://example.com/start"),
    )

    await _ssrf_redirect_hook(response)


async def test_redirect_hook_blocks_protocol_relative_private_location():
    import httpx
    import pytest

    from cyrene.plugins.builtin.cyrene_browser.runtime import SSRFBlockedError, _ssrf_redirect_hook

    response = httpx.Response(
        302,
        headers={"location": "//169.254.169.254/latest/meta-data/"},
        request=httpx.Request("GET", "https://example.com/start"),
    )

    with pytest.raises(SSRFBlockedError):
        await _ssrf_redirect_hook(response)


async def test_navigate_returns_error_for_blocked_url(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser
    from cyrene import localization

    monkeypatch.setattr(localization, "app_language", lambda explicit=None: "en")

    # Shouldn't reach Playwright or httpx at all
    called = []
    monkeypatch.setattr(browser, "_httpx_navigate", lambda *a, **kw: called.append(1))

    result = await browser.navigate("http://169.254.169.254/latest/meta-data/")

    assert result["error"] is not None
    assert "blocked" in result["error"].lower()
    assert called == []  # httpx path never invoked


async def test_screenshot_returns_error_for_blocked_url(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser
    from cyrene import localization

    monkeypatch.setattr(localization, "app_language", lambda explicit=None: "en")

    monkeypatch.setattr(browser, "_PLAYWRIGHT_AVAILABLE", True)

    result = await browser.screenshot("http://192.168.0.1/")

    assert result["ok"] is False
    assert "blocked" in result["error"].lower()


async def test_session_navigate_blocks_redirect_to_private_ip(monkeypatch):
    """_BrowserSession.navigate() must reject the final URL if the server redirected
    to a blocked destination (e.g. public URL → 301 → 169.254.169.254)."""
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser
    from cyrene import localization

    monkeypatch.setattr(localization, "app_language", lambda explicit=None: "en")

    _capture_publish(monkeypatch)
    session = browser._BrowserSession()

    class _RedirectedPage(_FakePage):
        """Simulates a page that ended up at a private IP after a redirect."""

        async def goto(self, url, **_kw):
            # Ignore the initial URL — simulate a server-side redirect to internal addr.
            self.url = "http://169.254.169.254/latest/meta-data/"
            return MagicMock(status=200)

    session._page = _RedirectedPage()

    async def _noop(**_kw):
        return None

    monkeypatch.setattr(session, "_ensure_started", _noop)

    result = await session.navigate("https://legit.example.com/")

    assert result["error"] is not None
    assert "blocked" in result["error"].lower()
    assert result["text"] == ""  # no internal content leaked


async def test_httpx_navigate_ssrf_redirect_error_no_exception_log(monkeypatch):
    """SSRFBlockedError from the redirect hook must produce a clean error string,
    not fall through to logger.exception (which would log a noisy traceback)."""
    import logging

    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    logged_exceptions: list = []

    class _CapturingHandler(logging.Handler):
        def emit(self, record):
            if record.exc_info:
                logged_exceptions.append(record)

    handler = _CapturingHandler()
    logger = logging.getLogger("cyrene.plugins.builtin.cyrene_browser.runtime")
    logger.addHandler(handler)
    try:
        # Patch _ssrf_redirect_hook to raise SSRFBlockedError unconditionally,
        # simulating a redirect to a blocked target.
        from cyrene.plugins.builtin.cyrene_browser.runtime import SSRFBlockedError

        async def _always_block(response):
            if 300 <= response.status_code < 400:
                raise SSRFBlockedError("redirect blocked")

        monkeypatch.setattr(browser, "_ssrf_redirect_hook", _always_block)
        monkeypatch.setattr(browser, "_PLAYWRIGHT_AVAILABLE", False)

        # We can't easily trigger a real redirect in a unit test, so verify the
        # error-handling path by directly calling _httpx_navigate with a mock.
        async def fake_get(*_a, **_kw):
            raise SSRFBlockedError("redirect to 10.0.0.1 blocked")

        async def fake_navigate(url, **kw):
            result = {"url": url, "status": 0, "title": "", "text": "", "error": None}
            try:
                await fake_get()
            except SSRFBlockedError as exc:
                result["error"] = str(exc)
            except Exception as exc:
                result["error"] = f"Failed to fetch {url}: {exc}"
                import logging as _log
                _log.getLogger("cyrene.plugins.builtin.cyrene_browser.runtime").exception("browser_navigate failed for %s", url)
            return result

        result = await fake_navigate("https://redirect-target.example.com/")

        assert "blocked" in result["error"]
        assert logged_exceptions == [], "SSRFBlockedError must not produce logger.exception traceback"
    finally:
        logger.removeHandler(handler)


# ---------------------------------------------------------------------------
# #87: temp PNG cleanup
# ---------------------------------------------------------------------------


async def test_screenshot_path_closes_file_handle(monkeypatch):
    """screenshot_path must close the fd immediately after creation (#87)."""
    import os
    import tempfile

    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    captured_file: list = []

    original_ntf = tempfile.NamedTemporaryFile

    def tracking_ntf(*a, **kw):
        f = original_ntf(*a, **kw)
        captured_file.append(f)
        return f

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", tracking_ntf)

    session = browser._BrowserSession()
    session._page = _FakePage()

    async def _noop(**_kw):
        return None

    monkeypatch.setattr(session, "_ensure_started", _noop)

    path = await session.screenshot_path()

    assert captured_file, "NamedTemporaryFile was not called"
    assert path == captured_file[0].name
    assert captured_file[0].closed, "file handle was not closed after screenshot_path()"
    os.unlink(path)


async def test_tool_browser_screenshot_returns_tmp_file(monkeypatch):
    """The tool handler keeps the temp PNG path so agents can inspect current-page screenshots."""
    import os
    import tempfile

    from cyrene.plugins.builtin.cyrene_browser import browser_screenshot as _mod
    from cyrene.platform import attachments as _attachments

    # Create a real temp file to simulate what screenshot() returns.
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.write(_VALID_PNG)
    tmp.close()

    async def fake_screenshot(url, **_kw):
        return {"ok": True, "path": tmp.name, "title": "Test Page"}

    from cyrene.plugins.builtin.cyrene_browser import runtime as _browser
    monkeypatch.setattr(_browser, "screenshot", fake_screenshot)
    monkeypatch.setattr(_attachments, "primary_model_supports_vision", lambda: False)

    result = await _mod._tool_browser_screenshot(
        {"url": "https://example.com"}, PluginContext(data={"language": "en"})
    )

    assert "Screenshot taken" in result
    assert tmp.name in result
    assert "Test Page" in result
    assert os.path.exists(tmp.name)
    os.unlink(tmp.name)


async def test_tool_browser_screenshot_returns_primary_model_visual_observation(monkeypatch):
    """A verified primary model receives the screenshot and reports it to the agent."""
    import os
    import tempfile

    from cyrene.platform import attachments as _attachments
    from cyrene.plugins.builtin.cyrene_browser import runtime as _browser
    from cyrene.plugins.builtin.cyrene_browser import browser_screenshot as _mod

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.write(_VALID_PNG)
    tmp.close()

    async def fake_screenshot(url, **_kw):
        return {"ok": True, "path": tmp.name, "title": "Visual Test Page"}

    seen = {}

    async def fake_analyze(path, prompt):
        seen["path"] = path
        seen["prompt"] = prompt
        return {"vision_model": "gpt-4o", "vision_text": "A green confirmation button is visible."}

    monkeypatch.setattr(_browser, "screenshot", fake_screenshot)
    monkeypatch.setattr(_attachments, "primary_model_supports_vision", lambda: True)
    monkeypatch.setattr(_attachments, "analyze_image_with_primary_model", fake_analyze)

    result = await _mod._tool_browser_screenshot(
        {}, PluginContext(data={"language": "en"})
    )

    assert seen["path"] == tmp.name
    assert "untrusted data" in seen["prompt"]
    assert "Visual observation from the primary model" in result
    assert "green confirmation button" in result
    os.unlink(tmp.name)


async def test_screenshot_path_cleans_up_on_failure(monkeypatch):
    """If page.screenshot() raises, the pre-created temp file must be deleted (#87)."""
    import os

    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    session = browser._BrowserSession()

    class _BrokenPage(_FakePage):
        async def screenshot(self, **_kw):
            raise RuntimeError("disk full")

    session._page = _BrokenPage()

    async def _noop(**_kw):
        return None

    monkeypatch.setattr(session, "_ensure_started", _noop)

    recorded: list[str] = []
    original_ntf = __import__("tempfile").NamedTemporaryFile

    def tracking_ntf(*a, **kw):
        f = original_ntf(*a, **kw)
        recorded.append(f.name)
        return f

    import tempfile
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", tracking_ntf)

    with pytest.raises(RuntimeError, match="disk full"):
        await session.screenshot_path()

    assert recorded, "no temp file was created"
    assert not os.path.exists(recorded[0]), "temp file leaked on failure"


def test_screenshot_validation_requires_png_format_and_decodability(tmp_path, real_pillow_modules):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    valid = tmp_path / "valid.png"
    valid.write_bytes(_VALID_PNG)
    assert browser.validate_screenshot_file(str(valid))["format"] == "PNG"

    empty = tmp_path / "empty.png"
    empty.touch()
    with pytest.raises(ValueError, match="empty"):
        browser.validate_screenshot_file(str(empty))

    jpeg = tmp_path / "wrong.png"
    real_pillow_modules.new("RGB", (2, 2), "red").save(jpeg, format="JPEG")
    with pytest.raises(ValueError, match="expected PNG format, got JPEG"):
        browser.validate_screenshot_file(str(jpeg))

    truncated = tmp_path / "truncated.png"
    truncated.write_bytes(_VALID_PNG[:32])
    with pytest.raises(ValueError, match="cannot be decoded"):
        browser.validate_screenshot_file(str(truncated))


async def test_tool_browser_screenshot_rejects_invalid_artifact(monkeypatch, tmp_path):
    from cyrene.plugins.builtin.cyrene_browser import runtime as _browser
    from cyrene.plugins.builtin.cyrene_browser import browser_screenshot as _mod

    invalid = tmp_path / "invalid.png"
    invalid.touch()

    async def fake_screenshot(_url, **_kw):
        return {"ok": True, "path": str(invalid), "title": "Broken"}

    monkeypatch.setattr(_browser, "screenshot", fake_screenshot)
    result = await _mod._tool_browser_screenshot(
        {}, PluginContext(data={"language": "en"})
    )

    assert "Screenshot failed: the screenshot file is invalid." in result
    assert "Screenshot taken" not in result


# --- S3: user live-control (CDP input) + native-window escape hatch ---------


async def test_user_input_injection_gated_by_control():
    """Mouse/key events are dropped unless the user has taken control, then they
    reach CDP Input.* with the expected params."""
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    session = browser._BrowserSession()
    session._page = _FakePage()
    cdp = _FakeCDP()
    session._cdp = cdp  # reused by _ensure_input_cdp

    # Control OFF → no-op.
    await session.dispatch_mouse(type="mousePressed", x=10, y=20, button="left")
    await session.dispatch_key(type="keyDown", key="a", text="a")
    assert cdp.sent == []

    # Control ON → forwarded.
    session.set_user_control(True)
    await session.dispatch_mouse(type="mousePressed", x=11, y=22, button="left", click_count=2)
    await session.dispatch_mouse(type="mouseWheel", x=11, y=22, delta_x=0, delta_y=-120)
    await session.dispatch_key(type="keyDown", key="a", code="KeyA", key_code=65, text="a")
    await session.dispatch_key(type="keyDown", key="Enter", code="Enter", key_code=13)

    methods = [m for m, _ in cdp.sent]
    assert methods == [
        "Input.dispatchMouseEvent",
        "Input.dispatchMouseEvent",
        "Input.dispatchKeyEvent",
        "Input.dispatchKeyEvent",
    ]
    press = cdp.sent[0][1]
    assert (press["x"], press["y"], press["button"], press["clickCount"]) == (11.0, 22.0, "left", 2)
    wheel = cdp.sent[1][1]
    assert wheel["deltaY"] == -120
    char_key = cdp.sent[2][1]
    assert char_key["text"] == "a" and char_key["windowsVirtualKeyCode"] == 65
    enter_key = cdp.sent[3][1]
    assert "text" not in enter_key and enter_key["key"] == "Enter"


async def test_open_close_user_window_toggles_flag(monkeypatch):
    """The user-initiated native window sets/clears _user_window_open and delegates
    to the headless<->headed restart helpers (no pending question)."""
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    session = browser._BrowserSession()
    calls: list = []

    async def fake_switch(url=""):
        calls.append(("switch", url))

    async def fake_end(url=""):
        calls.append(("end", url))

    monkeypatch.setattr(session, "switch_to_headed", fake_switch)
    monkeypatch.setattr(session, "end_takeover", fake_end)

    await session.open_user_window("https://x/")
    assert session._user_window_open is True
    assert ("switch", "https://x/") in calls

    await session.close_user_window("https://x/")
    assert session._user_window_open is False
    assert ("end", "https://x/") in calls


async def test_headed_close_routes_user_window_vs_agent_takeover(monkeypatch):
    """Closing the window auto-returns to headless for a user-opened window, but
    cancels the pending question for an agent-initiated takeover."""
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    session = browser._BrowserSession()
    hit: list = []

    async def fake_auto():
        hit.append("auto")

    async def fake_cancel():
        hit.append("cancel")

    monkeypatch.setattr(session, "_auto_return_headless", fake_auto)
    monkeypatch.setattr(session, "_publish_takeover_cancelled", fake_cancel)

    # User-opened escape-hatch window → silent return to headless.
    session._user_window_open = True
    session._takeover_active = True
    session._on_headed_close()
    await asyncio.sleep(0)
    assert hit == ["auto"]
    assert session._user_window_open is False

    # Agent-initiated takeover window → cancel path.
    hit.clear()
    session._user_window_open = False
    session._takeover_active = True
    session._on_headed_close()
    await asyncio.sleep(0)
    assert hit == ["cancel"]

    # Our own deliberate relaunch must not trigger either path.
    hit.clear()
    session._closing_deliberately = True
    session._user_window_open = True
    session._on_headed_close()
    await asyncio.sleep(0)
    assert hit == []


async def test_agent_actions_yield_while_user_controls(monkeypatch):
    """While the user holds live control, agent navigate/click/type skip the page
    and report the paused message instead of fighting for it."""
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    session = browser._BrowserSession()
    session.set_user_control(True)

    async def fake_wait(timeout=600.0):  # don't actually block the test
        return False

    monkeypatch.setattr(session, "_wait_for_control", fake_wait)

    nav = await session.navigate("https://example.com/")
    assert nav["error"] == browser._user_control_message() and nav["status"] == 0

    clicked = await session.click("#go")
    assert clicked["ok"] is False and clicked["error"] == browser._user_control_message()

    typed = await session.type_text("#q", "hi")
    assert typed["ok"] is False and typed["error"] == browser._user_control_message()


async def test_control_gate_blocks_then_releases():
    """_wait_for_control blocks while controlled and returns True once released."""
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    session = browser._BrowserSession()
    assert await session._wait_for_control(timeout=0.01) is True  # not controlling

    session.set_user_control(True)
    assert await session._wait_for_control(timeout=0.02) is False  # still controlled

    async def release_soon():
        await asyncio.sleep(0.02)
        session.set_user_control(False)

    asyncio.ensure_future(release_soon())
    assert await session._wait_for_control(timeout=1.0) is True  # released → resume


async def test_insert_text_gated_and_inserts_via_cdp():
    """IME/committed text reaches CDP Input.insertText only while user controls."""
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    session = browser._BrowserSession()
    session._page = _FakePage()
    cdp = _FakeCDP()
    session._cdp = cdp

    await session.insert_text("你好")          # control OFF → dropped
    assert cdp.sent == []

    session.set_user_control(True)
    await session.insert_text("你好")          # control ON → inserted
    assert ("Input.insertText", {"text": "你好"}) in cdp.sent

    cdp.sent.clear()
    await session.insert_text("")              # empty → no-op
    assert cdp.sent == []


async def test_navigate_uses_electron_rpc_when_available(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    monkeypatch.setenv("CYRENE_ELECTRON_RPC_PORT", "12345")
    monkeypatch.setenv("CYRENE_ELECTRON_RPC_TOKEN", "token")
    monkeypatch.setattr(browser, "_PLAYWRIGHT_AVAILABLE", False)

    calls = []
    captured = _capture_publish(monkeypatch)

    async def fake_rpc(method, args=None, **_kw):
        calls.append((method, args or {}))
        return {
            "ok": True,
            "url": args["url"],
            "status": 0,
            "title": "Electron Page",
            "text": "Readable body",
            "links": [{"text": "Result", "url": "https://example.com/result"}],
        }

    monkeypatch.setattr(browser, "_electron_browser_rpc", fake_rpc)

    result = await browser.navigate("example.com/electron")

    assert calls[0][0] == "navigate"
    assert calls[0][1]["url"] == "https://example.com/electron"
    assert result["title"] == "Electron Page"
    assert result["text"] == "Readable body"
    assert result["links"] == [{"text": "Result", "url": "https://example.com/result"}]
    assert [e for e in captured if e.get("type") == "browser_frame"]


async def test_electron_rpc_carries_originating_session_context(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    monkeypatch.setenv("CYRENE_ELECTRON_RPC_PORT", "12345")
    monkeypatch.setenv("CYRENE_ELECTRON_RPC_TOKEN", "token")
    captured: dict = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, **kwargs):
            captured.update(json.loads(kwargs["content"]))
            return _Response()

    monkeypatch.setattr(browser.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(
        browser,
        "_active_plugin_run_identity",
        lambda: ("chat-isolated-a", "round-a"),
    )
    result = await browser._electron_browser_rpc("navigate", {"url": "https://example.com"})

    assert result == {"ok": True}
    assert captured == {
        "method": "navigate",
        "sessionId": "chat-isolated-a",
        "roundId": "round-a",
        "args": {"url": "https://example.com"},
    }


async def test_close_electron_browser_session_targets_only_requested_chat(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    calls = []

    async def fake_rpc(method, args=None, **kwargs):
        calls.append((method, args or {}, kwargs))
        return {"ok": True, "sessionId": kwargs.get("session_id"), "closed": True}

    monkeypatch.setattr(browser, "electron_browser_available", lambda: True)
    monkeypatch.setattr(browser, "_electron_browser_rpc", fake_rpc)

    result = await browser.close_electron_browser_session("chat-to-delete")

    assert result["closed"] is True
    assert calls == [
        (
            "closeSession",
            {},
            {"timeout": 10.0, "session_id": "chat-to-delete", "round_id": ""},
        )
    ]


async def test_finish_electron_browser_round_targets_requested_run(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    calls = []

    async def fake_rpc(method, args=None, **kwargs):
        calls.append((method, args or {}, kwargs))
        return {"ok": True, "closedTabIds": ["tab_1"], "keptTabId": "tab_2"}

    monkeypatch.setattr(browser, "electron_browser_available", lambda: True)
    monkeypatch.setattr(browser, "_electron_browser_rpc", fake_rpc)

    result = await browser.finish_electron_browser_round("chat-a", "round-a")

    assert result["keptTabId"] == "tab_2"
    assert calls == [
        (
            "finishRound",
            {},
            {"timeout": 10.0, "session_id": "chat-a", "round_id": "round-a"},
        )
    ]


async def test_click_and_type_use_electron_rpc_when_available(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    monkeypatch.setenv("CYRENE_ELECTRON_RPC_PORT", "12345")
    monkeypatch.setenv("CYRENE_ELECTRON_RPC_TOKEN", "token")
    monkeypatch.setattr(browser, "_PLAYWRIGHT_AVAILABLE", False)

    calls = []

    async def fake_rpc(method, args=None, **_kw):
        calls.append((method, args or {}))
        return {"ok": True, "url": "https://example.com", "title": "Page"}

    monkeypatch.setattr(browser, "_electron_browser_rpc", fake_rpc)

    r1 = await browser.click("button.go")
    assert r1["ok"] is True
    assert any(c[0] == "click" for c in calls)

    r2 = await browser.type_text("input.q", "hello")
    assert r2["ok"] is True
    assert any(c[0] == "type" for c in calls)


async def test_new_browser_actions_use_electron_rpc(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    monkeypatch.setenv("CYRENE_ELECTRON_RPC_PORT", "12345")
    monkeypatch.setenv("CYRENE_ELECTRON_RPC_TOKEN", "token")
    monkeypatch.setattr(browser, "_PLAYWRIGHT_AVAILABLE", False)

    calls = []

    async def fake_rpc(method, args=None, **_kw):
        calls.append((method, args or {}))
        if method == "inspect":
            return {"ok": True, "url": "https://example.com", "title": "Page", "elements": [{"ref": "e1", "text": "Go"}]}
        if method == "networkLog":
            return {"ok": True, "url": "https://example.com", "title": "Page", "entries": [{"name": "https://api.example.test/data", "type": "fetch"}]}
        return {"ok": True, "url": "https://example.com", "title": "Page", "box": {"x": 1, "y": 2, "w": 3, "h": 4}}

    monkeypatch.setattr(browser, "_electron_browser_rpc", fake_rpc)

    assert (await browser.inspect_page())["ok"] is True
    assert (await browser.click_ref("e1"))["ok"] is True
    assert (await browser.click_text("Go", exact=True))["ok"] is True
    assert (await browser.click_at(10, 20))["ok"] is True
    assert (await browser.type_ref("e2", "hello", submit=True))["ok"] is True
    assert (await browser.wait_for_page(text="Ready"))["ok"] is True
    assert (await browser.network_log())["ok"] is True

    assert ("inspect", {"maxElements": 80, "textLimit": 160}) in calls
    assert ("clickRef", {"ref": "e1"}) in calls
    assert ("clickText", {"text": "Go", "exact": True}) in calls
    assert ("clickAt", {"x": 10, "y": 20}) in calls
    assert ("typeRef", {"ref": "e2", "text": "hello", "submit": True}) in calls
    assert any(method == "waitFor" and args["text"] == "Ready" for method, args in calls)
    assert ("networkLog", {"maxEntries": 40}) in calls


async def test_current_page_screenshot_uses_electron_without_navigation(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    monkeypatch.setenv("CYRENE_ELECTRON_RPC_PORT", "12345")
    monkeypatch.setenv("CYRENE_ELECTRON_RPC_TOKEN", "token")

    calls = []

    async def fake_rpc(method, args=None, **_kw):
        calls.append((method, args or {}))
        if method == "screenshot":
            return {
                "ok": True,
                "pngBase64": base64.b64encode(_VALID_PNG).decode(),
                "title": "Current Page",
                "url": "https://example.com/current",
            }
        raise AssertionError(f"unexpected RPC method: {method}")

    monkeypatch.setattr(browser, "_electron_browser_rpc", fake_rpc)

    result = await browser.screenshot("")

    assert result["ok"] is True
    assert result["title"] == "Current Page"
    assert calls == [("screenshot", {})]


async def test_electron_screenshot_rejects_empty_data_and_removes_artifact(monkeypatch, tmp_path):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser
    from cyrene import localization

    monkeypatch.setenv("CYRENE_ELECTRON_RPC_PORT", "12345")
    monkeypatch.setenv("CYRENE_ELECTRON_RPC_TOKEN", "token")
    monkeypatch.setattr(browser, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(
        localization,
        "app_language",
        lambda explicit=None: localization.normalize_language(explicit) or "en",
    )

    async def fake_rpc(method, args=None, **_kw):
        assert method == "screenshot"
        return {"ok": True, "pngBase64": "", "title": "Broken"}

    monkeypatch.setattr(browser, "_electron_browser_rpc", fake_rpc)

    result = await browser.screenshot("")

    assert result["ok"] is False
    assert result["error"] == "The browser screenshot failed."
    assert "empty" not in result["error"]
    assert list(tmp_path.iterdir()) == []


async def test_electron_ok_false_does_not_fall_back_to_playwright(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    monkeypatch.setenv("CYRENE_ELECTRON_RPC_PORT", "12345")
    monkeypatch.setenv("CYRENE_ELECTRON_RPC_TOKEN", "token")

    calls = []

    async def fake_rpc(method, args=None, **_kw):
        calls.append((method, args or {}))
        return {"ok": False, "error": "element not found"}

    monkeypatch.setattr(browser, "_electron_browser_rpc", fake_rpc)
    monkeypatch.setattr(
        browser,
        "_ensure_playwright",
        lambda: (_ for _ in ()).throw(AssertionError("Electron must not use Playwright")),
    )

    r1 = await browser.click("#btn")
    r2 = await browser.type_text("#input", "hello")
    r3 = await browser.navigate("https://example.com")

    assert r1["ok"] is False
    assert r2["ok"] is False
    assert r3["status"] == 0
    assert r3["error"]
    assert all("element not found" not in item["error"] for item in (r1, r2, r3))
    assert [method for method, _args in calls] == ["click", "type", "navigate"]


async def test_electron_rpc_errors_never_start_playwright(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    monkeypatch.setenv("CYRENE_ELECTRON_RPC_PORT", "12345")
    monkeypatch.setenv("CYRENE_ELECTRON_RPC_TOKEN", "token")

    async def failed_rpc(method, args=None, **_kw):
        raise ConnectionError(f"{method} RPC stopped")

    monkeypatch.setattr(browser, "_electron_browser_rpc", failed_rpc)
    monkeypatch.setattr(
        browser,
        "_ensure_playwright",
        lambda: (_ for _ in ()).throw(AssertionError("Electron must not use Playwright")),
    )

    results = [
        await browser.navigate("https://example.com"),
        await browser.screenshot(),
        await browser.inspect_page(),
        await browser.click("#button"),
        await browser.click_ref("e1"),
        await browser.click_text("Continue"),
        await browser.click_at(10, 20),
        await browser.type_text("#input", "hello"),
        await browser.type_ref("e2", "world"),
        await browser.wait_for_page(text="Ready"),
        await browser.network_log(),
        await browser.scroll_page(delta_y=100),
    ]

    assert all(result.get("error") for result in results)
    assert results[2]["elements"] == []
    assert results[10]["entries"] == []


async def test_electron_tab_management_apis(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    monkeypatch.setenv("CYRENE_ELECTRON_RPC_PORT", "12345")
    monkeypatch.setenv("CYRENE_ELECTRON_RPC_TOKEN", "token")

    calls = []

    async def fake_rpc(method, args=None, **_kw):
        calls.append((method, args or {}))
        return {"ok": True, "tabs": [], "activeTab": None, "activeTabId": ""}

    monkeypatch.setattr(browser, "_electron_browser_rpc", fake_rpc)

    await browser.list_tabs()
    assert any(c[0] == "state" for c in calls)

    calls.clear()
    await browser.new_tab("https://example.com")
    assert any(c[0] == "createTab" for c in calls)

    calls.clear()
    await browser.select_tab("tab_1")
    assert any(c[0] == "activateTab" for c in calls)


async def test_electron_scroll_forwards_nested_target_options(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    monkeypatch.setenv("CYRENE_ELECTRON_RPC_PORT", "12345")
    monkeypatch.setenv("CYRENE_ELECTRON_RPC_TOKEN", "token")
    calls = []

    async def fake_rpc(method, args=None, **_kw):
        calls.append((method, args or {}))
        return {"ok": True, "moved": True, "actualDeltaY": 240}

    monkeypatch.setattr(browser, "_electron_browser_rpc", fake_rpc)

    result = await browser.scroll_page(delta_x=4, delta_y=240, x=120, y=300, ref="e77")

    assert result["moved"] is True
    assert calls == [
        ("scroll", {"deltaX": 4, "deltaY": 240, "x": 120, "y": 300, "ref": "e77"})
    ]


async def test_browser_scroll_tool_reports_actual_nested_scroll(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import browser_scroll as tool

    captured = {}

    async def fake_scroll_page(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "moved": True,
            "actualDeltaY": 318,
            "target": {"tag": "div", "id": "noteContainer", "ref": "77"},
            "x": 174,
            "y": 302,
        }

    monkeypatch.setattr("cyrene.plugins.builtin.cyrene_browser.runtime.scroll_page", fake_scroll_page)

    result = await tool._tool_browser_scroll(
        {"delta_y": 500, "ref": "e77", "x": 174, "y": 302},
        PluginContext(data={"language": "en"}),
    )

    assert captured == {"delta_y": 500, "x": 174, "y": 302, "ref": "e77"}
    assert result == "Scrolled noteContainer by 318px."


async def test_browser_scroll_tool_does_not_claim_success_without_movement(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import browser_scroll as tool

    async def fake_scroll_page(**_kwargs):
        return {"ok": True, "moved": False, "actualDeltaY": 0, "x": 10, "y": 20}

    monkeypatch.setattr("cyrene.plugins.builtin.cyrene_browser.runtime.scroll_page", fake_scroll_page)

    result = await tool._tool_browser_scroll(
        {"delta_y": 500}, PluginContext(data={"language": "en"})
    )

    assert result.startswith("Scroll had no effect.")


async def test_browser_tab_tools_are_registered(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import plugin_pack

    names = {plugin.name for plugin in plugin_pack.plugins}
    assert {
        "browser_tab_list",
        "browser_tab_new",
        "browser_tab_select",
        "browser_tab_close",
        "browser_scroll",
    } <= names


async def test_browser_navigate_rejects_visible_target_link(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import browser_navigate as tool

    navigated = False

    async def fake_navigation_guard(url, reason, snapshot_token):
        assert (url, reason, snapshot_token) == (
            "https://example.com/detail",
            "ui_unreachable",
            "snapshot-secret",
        )
        return {
            "ok": False,
            "allowed": False,
            "code": "VISIBLE_LINK_AVAILABLE",
            "targetUrl": url,
            "matches": [{"ref": "e12", "text": "Details", "url": url}],
        }

    async def fake_navigate(*_args, **_kwargs):
        nonlocal navigated
        navigated = True
        return {}

    monkeypatch.setattr("cyrene.plugins.builtin.cyrene_browser.runtime.navigation_guard", fake_navigation_guard)
    monkeypatch.setattr("cyrene.plugins.builtin.cyrene_browser.runtime.navigate", fake_navigate)

    raw = await tool._tool_browser_navigate(
        {
            "url": "https://example.com/detail",
            "reason": "ui_unreachable",
            "snapshot_token": "snapshot-secret",
        },
        PluginContext(data={"language": "en"}),
    )
    result = json.loads(raw)

    assert result["code"] == "VISIBLE_LINK_AVAILABLE"
    assert result["matches"] == [
        {"ref": "e12", "text": "Details", "url": "https://example.com/detail"}
    ]
    assert navigated is False


async def test_browser_navigate_allows_user_requested_exact_url(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import browser_navigate as tool

    guard_args = None

    async def fake_navigation_guard(url, reason, snapshot_token):
        nonlocal guard_args
        guard_args = (url, reason, snapshot_token)
        return {"ok": True, "allowed": True, "targetUrl": url}

    async def fake_navigate(url, **_kwargs):
        return {"url": url, "title": "Exact", "text": "", "links": [], "error": None}

    monkeypatch.setattr("cyrene.plugins.builtin.cyrene_browser.runtime.navigation_guard", fake_navigation_guard)
    monkeypatch.setattr("cyrene.plugins.builtin.cyrene_browser.runtime.navigate", fake_navigate)

    raw = await tool._tool_browser_navigate(
        {"url": "https://example.com/exact", "reason": "user_exact_url"},
        PluginContext(data={"language": "en"}),
    )

    assert "Title: Exact" in raw
    assert guard_args == ("https://example.com/exact", "user_exact_url", "")


async def test_browser_navigate_returns_current_url_guard_error(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import browser_navigate as tool

    async def fake_navigation_guard(url, reason, snapshot_token):
        return {
            "ok": False,
            "allowed": False,
            "code": "ALREADY_AT_TARGET",
            "url": url,
            "error": "already there",
        }

    async def unexpected_navigate(*_args, **_kwargs):
        raise AssertionError("navigate must not run")

    monkeypatch.setattr("cyrene.plugins.builtin.cyrene_browser.runtime.navigation_guard", fake_navigation_guard)
    monkeypatch.setattr("cyrene.plugins.builtin.cyrene_browser.runtime.navigate", unexpected_navigate)

    result = json.loads(await tool._tool_browser_navigate(
        {"url": "https://example.com/current", "reason": "starting_page"},
        PluginContext(data={"language": "en"}),
    ))

    assert result["code"] == "ALREADY_AT_TARGET"


def test_browser_snapshot_prioritizes_interactive_elements_and_exposes_credential():
    import inspect

    from cyrene.plugins.builtin.cyrene_browser import runtime as browser
    from cyrene.plugins.builtin.cyrene_browser import browser_snapshot

    interactive = "input,textarea,select,button,a[href]"
    assert interactive in browser._BROWSER_INSPECT_JS
    assert browser._BROWSER_INSPECT_JS.index(interactive) < browser._BROWSER_INSPECT_JS.index("summary,label")
    assert "Snapshot credential:" in inspect.getsource(browser_snapshot._tool_browser_snapshot)


async def test_nested_ref_lookup_uses_recorded_frame_without_changing_legacy_lookup(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    class Frame:
        def __init__(self, url):
            self.url = url

        async def evaluate(self, script, args=None):
            assert browser._BROWSER_NESTED_FIND_JS in script
            assert args == ["ref", "e9", False, True]
            return {
                "ok": True,
                "x": 20,
                "y": 30,
                "box": {"x": 10, "y": 15, "w": 40, "h": 30},
            }

    main_frame = Frame("https://example.com/")
    child_frame = Frame("https://widget.example/")

    page = MagicMock()
    page.url = "https://example.com/"
    page.main_frame = main_frame
    page.frames = [main_frame, child_frame]

    session = browser._BrowserSession()
    session._page = page

    async def fake_offset(frame, page):
        assert frame is child_frame
        return (100.0, 200.0, 1.0, 1.0)

    monkeypatch.setattr(session, "_frame_offset", fake_offset)
    result = await session._find_target(
        "ref",
        "e9",
        frame_hint={"frame": child_frame, "nested": True},
    )

    assert result["ok"] is True
    assert (result["x"], result["y"]) == (120, 230)
    assert result["box"] == {"x": 110, "y": 215, "w": 40, "h": 30}
    assert result["_frame"] is child_frame


async def test_browser_click_ref_reports_popup_as_new_active_tab(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import browser_click_ref as tool

    async def fake_click_ref(ref):
        assert ref == "e9"
        return {
            "ok": True,
            "url": "https://www.bilibili.com/video/BV1test/",
            "title": "Video",
            "tabId": "tab_2",
            "active_tab_id": "tab_2",
            "opened_new_tab": True,
            "source_tab_id": "tab_1",
            "source_url": "https://search.bilibili.com/all?keyword=test",
        }

    monkeypatch.setattr("cyrene.plugins.builtin.cyrene_browser.runtime.click_ref", fake_click_ref)

    result = await tool._tool_browser_click_ref(
        {"ref": "e9"}, PluginContext(data={"language": "en"})
    )

    assert "URL: https://www.bilibili.com/video/BV1test/" in result
    assert "Opened new active tab: tab_2" in result
    assert "source tab: tab_1" in result


def test_electron_click_finish_uses_active_popup_tab():
    main = (Path(__file__).resolve().parent.parent / "electron" / "main.js").read_text(encoding="utf-8")
    finish = main.split("async _finishClick(tab, info)", 1)[1].split("async click({ selector", 1)[0]

    assert "this.tabs.get(this.activeTabId) || tab" in finish
    assert "this.pageSnapshot(activeTab.id" in finish
    assert "openedNewTab" in finish
    assert "sourceTabId" in finish


async def test_screenshot_uses_electron_rpc_and_writes_png(monkeypatch, tmp_path):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    monkeypatch.setenv("CYRENE_ELECTRON_RPC_PORT", "12345")
    monkeypatch.setenv("CYRENE_ELECTRON_RPC_TOKEN", "token")
    monkeypatch.setattr(browser, "_PLAYWRIGHT_AVAILABLE", False)

    calls = []
    async def fake_rpc(method, args=None, **_kw):
        calls.append((method, args or {}))
        if method == "navigate":
            return {"ok": True, "url": args["url"], "title": "Page"}
        return {"ok": True, "pngBase64": base64.b64encode(_VALID_PNG).decode(), "title": "Page"}

    monkeypatch.setattr(browser, "_electron_browser_rpc", fake_rpc)

    result = await browser.screenshot("example.com/screen")
    assert result.get("ok") is True
    assert any(c[0] == "screenshot" for c in calls)


async def test_browser_plugin_lifecycle_finishes_agent_owned_tabs(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from cyrene.core.hook import HookEvent, SESSION_END, STOP
    from cyrene.core.plugin import PluginSetupContext
    from cyrene.plugins.builtin.cyrene_browser.lifecycle import (
        setup_browser_lifecycle,
    )

    class Hooks:
        def __init__(self):
            self.hooks = []
            self.handlers = {}

        def list(self):
            return list(self.hooks)

        def register(self, event, handler, *, plugin_id, hook_id, **kwargs):
            self.hooks.append(SimpleNamespace(id=hook_id, event=event, **kwargs))
            self.handlers[hook_id] = handler

        def bind_plugin(self, plugin_id, handler, *, replace=False):
            self.handlers[plugin_id] = handler

    hooks = Hooks()
    finish = AsyncMock()
    browser_service = SimpleNamespace(finish_round=finish)
    setup_browser_lifecycle(PluginSetupContext(
        data_directory=tmp_path,
        plugin_directory=tmp_path,
        workspace=tmp_path,
        tree=None,
        tree_id="tree-browser",
        root_id="root-browser",
        hooks=hooks,
        data={"run_context": {"session_id": "chat-browser"}},
        services={"browser": browser_service},
    ))

    assert {hook.event for hook in hooks.hooks} == {SESSION_END, STOP}
    for hook in hooks.hooks:
        await hooks.handlers[hook.id](HookEvent(
            name=hook.event,
            tree_id="tree-browser",
            time=datetime.now(timezone.utc),
            payload={"run_id": "run-browser"},
            is_root=True,
        ))

    assert finish.await_count == 2
    finish.assert_awaited_with("chat-browser", "run-browser")
