from __future__ import annotations

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from agent.plugin import PluginContext


def _force_language(monkeypatch: pytest.MonkeyPatch, language: str) -> None:
    from cyrene import localization

    monkeypatch.setattr(
        localization,
        "app_language",
        lambda explicit=None: localization.normalize_language(explicit) or language,
    )


def test_browser_runtime_errors_are_localized_and_redacted(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_browser import runtime as browser

    _force_language(monkeypatch, "zh")

    runtime_message = browser.browser_runtime_unavailable_message(
        RuntimeError("secret runtime diagnostic")
    )
    electron_failure = browser._electron_browser_failure(
        RuntimeError("secret electron diagnostic")
    )
    native_failure = browser._sanitize_browser_result(
        {
            "ok": False,
            "code": "TARGET_CHECK_FAILED",
            "error": "secret native diagnostic",
        },
        "Browser operation failed.",
        "浏览器操作失败。",
    )

    assert runtime_message == "Cyrene 浏览器运行时不可用。"
    assert electron_failure["error"] == "Electron 桌面浏览器不可用。"
    assert native_failure["error"] == "无法验证浏览器目标。"
    assert "secret" not in " ".join(
        [runtime_message, electron_failure["error"], native_failure["error"]]
    )
    assert native_failure["code"] == "TARGET_CHECK_FAILED"


@pytest.mark.asyncio
async def test_browser_live_service_localizes_and_redacts_dispatch_errors(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_browser.live_service import (
        BrowserLiveController,
        BrowserServiceError,
    )

    _force_language(monkeypatch, "zh")

    class BrokenSession:
        async def dispatch_mouse(self, **_kwargs):
            raise RuntimeError("secret CDP failure")

    controller = BrowserLiveController(BrokenSession())

    with pytest.raises(BrowserServiceError) as caught:
        await controller.handle({"type": "mouse", "x": 1, "y": 2})

    assert caught.value.code == "browser_input_dispatch_failed"
    assert str(caught.value) == "无法处理浏览器输入。"
    assert "secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_browser_plugin_result_uses_invocation_language():
    from agent.plugin.plugin_impl.cyrene_browser import browser_scroll

    result = await browser_scroll._tool_browser_scroll(
        {"delta_y": "not-an-integer"},
        PluginContext(data={"language": "zh"}),
    )

    assert result == "滚动失败：delta_y 必须是整数。"


@pytest.mark.asyncio
async def test_browser_plugin_relocalizes_and_redacts_backend_error(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_browser import browser_scroll, runtime

    _force_language(monkeypatch, "en")

    async def failed_scroll(**_kwargs):
        return {
            "ok": False,
            "code": "TARGET_NOT_FOUND",
            "error": "secret native diagnostic",
        }

    monkeypatch.setattr(runtime, "scroll_page", failed_scroll)

    result = await browser_scroll._tool_browser_scroll(
        {"delta_y": 500},
        PluginContext(data={"language": "zh"}),
    )

    assert result == "滚动失败：未找到浏览器元素。"
    assert "secret" not in result


def test_file_chooser_instruction_uses_invocation_language():
    from agent.plugin.plugin_impl.cyrene_browser.browser_output import (
        file_chooser_instruction,
    )

    result = file_chooser_instruction(
        {
            "code": "FILE_CHOOSER_INTERCEPTED",
            "chooserId": "chooser_zh",
            "uploadTarget": {
                "origin": "https://upload.example",
                "multiple": False,
            },
        },
        PluginContext(data={"language": "zh"}),
    )

    assert "已阻止原生系统文件选择器" in result
    assert "下一步" in result
    assert "chooser_zh" in result


def test_browser_route_rejects_invalid_json_without_leaking_parser_details(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_browser.routes import register_browser_routes

    _force_language(monkeypatch, "zh")
    app = FastAPI()
    router = APIRouter()
    register_browser_routes(router, None, "")
    app.include_router(router)

    response = TestClient(app).post(
        "/api/browser/navigate",
        content=b"{not-json",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json() == {
        "ok": False,
        "error": "请求正文必须是有效的 JSON。",
        "code": "invalid_json",
    }
