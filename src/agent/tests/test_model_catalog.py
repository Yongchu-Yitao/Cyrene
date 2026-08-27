from __future__ import annotations

from agent.plugin import model_catalog


def test_configured_candidates_honor_session_selection_and_endpoint_affinity(
    monkeypatch,
):
    from cyrene.runtime import model_configuration, settings_store

    primary = {
        "id": "primary",
        "provider": "openai",
        "adapter": "openai",
        "model": "primary-model",
        "base_url": "https://primary.example/v1",
    }
    selected = {
        "id": "selected",
        "provider": "openai",
        "adapter": "openai",
        "model": "selected-model",
        "base_url": "https://selected.example/v1",
    }
    monkeypatch.setattr(
        model_configuration,
        "candidates_for_route",
        lambda route: [primary, selected] if route == "primary" else [],
    )
    settings = {
        "llm_session_model_preferences": {
            "chat-1": {
                "candidate_id": "selected",
                "adapter": "openai",
                "model": "selected-model",
                "base_url": "https://selected.example/v1",
                "reasoning_effort": "high",
            }
        },
        "llm_last_success_endpoints": {
            "session:chat-1:primary": {
                "candidate_id": "selected",
                "adapter": "openai",
                "model": "selected-model",
                "base_url": "https://selected.example/v1",
                "endpoint": "https://selected.example/v1/responses",
            }
        },
    }
    monkeypatch.setattr(
        settings_store,
        "get",
        lambda key, default=None: settings.get(key, default),
    )

    candidates = model_catalog.configured_model_candidates("chat-1")

    assert [candidate["id"] for candidate in candidates] == ["selected", "primary"]
    assert candidates[0]["reasoning_effort"] == "high"
    assert candidates[0]["preferred_endpoint"] == (
        "https://selected.example/v1/responses"
    )


def test_candidate_identity_never_exposes_credentials_or_paths():
    identity = model_catalog.candidate_identity(
        {
            "id": "candidate",
            "provider": "openai",
            "adapter": "openai",
            "model": "gpt-test",
            "base_url": "https://user:secret@example.test/private/v1?token=secret",
            "options": {"provider_preset": "openai"},
        },
        endpoint="https://user:secret@example.test/private/v1/responses?token=secret",
    )

    assert identity["baseUrl"] == "https://example.test"
    assert identity["endpoint"] == "https://example.test"
    assert "secret" not in str(identity)
