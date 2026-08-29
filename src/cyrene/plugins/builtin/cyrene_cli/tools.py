"""Agent-facing CLI Plugin discovery and lifecycle operations."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from cyrene.core.plugin import PluginContext, application_plugin_service
from cyrene.plugins.native_runtime import plugin_localized

from .definitions import get_definition


def _service(context: PluginContext):
    service = context.services.get("cli") or application_plugin_service("cli")
    if service is None:
        raise RuntimeError("cyrene_cli is not active")
    return service


def _installed(item: Mapping[str, Any]) -> bool:
    return str(item.get("observed_state") or "") == "installed" or str(item.get("ownership") or "") in {"builtin", "system", "cyrene"}


def _compact(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "name": str(item.get("name") or item.get("id") or ""),
        "description": str(item.get("description") or ""),
        "version": str(item.get("version") or ""),
        "status": str(item.get("observed_state") or ""),
        "enabled": bool(item.get("enabled", item.get("desired_state") != "disabled")),
        "health": str(item.get("health") or ""),
        "path": str(item.get("path") or ""),
        "source": item.get("source"),
    }


async def list_cli_plugins(arguments: dict[str, Any], context: PluginContext) -> str:
    query = str(arguments.get("query") or "").strip().casefold()
    cards = _service(context).list_extensions().get("cli", [])
    items = []
    for card in cards:
        if not isinstance(card, Mapping) or not _installed(card) or card.get("enabled") is False:
            continue
        compact = _compact(card)
        if query and query not in " ".join((compact["id"], compact["name"], compact["description"], compact["version"])).casefold():
            continue
        items.append(compact)
    return json.dumps({"ok": True, "count": len(items), "items": items}, ensure_ascii=False)


def _install_request(item: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "name", "kind", "manager", "tool", "ref", "version",
        "recommended_version", "executables", "version_args", "description",
        "publisher", "risk", "backend", "verified",
    )
    spec = {key: item[key] for key in keys if key in item}
    request: dict[str, Any] = {
        "version": str(item.get("version") or item.get("recommended_version") or "latest"),
        "spec": spec,
    }
    if str(item.get("ref") or "").strip():
        request["ref"] = str(item["ref"])
    return request


async def search_cli_plugins(arguments: dict[str, Any], context: PluginContext) -> str:
    query = str(arguments.get("query") or "").strip()
    if not query:
        return json.dumps({"ok": False, "error": plugin_localized(
            context,
            "A search query is required.",
            "必须提供搜索关键词。",
        )}, ensure_ascii=False)
    limit = max(1, min(int(arguments.get("limit") or 20), 50))
    service = _service(context)
    installed_ids = {
        str(item.get("id") or "")
        for item in service.list_extensions().get("cli", [])
        if isinstance(item, Mapping) and _installed(item)
    }
    outcome = await service.search("cli", query, advanced=bool(arguments.get("advanced")))
    results = []
    for raw in outcome.get("results", []) or []:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        plugin_id = str(item.get("id") or "")
        installed = plugin_id in installed_ids
        results.append({
            "id": plugin_id,
            "name": str(item.get("name") or plugin_id),
            "description": str(item.get("description") or ""),
            "version": str(item.get("version") or item.get("recommended_version") or ""),
            "source": item.get("source"),
            "verified": bool(item.get("verified", False)),
            "installed": installed,
            "install_request": None if installed else _install_request(item),
        })
    return json.dumps({"ok": True, "query": query, "count": min(len(results), limit), "results": results[:limit]}, ensure_ascii=False)


async def manage_cli_plugins(arguments: dict[str, Any], context: PluginContext) -> str:
    service = _service(context)
    action = str(arguments.get("action") or "").strip().lower()
    plugin_id = str(arguments.get("plugin_id") or "").strip()
    if not action or not plugin_id:
        return json.dumps({"ok": False, "error": plugin_localized(
            context,
            "action and plugin_id are required.",
            "必须提供 action 和 plugin_id。",
        )}, ensure_ascii=False)
    if action == "install":
        request = arguments.get("request")
        if not isinstance(request, Mapping):
            return json.dumps({"ok": False, "error": plugin_localized(
                context,
                "Installation requires the exact request returned by SearchCliPlugins.",
                "安装时必须使用 SearchCliPlugins 返回的原始 request。",
            )}, ensure_ascii=False)
        result = service.start_install("cli", plugin_id, dict(request), actor="agent")
    elif action == "uninstall":
        result = await service.uninstall("cli", plugin_id, version=str(arguments.get("version") or ""), actor="agent")
    elif action in {"enable", "disable"}:
        result = await service.set_extension_enabled("cli", plugin_id, action == "enable", actor="agent")
    elif action == "bind":
        result = service.bind_system_executable(plugin_id, str(arguments.get("path") or ""))
    elif action == "unbind":
        result = service.unbind_system_executable(plugin_id)
    else:
        return json.dumps({"ok": False, "error": plugin_localized(
            context,
            "The requested action is not supported.",
            "不支持所请求的操作。",
        )}, ensure_ascii=False)
    return json.dumps({"ok": True, **dict(result)}, ensure_ascii=False)


TOOL_SPECS = (
    ("ListCliPlugins", list_cli_plugins, True),
    ("SearchCliPlugins", search_cli_plugins, True),
    ("ManageCliPlugins", manage_cli_plugins, False),
)


def definitions():
    return tuple((get_definition(name), handler, read_only) for name, handler, read_only in TOOL_SPECS)


__all__ = ["definitions", "list_cli_plugins", "manage_cli_plugins", "search_cli_plugins"]
