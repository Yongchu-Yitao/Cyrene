"""Agent-facing environment operations with mandatory review."""

from __future__ import annotations

import json
from typing import Any

from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import plugin_localized

from cyrene.plugins.builtin.cyrene_extensions.extension_service import get_extension_service
from .definitions import get_native_tool_def

TOOL_NAME = "ManageExtensions"
TOOL_DEF = get_native_tool_def(TOOL_NAME)
TOOL_METADATA = {"read_only": False, "resource_keys": ("extensions:global",), "requires_order": True}


def permission_boundary(
    arguments: dict[str, Any],
    _context: PluginContext,
) -> dict[str, Any] | None:
    action = str(arguments.get("action") or "list").strip().lower()
    if action in {"list", "search"}:
        return None
    return {
        "kind": "extension_change",
        "operation": f"扩展环境变更：{action}",
        "reason": str(arguments.get("reason") or "")[:500],
        "always_review": True,
        "requires_human": False,
    }


def _localized_operation_result(
    result: dict[str, Any],
    context: PluginContext,
) -> dict[str, Any]:
    if result.get("ok") is not False:
        return result
    return {
        **result,
        "code": str(
            result.get("code")
            or result.get("reason_code")
            or "extension_operation_failed"
        ),
        "error": plugin_localized(
            context,
            "The extension operation failed.",
            "扩展操作失败。",
        ),
    }


async def _tool_manage_extensions(args: dict[str, Any], context: PluginContext) -> str:
    service = get_extension_service()
    action = str(args.get("action") or "list").strip().lower()
    if action == "list":
        state = dict(service.list_extensions())
        state.pop("cli", None)
        if isinstance(state.get("recommended"), list):
            state["recommended"] = [
                item for item in state["recommended"]
                if str(item.get("kind") or "") != "cli"
            ]
        return json.dumps({"ok": True, **state}, ensure_ascii=False)
    kind = str(args.get("kind") or "").strip().lower()
    if kind == "cli":
        return json.dumps(
            {
                "ok": False,
                "code": "cli_plugin_owned",
                "error": plugin_localized(
                    context,
                    "CLI lifecycle belongs to the cyrene_cli Plugin pack.",
                    "CLI 生命周期由 cyrene_cli 插件包管理。",
                ),
            },
            ensure_ascii=False,
        )
    extension_id = str(args.get("extension_id") or "").strip()
    if action == "search":
        result = await service.search(kind, str(args.get("query") or ""), advanced=bool(args.get("advanced")))
        return json.dumps({"ok": True, **result}, ensure_ascii=False)
    if action not in {"install", "install_local_mcp", "uninstall", "set_default", "enable", "disable"} or not kind or not extension_id:
        return json.dumps(
            {
                "ok": False,
                "code": "extension_parameters_required",
                "error": plugin_localized(
                    context,
                    "action, kind, and extension_id are required.",
                    "必须提供 action、kind 和 extension_id。",
                ),
            },
            ensure_ascii=False,
        )
    if action == "install_local_mcp" and kind != "mcp":
        return json.dumps(
            {
                "ok": False,
                "code": "invalid_extension_kind",
                "error": plugin_localized(
                    context,
                    "install_local_mcp requires kind=mcp.",
                    "install_local_mcp 要求 kind=mcp。",
                ),
            },
            ensure_ascii=False,
        )
    request = dict(args.get("request") or {})
    if action == "install_local_mcp" and not isinstance(request.get("config"), dict):
        return json.dumps(
            {
                "ok": False,
                "code": "mcp_config_required",
                "error": plugin_localized(
                    context,
                    "install_local_mcp requires request.config.",
                    "install_local_mcp 要求提供 request.config。",
                ),
            },
            ensure_ascii=False,
        )
    if action in {"install", "install_local_mcp"}:
        task = service.start_install(kind, extension_id, request, actor="agent")
        return json.dumps({"ok": True, "task": task}, ensure_ascii=False)
    if action == "uninstall":
        result = await service.uninstall(kind, extension_id, version=str(args.get("version") or ""), actor="agent")
        return json.dumps(
            _localized_operation_result(result, context),
            ensure_ascii=False,
        )
    if action in {"enable", "disable"}:
        result = await service.set_extension_enabled(kind, extension_id, action == "enable", actor="agent")
        return json.dumps(
            _localized_operation_result(result, context),
            ensure_ascii=False,
        )
    result = await service.set_default_version(extension_id, str(args.get("version") or ""), actor="agent")
    return json.dumps(
        _localized_operation_result(result, context),
        ensure_ascii=False,
    )


handler = _tool_manage_extensions

__all__ = [
    "TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler",
    "permission_boundary", "_tool_manage_extensions",
]
