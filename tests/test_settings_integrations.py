from pathlib import Path
import sys
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from route.registry import register_routes


@pytest.fixture
def integration_store(monkeypatch):
    from cyrene.runtime import integration_settings

    settings = {
        "zotero": {
            "base_url": "http://127.0.0.1:23119/api",
            "auto_sync": False,
            "copy_attachments": True,
        },
        "embedding": {
            "provider": "openai_compatible",
            "base_url": "https://embeddings.example/v1",
            "api_key": "stored-secret",
            "model": "embed-small",
            "dimensions": 768,
        },
    }
    env = {}

    monkeypatch.setattr(
        integration_settings.config_store,
        "get_setting",
        lambda key, default=None: settings.get(key, default),
    )
    monkeypatch.setattr(
        integration_settings.config_store,
        "set_setting",
        lambda key, value: settings.__setitem__(key, value),
    )
    monkeypatch.setattr(
        integration_settings.config_store,
        "get_env",
        lambda key, default="": env.get(key, default),
    )
    monkeypatch.setattr(
        integration_settings.config_store,
        "set_env_many",
        lambda values: env.update(values),
    )
    return integration_settings, settings, env


def test_public_integration_settings_never_return_embedding_secret(integration_store):
    integration_settings, _, _ = integration_store

    payload = integration_settings.public_settings()

    assert payload["embedding"]["api_key_configured"] is True
    assert "api_key" not in payload["embedding"]
    assert "stored-secret" not in str(payload)


def test_update_embedding_keeps_blank_secret_and_syncs_legacy_slots(integration_store):
    integration_settings, settings, env = integration_store

    payload = integration_settings.update_settings({
        "embedding": {
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434",
            "api_key": "",
            "model": "nomic-embed-text",
            "dimensions": 0,
        }
    })

    assert settings["embedding"]["api_key"] == "stored-secret"
    assert env["EMBEDDING_API_KEY"] == "stored-secret"
    assert payload["embedding"]["api_key_configured"] is True
    assert "api_key" not in payload["embedding"]


def test_zotero_local_api_rejects_non_loopback_urls():
    from cyrene.runtime.integration_settings import normalize_zotero

    with pytest.raises(ValueError, match="localhost:23119"):
        normalize_zotero({"base_url": "https://example.com/api"})

    with pytest.raises(ValueError, match="localhost:23119"):
        normalize_zotero({"base_url": "http://127.0.0.1:9999/api"})


def test_embedding_runtime_reads_persisted_settings_without_api_key(monkeypatch):
    from cyrene.knowledge import embeddings

    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.setattr(embeddings, "_persisted", lambda: {
        "provider": "ollama",
        "base_url": "http://127.0.0.1:11434",
        "api_key": "",
        "model": "nomic-embed-text",
        "dimensions": 0,
    })

    assert embeddings.is_configured() is True
    assert embeddings._base_url() == "http://127.0.0.1:11434"
    assert embeddings._model() == "nomic-embed-text"


def test_embedding_probe_requires_endpoint_and_model(integration_store):
    integration_settings, settings, _ = integration_store
    settings["embedding"].update({"base_url": "", "model": "", "api_key": ""})

    with pytest.raises(ValueError, match="base URL and model"):
        integration_settings.merged_test_config("embedding", {})


@pytest.mark.asyncio
async def test_ollama_embedding_request_and_response(monkeypatch):
    from cyrene.knowledge import embeddings
    from cyrene.knowledge import embedding_client

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"embeddings": [[0.25, 0.75]]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, endpoint, **kwargs):
            captured.update({"endpoint": endpoint, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(
        embedding_client.httpx,
        "AsyncClient",
        lambda: FakeClient(),
    )

    result = await embeddings.embed_texts_with_config(["hello"], {
        "provider": "ollama",
        "base_url": "http://127.0.0.1:11434",
        "api_key": "",
        "model": "nomic-embed-text",
        "dimensions": 2,
    })

    assert result == [[0.25, 0.75]]
    assert captured["endpoint"] == "http://127.0.0.1:11434/api/embed"
    assert captured["json"]["dimensions"] == 2
    assert "Authorization" not in captured["headers"]


def test_integration_settings_routes_hide_secrets_and_probe_drafts(monkeypatch, integration_store):
    integration_settings, _, _ = integration_store
    monkeypatch.setattr(
        integration_settings,
        "test_embedding",
        AsyncMock(return_value={
            "ok": True,
            "service": "embedding",
            "model": "draft-model",
            "dimensions": 384,
        }),
    )

    app = FastAPI()
    register_routes(app, bot=None, db_path="test.db")
    client = TestClient(app)

    get_response = client.get("/api/settings/integrations")
    assert get_response.status_code == 200
    assert "stored-secret" not in get_response.text
    assert "api_key" not in get_response.json()["embedding"]

    test_response = client.post("/api/settings/integrations/test", json={
        "service": "embedding",
        "config": {
            "provider": "openai_compatible",
            "base_url": "https://draft.example/v1",
            "api_key": "one-use-secret",
            "model": "draft-model",
            "dimensions": 384,
        },
    })
    assert test_response.status_code == 200
    assert test_response.json()["dimensions"] == 384
    assert "one-use-secret" not in test_response.text

    called_config = integration_settings.test_embedding.await_args.args[0]
    assert called_config["api_key"] == "one-use-secret"


def test_general_settings_ui_exposes_zotero_and_embedding_controls():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "settings-overlay.jsx").read_text(encoding="utf-8")
    translations = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(encoding="utf-8")

    assert 'fetch("/api/settings/integrations")' in source
    assert 'fetch("/api/settings/integrations/test"' in source
    assert 'value: "openai_compatible"' in source
    assert 'value: "ollama"' in source
    assert 'type: "password"' in source
    assert 'settings.zoteroCopyAttachments' in source
    assert 'function importFromZotero()' in source
    assert '"/api/workbench/library/zotero/sync?workspace="' in source
    assert 'disabled: !!integrationBusy || !(p.project && p.project.id)' in source
    assert translations.count('"settings.embeddingIntegration"') == 2
    assert translations.count('"settings.zoteroIntegration"') == 2
    assert translations.count('"settings.zoteroImportAction"') == 2
