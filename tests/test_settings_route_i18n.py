"""Focused i18n and error-sanitization contracts for settings HTTP adapters."""

from __future__ import annotations

import json

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _language(monkeypatch, value: str) -> None:
    from cyrene.runtime import settings_store

    monkeypatch.setattr(
        settings_store,
        "get",
        lambda key, default=None: value if key == "app_language" else default,
    )


def _payload(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


def test_media_settings_validation_is_localized_without_exception_text(monkeypatch):
    from cyrene.plugins.builtin.cyrene_media import settings_routes as media

    _language(monkeypatch, "zh")
    secret = "private-media-validation-detail"

    def fail_update(*_args, **_kwargs):
        raise ValueError(secret)

    monkeypatch.setattr(media, "merge_media_settings_update", fail_update)
    app = FastAPI()
    media.register_media_settings_routes(app)

    response = TestClient(app).put("/api/settings/media", json={})

    assert response.status_code == 400
    assert response.json() == {
        "error": "媒体设置无效。",
        "code": "invalid_media_settings",
    }
    assert secret not in response.text


def test_onboarding_upstream_failure_is_localized_without_detail(monkeypatch):
    from cyrene.workbench.http.settings import onboarding_context

    _language(monkeypatch, "zh")
    secret = "private-upstream-response"

    async def fail_setup(*_args, **_kwargs):
        raise httpx.ConnectError(secret)

    monkeypatch.setattr(
        onboarding_context,
        "save_and_test_llm_setup",
        fail_setup,
    )
    app = FastAPI()
    onboarding_context.register_onboarding_routes(app)

    response = TestClient(app).post(
        "/api/onboarding/llm",
        json={"base_url": "https://example.test", "model": "demo"},
    )

    assert response.status_code == 502
    assert response.json() == {
        "error": "上游模型请求失败。",
        "code": "model_request_failed",
    }
    assert secret not in response.text


def test_oauth_snapshot_failure_is_localized_without_exception_text(monkeypatch):
    from cyrene.model_runtime import codex_provider
    from cyrene.plugins.builtin.cyrene_model.oauth import register_oauth_routes

    _language(monkeypatch, "zh")
    secret = "private-oauth-process-detail"

    class Provider:
        async def snapshot(self, **_kwargs):
            raise RuntimeError(secret)

    monkeypatch.setattr(codex_provider, "get_codex_provider", lambda: Provider())
    app = FastAPI()
    register_oauth_routes(app)

    response = TestClient(app).get("/api/settings/openai-oauth")

    assert response.status_code == 200
    assert response.json()["error"] == "Codex 账户状态不可用。"
    assert response.json()["error_code"] == "codex_status_unavailable"
    assert secret not in response.text


def test_oauth_snapshot_sanitizes_nested_provider_errors(monkeypatch):
    from cyrene.plugins.builtin.cyrene_model.oauth import _public_oauth_snapshot

    _language(monkeypatch, "zh")
    secret = "private-codex-process-output"
    snapshot = _public_oauth_snapshot(
        {
            "available": False,
            "error": secret,
            "errors": {"limits": secret},
            "cli": {"installed": True, "broken": True, "error": secret},
        }
    )

    assert snapshot["error"] == "Codex 账户连接不可用。"
    assert snapshot["errors"] == {"limits": "Codex 配额暂时不可用。"}
    assert snapshot["cli"]["error"] == "Codex CLI 不可用。"
    assert secret not in json.dumps(snapshot, ensure_ascii=False)


def test_profile_service_error_keeps_safe_revision_only(monkeypatch):
    from cyrene.runtime.profile_data_service import ProfileDataError
    from cyrene.workbench.http.settings import profile_data

    _language(monkeypatch, "zh")
    secret = "private-profile-error"

    class Service:
        async def update_profile(self, _body):
            raise ProfileDataError(secret, 409, revision=7, detail=secret)

    app = FastAPI()
    profile_data.register_profile_routes(app, Service())

    response = TestClient(app).put("/api/profile", json={"name": "Cyrene"})

    assert response.status_code == 409
    assert response.json() == {
        "error": "设置已被其他客户端更改。",
        "code": "settings_revision_conflict",
        "revision": 7,
    }
    assert secret not in response.text


def test_config_service_error_drops_raw_payload_detail(monkeypatch):
    from cyrene.runtime.config_integration_service import ConfigIntegrationError
    from cyrene.workbench.http.settings.config_integrations import _error_response

    _language(monkeypatch, "zh")
    secret = "private-host-bridge-detail"
    response = _error_response(
        ConfigIntegrationError(
            secret,
            409,
            {"error": secret, "detail": secret, "revision": 11},
        ),
        en="Unable to update settings.",
        zh="无法更新设置。",
        code="settings_update_failed",
    )

    assert _payload(response) == {
        "error": "设置已被其他客户端更改。",
        "code": "settings_revision_conflict",
        "revision": 11,
    }
    assert secret not in response.body.decode("utf-8")


def test_local_model_status_sanitizes_background_download_errors(monkeypatch):
    from cyrene.plugins.builtin.cyrene_knowledge.settings_routes import (
        _public_local_model_status,
    )

    _language(monkeypatch, "zh")
    secret = "all mirrors failed at /private/cache/model.bin"
    payload = _public_local_model_status(
        {
            "models": [{"id": "demo", "error": secret}],
            "cv2_runtime": {"error": secret},
        }
    )

    assert payload["models"][0]["error"] == "本地模型下载失败，请重试。"
    assert payload["cv2_runtime"]["error"] == "OCR 运行时下载失败，请重试。"
    assert secret not in json.dumps(payload, ensure_ascii=False)


def test_plugin_activation_validation_localizes_technical_names(monkeypatch):
    from cyrene.workbench.http.settings.plugin_service import _activation_update_error

    _language(monkeypatch, "zh")
    response = _activation_update_error(
        {"plugins": {"demo_plugin": "yes"}},
        object(),
    )

    assert response is not None
    assert _payload(response) == {
        "error": "插件开关值必须是布尔值：demo_plugin",
        "code": "invalid_plugin_values",
        "plugins": ["demo_plugin"],
    }


def test_model_validation_hides_unknown_exception_text(monkeypatch):
    from cyrene.plugins.builtin.cyrene_model.routes import _validation_error

    _language(monkeypatch, "zh")
    secret = "provider failed at /private/path"
    response = _validation_error(
        ValueError(secret),
        en="Invalid model connection settings.",
        zh="模型连接设置无效。",
        code="invalid_model_connection",
    )

    assert _payload(response) == {
        "error": "模型连接设置无效。",
        "code": "invalid_model_connection",
    }
    assert secret not in response.body.decode("utf-8")


def test_office_error_translates_allowlisted_message_and_hides_unknowns(monkeypatch):
    from cyrene.plugins.builtin.cyrene_office.installation import OfficeInstallationError
    from cyrene.plugins.builtin.cyrene_office.settings_routes import _installation_error

    _language(monkeypatch, "zh")
    known = _installation_error(
        OfficeInstallationError("Certificate trust confirmation timed out."),
        action="install",
    )
    secret = "codesign failed at /private/path"
    unknown = _installation_error(
        OfficeInstallationError(secret),
        action="install",
    )

    assert _payload(known)["error"] == "证书信任确认已超时。"
    assert _payload(unknown) == {
        "error": "PowerPoint 集成安装失败。",
        "code": "office_install_failed",
    }
    assert secret not in unknown.body.decode("utf-8")


def test_gateway_error_preserves_openai_shape_and_localizes(monkeypatch):
    from cyrene.plugins.builtin.cyrene_extensions.agent_model_gateway_routes import _gateway_error

    _language(monkeypatch, "zh")
    response = _gateway_error(
        "Cyrene model request failed",
        "Cyrene 模型请求失败。",
        "model_request_failed",
        502,
    )

    assert _payload(response) == {
        "error": {
            "message": "Cyrene 模型请求失败。",
            "code": "model_request_failed",
        }
    }
