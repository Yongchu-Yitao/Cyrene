from __future__ import annotations

from typing import Any

import pytest

from agent.plugin import PluginContext, PluginRegistry, PluginRuntime
from agent.plugin import mcp_service as native_mcp


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

    registry = PluginRegistry()
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
