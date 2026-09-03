"""Tests for the Agent package, kept outside the shipped source tree."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI

from cyrene.core.plugin import (
    Plugin,
    PluginPack,
    PluginRegistry,
)
from cyrene.plugins import model_catalog
from cyrene.plugins import PluginApplicationHost, set_application_plugin_scope


def test_configured_candidates_honor_session_selection_and_endpoint_affinity(
    monkeypatch,
):
    from cyrene.plugins.builtin.cyrene_model import configuration as model_configuration
    from cyrene.platform import settings_store

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
    # Candidate resolution is an application service in the new Plugin
    # protocol; this unit test supplies that service explicitly.
    monkeypatch.setattr(model_catalog, "_model_configuration_port", lambda: model_configuration)

    candidates = model_catalog.configured_model_candidates("chat-1")

    assert [candidate["id"] for candidate in candidates] == ["selected", "primary"]
    assert candidates[0]["reasoning_effort"] == "high"
    assert candidates[0]["preferred_endpoint"] == (
        "https://selected.example/v1/responses"
    )


def test_configured_candidates_prepend_session_selection_outside_primary_route(
    monkeypatch,
):
    from cyrene.platform import settings_store

    primary = {
        "id": "primary",
        "provider": "openai",
        "adapter": "openai",
        "model": "primary-model",
        "base_url": "https://primary.example/v1",
        "capabilities": ["chat"],
    }
    selected = {
        "id": "selected",
        "provider": "openai",
        "adapter": "openai",
        "model": "selected-model",
        "base_url": "https://selected.example/v1",
        "capabilities": ["chat", "vision"],
    }
    service = SimpleNamespace(
        candidates_for_route=lambda route: [primary] if route == "primary" else [],
        candidate_for_profile=lambda profile_id: (
            selected if profile_id == "selected" else None
        ),
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
    }
    monkeypatch.setattr(
        settings_store,
        "get",
        lambda key, default=None: settings.get(key, default),
    )
    monkeypatch.setattr(model_catalog, "_model_configuration_port", lambda: service)

    candidates = model_catalog.configured_model_candidates("chat-1")

    assert [candidate["id"] for candidate in candidates] == [
        "selected",
        "primary",
    ]
    assert candidates[0]["_session_selected"] is True
    assert candidates[0]["reasoning_effort"] == "high"
    assert candidates[1].get("_session_selected") is not True


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


def _provider(name: str = "ProviderOne") -> Plugin:
    return Plugin(
        name=name,
        description="provider",
        input_schema={"type": "object", "additionalProperties": True},
        handler=lambda _arguments, _context: {},
        kind="model",
        metadata={"provider": {"id": "provider_one", "name": "Provider one"}},
    )


def test_openai_adapter_without_provider_preset_uses_compatible_plugin():
    compatible = Plugin(
        name="OpenAICompatible",
        description="generic OpenAI-compatible provider",
        input_schema={"type": "object", "additionalProperties": True},
        handler=lambda _arguments, _context: {},
        kind="model",
        metadata={
            "provider": {
                "id": "openai_compatible",
                "name": "OpenAI Compatible",
            }
        },
    )
    registry = PluginRegistry(include_core=False)
    registry.register_plugin(compatible, source="test")
    candidate = {
        "provider": "openai",
        "provider_preset": "",
        "adapter": "openai",
    }

    provider_id = model_catalog.candidate_provider_id(candidate)
    resolved = model_catalog.resolve_registered_model_plugin(
        registry,
        provider_id,
        candidate["adapter"],
    )

    assert provider_id == ""
    assert resolved is not None
    assert resolved.name == "OpenAICompatible"
    assert resolved.metadata["provider"]["id"] == "openai_compatible"
    assert model_catalog.candidate_provider_id({
        "provider": "openai",
        "adapter": "openai",
    }) == "openai"


def test_model_catalog_uses_active_registry_and_honors_provider_activation(tmp_path):
    registry = PluginRegistry(include_core=False)
    registry.register_pack(
        PluginPack(
            id="model_pack",
            description="models",
            plugins=(_provider(),),
        ),
        source="test",
    )
    host = PluginApplicationHost(
        app=FastAPI(),
        registry=registry,
        bot=None,
        db_path=str(tmp_path / "app.db"),
        data_directory=tmp_path / "data",
        plugin_directory=tmp_path / "plugins",
    )
    set_application_plugin_scope(host)
    try:
        assert [item["id"] for item in model_catalog.model_plugin_catalog()] == [
            "provider_one"
        ]
        registry.set_plugin_enabled("ProviderOne", False)
        assert model_catalog.model_plugin_catalog() == []
        registry.set_plugin_enabled("ProviderOne", True)
        registry.set_pack_enabled("model_pack", False)
        assert model_catalog.model_plugin_catalog() == []
    finally:
        set_application_plugin_scope(None)


def test_offline_model_registry_loads_persisted_activation_and_customization(
    tmp_path,
    monkeypatch,
):
    from cyrene.platform import settings_store

    root = tmp_path / "plugins"
    pack = root / "cyrene_model"
    pack.mkdir(parents=True)
    (pack / "__init__.py").write_text(
        """
from cyrene.core.plugin import Plugin, PluginPack

plugin = Plugin(
    name="OfflineProvider",
    description="offline",
    input_schema={"type": "object", "additionalProperties": True},
    handler=lambda arguments, context: {},
    kind="model",
    metadata={"provider": {"id": "offline", "name": "Offline"}},
)
plugin_pack = PluginPack(id="cyrene_model", description="models", plugins=(plugin,))
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(model_catalog, "seed_builtin_plugin_directory", lambda _root: None)
    monkeypatch.setattr(settings_store, "get_enabled_plugins", lambda: {})
    monkeypatch.setattr(
        settings_store,
        "get_enabled_plugin_packs",
        lambda: {"cyrene_model": False},
    )
    monkeypatch.setattr(
        settings_store,
        "get",
        lambda key, default=None: (
            {"OfflineProvider": {"deleted": True}}
            if key == "plugin_tool_customizations"
            else default
        ),
    )
    monkeypatch.setattr(model_catalog, "_CACHE_REGISTRY", None)

    registry, failures = model_catalog.editable_model_registry(root)

    assert failures == ()
    assert registry.list_plugins() == ()
    assert model_catalog.model_plugin_catalog(root) == []
