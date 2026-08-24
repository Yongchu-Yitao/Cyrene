"""Focused coverage for Cyrene's explicit shared proxy policy."""

from __future__ import annotations

import httpx
import pytest


def test_proxy_master_and_feature_scopes_are_both_required(monkeypatch):
    from cyrene.runtime import config_store, network_proxy

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
    from cyrene.runtime import config_store, network_proxy

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
    from cyrene.runtime import config_store, network_proxy

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
    from cyrene.runtime.settings_service import (
        SettingsValidationError,
        validate_changes,
    )

    normalized, _specs = validate_changes(
        "runtime",
        {"external_agent_proxy_url": "proxy.example.com:8080"},
        actor="ui",
    )
    assert normalized["external_agent_proxy_url"] == "http://proxy.example.com:8080"

    with pytest.raises(SettingsValidationError):
        validate_changes(
            "runtime",
            {"external_agent_proxy_url": "http://user:secret@proxy.example.com:8080"},
            actor="ui",
        )


def test_config_projection_exposes_proxy_feature_scopes(monkeypatch):
    from cyrene.runtime import config_store
    from cyrene.runtime.config_integration_service import (
        ConfigIntegrationApplicationService,
    )

    class Query:
        def config(self):
            return {
                "external_agent_proxy_enabled": True,
                "external_agent_proxy_port": 7897,
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


@pytest.mark.asyncio
async def test_model_http_pool_is_separated_by_proxy(monkeypatch):
    from cyrene.model_runtime import client as model_client

    transport_options = []

    class CapturingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):  # pragma: no cover - no I/O
            raise AssertionError(f"unexpected request: {request.url}")

    def transport_factory(**kwargs):
        transport_options.append(kwargs)
        return CapturingTransport()

    monkeypatch.setattr(model_client.httpx, "AsyncHTTPTransport", transport_factory)
    model_client._http_clients.clear()
    try:
        proxied, proxied_key, reused = model_client._get_http_client(
            20.0, "http://127.0.0.1:7897"
        )
        same_proxied, same_key, same_reused = model_client._get_http_client(
            20.0, "http://127.0.0.1:7897"
        )
        direct, direct_key, direct_reused = model_client._get_http_client(20.0)

        assert transport_options[0]["proxy"] == "http://127.0.0.1:7897"
        assert transport_options[1]["proxy"] is None
        assert same_proxied is proxied
        assert direct is not proxied
        assert same_key == proxied_key
        assert ":proxy:configured" in proxied_key
        assert ":proxy:configured" not in direct_key
        assert reused is False
        assert same_reused is True
        assert direct_reused is False
    finally:
        clients = [
            value[1]
            for per_loop in model_client._http_clients.values()
            for value in per_loop.values()
        ]
        model_client._http_clients.clear()
        for client in clients:
            await client.aclose()
