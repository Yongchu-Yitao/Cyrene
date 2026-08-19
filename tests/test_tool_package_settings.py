from fastapi import FastAPI
from fastapi.testclient import TestClient

from route.registry import register_routes


def _client(monkeypatch):
    from cyrene.runtime import settings_service, settings_store

    state = {
        "packages": {"browser_tools": False},
        "tools": {"browser_navigate": False},
        "saved_packages": [],
        "saved_tools": [],
    }

    monkeypatch.setattr(
        settings_store,
        "get_enabled_tool_packs",
        lambda: dict(state["packages"]),
    )
    monkeypatch.setattr(
        settings_store,
        "is_tool_pack_enabled",
        lambda name: state["packages"].get(name, True),
    )
    monkeypatch.setattr(
        settings_store,
        "save_enabled_tool_packs",
        lambda value: (
            state["saved_packages"].append(dict(value)),
            state.__setitem__("packages", dict(value)),
        ),
    )
    monkeypatch.setattr(
        settings_store,
        "get_enabled_tools",
        lambda: dict(state["tools"]),
    )
    monkeypatch.setattr(
        settings_store,
        "save_enabled_tools",
        lambda value: state["saved_tools"].append(dict(value)),
    )

    def fake_update(_namespace, changes, **_kwargs):
        if "enabled_tool_packs" in changes:
            value = dict(changes["enabled_tool_packs"])
            state["saved_packages"].append(value)
            state["packages"] = value
        if "enabled_tools" in changes:
            value = dict(changes["enabled_tools"])
            state["saved_tools"].append(value)
            state["tools"] = value
        return {
            "revision": 1,
            "apply_mode": "next_run",
            "changed": list(changes),
            "diff": {},
        }

    monkeypatch.setattr(settings_service, "update", fake_update)

    app = FastAPI()
    register_routes(app, bot=None, db_path="test.db")
    return TestClient(app), state


def test_settings_api_exposes_stable_package_groups(monkeypatch):
    client, _state = _client(monkeypatch)

    response = client.get("/api/settings/tools")

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload["packages"]] == [
        "code_tools",
        "browser_tools",
        "desktop_tools",
        "memory_tools",
        "knowledge_tools",
        "task_tools",
        "entity_tools",
        "map_tools",
        "subagent_tools",
        "delivery_tools",
        "environment_tools",
        "skill_tools",
        "remote_tools",
        "cyrene_tools",
        "integration_tools",
        "custom_tools",
    ]
    groups = payload["tool_groups"]
    assert len(groups) == 16
    assert all(item["kind"] == "package" for item in groups)
    browser = next(
        item for item in payload["packages"]
        if item["id"] == "browser_tools"
    )
    assert browser["enabled"] is False
    assert browser["enabled_count"] == 0
    custom = next(
        item for item in payload["packages"]
        if item["id"] == "custom_tools"
    )
    # Existing settings files predate this package key. Missing means enabled,
    # preserving the global default during migration.
    assert custom["enabled"] is True
    assert custom["source"] == "custom"

    tools = {item["name"]: item for item in payload["tools"]}
    assert tools["AnalyzeAttachment"]["package_id"] == "direct_tools"
    assert tools["AnalyzeAttachment"]["effective_enabled"] is True
    assert tools["browser_navigate"]["configured_enabled"] is False
    assert tools["browser_navigate"]["effective_enabled"] is False
    assert tools["SearchKnowledge"]["package_id"] == "knowledge_tools"
    assert tools["ListEnvironment"]["package_id"] == "environment_tools"
    assert tools["SearchEnvironment"]["package_id"] == "environment_tools"


def test_settings_api_updates_package_atomically(monkeypatch):
    client, state = _client(monkeypatch)

    response = client.put(
        "/api/settings/tools",
        json={"packages": {"browser_tools": True}},
    )

    assert response.status_code == 200
    assert response.json()["updated_packages"] == ["browser_tools"]
    assert state["packages"]["browser_tools"] is True
    assert state["saved_packages"] == [{"browser_tools": True}]
    assert state["saved_tools"] == []


def test_settings_api_rejects_invalid_package_without_partial_save(
    monkeypatch,
):
    client, state = _client(monkeypatch)

    unknown = client.put(
        "/api/settings/tools",
        json={
            "tools": {"Read": False},
            "packages": {"not_a_package": True},
        },
    )
    non_boolean = client.put(
        "/api/settings/tools",
        json={"packages": {"browser_tools": "false"}},
    )

    assert unknown.status_code == 400
    assert non_boolean.status_code == 400
    assert state["saved_packages"] == []
    assert state["saved_tools"] == []
