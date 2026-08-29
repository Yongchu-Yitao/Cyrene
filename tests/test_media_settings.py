from __future__ import annotations

import json
import threading

import pytest
from fastapi import APIRouter


@pytest.fixture
def isolated_config_store(tmp_path, monkeypatch):
    from cyrene.runtime import config_store

    monkeypatch.setattr(config_store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config_store, "_ENCRYPTED_PATH", tmp_path / "config.enc")
    monkeypatch.setattr(config_store, "_KEY_PATH", tmp_path / ".config_key")
    monkeypatch.setattr(config_store, "_cache", None)
    monkeypatch.setattr(config_store, "_fernet", None)
    monkeypatch.setattr(config_store, "_initialized", False)
    return config_store


def test_media_settings_redact_preserve_clear_secrets_and_enforce_cas(
    isolated_config_store,
):
    from cyrene.plugins.builtin.cyrene_media.settings import (
        get_media_settings,
        merge_media_settings_update,
        public_media_settings,
    )

    secret = "sk-media-secret-value"
    merge_media_settings_update(
        {
            "max_parallel_jobs": 4,
            "providers": {
                "openai": {
                    "enabled": True,
                    "api_key": secret,
                    "image_model": "gpt-image-2",
                }
            },
        },
        expected_revision=0,
    )
    assert isolated_config_store.get_settings_revision() == 1
    assert get_media_settings()["providers"]["openai"]["api_key"] == secret

    public = public_media_settings()
    assert public["providers"]["openai"]["api_key_configured"] is True
    assert "api_key" not in public["providers"]["openai"]
    assert secret not in json.dumps(public, ensure_ascii=False)
    assert public["completion_behavior"] == "attach_then_wake_agent"

    # Leaving the write-only field blank preserves the existing credential.
    merge_media_settings_update(
        {"providers": {"openai": {"enabled": False, "api_key": ""}}},
        expected_revision=1,
    )
    assert get_media_settings()["providers"]["openai"]["api_key"] == secret
    assert isolated_config_store.get_settings_revision() == 2

    merge_media_settings_update(
        {"providers": {"openai": {"clear_api_key": True}}},
        expected_revision=2,
    )
    assert get_media_settings()["providers"]["openai"]["api_key"] == ""
    assert public_media_settings()["providers"]["openai"]["api_key_configured"] is False
    assert isolated_config_store.get_settings_revision() == 3

    with pytest.raises(isolated_config_store.SettingsRevisionConflict) as conflict:
        merge_media_settings_update(
            {"max_parallel_jobs": 8},
            expected_revision=2,
        )
    assert conflict.value.expected == 2
    assert conflict.value.actual == 3
    assert get_media_settings()["max_parallel_jobs"] == 4
    assert isolated_config_store.get_settings_revision() == 3


def test_media_settings_use_current_safe_defaults_and_closed_provider_schema(
    isolated_config_store,
):
    from cyrene.plugins.builtin.cyrene_media.settings import get_media_settings, merge_media_settings_update

    defaults = get_media_settings()
    assert defaults["max_download_mb"] == 256
    assert defaults["providers"]["comfyui"]["upload_tool"] == "upload_file"
    assert defaults["providers"]["comfyui"]["confirm_spend"] is False
    assert defaults["providers"]["minimax"]["video_model"] == "MiniMax-H3"
    assert defaults["providers"]["google"]["video_model"] == "gemini-omni-flash-preview"

    with pytest.raises(ValueError, match="unsupported openai provider setting: headers"):
        merge_media_settings_update(
            {
                "providers": {
                    "openai": {
                        "headers": {"Authorization": "Bearer must-not-be-stored"},
                    },
                },
            }
        )
    assert isolated_config_store.get_settings_revision() == 0

    with pytest.raises(ValueError, match="unsupported media provider"):
        merge_media_settings_update({"providers": {"custom": {"enabled": True}}})
    assert isolated_config_store.get_settings_revision() == 0


def test_media_settings_accept_google_reference_upload_timeout(
    isolated_config_store,
):
    from cyrene.plugins.builtin.cyrene_media.settings import get_media_settings, merge_media_settings_update

    merge_media_settings_update(
        {
            "providers": {"google": {"upload_timeout_seconds": 900}},
        }
    )

    assert get_media_settings()["providers"]["google"]["upload_timeout_seconds"] == 900

    with pytest.raises(ValueError, match="upload_timeout_seconds must be a number"):
        merge_media_settings_update({"providers": {"google": {"upload_timeout_seconds": float("nan")}}})


@pytest.mark.parametrize(
    ("base_url", "message"),
    [
        ("https:///v1", "hostname"),
        ("https://user:pass@example.com/v1", "credentials"),
        ("https://example.com/v1?token=value", "query or fragment"),
        ("https://example.com/v1#private", "query or fragment"),
        ("https://example.com:invalid/v1", "invalid port"),
    ],
)
def test_media_settings_reject_unsafe_or_ambiguous_base_urls(
    isolated_config_store,
    base_url,
    message,
):
    from cyrene.plugins.builtin.cyrene_media.settings import merge_media_settings_update

    with pytest.raises(ValueError, match=message):
        merge_media_settings_update(
            {
                "providers": {"openai": {"base_url": base_url}},
            }
        )
    assert isolated_config_store.get_settings_revision() == 0


def test_media_settings_recursive_redaction_and_legacy_field_filtering(
    isolated_config_store,
):
    from cyrene.plugins.builtin.cyrene_media.settings import (
        get_media_settings,
        public_media_settings,
        redact_media_secrets,
    )

    isolated_config_store.update_settings_atomic(
        {
            "media": {
                "providers": {
                    "openai": {
                        "api_key": "stored-api-key",
                        "headers": {
                            "Authorization": "Bearer nested-secret",
                            "X-Access-Token": "nested-token",
                        },
                        "nested_secret": {"private_key": "private-material"},
                    },
                },
            },
        }
    )
    stored_view = get_media_settings()
    assert "headers" not in stored_view["providers"]["openai"]
    assert "nested_secret" not in stored_view["providers"]["openai"]

    public = public_media_settings(stored_view)
    encoded = json.dumps(public, ensure_ascii=False)
    assert "stored-api-key" not in encoded
    assert public["providers"]["openai"]["api_key_configured"] is True

    redacted = redact_media_secrets(
        {
            "safe": [
                {
                    "accessToken": "token-value",
                    "Authorization": "Bearer value",
                    "signing_key": "key-value",
                    "api_key_configured": True,
                }
            ],
        }
    )
    encoded = json.dumps(redacted, ensure_ascii=False)
    assert "token-value" not in encoded
    assert "Bearer value" not in encoded
    assert "key-value" not in encoded
    assert redacted["safe"][0]["api_key_configured"] is True


def test_media_status_projection_is_an_explicit_public_shape_without_paths_or_errors():
    from cyrene.plugins.builtin.cyrene_media.routes import _public_batch, _public_daemon_status, _public_job

    raw_job = {
        "job_id": "job-1",
        "batch_id": "batch-1",
        "chat_id": "chat-1",
        "project_id": "project-1",
        "kind": "video",
        "provider": "google",
        "model": "veo-3.1-generate-preview",
        "status": "failed",
        "progress": "failed\nwith a second line",
        "error": "raw-provider-error-secret",
        "error_code": "provider_failed",
        "delivery_error": "delivery-error-secret",
        "lease_token": "lease-secret",
        "provider_state": {"access_token": "state-secret"},
        "provider_metadata": {"Authorization": "metadata-secret"},
        "request": {
            "kind": "video",
            "prompt": "a public prompt",
            "reference_paths": ["/Users/private/reference.png"],
            "mask_path": "/Users/private/mask.png",
            "parameters": {"api_key": "nested-request-secret"},
        },
        "attachments": [
            {
                "id": "output.mp4",
                "name": "output.mp4",
                "path": "/Users/private/output.mp4",
                "content_type": "video/mp4",
                "size": 42,
                "kind": "video",
                "url": "/api/chat/export/output.mp4",
                "access_token": "attachment-secret",
            }
        ],
    }
    public_job = _public_job(raw_job)
    assert public_job["progress"] == "failed with a second line"
    assert public_job["request"]["reference_count"] == 1
    assert public_job["request"]["has_mask"] is True
    assert "reference_paths" not in public_job["request"]
    assert "mask_path" not in public_job["request"]
    assert "parameters" not in public_job["request"]
    assert "path" not in public_job["attachments"][0]

    public_batch = _public_batch(
        {
            "batch_id": "batch-1",
            "wake_id": "wake-1",
            "chat_id": "chat-1",
            "project_id": "project-1",
            "idempotency_key": "idempotency-secret",
            "owner_tool_call_id": "owner-secret",
            "wake": {
                "wake_id": "wake-1",
                "batch_id": "batch-1",
                "status": "ready",
                "prompt": "wake-prompt-secret",
                "lease_token": "wake-lease-secret",
                "summary": {"batch_id": "batch-1", "succeeded": 0, "jobs": []},
            },
            "jobs": [raw_job],
        }
    )
    encoded = json.dumps(public_batch, ensure_ascii=False)
    for secret in (
        "raw-provider-error-secret",
        "delivery-error-secret",
        "lease-secret",
        "state-secret",
        "metadata-secret",
        "nested-request-secret",
        "/Users/private",
        "attachment-secret",
        "idempotency-secret",
        "owner-secret",
        "wake-prompt-secret",
        "wake-lease-secret",
    ):
        assert secret not in encoded

    assert _public_daemon_status(
        {
            "running": True,
            "worker_count": 2,
            "active_job_ids": ["job-1"],
            "counts": {"claimed": 1, "internal": 999},
            "authorization": "daemon-secret",
        }
    ) == {
        "running": True,
        "worker_count": 2,
        "active_job_ids": ["job-1"],
        "counts": {
            "queued": 0,
            "claimed": 1,
            "succeeded": 0,
            "failed": 0,
            "cancelled": 0,
        },
    }


@pytest.mark.asyncio
async def test_media_settings_get_route_offloads_synchronous_store_io(monkeypatch):
    from cyrene.plugins.builtin.cyrene_media import settings_routes as media_routes

    event_loop_thread = threading.get_ident()
    called_from: list[int] = []

    def fake_get_media_settings():
        called_from.append(threading.get_ident())
        return {}

    monkeypatch.setattr(media_routes, "get_media_settings", fake_get_media_settings)
    monkeypatch.setattr(media_routes, "get_revision", lambda: 7)
    router = APIRouter()
    media_routes.register_media_settings_routes(router)
    endpoint = next(route.endpoint for route in router.routes if getattr(route, "path", "") == "/api/settings/media" and "GET" in getattr(route, "methods", set()))

    response = await endpoint()
    assert response["revision"] == 7
    assert called_from
    assert all(thread_id != event_loop_thread for thread_id in called_from)
