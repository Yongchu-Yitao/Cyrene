from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(monkeypatch):
    from cyrene import settings_store
    from webui import routes

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

    app = FastAPI()
    routes.register_routes(app, bot=None, db_path="test.db")
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
        "skill_tools",
        "integration_tools",
    ]
    groups = payload["tool_groups"]
    assert len(groups) == 12
    assert all(item["kind"] == "package" for item in groups)
    browser = next(
        item for item in payload["packages"]
        if item["id"] == "browser_tools"
    )
    assert browser["enabled"] is False
    assert browser["enabled_count"] == 0

    tools = {item["name"]: item for item in payload["tools"]}
    assert tools["AnalyzeAttachment"]["package_id"] == "direct_tools"
    assert tools["AnalyzeAttachment"]["effective_enabled"] is True
    assert tools["browser_navigate"]["configured_enabled"] is False
    assert tools["browser_navigate"]["effective_enabled"] is False
    assert tools["SearchKnowledge"]["package_id"] == "knowledge_tools"


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
