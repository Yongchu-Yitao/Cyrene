"""Focused coverage for Cyrene's explicit shared proxy policy."""

from __future__ import annotations

import pytest


def test_model_client_ignores_implicit_proxy_and_uses_only_opted_in_proxy(
    monkeypatch,
):
    from cyrene.core.plugin import PluginContext
    from cyrene.platform import network_proxy
    from cyrene.plugins.builtin.cyrene_model._shared import (
        ModelProvider,
        _client_options,
    )

    provider = ModelProvider(
        id="custom",
        name="Custom",
        plugin_name="Custom",
        adapter="openai_compatible",
        default_base_url="https://custom.test/v1",
    )
    direct = _client_options(
        PluginContext(data={"model_connection": {"use_proxy": False}}),
        provider,
        discovery=True,
    )

    assert direct["trust_env"] is False
    assert "proxy" not in direct

    monkeypatch.setattr(
        network_proxy,
        "configured_proxy_url",
        lambda *, opt_in: "http://127.0.0.1:6578" if opt_in else "",
    )
    proxied = _client_options(
        PluginContext(data={"model_connection": {"use_proxy": True}}),
        provider,
        discovery=True,
    )

    assert proxied["trust_env"] is False
    assert proxied["proxy"] == "http://127.0.0.1:6578"


def test_proxy_master_and_feature_scopes_are_both_required(monkeypatch):
    from cyrene.platform import config_store, network_proxy

    values = {
        "external_agent_proxy_enabled": False,
        "external_agent_proxy_port": 7897,
        "proxy_search_enabled": True,
        "proxy_browser_enabled": False,
        "proxy_extensions_enabled": True,
    }
    monkeypatch.setattr(
        config_store,
        "get_setting",
        lambda key, default=None: values.get(key, default),
    )

    assert network_proxy.configured_proxy_url() == ""
    assert network_proxy.scoped_proxy_url("search") == ""

    values["external_agent_proxy_enabled"] = True
    assert network_proxy.configured_proxy_url() == "http://127.0.0.1:7897"
    assert network_proxy.scoped_proxy_url("search") == "http://127.0.0.1:7897"
    assert network_proxy.scoped_proxy_url("browser") == ""
    assert network_proxy.scoped_proxy_url("extensions") == "http://127.0.0.1:7897"


def test_proxy_policy_rejects_invalid_ports_and_builds_process_environment(monkeypatch):
    from cyrene.platform import config_store, network_proxy

    values = {
        "external_agent_proxy_enabled": True,
        "external_agent_proxy_port": 6578,
    }
    monkeypatch.setattr(
        config_store,
        "get_setting",
        lambda key, default=None: values.get(key, default),
    )

    environment = network_proxy.proxy_environment()
    assert environment["HTTPS_PROXY"] == "http://127.0.0.1:6578"
    assert environment["all_proxy"] == "http://127.0.0.1:6578"
    assert environment["NO_PROXY"] == "127.0.0.1,localhost,::1"

    values["external_agent_proxy_port"] = 70000
    assert network_proxy.configured_proxy_url() == ""
    assert network_proxy.proxy_environment() == {}


def test_custom_proxy_address_overrides_legacy_localhost_port(monkeypatch):
    from cyrene.platform import config_store, network_proxy

    values = {
        "external_agent_proxy_enabled": True,
        "external_agent_proxy_url": "proxy.example.com:8080",
        "external_agent_proxy_port": 6578,
    }
    monkeypatch.setattr(
        config_store,
        "get_setting",
        lambda key, default=None: values.get(key, default),
    )

    assert network_proxy.configured_proxy_url() == "http://proxy.example.com:8080"
    assert network_proxy.proxy_environment()["HTTPS_PROXY"] == "http://proxy.example.com:8080"

    values["external_agent_proxy_url"] = "http://user:secret@proxy.example.com:8080"
    assert network_proxy.configured_proxy_url() == ""


def test_proxy_address_setting_is_validated_and_canonicalized():
    from cyrene.platform.settings_service import (
        SettingsValidationError,
        _normalize,
        plugin_setting_spec,
    )

    spec = plugin_setting_spec(
        "external_agent_proxy_url", "string", "", tab="general"
    )
    assert _normalize(spec, "proxy.example.com:8080") == "http://proxy.example.com:8080"

    with pytest.raises(SettingsValidationError):
        _normalize(spec, "http://user:secret@proxy.example.com:8080")


def test_config_projection_exposes_proxy_feature_scopes(monkeypatch):
    from cyrene.platform import config_store
    from cyrene.platform.config_integration_service import (
        ConfigIntegrationApplicationService,
    )

    class Query:
        def config(self):
            return {
                "external_agent_proxy_enabled": True,
                "external_agent_proxy_port": 7897,
                "external_agent_proxy_url": "http://proxy.example.com:8080",
                "proxy_search_enabled": True,
                "proxy_browser_enabled": False,
                "proxy_extensions_enabled": True,
            }

    values = {
        "external_agent_proxy_url": "http://proxy.example.com:8080",
        "proxy_search_enabled": True,
        "proxy_browser_enabled": False,
        "proxy_extensions_enabled": True,
    }
    monkeypatch.setattr(
        config_store,
        "get_setting",
        lambda key, default=None: values.get(key, default),
    )

    async def publish(*_args):
        return None

    payload = ConfigIntegrationApplicationService(Query(), publish).config()
    assert payload["external_agent_proxy_url"] == "http://proxy.example.com:8080"
    assert payload["proxy_search_enabled"] is True
    assert payload["proxy_browser_enabled"] is False
    assert payload["proxy_extensions_enabled"] is True
