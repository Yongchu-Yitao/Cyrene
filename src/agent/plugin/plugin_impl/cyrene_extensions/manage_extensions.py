"""Agent-facing environment operations with mandatory review."""

from __future__ import annotations

import json
from typing import Any

from agent.plugin import PluginContext

from cyrene.extensions.service import get_extension_service
from .definitions import get_native_tool_def

TOOL_NAME = "ManageExtensions"
TOOL_DEF = get_native_tool_def(TOOL_NAME)
TOOL_METADATA = {"read_only": False, "resource_keys": ("extensions:global",), "requires_order": True}


async def _tool_manage_extensions(args: dict[str, Any], _context: PluginContext) -> str:
    service = get_extension_service()
    action = str(args.get("action") or "list").strip().lower()
    if action == "list":
        return json.dumps({"ok": True, **service.list_extensions()}, ensure_ascii=False)
    kind = str(args.get("kind") or "").strip().lower()
    extension_id = str(args.get("extension_id") or "").strip()
    if action == "search":
        result = await service.search(kind, str(args.get("query") or ""), advanced=bool(args.get("advanced")))
        return json.dumps({"ok": True, **result}, ensure_ascii=False)
    if action not in {"install", "install_local_mcp", "uninstall", "set_default", "enable", "disable"} or not kind or not extension_id:
        return json.dumps({"ok": False, "error": "action, kind, and extension_id are required"}, ensure_ascii=False)
    if action == "install_local_mcp" and kind != "mcp":
        return json.dumps({"ok": False, "error": "install_local_mcp requires kind=mcp"}, ensure_ascii=False)
    request = dict(args.get("request") or {})
    if action == "install_local_mcp" and not isinstance(request.get("config"), dict):
        return json.dumps({"ok": False, "error": "install_local_mcp requires request.config"}, ensure_ascii=False)
    if action in {"install", "install_local_mcp"}:
        task = service.start_install(kind, extension_id, request, actor="agent")
        return json.dumps({"ok": True, "task": task}, ensure_ascii=False)
    if action == "uninstall":
        result = await service.uninstall(kind, extension_id, version=str(args.get("version") or ""), actor="agent")
        return json.dumps(result, ensure_ascii=False)
    if action in {"enable", "disable"}:
        result = await service.set_extension_enabled(kind, extension_id, action == "enable", actor="agent")
        return json.dumps(result, ensure_ascii=False)
    result = await service.set_default_version(extension_id, str(args.get("version") or ""), actor="agent")
    return json.dumps(result, ensure_ascii=False)


handler = _tool_manage_extensions

__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler", "_tool_manage_extensions"]
