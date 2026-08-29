import copy
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from cyrene.plugins.builtin.cyrene_mcp import application
from cyrene.plugins.builtin.cyrene_mcp import service as service_module
from cyrene.plugins.builtin.cyrene_mcp.service import (
    MCPPluginService,
    MCPServerNotFoundError,
)


def _persisted_service(monkeypatch, tmp_path, initial):
    stored = copy.deepcopy(initial)
    restarts = []

    monkeypatch.setattr(
        service_module,
        "_load_configs",
        lambda: copy.deepcopy(stored),
    )

    def save(configs):
        stored[:] = copy.deepcopy(configs)

    monkeypatch.setattr(service_module, "_save_configs", save)
    service = MCPPluginService(data_directory=tmp_path)

    async def restart():
        restarts.append(True)

    monkeypatch.setattr(service, "restart", restart)
    return service, stored, restarts


@pytest.mark.asyncio
async def test_single_configuration_preserves_metadata_and_redacted_credentials(
    monkeypatch,
    tmp_path,
):
    existing = {
        "name": "docs",
        "transport": "streamable_http",
        "url": "https://old.example.test/mcp",
        "headers": {"Authorization": "Bearer secret", "X-Tenant": "old"},
        "enabled": True,
        "source": {"type": "mcp-registry-package", "id": "io.demo/docs"},
        "version": "1.4.2",
        "managed_package": {"registry": "npm", "name": "@demo/docs"},
    }
    service, stored, restarts = _persisted_service(
        monkeypatch,
        tmp_path,
        [existing],
    )
    monkeypatch.setattr(
        service,
        "server_status",
        lambda _name: {
            "status": "connected",
            "error": "",
            "pack_id": "mcp.docs",
            "tools": [{"name": "search", "plugin": "mcp__docs__search"}],
        },
    )

    result = await service.update_configuration(
        "docs",
        {
            "transport": "http",
            "url": "https://new.example.test/mcp",
            "headers": {
                "Authorization": "[configured]",
                "X-Tenant": "replacement",
            },
            "enabled": True,
        },
    )

    assert restarts == [True]
    assert stored[0]["name"] == "docs"
    assert stored[0]["transport"] == "streamable_http"
    assert stored[0]["headers"] == {
        "Authorization": "Bearer secret",
        "X-Tenant": "replacement",
    }
    assert stored[0]["source"] == existing["source"]
    assert stored[0]["version"] == "1.4.2"
    assert stored[0]["managed_package"] == existing["managed_package"]
    assert result == {
        "id": "docs",
        "config": {
            **stored[0],
            "headers": {
                "Authorization": "[configured]",
                "X-Tenant": "[configured]",
            },
        },
        "status": "connected",
        "error": "",
        "tools": [{"name": "search", "plugin": "mcp__docs__search"}],
        "tool_count": 1,
        "pack_id": "mcp.docs",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("existing", "update", "removed_fields"),
    (
        (
            {
                "name": "switcher",
                "transport": "streamable_http",
                "url": "https://example.test/mcp",
                "headers": {"Authorization": "secret"},
                "enabled": True,
                "source": {"type": "managed"},
                "version": "2.0.0",
            },
            {
                "transport": "stdio",
                "command": "/opt/cyrene/mcp-server",
                "args": ["--stdio"],
                "env": {"TOKEN": "new"},
                "enabled": True,
            },
            {"url", "headers"},
        ),
        (
            {
                "name": "switcher",
                "transport": "stdio",
                "command": "/opt/cyrene/mcp-server",
                "args": ["--stdio"],
                "env": {"TOKEN": "secret"},
                "enabled": True,
                "source": {"type": "managed"},
                "version": "2.0.0",
            },
            {
                "transport": "sse",
                "url": "https://example.test/mcp",
                "headers": {"Authorization": "new"},
                "enabled": True,
            },
            {"command", "args", "env"},
        ),
    ),
)
async def test_transport_switch_removes_incompatible_configuration(
    monkeypatch,
    tmp_path,
    existing,
    update,
    removed_fields,
):
    service, stored, _restarts = _persisted_service(
        monkeypatch,
        tmp_path,
        [existing],
    )
    monkeypatch.setattr(service, "server_status", lambda _name: None)

    await service.update_configuration("switcher", update)

    assert removed_fields.isdisjoint(stored[0])
    assert stored[0]["source"] == {"type": "managed"}
    assert stored[0]["version"] == "2.0.0"


@pytest.mark.asyncio
async def test_single_configuration_rejects_identity_and_unknown_fields(
    monkeypatch,
    tmp_path,
):
    initial = [{"name": "docs", "transport": "stdio", "enabled": False}]
    service, stored, restarts = _persisted_service(
        monkeypatch,
        tmp_path,
        initial,
    )

    with pytest.raises(ValueError, match="Unsupported MCP configuration fields"):
        await service.update_configuration(
            "docs",
            {"name": "renamed", "source": {"type": "user"}},
        )

    assert stored == initial
    assert restarts == []
    with pytest.raises(MCPServerNotFoundError, match="not found"):
        await service.update_configuration("missing", {"enabled": False})


def test_plugin_owned_configuration_route_returns_live_redacted_result(
    monkeypatch,
    tmp_path,
):
    mcp_service = MCPPluginService(data_directory=tmp_path)
    calls = []

    async def update_configuration(extension_id, configuration):
        calls.append((extension_id, configuration))
        return {
            "id": extension_id,
            "config": {"name": extension_id, "headers": {"Token": "[configured]"}},
            "status": "connected",
            "error": "",
            "tools": [{"name": "lookup"}],
            "tool_count": 1,
            "pack_id": "mcp.docs",
        }

    monkeypatch.setattr(mcp_service, "update_configuration", update_configuration)
    router = APIRouter()
    context = SimpleNamespace(
        router=router,
        services={"extensions": object(), "mcp": mcp_service},
    )
    application.setup_plugin_center(context)
    app = FastAPI()
    app.include_router(router)

    response = TestClient(app).put(
        "/api/plugin-center/mcp/docs/configuration",
        json={"enabled": True, "headers": {"Token": "[configured]"}},
    )

    assert response.status_code == 200
    assert response.json()["pack_id"] == "mcp.docs"
    assert response.json()["config"]["headers"] == {"Token": "[configured]"}
    assert calls == [
        ("docs", {"enabled": True, "headers": {"Token": "[configured]"}})
    ]

    slash_response = TestClient(app).put(
        "/api/plugin-center/mcp/team%2Fdocs/configuration",
        json={"enabled": False},
    )

    assert slash_response.status_code == 200
    assert calls[-1] == ("team/docs", {"enabled": False})
