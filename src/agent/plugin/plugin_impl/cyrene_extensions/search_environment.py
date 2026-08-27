"""Search installable MCP servers and runtimes."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from agent.plugin import PluginContext
from agent.plugin.native_runtime import plugin_localized

from agent.plugin.plugin_impl.cyrene_extensions.extension_service import get_extension_service
from .list_environment import (
    environment_items,
    environment_key,
    is_explicitly_disabled,
    is_installed,
)
from .definitions import get_native_tool_def

TOOL_NAME = "SearchEnvironment"
TOOL_DEF = get_native_tool_def(TOOL_NAME)
TOOL_METADATA = {
    "read_only": True,
    "resource_keys": ("extensions:catalog",),
    "requires_order": False,
}

_KINDS = ("toolchain", "mcp")
logger = logging.getLogger(__name__)


def _extension_keys(service: Any) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    state = service.list_extensions()
    installed: set[tuple[str, str]] = set()
    disabled: set[tuple[str, str]] = set()
    for kind, item in environment_items(state):
        key = environment_key(kind, item.get("id"))
        if is_explicitly_disabled(item):
            disabled.add(key)
        elif is_installed(item):
            installed.add(key)
    return installed, disabled


def _install_request(item: dict[str, Any]) -> dict[str, Any] | None:
    kind = str(item.get("kind") or "")
    version = str(item.get("version") or item.get("recommended_version") or "")
    if kind == "mcp":
        remote = next(iter(item.get("installable_remotes") or []), None)
        if isinstance(remote, dict):
            return {
                "version": version,
                "remote": remote,
                "source": {"type": "mcp-registry", "id": item.get("id"), "version": version},
            }
        package = next(iter(item.get("installable_packages") or []), None)
        if isinstance(package, dict):
            return {
                "version": version,
                "package": package,
                "source": {"type": "mcp-registry-package", "id": item.get("id"), "version": version},
            }
        return None
    if kind == "toolchain":
        spec_keys = (
            "name", "kind", "manager", "tool", "ref", "version",
            "recommended_version", "executables", "version_args",
            "description", "publisher", "risk", "backend", "verified",
        )
        spec = {key: item[key] for key in spec_keys if key in item}
        request: dict[str, Any] = {"version": version or "latest", "spec": spec}
        ref = str(item.get("ref") or "")
        if ref:
            request["ref"] = ref
        return request
    return None


def _compact_candidate(item: dict[str, Any], installed: set[tuple[str, str]]) -> dict[str, Any]:
    kind = str(item.get("kind") or "")
    extension_id = str(item.get("id") or "")
    installed_locally = environment_key(kind, extension_id) in installed
    request = None if installed_locally else _install_request(item)
    fallback_request = None if installed_locally else item.get("fallback_request")
    reason_code = "already_installed" if installed_locally else str(item.get("reason_code") or "")
    if not request and not fallback_request and not reason_code:
        reason_code = "unsupported_registry_type" if kind == "mcp" else "not_installable"
    return {
        "kind": kind,
        "id": extension_id,
        "name": str(item.get("name") or extension_id),
        "description": str(item.get("description") or ""),
        "version": str(item.get("version") or item.get("recommended_version") or ""),
        "registry_version": str(item.get("registry_version") or ""),
        "package_latest_version": str(item.get("package_latest_version") or ""),
        "resolved_version": str(item.get("resolved_version") or item.get("version") or ""),
        "version_status": str(item.get("version_status") or ""),
        "source": item.get("source"),
        "publisher": str(item.get("publisher") or ""),
        "backend": str(item.get("backend") or ""),
        "risk": str(item.get("risk") or ""),
        "verified": bool(item.get("verified", False)),
        "installed": installed_locally,
        "installable": bool(request) and item.get("installable") is not False,
        "install_request": request,
        "reason_code": reason_code,
        "fallback_request": fallback_request,
    }


async def _tool_search_environment(args: dict[str, Any], context: PluginContext) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        return json.dumps(
            {
                "ok": False,
                "code": "query_required",
                "error": plugin_localized(
                    context,
                    "A search query is required.",
                    "必须提供搜索关键词。",
                ),
            },
            ensure_ascii=False,
        )
    kind = str(args.get("kind") or "all").strip().lower()
    if kind != "all" and kind not in _KINDS:
        return json.dumps(
            {
                "ok": False,
                "code": "unsupported_environment_kind",
                "error": plugin_localized(
                    context,
                    "Unsupported environment kind: {kind}",
                    "不支持的环境类型：{kind}",
                    kind=kind,
                ),
            },
            ensure_ascii=False,
        )
    limit = max(1, min(int(args.get("limit") or 20), 50))
    advanced = bool(args.get("advanced", False))
    cursor = str(args.get("cursor") or "")
    service = get_extension_service()
    installed, disabled = _extension_keys(service)
    kinds = list(_KINDS) if kind == "all" else [kind]

    calls = [service.search(selected, query, advanced=advanced, cursor=cursor if selected == "mcp" else "") for selected in kinds]
    outcomes = await asyncio.gather(*calls, return_exceptions=True)
    candidates: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    next_cursors: dict[str, str] = {}
    for selected, outcome in zip(kinds, outcomes):
        if isinstance(outcome, BaseException):
            logger.warning(
                "Environment source search failed for %s",
                selected,
                exc_info=(type(outcome), outcome, outcome.__traceback__),
            )
            errors[selected] = plugin_localized(
                context,
                "This catalog source is temporarily unavailable.",
                "此目录源暂时不可用。",
            )
            continue
        for item in outcome.get("results", []) or []:
            if isinstance(item, dict):
                candidate = {**item, "kind": selected}
                if environment_key(selected, candidate.get("id")) in disabled:
                    continue
                candidates.append(_compact_candidate(candidate, installed))
        if outcome.get("next_cursor"):
            next_cursors[selected] = str(outcome["next_cursor"])

    return json.dumps({
        "ok": bool(candidates) or not errors,
        "query": query,
        "kind": kind,
        "count": min(len(candidates), limit),
        "results": candidates[:limit],
        "source_errors": errors,
        "next_cursors": next_cursors,
        "next_step": plugin_localized(
            context,
            "Disabled entries are intentionally hidden. Use the cyrene_cli Plugin pack for CLI tools. For results here, invoke ManageExtensions with only the exact install_request returned here. Never guess request fields.",
            "已禁用的条目会被隐藏。CLI 工具请使用 cyrene_cli 插件包；此处的结果只能把返回的精确 install_request 交给 ManageExtensions，绝不能猜测请求字段。",
        ),
    }, ensure_ascii=False)


handler = _tool_search_environment

__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler", "_tool_search_environment"]
