"""Application service for tool and progressive-package settings."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi.responses import JSONResponse

from cyrene.runtime import config_store, settings_service, settings_store
from cyrene.tooling.catalog import TOOL_DEFS
from cyrene.tooling.packs import (
    CAPABILITY_BINDINGS,
    PACK_BY_WIRE_NAME,
    PACKS,
    WIRE_NAME_BY_CONCRETE_TOOL,
)

SettingsChangedPublisher = Callable[[str, int | None, list[str]], Awaitable[None]]


def _native_tools(enabled: dict[str, bool]) -> list[dict[str, Any]]:
    tools = []
    for definition in TOOL_DEFS:
        name = definition["function"]["name"]
        tools.append({
            "name": name, "desc": definition["function"]["description"],
            "enabled": enabled.get(name, True), "configured_enabled": enabled.get(name, True),
            "effective_enabled": settings_store.is_tool_pack_enabled(WIRE_NAME_BY_CONCRETE_TOOL.get(name, "")) if name in WIRE_NAME_BY_CONCRETE_TOOL else True,
            "package_id": WIRE_NAME_BY_CONCRETE_TOOL.get(name, "direct_tools"),
            "locked": name == "quit",
        })
    return tools


def _append_mcp_tools(tools: list[dict[str, Any]], enabled: dict[str, bool]) -> None:
    try:
        from cyrene.tooling.backends.mcp_manager import get_manager

        for definition in get_manager().get_tool_defs():
            name = definition["function"]["name"]
            tools.append({
                "name": name, "desc": definition["function"]["description"],
                "enabled": enabled.get(name, True), "configured_enabled": enabled.get(name, True),
                "effective_enabled": settings_store.is_tool_pack_enabled("integration_tools"),
                "package_id": "integration_tools", "source": "mcp",
            })
    except Exception:
        pass


def _append_custom_tools(tools: list[dict[str, Any]]) -> None:
    try:
        from cyrene.custom_tools.manager import get_custom_tool_manager

        for tool in get_custom_tool_manager().get_tool_definitions():
            tools.append({
                "name": tool.stable_name, "execution_name": tool.concrete_name,
                "public_name": tool.name, "desc": tool.description,
                "enabled": True, "configured_enabled": True,
                "effective_enabled": settings_store.is_tool_pack_enabled("custom_tools"),
                "package_id": "custom_tools", "custom_package_id": tool.package_id,
                "source": "custom",
            })
    except Exception:
        pass


def _tool_packages(tools: list[dict[str, Any]], enabled: dict[str, bool]) -> list[dict[str, Any]]:
    package_tools = {wire: [concrete for _capability, concrete in bindings] for wire, bindings in CAPABILITY_BINDINGS.items()}
    package_tools["integration_tools"] = [item["name"] for item in tools if item.get("package_id") == "integration_tools"]
    package_tools["custom_tools"] = [item["name"] for item in tools if item.get("package_id") == "custom_tools"]
    packages = []
    for pack in PACKS:
        member_names = set(package_tools.get(pack.wire_name, ()))
        members = [item for item in tools if item["name"] in member_names]
        package_enabled = enabled.get(pack.wire_name, True)
        packages.append({
            "id": pack.wire_name, "wire_name": pack.wire_name,
            "description": pack.description, "enabled": package_enabled,
            "enabled_count": sum(1 for _item in members if package_enabled),
            "configured_enabled_count": sum(1 for item in members if item["enabled"]),
            "tool_count": len(members),
            "source": "integration" if pack.wire_name == "integration_tools" else "custom" if pack.wire_name == "custom_tools" else "native",
        })
    return packages


def get_tool_settings() -> dict[str, Any]:
    enabled = settings_store.get_enabled_tools()
    enabled_packs = settings_store.get_enabled_tool_packs()
    tools = _native_tools(enabled)
    _append_mcp_tools(tools, enabled)
    _append_custom_tools(tools)
    packages = _tool_packages(tools, enabled_packs)
    return {"tools": tools, "packages": packages, "tool_groups": [{**package, "kind": "package"} for package in packages]}


def _tool_update_error(body: dict[str, Any]) -> JSONResponse | None:
    tools = body.get("tools")
    packages = body.get("packages")
    if not (isinstance(tools, dict) and tools) and not (isinstance(packages, dict) and packages):
        return JSONResponse({"error": "tools or packages must be a non-empty dict"}, status_code=400)
    if tools is not None and not isinstance(tools, dict):
        return JSONResponse({"error": "tools must be a dict"}, status_code=400)
    if packages is not None and not isinstance(packages, dict):
        return JSONResponse({"error": "packages must be a dict"}, status_code=400)
    invalid_tools = [str(name) for name, value in (tools or {}).items() if not isinstance(value, bool)]
    if invalid_tools:
        return JSONResponse({"error": "tool values must be booleans: " + ", ".join(sorted(invalid_tools))}, status_code=400)
    invalid_packages = [str(name) for name, value in (packages or {}).items() if not isinstance(value, bool)]
    if invalid_packages:
        return JSONResponse({"error": "package values must be booleans: " + ", ".join(sorted(invalid_packages))}, status_code=400)
    unknown = sorted(set(packages or {}) - set(PACK_BY_WIRE_NAME))
    if unknown:
        return JSONResponse({"error": "unknown tool package(s): " + ", ".join(unknown)}, status_code=400)
    return None


class ToolSettingsApplicationService:
    def __init__(self, publish_settings_changed: SettingsChangedPublisher) -> None:
        self._publish_settings_changed = publish_settings_changed

    def get_settings(self) -> dict[str, Any]:
        return get_tool_settings()

    async def update_settings(self, body: dict[str, Any]) -> dict[str, Any] | JSONResponse:
        error = _tool_update_error(body)
        if error is not None:
            return error
        tool_updates = body.get("tools")
        package_updates = body.get("packages")
        changes = {}
        if isinstance(tool_updates, dict) and tool_updates:
            changes["enabled_tools"] = {str(name): value for name, value in tool_updates.items() if str(name) != "quit"}
        if isinstance(package_updates, dict) and package_updates:
            next_packages = settings_store.get_enabled_tool_packs()
            next_packages.update({str(name): value for name, value in package_updates.items()})
            changes["enabled_tool_packs"] = next_packages
        try:
            result = settings_service.update("runtime", changes, actor="ui", expected_revision=body.get("expected_revision"))
        except config_store.SettingsRevisionConflict as exc:
            return JSONResponse({"error": str(exc), "revision": exc.actual}, status_code=409)
        except settings_service.SettingsServiceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        await self._publish_settings_changed("runtime", result["revision"], list(changes))
        if isinstance(package_updates, dict) and "custom_tools" in package_updates:
            from cyrene.custom_tools.manager import get_custom_tool_manager

            await get_custom_tool_manager().sync_pack_state()
        return {
            "ok": True, "updated": list(tool_updates or {}),
            "updated_packages": list(package_updates or {}), "revision": result["revision"],
        }
