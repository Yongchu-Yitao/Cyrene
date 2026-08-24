import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from conftest import workbench_shell_source

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from route.registry import register_routes


def _patch_paths(monkeypatch, tmp_path, soul_content, default_content):
    from cyrene.runtime import onboarding, setup
    from cyrene.runtime.memory import conversations

    soul_path = tmp_path / "workspace" / "SOUL.md"
    soul_path.parent.mkdir(parents=True, exist_ok=True)
    soul_path.write_text(soul_content, encoding="utf-8")

    monkeypatch.setattr(onboarding, "DATA_DIR", tmp_path)
    monkeypatch.setattr(onboarding, "STORE_DIR", tmp_path / "store")
    monkeypatch.setattr(
        onboarding,
        "DB_PATH",
        tmp_path / "store" / "cyrene.runtime.database",
    )
    monkeypatch.setattr(onboarding, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(onboarding, "get_soul_path", lambda: soul_path)
    monkeypatch.setattr(onboarding, "read_soul", lambda: soul_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(onboarding, "get_default_soul_content", lambda name=None: default_content)
    monkeypatch.setattr(setup, "DATA_DIR", tmp_path)
    monkeypatch.setattr(setup, "_SETUP_FLAG", None)
    monkeypatch.setattr(conversations, "CONVERSATIONS_DIR", tmp_path / "conversations")
    return soul_path


def test_get_onboarding_status_detects_absolute_fresh_start(monkeypatch, tmp_path):
    from cyrene.runtime import onboarding

    default_soul = "# Cyrene's Soul\n\n## SELF:IDENTITY\n- default\n"
    _patch_paths(monkeypatch, tmp_path, default_soul, default_soul)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    status = onboarding.get_onboarding_status()
    assert status["hasExistingData"] is False

    assert status["needsOnboarding"] is True
    assert status["isAbsoluteFreshStart"] is True
    assert status["activeStep"] == "llm"


def test_get_onboarding_status_infers_existing_setup(monkeypatch, tmp_path):
    from cyrene.runtime import onboarding

    default_soul = "# Cyrene's Soul\n\n## SELF:IDENTITY\n- default\n"
    custom_soul = "# Sherlock's Soul\n\n## CORE IDENTITY\n- sharp and theatrical\n"
    _patch_paths(monkeypatch, tmp_path, custom_soul, default_soul)
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("OPENAI_MODEL", "example-model")

    status = onboarding.get_onboarding_status()

    assert status["needsOnboarding"] is False
    assert status["activeStep"] == "done"
    assert status["personality"]["configured"] is True
    assert (tmp_path / "onboarding_state.json").exists()


async def test_save_and_test_llm_setup_persists_completion(monkeypatch, tmp_path):
    from cyrene.runtime import onboarding, config_store

    default_soul = "# Cyrene's Soul\n\n## SELF:IDENTITY\n- default\n"
    _patch_paths(monkeypatch, tmp_path, default_soul, default_soul)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(onboarding, "write_env_keys", lambda updates: True)
    monkeypatch.setattr(onboarding, "test_llm_connection", AsyncMock(return_value="OK"))
    monkeypatch.setattr(onboarding, "test_llm_vision_capability", AsyncMock(return_value={
        "vision_capable": True,
        "vision_checked_at": "2026-07-12T00:00:00+00:00",
        "vision_check_error": "",
    }))

    # Isolate the encrypted config store so the test does not touch user data.
    monkeypatch.setattr(config_store, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config_store, "_ENCRYPTED_PATH", tmp_path / "data" / "config.enc")
    monkeypatch.setattr(config_store, "_KEY_PATH", tmp_path / "data" / ".config_key")
    monkeypatch.setattr(config_store, "_LEGACY_ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(config_store, "_LEGACY_SETTINGS_PATH", tmp_path / "data" / "web_settings.json")
    monkeypatch.setattr(config_store, "_cache", None)
    monkeypatch.setattr(config_store, "_migrated", False)
    monkeypatch.setattr(config_store, "_fernet", None)
    monkeypatch.setattr(config_store, "_SETTINGS_MIGRATIONS_DONE", False)

    payload = await onboarding.save_and_test_llm_setup("sk-test", "http://localhost:11434/v1", "qwen3")

    assert payload["ok"] is True
    assert payload["preview"] == "OK"
    assert payload["onboarding"]["llm"]["configured"] is True
    assert payload["onboarding"]["activeStep"] == "personality"

    from cyrene.runtime.settings_store import get_models
    models = get_models()
    assert len(models) == 1
    assert models[0]["id"] == "qwen3"
    assert models[0]["name"] == "qwen3"
    assert models[0]["model"] == "qwen3"
    assert models[0]["base_url"] == "http://localhost:11434/v1"
    assert models[0]["api_key"] == "sk-test"
    assert models[0]["vision_capable"] is True


async def test_save_codex_oauth_setup_persists_model_and_effort(monkeypatch, tmp_path):
    from cyrene.model_runtime import codex_provider
    from cyrene.runtime import onboarding, settings_store

    default_soul = "# Cyrene's Soul\n\n## SELF:IDENTITY\n- default\n"
    _patch_paths(monkeypatch, tmp_path, default_soul, default_soul)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    class FakeProvider:
        async def account(self):
            return {
                "account": {
                    "type": "chatgpt",
                    "email": "user@example.com",
                }
            }

        async def models(self):
            return [{
                "id": "gpt-5.6-terra",
                "supportedReasoningEfforts": [
                    {"reasoningEffort": "low"},
                    {"reasoningEffort": "high"},
                ],
            }]

        async def close(self):
            return None

    saved = {}
    env_updates = {}
    monkeypatch.setattr(codex_provider, "get_codex_provider", lambda: FakeProvider())
    monkeypatch.setattr(settings_store, "get_models", lambda: [{
        "id": "custom-primary",
        "model": "deepseek-chat",
        "provider": "openai_compatible",
    }])
    monkeypatch.setattr(settings_store, "save_models", lambda models: saved.setdefault("models", models))
    monkeypatch.setattr(settings_store, "save_codex_model", lambda model: saved.setdefault("codex_model", model))
    monkeypatch.setattr(settings_store, "save_model_source", lambda source: saved.setdefault("model_source", source))
    monkeypatch.setattr(onboarding, "write_env_keys", lambda updates: env_updates.update(updates))

    payload = await onboarding.save_codex_oauth_setup(
        "gpt-5.6-terra",
        "high",
    )

    assert payload["ok"] is True
    assert payload["onboarding"]["llm"]["provider"] == "codex_oauth"
    assert payload["onboarding"]["llm"]["model"] == "gpt-5.6-terra"
    assert payload["onboarding"]["llm"]["reasoningEffort"] == "high"
    assert payload["onboarding"]["activeStep"] == "personality"
    assert saved["models"][0]["provider"] == "codex_oauth"
    assert saved["models"][0]["model"] == "gpt-5.6-terra"
    assert saved["models"][0]["reasoning_effort"] == "high"
    assert saved["models"][0]["vision_capable"] is True
    assert saved["models"][0]["vision_check_error"] == ""
    assert len(saved["models"]) == 1
    assert saved["codex_model"]["model"] == "gpt-5.6-terra"
    assert saved["model_source"] == "codex"
    assert env_updates == {"OPENAI_MODEL": "gpt-5.6-terra"}


async def test_vision_capability_probe_sends_an_image(monkeypatch):
    from cyrene.runtime import onboarding
    from cyrene.runtime import model_probe_service

    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "Image received"}}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, endpoint, headers=None, json=None):
            calls.append({"endpoint": endpoint, "headers": headers, "json": json})
            return FakeResponse()

    monkeypatch.setattr(model_probe_service.httpx, "AsyncClient", lambda *args, **kwargs: FakeClient())

    result = await onboarding.test_llm_vision_capability("sk-test", "https://example.test/v1", "vision-model")

    assert result["vision_capable"] is True
    content = calls[0]["json"]["messages"][0]["content"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://api.deepseek.com", "https://api.deepseek.com/v1/chat/completions"),
        ("https://api.minimaxi.com", "https://api.minimaxi.com/v1/chat/completions"),
    ],
)
async def test_text_connection_probe_normalizes_official_provider_endpoint(
    monkeypatch,
    base_url,
    expected,
):
    from cyrene.runtime import onboarding
    from cyrene.runtime import model_probe_service

    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "OK"}}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, endpoint, headers=None, json=None):
            calls.append(endpoint)
            return FakeResponse()

    monkeypatch.setattr(model_probe_service.httpx, "AsyncClient", lambda *args, **kwargs: FakeClient())

    assert await onboarding.test_llm_connection("sk-test", base_url, "model") == "OK"
    assert calls == [expected]


def test_settings_model_save_persists_vision_probe_result(monkeypatch, tmp_path):
    """The Settings model form records the probe result used by browser_screenshot."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cyrene import config
    from cyrene.runtime import settings_store
    from cyrene.runtime.model_probe_service import ModelProbeService

    saved = {}

    async def fake_text_probe(self, api_key, base_url, model):
        return "OK"

    async def fake_vision_probe(self, api_key, base_url, model):
        return {
            "vision_capable": model == "visual-primary",
            "vision_checked_at": "2026-07-12T00:00:00+00:00",
            "vision_check_error": "" if model == "visual-primary" else "image input unsupported",
        }

    monkeypatch.setattr(ModelProbeService, "test_connection", fake_text_probe)
    monkeypatch.setattr(ModelProbeService, "probe_vision", fake_vision_probe)
    monkeypatch.setattr(settings_store, "save_models", lambda models: saved.setdefault("models", models))
    monkeypatch.setattr(settings_store, "save_custom_models", lambda models: saved.setdefault("custom_models", models))
    monkeypatch.setattr(settings_store, "save_model_source", lambda source: saved.setdefault("model_source", source))
    monkeypatch.setattr(settings_store, "save_vision_models", lambda models: saved.setdefault("vision_models", models))
    monkeypatch.setattr(settings_store, "save_secondary_model", lambda model: None)
    monkeypatch.setattr(settings_store, "get_secondary_model", lambda: {})
    monkeypatch.setattr(config, "write_env_keys", lambda values: None)

    app = FastAPI()
    register_routes(app, bot=None, db_path=str(tmp_path / "test.db"))
    response = TestClient(app).put("/api/settings/models", json={
        "models": [{"id": "primary", "model": "visual-primary", "api_key": "sk-test", "base_url": "https://example.test/v1"}],
        "vision_models": [{"id": "vision", "model": "visual-primary", "api_key": "sk-test", "base_url": "https://example.test/v1"}],
    })

    assert response.status_code == 200
    assert saved["models"][0]["vision_capable"] is True
    assert saved["models"][0]["vision_checked_at"]
    assert saved["vision_models"][0]["vision_capable"] is True
    assert response.json()["models"][0]["vision_capable"] is True


def test_settings_rejects_codex_oauth_as_fallback(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    register_routes(app, bot=None, db_path=str(tmp_path / "test.db"))
    response = TestClient(app).put("/api/settings/models", json={
        "models": [
            {
                "id": "custom-primary",
                "model": "deepseek-chat",
                "provider": "openai_compatible",
                "api_key": "sk-test",
                "base_url": "https://example.test/v1",
            },
            {
                "id": "codex-fallback",
                "model": "gpt-5.6-sol",
                "provider": "codex_oauth",
            },
        ],
    })

    assert response.status_code == 400
    assert response.json()["error"] == (
        "Codex OAuth can only be used as the primary model"
    )


def test_settings_requires_separate_codex_model_for_oauth_source(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    custom_model = {
        "id": "custom-primary",
        "model": "deepseek-chat",
        "provider": "openai_compatible",
        "api_key": "sk-test",
        "base_url": "https://example.test/v1",
    }
    app = FastAPI()
    register_routes(app, bot=None, db_path=str(tmp_path / "test.db"))
    response = TestClient(app).put("/api/settings/models", json={
        "primary_source": "codex",
        "models": [custom_model],
        "custom_models": [custom_model],
        "codex_model": None,
    })

    assert response.status_code == 400
    assert response.json()["error"] == (
        "Codex model is required when OpenAI OAuth is active"
    )


def test_settings_keeps_custom_and_codex_models_in_parallel(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cyrene import config
    from cyrene.runtime import settings_store
    from cyrene.runtime.model_probe_service import ModelProbeService

    saved = {}

    async def fake_text_probe(self, api_key, base_url, model):
        return "OK"

    async def fake_vision_probe(self, api_key, base_url, model):
        return {
            "vision_capable": False,
            "vision_checked_at": "2026-07-30T00:00:00+00:00",
            "vision_check_error": "unsupported",
        }

    monkeypatch.setattr(ModelProbeService, "test_connection", fake_text_probe)
    monkeypatch.setattr(ModelProbeService, "probe_vision", fake_vision_probe)
    monkeypatch.setattr(settings_store, "save_models", lambda models: saved.__setitem__("models", models))
    monkeypatch.setattr(settings_store, "save_custom_models", lambda models: saved.__setitem__("custom_models", models))
    monkeypatch.setattr(settings_store, "save_codex_model", lambda model: saved.__setitem__("codex_model", model))
    monkeypatch.setattr(settings_store, "save_model_source", lambda source: saved.__setitem__("model_source", source))
    monkeypatch.setattr(settings_store, "save_vision_models", lambda models: None)
    monkeypatch.setattr(settings_store, "save_secondary_model", lambda model: None)
    monkeypatch.setattr(settings_store, "get_secondary_model", lambda: {})
    monkeypatch.setattr(config, "write_env_keys", lambda values: None)

    custom_model = {
        "id": "custom-primary",
        "model": "deepseek-chat",
        "provider": "openai_compatible",
        "api_key": "sk-test",
        "base_url": "https://example.test/v1",
    }
    app = FastAPI()
    register_routes(app, bot=None, db_path=str(tmp_path / "test.db"))
    response = TestClient(app).put("/api/settings/models", json={
        "primary_source": "custom",
        "models": [custom_model],
        "custom_models": [custom_model],
        "codex_model": {
            "id": "codex-gpt",
            "model": "gpt-5.6-sol",
            "provider": "codex_oauth",
        },
        "vision_models": [{
            "id": "vision",
            "model": "deepseek-chat",
            "api_key": "sk-test",
            "base_url": "https://example.test/v1",
        }],
    })

    assert response.status_code == 200
    assert saved["model_source"] == "custom"
    assert [model["model"] for model in saved["models"]] == ["deepseek-chat"]
    assert [model["model"] for model in saved["custom_models"]] == ["deepseek-chat"]
    assert saved["codex_model"]["model"] == "gpt-5.6-sol"
    assert response.json()["primary_source"] == "custom"
    assert response.json()["codex_model"]["model"] == "gpt-5.6-sol"


async def test_save_personality_setup_marks_setup_done(monkeypatch, tmp_path):
    from cyrene.runtime import onboarding

    default_soul = "# Cyrene's Soul\n\n## SELF:IDENTITY\n- default\n"
    soul_path = _patch_paths(monkeypatch, tmp_path, default_soul, default_soul)
    fake_agent = types.SimpleNamespace(clear_session_id=AsyncMock())
    monkeypatch.setitem(sys.modules, "cyrene.agent", fake_agent)

    onboarding.save_onboarding_state({
        "llm": {
            "completed_at": "2026-05-19T00:00:00+00:00",
            "source": "wizard",
            "base_url": "https://example.test/v1",
            "model": "example-model",
        }
    })

    payload = await onboarding.save_personality_setup("default")

    assert payload["ok"] is True
    assert soul_path.read_text(encoding="utf-8") == default_soul
    assert (tmp_path / ".setup_done").exists()
    assert payload["onboarding"]["needsOnboarding"] is False
    fake_agent.clear_session_id.assert_awaited_once()


def test_completed_onboarding_enters_chat_instead_of_welcome():
    root = Path(__file__).resolve().parent.parent
    welcome_source = (
        root / "src" / "webui" / "frontend" / "workbench-welcome.jsx"
    ).read_text(encoding="utf-8")
    workbench_source = workbench_shell_source()

    assert (
        "p.onboarding && !p.onboarding.needsOnboarding && props.onComplete"
        in welcome_source
    )
    assert "props.onComplete();" in welcome_source
    assert "Page: OnboardingFlow" in welcome_source

    completion_handler = workbench_source.split(
        "function handleOnboardingComplete() {", 1
    )[1].split("}", 1)[0]
    assert 'setFullPage("chat");' in completion_handler
    assert "onComplete={handleOnboardingComplete}" in workbench_source
