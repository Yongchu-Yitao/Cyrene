from fastapi import FastAPI
from fastapi.testclient import TestClient

from route.registry import register_routes


def _client(monkeypatch, tmp_path):
    from cyrene.runtime import settings_service, settings_store

    state = {
        "packs": {"cyrene_browser": False},
        "plugins": {"browser_navigate": False},
        "saved_packs": [],
        "saved_plugins": [],
    }

    monkeypatch.setattr(
        settings_store,
        "get_enabled_plugin_packs",
        lambda: dict(state["packs"]),
    )
    monkeypatch.setattr(
        settings_store,
        "is_plugin_pack_enabled",
        lambda name: state["packs"].get(name, True),
    )
    monkeypatch.setattr(
        settings_store,
        "save_enabled_plugin_packs",
        lambda value: (
            state["saved_packs"].append(dict(value)),
            state.__setitem__("packs", dict(value)),
        ),
    )
    monkeypatch.setattr(
        settings_store,
        "get_enabled_plugins",
        lambda: dict(state["plugins"]),
    )
    monkeypatch.setattr(
        settings_store,
        "save_enabled_plugins",
        lambda value: state["saved_plugins"].append(dict(value)),
    )

    def fake_update(_namespace, changes, **_kwargs):
        if "enabled_plugin_packs" in changes:
            value = {**state["packs"], **dict(changes["enabled_plugin_packs"])}
            state["saved_packs"].append(value)
            state["packs"] = value
        if "enabled_plugins" in changes:
            value = {**state["plugins"], **dict(changes["enabled_plugins"])}
            state["saved_plugins"].append(value)
            state["plugins"] = value
        return {
            "revision": 1,
            "apply_mode": "next_run",
            "changed": list(changes),
            "diff": {},
        }

    monkeypatch.setattr(settings_service, "update", fake_update)

    app = FastAPI()
    register_routes(app, bot=None, db_path=str(tmp_path / "cyrene.db"))
    return TestClient(app), state


def test_settings_api_exposes_registry_packs_and_standalone_plugins(monkeypatch, tmp_path):
    client, _state = _client(monkeypatch, tmp_path)

    response = client.get("/api/plugins")

    assert response.status_code == 200
    payload = response.json()
    package_ids = {item["id"] for item in payload["packs"]}
    assert {"core", "cyrene_browser", "cyrene_memory"} <= package_ids
    browser = next(
        item for item in payload["packs"]
        if item["id"] == "cyrene_browser"
    )
    assert browser["configured_enabled"] is False
    assert browser["enabled_count"] == 0
    core = next(item for item in payload["packs"] if item["id"] == "core")
    assert core["locked"] is True
    assert core["source"] == "core"

    plugins = {item["name"]: item for item in payload["plugins"]}
    assert plugins["toolbox"]["pack_id"] == "core"
    assert plugins["toolbox"]["locked"] is True
    assert plugins["browser_navigate"]["pack_id"] == "cyrene_browser"
    assert plugins["browser_navigate"]["effective_enabled"] is False
    standalone = {item["name"] for item in payload["standalone_plugins"]}
    assert {"Edit", "Glob", "Grep"} <= standalone


def test_settings_api_updates_package_atomically(monkeypatch, tmp_path):
    client, state = _client(monkeypatch, tmp_path)

    response = client.put(
        "/api/plugins/activation",
        json={"packs": {"cyrene_browser": True}},
    )

    assert response.status_code == 200
    browser = next(
        item for item in response.json()["packs"]
        if item["id"] == "cyrene_browser"
    )
    assert browser["configured_enabled"] is True
    assert state["packs"]["cyrene_browser"] is True
    assert state["saved_packs"] == [{"cyrene_browser": True}]
    assert state["saved_plugins"] == []


def test_settings_api_rejects_invalid_package_without_partial_save(
    monkeypatch, tmp_path,
):
    client, state = _client(monkeypatch, tmp_path)

    unknown = client.put(
        "/api/plugins/activation",
        json={
            "plugins": {"Read": False},
            "packs": {"not_a_package": True},
        },
    )
    non_boolean = client.put(
        "/api/plugins/activation",
        json={"packs": {"cyrene_browser": "false"}},
    )

    assert unknown.status_code == 400
    assert non_boolean.status_code == 400
    assert state["saved_packs"] == []
    assert state["saved_plugins"] == []
