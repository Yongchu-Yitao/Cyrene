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


def test_restore_drops_removed_configuration_keys(isolated_config_store):
    normalized, _encrypted = isolated_config_store.prepare_restored_snapshot({
        "env": {
            "OPENAI_MODEL": "example-model",
        },
        "settings": {"budget_mode": "economy", "budget_enabled": True},
    })

    assert normalized["env"] == {}
    assert normalized["settings"] == {"budget_enabled": True}
