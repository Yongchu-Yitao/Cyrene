"""Map pin Plugins backed by the pack-owned durable map service."""

import json
from collections.abc import Mapping
from typing import Any

from agent.plugin import Plugin, PluginContext
from agent.plugin.native_runtime import plugin_localized

from .service import MapServiceError


PIN_LOCATION_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "pin_location",
        "description": "Pin a place on the map so it can be viewed and connected to other pins.",
        "parameters": {
            "type": "object",
            "properties": {
                "lat": {
                    "type": "number",
                    "description": "Latitude, for example 39.9042.",
                },
                "lng": {
                    "type": "number",
                    "description": "Longitude, for example 116.4074.",
                },
                "name": {
                    "type": "string",
                    "description": "Place name, for example Beijing.",
                },
                "note": {
                    "type": "string",
                    "description": "Optional Markdown note shown with the pin.",
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
        "description": "Connect two existing named map pins with an optional travel mode and Markdown note.",
        "parameters": {
            "type": "object",
            "properties": {
                "from_name": {
                    "type": "string",
                    "description": "Origin pin name created with pin_location.",
                },
                "to_name": {
                    "type": "string",
                    "description": "Destination pin name created with pin_location.",
                },
                "transport": {
                    "type": "string",
                    "description": "Optional travel mode, such as flight, train, driving, or walking.",
                },
                "route_note": {
                    "type": "string",
                    "description": "Optional Markdown note shown with the route.",
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

    try:
        result = _map_service(context).add_pin(
            _session_id(context),
            lat=float(args["lat"]),
            lng=float(args["lng"]),
            name=str(args.get("name") or ""),
            note=str(args.get("note") or ""),
        )
    except MapServiceError as exc:
        return _map_error_result(exc, context)

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
        return _map_error_result(exc, context)

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
        raise RuntimeError(plugin_localized(
            context,
            "The map service is unavailable.",
            "地图服务当前不可用。",
        ))
    return service


def _map_error_result(exc: MapServiceError, context: PluginContext) -> str:
    if exc.code == "map_pin_name_required":
        message = plugin_localized(
            context,
            "A pin name is required.",
            "必须填写标记名称。",
        )
    elif exc.code == "map_origin_not_found":
        message = plugin_localized(
            context,
            "Origin pin was not found: {pin_name}",
            "未找到起点标记：{pin_name}",
            pin_name=exc.pin_name,
        )
    elif exc.code == "map_destination_not_found":
        message = plugin_localized(
            context,
            "Destination pin was not found: {pin_name}",
            "未找到终点标记：{pin_name}",
            pin_name=exc.pin_name,
        )
    else:
        message = plugin_localized(
            context,
            "The map could not be updated.",
            "无法更新地图。",
        )
    return json.dumps(
        {"status": "error", "code": exc.code, "message": message},
        ensure_ascii=False,
    )


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
