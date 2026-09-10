from __future__ import annotations

import pytest


@pytest.mark.parametrize("engines", [None, [], ["google", "bing"]])
def test_search_settings_persist_order_switches_and_encrypted_keys(monkeypatch, engines):
    from cyrene.core.plugin import PluginRegistry
    from cyrene.plugins.builtin.cyrene_content import plugin_pack
    from cyrene.plugins.builtin.cyrene_content import search_settings

    registry = PluginRegistry()
    registry.register_pack(plugin_pack, source="test-content")

    state = {
        "search": {
            "provider_order": [],
            "provider_enabled": dict(search_settings.DEFAULT_PROVIDER_ENABLED),
        },
        "enabled_plugins": {"WebSearch": True},
    }
    env = {}
    revision = 4

    monkeypatch.setattr(
        search_settings.config_store,
        "get_setting",
        lambda key, default=None: state.get(key, default),
    )
    monkeypatch.setattr(
        search_settings.config_store,
        "get_enabled_plugins",
        lambda: dict(state["enabled_plugins"]),
    )
    monkeypatch.setattr(
        search_settings.config_store,
        "get_env",
        lambda key, default="": env.get(key, default),
    )
    monkeypatch.setattr(
        search_settings.config_store,
        "get_settings_revision",
        lambda: revision,
    )

    def update(settings_updates, env_updates, *, expected_revision=None):
        nonlocal revision
        assert expected_revision == revision
        state.update(settings_updates)
        env.update(env_updates)
        revision += 1
        return revision, dict(state)

    monkeypatch.setattr(
        search_settings.config_store,
        "update_settings_and_env_atomic",
        update,
    )

    payload = search_settings.update_settings(
        {
            "enabled": False,
            "expected_revision": 4,
            "providers": [
                {"id": "tavily", "enabled": True, "api_key": "tvly-secret"},
                {"id": "brave", "enabled": True, "api_key": "brave-secret"},
                {"id": "deepseek", "enabled": False},
                {"id": "simplexng", "enabled": True, "engines": engines},
            ],
        },
        canonical_name="WebSearch",
        registry=registry,
    )

    assert state["search"]["provider_order"] == [
        "tavily",
        "brave",
        "deepseek",
        "simplexng",
    ]
    assert state["search"]["simplexng_engines"] == engines
    assert payload["providers"][-1]["engines"] == engines
    assert search_settings.simplexng_engines() == engines
    assert state["enabled_plugins"]["WebSearch"] is False
    assert env == {
        "TAVILY_API_KEY": "tvly-secret",
        "BRAVE_SEARCH_API_KEY": "brave-secret",
    }
    assert payload["enabled"] is False
    assert payload["providers"][0]["id"] == "tavily"
    assert "tvly-secret" not in str(payload)
    assert "brave-secret" not in str(payload)


def test_search_settings_require_one_provider_when_search_is_enabled(monkeypatch):
    from cyrene.core.plugin import PluginRegistry
    from cyrene.plugins.builtin.cyrene_content import plugin_pack
    from cyrene.plugins.builtin.cyrene_content import search_settings

    registry = PluginRegistry()
    registry.register_pack(plugin_pack, source="test-content")
    rows = [{"id": provider, "enabled": False} for provider in search_settings.PROVIDER_IDS]

    with pytest.raises(search_settings.SearchSettingsError, match="at least one"):
        search_settings.update_settings(
            {"enabled": True, "providers": rows},
            canonical_name="WebSearch",
            registry=registry,
        )


async def test_search_settings_publish_realtime_change_after_save(monkeypatch):
    from cyrene.core.plugin import PluginRegistry
    from cyrene.plugins.builtin.cyrene_content import plugin_pack
    from cyrene.plugins.builtin.cyrene_content import search_settings

    registry = PluginRegistry()
    registry.register_pack(plugin_pack, source="test-content")

    monkeypatch.setattr(
        search_settings,
        "update_settings",
        lambda body, **_kwargs: {
            "ok": True,
            "revision": 9,
            "enabled": body["enabled"],
        },
    )
    events = []

    async def publish(namespace, revision, changed):
        events.append((namespace, revision, changed))

    service = search_settings.SearchSettingsApplicationService(
        registry,
        "WebSearch",
        publish,
    )

    result = await service.update_settings({"enabled": False})

    assert result["revision"] == 9
    assert events == [("search", 9, ["search", "enabled_plugins"])]


@pytest.mark.parametrize("value", ["google", [12], [""], ["google,bing"], ["bad\nname"], ["x"] * 301])
def test_search_engine_settings_reject_invalid_values(value):
    from cyrene.plugins.builtin.cyrene_content.search_settings import (
        SearchSettingsError, _normalize_engines,
    )

    with pytest.raises(SearchSettingsError):
        _normalize_engines(value)
