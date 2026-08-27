"""Map pin Plugins backed by the pack-owned durable map service."""

import json
from collections.abc import Mapping
from typing import Any

from agent.plugin import Plugin, PluginContext

from .service import MapServiceError


PIN_LOCATION_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "pin_location",
        "description": (
            "在地图上标记一个地点。标记后会出现在右侧边栏地图上。"
            "之后再使用 connect_pins 工具在两个标记之间建立路线连接。"
            "支持添加 Markdown 注释。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "lat": {
                    "type": "number",
                    "description": "纬度，例如 39.9042",
                },
                "lng": {
                    "type": "number",
                    "description": "经度，例如 116.4074",
                },
                "name": {
                    "type": "string",
                    "description": "地点名称，例如 北京",
                },
                "note": {
                    "type": "string",
                    "description": "关于该地点的 Markdown 注释（可选），用户点击标记会看到此内容",
                },
            },
            "required": ["lat", "lng", "name"],
        },
    },
}

CONNECT_PINS_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "connect_pins",
        "description": (
            "在两个已有的标记点之间创建路线连接。"
            "标记点必须已通过 pin_location 创建，通过名称引用。"
            "支持添加交通方式和 Markdown 说明，用户点击路线会看到。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "from_name": {
                    "type": "string",
                    "description": "起点标记的名称，必须与 pin_location 创建的 name 一致",
                },
                "to_name": {
                    "type": "string",
                    "description": "终点标记的名称，必须与 pin_location 创建的 name 一致",
                },
                "transport": {
                    "type": "string",
                    "description": "交通方式（可选），例如 飞机、高铁、驾车、步行",
                },
                "route_note": {
                    "type": "string",
                    "description": "路线的 Markdown 说明（可选），用户点击路线会看到",
                },
            },
            "required": ["from_name", "to_name"],
        },
    },
}


async def _tool_pin_location(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    from cyrene.observability import debug

    result = _map_service(context).add_pin(
        _session_id(context),
        lat=float(args["lat"]),
        lng=float(args["lng"]),
        name=str(args.get("name") or ""),
        note=str(args.get("note") or ""),
    )

    await debug.publish_event({
        "type": "map_pin",
        "pins": result["pins"],
        "routes": result["routes"],
    })

    pin = result["pin"]
    return json.dumps(
        {
            "status": "ok",
            "pin_id": pin["id"],
            "total_pins": len(result["pins"]),
            "name": pin["name"],
        },
        ensure_ascii=False,
    )


async def _tool_connect_pins(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    from cyrene.observability import debug

    try:
        result = _map_service(context).add_route(
            _session_id(context),
            from_name=str(args["from_name"]),
            to_name=str(args["to_name"]),
            transport=str(args.get("transport") or ""),
            note=str(args.get("route_note") or ""),
        )
    except MapServiceError as exc:
        return json.dumps(
            {"status": "error", "message": str(exc)},
            ensure_ascii=False,
        )

    await debug.publish_event({
        "type": "map_pin",
        "pins": result["pins"],
        "routes": result["routes"],
    })

    route = result["route"]
    return json.dumps(
        {
            "status": "ok",
            "route_id": route["id"],
            "from": route["from_name"],
            "to": route["to_name"],
        },
        ensure_ascii=False,
    )


def _map_service(context: PluginContext) -> Any:
    service = context.services.get("maps")
    if service is None or not all(
        callable(getattr(service, name, None))
        for name in ("add_pin", "add_route", "snapshot")
    ):
        raise RuntimeError("cyrene_map service is unavailable")
    return service


def _session_id(context: PluginContext) -> str:
    value = str(context.data.get("session_id") or "").strip()
    run_context = context.data.get("run_context")
    if not value and isinstance(run_context, Mapping):
        value = str(run_context.get("session_id") or "").strip()
    return value or str(context.tree_id or "").strip()


def _plugin(
    definition: Mapping[str, Any],
    handler: Any,
) -> Plugin:
    function = definition.get("function")
    if not isinstance(function, Mapping):
        raise TypeError("map Plugin definition must contain a function object")
    parameters = function.get("parameters")
    if not isinstance(parameters, Mapping):
        raise TypeError("map Plugin definition must contain an input schema")
    return Plugin(
        name=str(function.get("name") or ""),
        description=str(function.get("description") or ""),
        input_schema=dict(parameters),
        handler=handler,
        allow_parallel=False,
        timeout_seconds=30.0,
        metadata={
            "read_only": False,
            "requires_order": True,
            "resource_keys": ("session:map",),
        },
    )


pin_location_plugin = _plugin(PIN_LOCATION_TOOL_DEF, _tool_pin_location)
connect_pins_plugin = _plugin(CONNECT_PINS_TOOL_DEF, _tool_connect_pins)


__all__ = [
    "CONNECT_PINS_TOOL_DEF",
    "PIN_LOCATION_TOOL_DEF",
    "connect_pins_plugin",
    "pin_location_plugin",
]
