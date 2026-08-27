from __future__ import annotations

import asyncio
import json

from agent.plugin import PluginContext, PluginRegistry, PluginRuntime
from agent.plugin.plugin_impl.cyrene_map import plugin_pack
from agent.plugin.plugin_impl.cyrene_map.service import MapService, map_database


def run(coroutine):
    return asyncio.run(coroutine)


def test_map_pack_completes_toolbox_list_describe_invoke_chain(tmp_path):
    service = MapService(map_database(tmp_path))
    registry = PluginRegistry()
    registry.register_pack(plugin_pack, source="test-map")
    runtime = PluginRuntime(registry)
    context = PluginContext(
        workspace=tmp_path,
        tree_id="chat-map",
        data={"session_id": "chat-map"},
        services={"maps": service},
    )

    listing = run(runtime.call("toolbox", {"operation": "list"}, context))
    assert "cyrene_map" in listing.value["packs"]

    described = run(
        runtime.call(
            "toolbox",
            {"operation": "describe", "name": "cyrene_map"},
            context,
        )
    )
    assert {item["name"] for item in described.value["plugins"]} == {
        "connect_pins",
        "pin_location",
    }

    pinned = run(
        runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "pin_location",
                "arguments": {
                    "lat": 39.9042,
                    "lng": 116.4074,
                    "name": "Beijing",
                },
            },
            context,
        )
    )
    assert json.loads(pinned.value["result"])["status"] == "ok"

    connected = run(
        runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "connect_pins",
                "arguments": {
                    "from_name": "Beijing",
                    "to_name": "Beijing",
                    "transport": "walking",
                },
            },
            context,
        )
    )
    assert json.loads(connected.value["result"])["status"] == "ok"
    assert service.snapshot("chat-map")["routes"][0]["transport"] == "walking"
