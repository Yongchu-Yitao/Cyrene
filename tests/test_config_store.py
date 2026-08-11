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
    from cyrene.runtime import config_store

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


def test_config_uses_permission_restricted_local_key(isolated_config_store):
    cipher = isolated_config_store._get_fernet()

    assert cipher is not None
    assert isolated_config_store._KEY_PATH.exists()
    assert isolated_config_store._KEY_PATH.stat().st_mode & 0o777 == 0o600


def test_decryption_failure_preserves_config_instead_of_restoring_legacy_backup(
    isolated_config_store,
):
    stale_legacy = {
        "vision_models": [{"model": "gpt-4.1-mini"}],
    }
    legacy_backup = isolated_config_store._LEGACY_SETTINGS_PATH.with_suffix(
        ".json.bak"
    )
    legacy_backup.parent.mkdir(parents=True, exist_ok=True)
    legacy_backup.write_text(json.dumps(stale_legacy), encoding="utf-8")

    encrypted_key = Fernet.generate_key()
    wrong_local_key = Fernet.generate_key()
    encrypted = Fernet(encrypted_key).encrypt(
        json.dumps(
            {"env": {}, "settings": {"vision_models": [{"model": "new-vision"}]}}
        ).encode("utf-8")
    )
    isolated_config_store._ENCRYPTED_PATH.parent.mkdir(parents=True, exist_ok=True)
    isolated_config_store._ENCRYPTED_PATH.write_bytes(encrypted)
    isolated_config_store._KEY_PATH.write_bytes(wrong_local_key)

    with pytest.raises(RuntimeError, match="existing config was preserved"):
        isolated_config_store._ensure_loaded()

    assert isolated_config_store._ENCRYPTED_PATH.read_bytes() == encrypted
    assert json.loads(legacy_backup.read_text(encoding="utf-8")) == stale_legacy


def test_missing_local_key_preserves_existing_config_and_starts_with_defaults(
    isolated_config_store,
):
    encrypted = b"existing-encrypted-config"
    isolated_config_store._ENCRYPTED_PATH.parent.mkdir(parents=True, exist_ok=True)
    isolated_config_store._ENCRYPTED_PATH.write_bytes(encrypted)

    loaded = isolated_config_store._ensure_loaded()

    backup = isolated_config_store._ENCRYPTED_PATH.with_name(
        "config.enc.missing-key.bak"
    )
    assert backup.read_bytes() == encrypted
    assert isolated_config_store._KEY_PATH.exists()
    assert isolated_config_store._KEY_PATH.stat().st_mode & 0o777 == 0o600
    assert loaded["env"] == isolated_config_store._DEFAULT_ENV
    assert loaded["settings"] == isolated_config_store._DEFAULT_SETTINGS

    persisted = isolated_config_store._ENCRYPTED_PATH.read_bytes()
    plain = Fernet(isolated_config_store._KEY_PATH.read_bytes()).decrypt(persisted)
    assert json.loads(plain.decode("utf-8")) == loaded


def test_missing_local_key_uses_unique_backup_name(isolated_config_store):
    encrypted = b"new-unreadable-config"
    isolated_config_store._ENCRYPTED_PATH.parent.mkdir(parents=True, exist_ok=True)
    isolated_config_store._ENCRYPTED_PATH.write_bytes(encrypted)
    first_backup = isolated_config_store._ENCRYPTED_PATH.with_name(
        "config.enc.missing-key.bak"
    )
    first_backup.write_bytes(b"previous-unreadable-config")

    isolated_config_store._ensure_loaded()

    assert first_backup.read_bytes() == b"previous-unreadable-config"
    second_backup = first_backup.with_name("config.enc.missing-key.bak.1")
    assert second_backup.read_bytes() == encrypted


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


def test_removed_tool_round_setting_is_purged_and_rejected(
    isolated_config_store,
):
    config = {
        "env": {
            "OPENAI_MODEL": "example-model",
            "MAX_TOOL_ROUNDS": "15",
        },
        "settings": {},
    }
    _write_encrypted(isolated_config_store, config)

    loaded = isolated_config_store._ensure_loaded()

    assert "MAX_TOOL_ROUNDS" not in loaded["env"]
    assert "MAX_TOOL_ROUNDS" not in isolated_config_store.export_snapshot()["env"]
    with pytest.raises(ValueError, match="has been removed"):
        isolated_config_store.set_env("MAX_TOOL_ROUNDS", "15")
    with pytest.raises(ValueError, match="have been removed"):
        isolated_config_store.set_env_many({"MAX_TOOL_ROUNDS": "15"})


def test_migration_removes_legacy_global_tool_output_cap(isolated_config_store):
    config = {
        "env": {
            "OPENAI_MODEL": "example-model",
            "MAX_TOOL_OUTPUT_CHARS": "12000",
        },
        "settings": {},
    }
    _write_encrypted(isolated_config_store, config)

    loaded = isolated_config_store._ensure_loaded()

    assert loaded["env"]["MAX_TOOL_OUTPUT_CHARS"] == "0"
    assert isolated_config_store.get_env("MAX_TOOL_OUTPUT_CHARS") == "0"


def test_restore_drops_removed_tool_round_setting(isolated_config_store):
    normalized, _encrypted = isolated_config_store.prepare_restored_snapshot({
        "env": {
            "OPENAI_MODEL": "example-model",
            "MAX_TOOL_ROUNDS": "15",
        },
        "settings": {},
    })

    assert normalized["env"] == {"OPENAI_MODEL": "example-model"}


def test_activate_workspace_updates_active_state_and_history_in_one_write(
    isolated_config_store, monkeypatch,
):
    isolated_config_store._cache = {
        "env": {},
        "settings": {
            "workspace_active": False,
            "workspace_history": ["/old", "/selected"],
        },
    }
    persisted = []
    monkeypatch.setattr(
        isolated_config_store,
        "_persist",
        lambda config: persisted.append(config),
    )

    isolated_config_store.activate_workspace("/selected")

    assert isolated_config_store.is_workspace_active() is True
    assert isolated_config_store.get_workspace_history() == ["/selected", "/old"]
    assert len(persisted) == 1


def test_portable_snapshot_is_detached_reencrypted_and_activated(
    isolated_config_store,
):
    original = {
        "env": {"OPENAI_API_KEY": "old-secret", "OPENAI_MODEL": "old-model"},
        "settings": {"app_language": "en"},
    }
    _write_encrypted(isolated_config_store, original)

    exported = isolated_config_store.export_snapshot()
    exported["env"]["OPENAI_API_KEY"] = "changed-only-in-snapshot"
    assert isolated_config_store.get_env("OPENAI_API_KEY") == "old-secret"

    normalized, encrypted = isolated_config_store.prepare_restored_snapshot(exported)
    decrypted = json.loads(isolated_config_store._cipher().decrypt(encrypted))
    assert decrypted == normalized == exported

    isolated_config_store.activate_restored_snapshot(normalized)
    assert isolated_config_store.get_env("OPENAI_API_KEY") == "changed-only-in-snapshot"
    assert isolated_config_store.get_setting("app_language") == "en"


def test_unknown_model_context_uses_smallest_known_candidate_window(monkeypatch):
    from cyrene.runtime import config_store

    models = [
        {"model": "unknown-custom"},
        {"model": "deepseek-v4-flash", "ctx": "1M"},
        {"model": "google/gemma-4-12b-qat", "ctx": "200K"},
    ]
    monkeypatch.setattr(config_store, "get_models", lambda: models)

    assert config_store.effective_ctx_limit_for_model("unknown-custom") == 200_000


def test_explicit_model_context_is_not_reduced_by_fallbacks(monkeypatch):
    from cyrene.runtime import config_store

    models = [
        {"model": "primary", "ctx": "500K"},
        {"model": "backup", "ctx": "200K"},
    ]
    monkeypatch.setattr(config_store, "get_models", lambda: models)

    assert config_store.effective_ctx_limit_for_model("primary") == 500_000


def test_known_model_default_is_not_reduced_by_fallbacks(monkeypatch):
    from cyrene.runtime import config_store

    models = [
        {"model": "mimo-v2.5", "ctx": ""},
        {"model": "google/gemma-4-12b-qat", "ctx": "200K"},
    ]
    monkeypatch.setattr(config_store, "get_models", lambda: models)

    assert config_store.effective_ctx_limit_for_model("mimo-v2.5") == 1_000_000


def test_unknown_context_preserves_zero_without_known_candidates():
    from cyrene.runtime import config_store

    assert config_store.effective_ctx_limit_for_model("custom", []) == 0


def test_parallel_model_settings_migrate_from_legacy_candidate_order(
    isolated_config_store,
):
    custom = {
        "id": "custom-primary",
        "model": "deepseek-chat",
        "provider": "openai_compatible",
    }
    codex = {
        "id": "codex-primary",
        "model": "gpt-5.6-sol",
        "provider": "codex_oauth",
    }
    isolated_config_store._cache = {
        "env": {},
        "settings": {"models": [codex, custom]},
    }

    assert isolated_config_store.get_model_source() == "codex"
    assert isolated_config_store.get_codex_model() == codex
    assert isolated_config_store.get_custom_models() == [custom]


def test_parallel_model_settings_are_saved_independently(isolated_config_store):
    custom = [{"model": "deepseek-chat", "provider": "openai_compatible"}]
    codex = {"model": "gpt-5.6-sol", "provider": "codex_oauth"}

    isolated_config_store.save_custom_models(custom)
    isolated_config_store.save_codex_model(codex)
    isolated_config_store.save_model_source("codex")

    assert isolated_config_store.get_custom_models() == custom
    assert isolated_config_store.get_codex_model() == codex
    assert isolated_config_store.get_model_source() == "codex"
