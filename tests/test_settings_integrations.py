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


def test_local_qwen_embedding_does_not_require_endpoint(integration_store):
    integration_settings, settings, _ = integration_store
    settings["embedding"] = {
        "provider": "local_onnx",
        "base_url": "",
        "api_key": "",
        "model": "qwen3-embedding-0.6b",
        "dimensions": 1024,
    }

    config = integration_settings.merged_test_config("embedding", {})

    assert config["provider"] == "local_onnx"
    assert config["base_url"] == ""
    assert config["dimensions"] == 1024


def test_missing_local_qwen_falls_back_to_keyword_retrieval(monkeypatch):
    from cyrene.knowledge import embeddings, local_models

    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.setattr(embeddings, "_persisted", lambda: {
        "provider": "local_onnx",
        "base_url": "",
        "api_key": "",
        "model": "qwen3-embedding-0.6b",
        "dimensions": 1024,
    })
    monkeypatch.setattr(local_models, "is_ready", lambda _model_id: False)

    assert embeddings.is_configured() is False

    monkeypatch.setattr(local_models, "is_ready", lambda _model_id: True)
    assert embeddings.is_configured() is True


@pytest.mark.asyncio
async def test_local_embedding_probe_reports_keyword_fallback_when_pack_is_missing(monkeypatch):
    from cyrene.knowledge import local_models
    from cyrene.runtime import integration_settings

    monkeypatch.setattr(local_models, "is_ready", lambda _model_id: False)

    result = await integration_settings.test_embedding({
        "provider": "local_onnx",
        "model": "qwen3-embedding-0.6b",
        "dimensions": 1024,
    })

    assert result["ok"] is True
    assert result["fallback"] == "keyword"
    assert result["dimensions"] == 0


@pytest.mark.asyncio
async def test_embedding_transport_normalizes_vectors(monkeypatch):
    from cyrene.knowledge import embedding_client

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"index": 0, "embedding": [3.0, 4.0]}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(embedding_client.httpx, "AsyncClient", lambda: FakeClient())
    vectors = await embedding_client.embed_texts_with_config(["hello"], {
        "provider": "openai_compatible", "base_url": "https://example.test/v1",
        "model": "embed", "dimensions": 0,
    })

    assert vectors[0] == pytest.approx([0.6, 0.8])


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

    assert result[0] == pytest.approx([0.316227766, 0.948683298])
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


def test_settings_ui_keeps_zotero_in_general_and_embedding_in_models():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "webui" / "frontend" / "settings-overlay.jsx").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(encoding="utf-8")
    translations = (root / "src" / "webui" / "frontend" / "workbench-i18n.jsx").read_text(encoding="utf-8")

    assert 'settingsFetch("/api/settings/integrations")' in source
    assert 'settingsFetch("/api/settings/integrations/test"' in source
    assert 'value: "openai_compatible"' in source
    assert 'value: "ollama"' in source
    assert 'value: "local_onnx"' in source
    assert 'qwen3-embedding-0.6b' in source
    assert 'type: "password"' in source
    assert 'settings.zoteroCopyAttachments' in source
    assert 'function importFromZotero()' in source
    assert '"/api/workbench/library/zotero/sync?workspace="' in source
    assert 'disabled: !!integrationBusy || !(p.project && p.project.id)' in source
    general_panel = source.split("function GeneralPanel(p) {", 1)[1].split("// ── Models Panel ──", 1)[0]
    models_panel = source.split("// ── Models Panel ──", 1)[1].split("// ── Channels Panel ──", 1)[0]
    assert 'settings.zoteroIntegration' in general_panel
    assert 'settings.embeddingIntegration' not in general_panel
    assert 'function EmbeddingSettingsSection(p)' in models_panel
    assert 'React.createElement(EmbeddingSettingsSection, {' in models_panel
    assert 'settings.localModels' in models_panel
    assert 'settings.localModelOptional' in models_panel
    assert 'className: "wb-local-model-icon is-" + kind' in models_panel
    for kind in ("embedding", "ocr", "asr", "tts"):
        assert f".wb-local-model-icon.is-{kind}" in styles
    assert "var(--wb-local-model-icon-color)" in styles
    assert '!coverage.configured' in models_panel
    assert 'saveAllModels' in models_panel
    assert 'settings.reembedPromptTitle' in models_panel
    assert 'coverage.pending_vectors' in models_panel
    assert '"/api/workbench/knowledge/reembed?workspace="' in models_panel
    embedding_section = models_panel.split("function EmbeddingSettingsSection(p) {", 1)[1].split("function modelCredentialFields", 1)[0]
    assert 'onClick: save' not in embedding_section
    assert translations.count('"settings.embeddingIntegration"') == 2
    assert translations.count('"settings.reembedPromptTitle"') == 2
    assert translations.count('"settings.zoteroIntegration"') == 2
    assert translations.count('"settings.zoteroImportAction"') == 2

    library = (root / "src/webui/frontend/workbench-library.jsx").read_text(encoding="utf-8")
    assert '"/api/workbench/knowledge/embedding/status?workspace="' in library
    assert '"/api/workbench/knowledge/reembed?workspace="' in library
    assert 'L("library.vectorizeAll", "Vectorize all")' in library
    assert translations.count('"library.vectorizeAll"') == 2


def test_general_settings_has_opt_in_external_agent_proxy():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src/webui/frontend/settings-overlay.jsx").read_text(encoding="utf-8")
    i18n = (root / "src/webui/frontend/workbench-i18n.jsx").read_text(encoding="utf-8")
    general_panel = source.split("function GeneralPanel(p) {", 1)[1].split("// ── Models Panel ──", 1)[0]

    assert 't("settings.agentProxyEnabled")' in general_panel
    assert 'external_agent_proxy_enabled: !!nextEnabled' in general_panel
    assert 'external_agent_proxy_port: port' in general_panel
    assert 'disabled: !agentProxyEnabled' in general_panel
    assert i18n.count('"settings.agentProxyEnabled"') == 2
    assert i18n.count('"settings.agentProxyPort"') == 2
def test_performance_mode_is_an_appearance_runtime_setting():
    from cyrene.runtime.settings_service import SETTING_SPECS
    from cyrene.workbench.runtime import _build_config

    spec = next(item for item in SETTING_SPECS if item.key == "performance_mode")
    assert spec.namespace == "runtime"
    assert spec.tab == "appearance"
    assert spec.value_type == "boolean"
    assert spec.default is False
    assert "performance_mode" in _build_config()
