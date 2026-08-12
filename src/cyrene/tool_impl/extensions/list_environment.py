"""List installed and detected Cyrene runtimes, CLI tools, and MCP servers."""

from __future__ import annotations

import json
from typing import Any

from cyrene.extensions.service import get_extension_service
from cyrene.tooling.native_definitions import get_native_tool_def

TOOL_NAME = "ListEnvironment"
TOOL_DEF = get_native_tool_def(TOOL_NAME)
TOOL_METADATA = {
    "read_only": True,
    "resource_keys": ("extensions:global",),
    "requires_order": False,
}

_COLLECTIONS = {
    "mcp": "mcp",
    "cli": "cli",
    "toolchain": "toolchains",
}


def _source_summary(source: Any) -> Any:
    if not isinstance(source, dict):
        return source
    allowed = (
        "type", "ref", "backend", "repo", "tag", "bundle", "transport",
        "binding", "version", "id",
    )
    return {key: source[key] for key in allowed if source.get(key) not in (None, "")}


def _compact_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": str(item.get("kind") or ""),
        "id": str(item.get("id") or ""),
        "name": str(item.get("name") or item.get("id") or ""),
        "description": str(item.get("description") or ""),
        "status": str(item.get("observed_state") or "installed"),
        "enabled": bool(item.get("enabled", item.get("desired_state") != "disabled")),
        "version": str(item.get("version") or ""),
        "ownership": str(item.get("ownership") or ""),
        "health": str(item.get("health") or ""),
        "source": _source_summary(item.get("source")),
        "manual_selection": bool(item.get("manual_binding")),
    }


def is_installed(item: dict[str, Any]) -> bool:
    return (
        str(item.get("observed_state") or "") == "installed"
        or str(item.get("ownership") or "") in {"builtin", "system", "cyrene"}
    )


def is_explicitly_disabled(item: dict[str, Any]) -> bool:
    if str(item.get("desired_state") or "").strip().lower() == "disabled":
        return True
    return is_installed(item) and item.get("enabled") is False


def environment_key(kind: Any, extension_id: Any) -> tuple[str, str]:
    return (
        str(kind or "").strip().casefold(),
        str(extension_id or "").strip().casefold(),
    )


def environment_items(state: dict[str, Any]):
    """Yield each Agent-visible environment card once, including special built-ins."""
    seen: set[tuple[str, str]] = set()
    for kind, collection in _COLLECTIONS.items():
        for raw in state.get(collection, []) or []:
            if not isinstance(raw, dict):
                continue
            item = {**raw, "kind": kind}
            key = environment_key(kind, item.get("id"))
            if not key[1] or key in seen:
                continue
            seen.add(key)
            yield kind, item

    # Built-in uv is exposed by ExtensionService under infrastructure instead
    # of toolchains. Other internal infrastructure (for example mise) has no
    # public extension kind and is intentionally skipped.
    infrastructure = state.get("infrastructure")
    if isinstance(infrastructure, dict):
        for raw in infrastructure.values():
            if not isinstance(raw, dict):
                continue
            kind = str(raw.get("kind") or "").strip().casefold()
            if kind not in _COLLECTIONS:
                continue
            item = {**raw, "kind": kind}
            key = environment_key(kind, item.get("id"))
            if not key[1] or key in seen:
                continue
            seen.add(key)
            yield kind, item


async def _tool_list_environment(args: dict[str, Any], *_unused: Any) -> str:
    kind = str(args.get("kind") or "all").strip().lower()
    if kind != "all" and kind not in _COLLECTIONS:
        return json.dumps({"ok": False, "error": f"unsupported environment kind: {kind}"}, ensure_ascii=False)

    query = str(args.get("query") or "").strip().casefold()
    state = get_extension_service().list_extensions()
    items: list[dict[str, Any]] = []
    for selected_kind, raw in environment_items(state):
        if kind != "all" and selected_kind != kind:
            continue
        if not is_installed(raw) or is_explicitly_disabled(raw):
            continue
        compact = _compact_item(raw)
        haystack = " ".join((compact["id"], compact["name"], compact["description"], compact["version"])).casefold()
        if query and query not in haystack:
            continue
        items.append(compact)

    return json.dumps({
        "ok": True,
        "kind": kind,
        "query": query,
        "count": len(items),
        "items": items,
        "note": "This is discovery metadata only. Disabled extensions are intentionally hidden. CLI and runtime binaries are exposed through Cyrene's Agent process environment. Skills are discovered separately through skill_tools.",
    }, ensure_ascii=False)


handler = _tool_list_environment

__all__ = [
    "TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler",
    "environment_items", "environment_key", "is_explicitly_disabled",
    "is_installed", "_tool_list_environment",
]
