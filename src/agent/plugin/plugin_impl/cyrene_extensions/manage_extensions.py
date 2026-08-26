"""Agent-facing Extension Center operations with mandatory review."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from cyrene.extensions.service import get_extension_service
from .definitions import get_native_tool_def
from cyrene.tooling.runtime_api import request_scope_elevation

TOOL_NAME = "ManageExtensions"
TOOL_DEF = get_native_tool_def(TOOL_NAME)
TOOL_METADATA = {"read_only": False, "resource_keys": ("extensions:global",), "requires_order": True}


async def _review(operation: str, target: str, arguments: dict[str, Any]) -> str | None:
    fingerprint = hashlib.sha256(json.dumps({"operation": operation, "target": target, "arguments": arguments}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    # extension_change is intentionally not a human-only confirmation kind.
    # It always reaches the existing reviewer, including full_access, while
    # preserving auto-mode autonomous decisions.
    return await request_scope_elevation(
        tool_name=TOOL_NAME,
        path_hint=f"extension:{target}:{fingerprint[:16]}",
        operation=f"扩展中心持久化操作：{operation} {target}",
        reason="Installing, updating, removing, or reconfiguring an extension changes Cyrene's persistent capabilities.",
        permission_kind="extension_change",
        scope_hint="本机全局能力的 ",
    )


async def _tool_manage_extensions(args: dict[str, Any], *_unused: Any) -> str:
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
    reviewed = await _review(action, f"{kind}:{extension_id}", args)
    if reviewed is not None:
        return reviewed
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
