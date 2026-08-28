from __future__ import annotations

from typing import Any

import pytest

from agent.plugin import PluginContext, PluginPack, PluginRegistry, PluginRuntime
from agent.plugin.customization import PluginCustomizationState
from agent.plugin.plugin_impl.cyrene_mcp import service as native_mcp


class _FakeConnection:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = dict(config)
        self.name = str(config["name"])
        self.status = "disconnected"
        self.error = ""
        self.tool_timeout_seconds = 10.0
        self.tools = (
            {
                "name": "search",
                "description": "Search the fake server",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        )

    async def start(self) -> None:
        self.status = "connected"

    async def stop(self) -> None:
        self.status = "disconnected"

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        assert name == "search"
        return {
            "content": [{"type": "text", "text": f"found:{arguments['query']}"}],
            "structured_content": {},
            "is_error": False,
        }


@pytest.mark.asyncio
async def test_mcp_server_is_a_dynamic_pack_through_toolbox(
    monkeypatch,
    tmp_path,
) -> None:
    service = native_mcp.MCPPluginService(data_directory=tmp_path)
    configs = [
        {
            "name": "docs",
            "transport": "stdio",
            "command": "/opt/fake/mcp-docs",
            "args": [],
            "enabled": True,
        }
    ]
    monkeypatch.setattr(service, "configs", lambda **_kwargs: [dict(configs[0])])
    monkeypatch.setattr(native_mcp, "MCPServerConnection", _FakeConnection)

    registry = PluginRegistry(customizations=PluginCustomizationState())
    service.attach_registry(registry)
    await service.startup()
    runtime = PluginRuntime(registry)

    listed = await runtime.call(
        "toolbox",
        {"operation": "list"},
        PluginContext(),
    )
    assert listed.success is True
    assert listed.value["packs"] == ["mcp.docs"]

    described = await runtime.call(
        "toolbox",
        {"operation": "describe", "name": "mcp.docs"},
        PluginContext(),
    )
    assert described.success is True
    plugin_name = described.value["plugins"][0]["name"]
    assert plugin_name == "mcp__docs__search"
    assert described.value["plugins"][0]["input_schema"]["required"] == ["query"]

    invoked = await runtime.call(
        "toolbox",
        {
            "operation": "invoke",
            "name": plugin_name,
            "arguments": {"query": "Cyrene"},
        },
        PluginContext(),
    )
    assert invoked.success is True
    assert invoked.value["result"] == "found:Cyrene"
    assert registry.registered(plugin_name).source == "mcp:docs"
    from route.settings.plugin_service import get_plugin_settings

    pack_setting = next(
        item
        for item in get_plugin_settings(registry)["packs"]
        if item["id"] == "mcp.docs"
    )
    assert pack_setting["source"] == "mcp"
    assert pack_setting["source_path"] == "docs"

    await service.shutdown()
    assert all(pack.id != "mcp.docs" for pack in registry.list_packs())


@pytest.mark.asyncio
async def test_mcp_raw_invoke_obeys_dynamic_pack_and_tool_activation(
    monkeypatch,
    tmp_path,
) -> None:
    service = native_mcp.MCPPluginService(data_directory=tmp_path)
    config = {
        "name": "docs",
        "transport": "stdio",
        "command": "/opt/fake/mcp-docs",
        "args": [],
        "enabled": True,
    }
    monkeypatch.setattr(service, "configs", lambda **_kwargs: [dict(config)])
    monkeypatch.setattr(native_mcp, "MCPServerConnection", _FakeConnection)
    registry = PluginRegistry(customizations=PluginCustomizationState())
    service.attach_registry(registry, authoritative=True)
    await service.startup()

    result = await service.invoke_raw("docs", "search", {"query": "Cyrene"})
    assert result["content"][0]["text"] == "found:Cyrene"
    renamed = registry.customize_tool(
        "mcp__docs__search",
        {"name": "renamed_mcp_docs_search"},
    )
    assert renamed is not None
    assert (
        await service.invoke_raw("docs", "search", {"query": "renamed"})
    )["content"][0]["text"] == "found:renamed"

    registry.set_pack_enabled("mcp.docs", False)
    with pytest.raises(RuntimeError, match="disabled"):
        await service.invoke_raw("docs", "search", {"query": "blocked"})

    registry.set_pack_enabled("mcp.docs", True)
    registry.set_plugin_enabled("renamed_mcp_docs_search", False)
    with pytest.raises(RuntimeError, match="disabled"):
        await service.invoke_raw("docs", "search", {"query": "blocked"})

    await service.shutdown()


@pytest.mark.asyncio
async def test_mcp_raw_invoke_rejects_deleted_dynamic_tool(
    monkeypatch,
    tmp_path,
) -> None:
    service = native_mcp.MCPPluginService(data_directory=tmp_path)
    config = {
        "name": "docs",
        "transport": "stdio",
        "command": "/opt/fake/mcp-docs",
        "args": [],
        "enabled": True,
    }
    monkeypatch.setattr(service, "configs", lambda **_kwargs: [dict(config)])
    monkeypatch.setattr(native_mcp, "MCPServerConnection", _FakeConnection)
    registry = PluginRegistry(customizations=PluginCustomizationState())
    service.attach_registry(registry, authoritative=True)
    await service.startup()

    registry.customize_tool("mcp__docs__search", {"deleted": True})
    with pytest.raises(RuntimeError, match="not registered"):
        await service.invoke_raw("docs", "search", {"query": "blocked"})

    await service.shutdown()


@pytest.mark.asyncio
async def test_mcp_registry_collision_marks_connected_server_as_error(
    monkeypatch,
    tmp_path,
) -> None:
    service = native_mcp.MCPPluginService(data_directory=tmp_path)
    config = {
        "name": "docs",
        "transport": "stdio",
        "command": "/opt/fake/mcp-docs",
        "args": [],
        "enabled": True,
    }
    monkeypatch.setattr(service, "configs", lambda **_kwargs: [dict(config)])
    monkeypatch.setattr(native_mcp, "MCPServerConnection", _FakeConnection)

    conflicting_registry = PluginRegistry(customizations=PluginCustomizationState())
    conflicting_registry.register_pack(
        PluginPack(id="mcp.docs", description="occupied", plugins=()),
        source="test",
    )
    healthy_registry = PluginRegistry(customizations=PluginCustomizationState())
    # Keep this order: a later successful sync must not erase the earlier
    # registry-specific collision from the service status.
    service.attach_registry(conflicting_registry)
    service.attach_registry(healthy_registry)

    await service.startup()
    status = service.server_status("docs")

    assert status is not None
    assert status["status"] == "error"
    assert status["error"]
    occupied = next(
        pack
        for pack in conflicting_registry.list_packs()
        if pack.id == "mcp.docs"
    )
    assert occupied.description == "occupied"
    assert healthy_registry.registered("mcp__docs__search").source == "mcp:docs"
    with pytest.raises(RuntimeError, match="not registered"):
        await service.invoke_raw("docs", "search", {"query": "blocked"})

    await service.shutdown()
    assert any(
        pack.id == "mcp.docs" for pack in conflicting_registry.list_packs()
    )
    assert all(pack.id != "mcp.docs" for pack in healthy_registry.list_packs())


def test_mcp_config_redaction_preserves_existing_secrets() -> None:
    existing = [
        {
            "name": "remote",
            "transport": "streamable_http",
            "url": "https://example.test/mcp",
            "headers": {"Authorization": "Bearer secret"},
            "enabled": True,
        }
    ]
    redacted = native_mcp.redact_mcp_configs(existing)
    assert redacted[0]["headers"] == {"Authorization": "[configured]"}
    merged = native_mcp.merge_redacted_mcp_configs(existing, redacted)
    assert merged[0]["headers"] == {"Authorization": "Bearer secret"}


@pytest.mark.parametrize(
    "url",
    (
        "https://example.test/mcp?api_key=secret",
        "https://example.test/mcp#access-token",
    ),
)
def test_mcp_config_rejects_url_embedded_secrets(url: str) -> None:
    with pytest.raises(ValueError, match="use headers for authentication"):
        native_mcp.validate_mcp_configs(
            [
                {
                    "name": "remote",
                    "transport": "streamable_http",
                    "url": url,
                    "headers": {},
                    "enabled": True,
                }
            ]
        )
