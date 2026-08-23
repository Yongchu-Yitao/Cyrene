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


def test_model_configuration_invalidation_drops_all_stale_affinity():
    from cyrene.model_runtime import client

    client._last_success_cache = {"primary": {"model": "old"}}
    client._session_model_preference_cache = {"chat": {"model": "old"}}
    client._candidate_cooldowns[("chat", "old", "https://old.test")] = 1.0
    client._published_fallback_notices[("chat", "round", "old", "fallback")] = None

    client.invalidate_model_configuration()

    assert client._last_success_cache is None
    assert client._session_model_preference_cache is None
    assert client._candidate_cooldowns == {}
    assert client._published_fallback_notices == {}


def test_unconfigured_model_failure_has_actionable_error_metadata():
    from cyrene.workbench.chat import _workbench_chat_error_metadata
    from cyrene.workbench.runtime import _WorkbenchAgentRunError

    error = _WorkbenchAgentRunError(
        "model_not_configured",
        "No model is configured.",
        status_code=400,
    )

    assert _workbench_chat_error_metadata(error) == {
        "code": "model_not_configured",
        "detail_key": "workbenchChat.error.modelNotConfigured",
    }


@pytest.mark.asyncio
async def test_delete_all_local_models_cancels_runtime_and_removes_root(
    monkeypatch, tmp_path: Path
):
    from cyrene.knowledge import local_models

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


def test_reset_clears_legacy_workspace_root_leftovers_but_keeps_user_folders(
    monkeypatch, tmp_path: Path
):
    from cyrene.workbench import runtime

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(runtime, "WORKSPACE_DIR", workspace)

    # Signature-matching Cyrene leftovers from a failed migration/old restore.
    conversations = workspace / "conversations"
    conversations.mkdir()
    (conversations / "2026-01-01.md").write_text(
        "# Conversations - 2026-01-01\nold", encoding="utf-8"
    )
    (workspace / "plan").mkdir()
    (workspace / "plan" / "plan_deadbeef01.md").write_text("# plan", encoding="utf-8")
    (workspace / "projects").mkdir()
    (workspace / "projects" / "project_deadbeef01").mkdir()
    (workspace / "scratch").mkdir()
    (workspace / "scratch" / "tmp.bin").write_bytes(b"\x00")
    soul = workspace / "SOUL.md"
    soul.write_text("# Soul\n\n## SELF:IDENTITY\n- legacy\n", encoding="utf-8")

    # User-owned folders with the same names but no Cyrene signature survive.
    user_plan = workspace / "patterns"
    user_plan.mkdir()
    (user_plan / "notes.md").write_text("# my notes", encoding="utf-8")
    user_soul = workspace / "notes.md"
    user_soul.write_text("# not a soul", encoding="utf-8")

    runtime._reset_legacy_workspace_root_leftovers()

    assert not conversations.exists()
    assert not (workspace / "plan").exists()
    assert not (workspace / "projects").exists()
    assert not (workspace / "scratch").exists()
    assert not soul.exists()
    assert user_plan.exists()
    assert (user_plan / "notes.md").read_text(encoding="utf-8") == "# my notes"
    assert user_soul.exists()


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
