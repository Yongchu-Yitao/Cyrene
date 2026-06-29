"""Tests for the encrypted config store and its migrations."""

import json
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


@pytest.fixture
def isolated_config_store(tmp_path, monkeypatch):
    """Return a config_store module whose paths point into a temp directory."""
    from cyrene import config_store

    monkeypatch.setattr(config_store, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config_store, "_ENCRYPTED_PATH", tmp_path / "data" / "config.enc")
    monkeypatch.setattr(config_store, "_KEY_PATH", tmp_path / "data" / ".config_key")
    monkeypatch.setattr(config_store, "_LEGACY_ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(config_store, "_LEGACY_SETTINGS_PATH", tmp_path / "data" / "web_settings.json")
    monkeypatch.setattr(config_store, "_cache", None)
    monkeypatch.setattr(config_store, "_migrated", False)
    monkeypatch.setattr(config_store, "_fernet", None)
    monkeypatch.setattr(config_store, "_SETTINGS_MIGRATIONS_DONE", False)
    return config_store


def _write_encrypted(config_store, config: dict) -> None:
    """Write a config dict directly into the isolated encrypted store."""
    key = Fernet.generate_key()
    config_store._KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    config_store._KEY_PATH.write_bytes(key)
    config_store._fernet = Fernet(key)
    plain = json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8")
    config_store._ENCRYPTED_PATH.parent.mkdir(parents=True, exist_ok=True)
    config_store._ENCRYPTED_PATH.write_bytes(Fernet(key).encrypt(plain))


def test_migration_fixes_incomplete_model_entries(isolated_config_store):
    """Older onboarding wrote model entries without model/base_url/api_key."""
    config = {
        "env": {
            "OPENAI_API_KEY": "sk-env",
            "OPENAI_BASE_URL": "https://api.example.com/v1",
            "OPENAI_MODEL": "example-model",
        },
        "settings": {
            "models": [
                {"id": "qwen3", "name": "qwen3", "desc": "", "ctx": "", "price": ""},
                {"id": "local", "name": "local-llm", "model": "local-llm", "base_url": "http://localhost:11434/v1"},
            ],
            "vision_models": [
                {"id": "vision-1", "name": "gpt-4o-mini", "model": "gpt-4o-mini"},
            ],
        },
    }
    _write_encrypted(isolated_config_store, config)

    loaded = isolated_config_store._ensure_loaded()
    models = loaded["settings"]["models"]
    vision = loaded["settings"]["vision_models"]

    assert models[0]["model"] == "qwen3"
    assert models[0]["base_url"] == "https://api.example.com/v1"
    assert models[0]["api_key"] == "sk-env"
    assert models[1]["model"] == "local-llm"
    assert models[1]["base_url"] == "http://localhost:11434/v1"
    assert models[1]["api_key"] == ""  # different endpoint, no env key backfill

    assert vision[0]["model"] == "gpt-4o-mini"
    assert vision[0]["base_url"] == "https://api.example.com/v1"
    assert vision[0]["api_key"] == "sk-env"
