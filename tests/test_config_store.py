"""Tests for the encrypted canonical configuration store."""

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
    monkeypatch.setattr(config_store, "_cache", None)
    monkeypatch.setattr(config_store, "_fernet", None)
    monkeypatch.setattr(config_store, "_initialized", False)
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


def test_decryption_failure_preserves_existing_encrypted_config(
    isolated_config_store,
):
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


def test_removed_economy_mode_setting_is_purged_and_rejected(
    isolated_config_store,
):
    config = {
        "env": {},
        "settings": {"budget_mode": "economy", "budget_enabled": True},
    }
    _write_encrypted(isolated_config_store, config)

    loaded = isolated_config_store._ensure_loaded()

    assert "budget_mode" not in loaded["settings"]
    assert isolated_config_store.get_setting("budget_mode") is None
    assert isolated_config_store.get_setting("budget_enabled") is True
    with pytest.raises(ValueError, match="have been removed"):
        isolated_config_store.set_setting("budget_mode", "economy")


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
        "settings": {"budget_mode": "economy", "budget_enabled": True},
    })

    assert normalized["env"] == {}
    assert normalized["settings"] == {"budget_enabled": True}


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


def test_portable_snapshot_redacts_extension_credentials(isolated_config_store):
    isolated_config_store.set_setting("extension_sources", {"github_token": "github-secret"})
    isolated_config_store.set_setting("mcp_servers", [{
        "name": "remote", "headers": {"Authorization": "Bearer secret"},
        "env": {"API_KEY": "local-secret"},
    }])

    snapshot = isolated_config_store.export_snapshot()
    encoded = json.dumps(snapshot)
    assert "github-secret" not in encoded
    assert "Bearer secret" not in encoded
    assert "local-secret" not in encoded


def test_portable_snapshot_recursively_redacts_media_provider_credentials(
    isolated_config_store,
):
    media = {
        "max_parallel_jobs": 4,
        "default_providers": {"image": "openai", "video": "google"},
        "providers": {
            "openai": {
                "enabled": True,
                "api_key": "openai-secret",
                "base_url": "https://api.openai.com/v1",
                "image_model": "gpt-image-2",
                "transport": {
                    "headers": {
                        "Authorization": "Bearer header-secret",
                        "X-Trace-Id": "safe-trace-id",
                    },
                    "clientSecret": "oauth-client-secret",
                },
            },
            "custom": {
                "enabled": False,
                "nested": [
                    {
                        "access_token": "access-secret",
                        "refresh-token": "refresh-secret",
                        "region": "cn-beijing",
                    },
                    {"signingKey": "signing-secret", "timeout_seconds": 45},
                ],
                "api_key_configured": True,
                "api_key_requires_reentry": False,
            },
        },
    }
    isolated_config_store.set_setting("media", media)

    snapshot = isolated_config_store.export_snapshot()
    exported_media = snapshot["settings"]["media"]
    encoded = json.dumps(exported_media)

    for secret in (
        "openai-secret",
        "Bearer header-secret",
        "oauth-client-secret",
        "access-secret",
        "refresh-secret",
        "signing-secret",
    ):
        assert secret not in encoded
    assert exported_media["max_parallel_jobs"] == 4
    assert exported_media["default_providers"] == {
        "image": "openai",
        "video": "google",
    }
    assert exported_media["providers"]["openai"]["base_url"] == (
        "https://api.openai.com/v1"
    )
    assert exported_media["providers"]["openai"]["image_model"] == "gpt-image-2"
    assert (
        exported_media["providers"]["openai"]["transport"]["headers"]["X-Trace-Id"]
        == "safe-trace-id"
    )
    assert exported_media["providers"]["custom"]["nested"][0]["region"] == (
        "cn-beijing"
    )
    assert (
        exported_media["providers"]["custom"]["nested"][1]["timeout_seconds"]
        == 45
    )
    assert exported_media["providers"]["custom"]["api_key_configured"] is True
    assert exported_media["providers"]["custom"]["api_key_requires_reentry"] is False
    assert isolated_config_store.get_setting("media") == media
