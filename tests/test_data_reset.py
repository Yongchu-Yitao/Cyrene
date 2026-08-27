from pathlib import Path
from types import SimpleNamespace

from conftest import workbench_settings_source
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest


def test_reset_endpoint_requires_explicit_confirmation(monkeypatch, tmp_path: Path):
    from route.settings import general

    reset = AsyncMock(return_value={"ok": True})
    app = FastAPI()
    general.register_settings_routes(
        app,
        None,
        str(tmp_path / "runtime.db"),
        data_reset_service=SimpleNamespace(reset_app_data=reset),
    )

    client = TestClient(app)
    missing = client.post("/api/settings/reset-data", json={})
    assert missing.status_code == 400
    assert missing.json()["code"] == "reset_confirmation_required"
    reset.assert_not_awaited()

    confirmed = client.post(
        "/api/settings/reset-data",
        json={"confirmation": "RESET CYRENE DATA"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json() == {"ok": True}
    reset.assert_awaited_once()


def test_config_reset_replaces_persisted_and_live_environment(monkeypatch):
    from cyrene.runtime import config_store

    current = {
        "env": {
            **config_store._DEFAULT_ENV,
            "OPENAI_API_KEY": "old-secret",
            "REMOVED_AFTER_RESET": "old-value",
        },
        "settings": {"models": [{"model": "old-model"}]},
        "settings_revision": 8,
    }
    persisted = {}
    live_env = {
        "OPENAI_API_KEY": "old-secret",
        "REMOVED_AFTER_RESET": "old-value",
    }
    monkeypatch.setattr(config_store, "_cache", current)
    monkeypatch.setattr(config_store, "_ensure_loaded", lambda: current)
    monkeypatch.setattr(config_store, "_persist", lambda value: persisted.update(value))
    monkeypatch.setattr(config_store.os, "environ", live_env)

    config_store.reset_all()

    assert persisted["settings_revision"] == 9
    assert "models" not in persisted["settings"]
    assert persisted["env"] == config_store._DEFAULT_ENV
    assert "OPENAI_API_KEY" not in live_env
    assert "REMOVED_AFTER_RESET" not in live_env
    assert live_env["OPENAI_MODEL"] == config_store._DEFAULT_ENV["OPENAI_MODEL"]


@pytest.mark.asyncio
async def test_delete_all_local_models_cancels_runtime_and_removes_root(
    monkeypatch, tmp_path: Path
):
    from agent.plugin.plugin_impl.cyrene_knowledge import local_models

    root = tmp_path / "knowledge_models"
    (root / "qwen3-embedding-0.6b").mkdir(parents=True)
    (root / "qwen3-embedding-0.6b" / "model.onnx").write_bytes(b"model")
    reset_calls = []
    monkeypatch.setattr(local_models, "MODEL_ROOT", root)
    monkeypatch.setattr(
        local_models,
        "_RESETTERS",
        {"qwen3-embedding-0.6b": lambda: reset_calls.append(True)},
    )
    monkeypatch.setattr(local_models, "_TASKS", {})
    monkeypatch.setattr(local_models, "_PROGRESS", {"qwen3-embedding-0.6b": {}})
    monkeypatch.setattr(local_models, "_VALIDATED", {"qwen3-embedding-0.6b"})

    await local_models.delete_all_models()

    assert not root.exists()
    assert reset_calls == [True]
    assert local_models._PROGRESS == {}
    assert local_models._VALIDATED == set()


@pytest.mark.asyncio
async def test_clear_browser_data_erases_electron_and_playwright_profiles(
    monkeypatch, tmp_path: Path
):
    from cyrene import browser, config

    profile = tmp_path / "browser_profile"
    profile.mkdir()
    (profile / "Cookies").write_text("logged-in", encoding="utf-8")
    calls = []

    async def rpc(method, args, **kwargs):
        calls.append((method, args, kwargs))
        return {"ok": True}

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(browser, "electron_browser_available", lambda: True)
    monkeypatch.setattr(browser, "_electron_browser_rpc", rpc)
    monkeypatch.setattr(browser, "close_session", AsyncMock())

    result = await browser.clear_browser_data()

    assert result == {"ok": True, "electron": True, "playwright": True}
    assert calls[0][0] == "clearStorage"
    assert not profile.exists()
    browser.close_session.assert_awaited_once()


def test_reset_frontend_confirms_and_clears_all_client_storage():
    root = Path(__file__).resolve().parent.parent
    settings = workbench_settings_source()
    electron = (root / "electron/main.js").read_text(encoding="utf-8")

    assert 'feedback.confirmModal({' in settings
    assert 'confirmation: "RESET CYRENE DATA"' in settings
    assert "localStorage.clear()" in settings
    assert "sessionStorage.clear()" in settings
    assert "if (method === 'clearStorage')" in electron
    assert "await browserSession.clearStorageData()" in electron
    assert "resetDesktopSettings();" in electron
