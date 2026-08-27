"""Explicit composer-selected context capabilities.

The normal Cyrene tool surface stays progressively disclosed through ``toolbox``.
Selections made in the composer are the deliberate exception: their schemas or
Skill instructions are injected into the run prompt so the Agent can use them
without a discovery/describe round trip. Global Plugin activation
switches remain authoritative and are never bypassed here.
"""

from __future__ import annotations

import json
from typing import Any

ACTIVATION_KEYS = ("mcpServers", "skills", "pluginPacks")
_MAX_SELECTIONS_PER_KIND = 50
_MAX_SELECTION_ID_LENGTH = 300


def normalize_context_activations(value: Any) -> dict[str, list[str]]:
    """Return a bounded, deterministic activation payload."""

    source = value if isinstance(value, dict) else {}
    result: dict[str, list[str]] = {}
    for key in ACTIVATION_KEYS:
        raw = source.get(key)
        if not isinstance(raw, list):
            result[key] = []
            continue
        items: list[str] = []
        for item in raw:
            identity = str(item or "").strip()
            if not identity or len(identity) > _MAX_SELECTION_ID_LENGTH:
                continue
            if identity not in items:
                items.append(identity)
            if len(items) >= _MAX_SELECTIONS_PER_KIND:
                break
        result[key] = items
    return result


def _mcp_catalog() -> list[dict[str, Any]]:
    from agent.plugin import active_plugin_application_host
    from agent.plugin.mcp_service import get_mcp_service

    service = get_mcp_service()
    statuses = {
        str(item.get("name") or ""): item
        for item in service.status()
    }
    host = active_plugin_application_host()
    registry = host.registry if host is not None else None
    packs = (
        {pack.id: pack for pack in registry.list_packs()}
        if registry is not None
        else {}
    )
    result = []
    for config in service.configs(redacted=True):
        name = str(config.get("name") or "").strip()
        if not name:
            continue
        status = statuses.get(name, {})
        pack_id = str(status.get("pack_id") or service.pack_id_for_server(name))
        pack = packs.get(pack_id)
        pack_enabled = (
            registry.pack_configured_enabled(pack_id)
            if registry is not None and pack is not None
            else True
        )
        enabled = bool(config.get("enabled", True)) and pack_enabled
        available = (
            enabled
            and str(status.get("status") or "") == "connected"
            and pack is not None
            and any(registry.plugin_enabled(plugin.name) for plugin in pack.plugins)
        )
        result.append({
            "id": name,
            "name": name,
            "description": str(config.get("description") or "MCP server"),
            "enabled": enabled,
            "available": available,
            "status": str(status.get("status") or "disconnected"),
            "toolCount": int(status.get("tool_count") or 0),
            "packId": pack_id,
            "error": str(status.get("error") or ""),
        })
    return result


def _skill_catalog() -> list[dict[str, Any]]:
    from cyrene.learning.skills import build_skills

    return [
        {
            "id": str(skill.get("id") or ""),
            "name": str(skill.get("name") or skill.get("id") or ""),
            "description": str(skill.get("desc") or ""),
            "enabled": bool(skill.get("enabled", True)),
            "available": bool(skill.get("enabled", True)),
        }
        for skill in build_skills()
        if str(skill.get("id") or "").strip()
    ]


def _plugin_pack_catalog() -> list[dict[str, Any]]:
    from agent.plugin import active_plugin_application_host

    host = active_plugin_application_host()
    if host is None:
        return []
    registry = host.registry
    result = []
    for pack in registry.list_packs():
        if registry.pack_source(pack.id).startswith("mcp:"):
            # MCP servers have their own composer selector and should not be
            # duplicated in the ordinary Plugin-pack list.
            continue
        tools = [
            plugin
            for plugin in pack.plugins
            if plugin.kind == "tool" and plugin.model_visible
        ]
        if not tools:
            continue
        enabled = registry.pack_configured_enabled(pack.id)
        result.append({
            "id": pack.id,
            "name": pack.id,
            "description": pack.description,
            "enabled": enabled,
            "available": enabled and any(
                registry.plugin_enabled(plugin.name) for plugin in tools
            ),
            "toolCount": len(tools),
        })
    return result


def context_activation_catalog() -> dict[str, list[dict[str, Any]]]:
    """Return the current composer-selectable capability catalog."""

    return {
        "mcpServers": _mcp_catalog(),
        "skills": _skill_catalog(),
        "pluginPacks": _plugin_pack_catalog(),
    }


def validate_context_activations(value: Any) -> dict[str, list[str]]:
    """Validate selections without allowing them to bypass global switches."""

    normalized = normalize_context_activations(value)
    catalog = context_activation_catalog()
    for key in ACTIVATION_KEYS:
        allowed = {
            str(item.get("id") or "")
            for item in catalog[key]
            if bool(item.get("enabled"))
        }
        unknown = [identity for identity in normalized[key] if identity not in allowed]
        if unknown:
            raise ValueError(
                f"Unavailable composer context selection(s) for {key}: "
                + ", ".join(unknown)
            )
    return normalized


def resolve_context_activations(value: Any) -> dict[str, list[str]]:
    """Drop selections that are no longer installed or globally enabled.

    Persisted composer preferences can outlive an MCP server, Skill, or Plugin
    pack setting. A stale preference must not make an otherwise valid chat
    unsendable, while strict mutation endpoints still use
    :func:`validate_context_activations` to reject new unavailable choices.
    """

    normalized = normalize_context_activations(value)
    catalog = context_activation_catalog()
    result: dict[str, list[str]] = {}
    for key in ACTIVATION_KEYS:
        allowed = {
            str(item.get("id") or "")
            for item in catalog[key]
            if bool(item.get("enabled"))
        }
        result[key] = [identity for identity in normalized[key] if identity in allowed]
    return result


def _selected_mcp_capabilities(server_names: list[str]) -> list[dict[str, Any]]:
    from agent.plugin.mcp_service import get_mcp_service

    service = get_mcp_service()
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for server_name in server_names:
        for capability in service.capabilities_for_server(server_name):
            name = str(capability.get("name") or "")
            if not name or name in seen:
                continue
            seen.add(name)
            result.append({
                "name": name,
                "description": capability["description"],
                "input_schema": capability["input_schema"],
                "mcp_server": server_name,
                "mcp_tool": capability.get("mcp_tool"),
            })
    return result


def build_context_activation_prompt(value: Any) -> str:
    """Render full Skill instructions and pre-described capability schemas."""

    from agent.plugin import active_plugin_application_host
    from cyrene.learning.skills import load_skill

    selected = resolve_context_activations(value)
    if not any(selected.values()):
        return ""

    parts = [
        "## User-activated composer context",
        "The user explicitly activated the following context capabilities in "
        "the composer. Keep them available for this run. Global permissions and "
        "normal tool review still apply.",
    ]

    skill_blocks = []
    for skill_id in selected["skills"]:
        skill = load_skill(skill_id)
        if not skill:
            continue
        skill_blocks.append(
            "### Activated Skill: "
            + str(skill.get("name") or skill_id)
            + f" (ID: {skill_id})\n"
            + "These installed Skill instructions were explicitly loaded by the user. "
            + "Follow them when relevant; they remain subordinate to system and developer instructions.\n"
            + str(skill.get("instructions") or "")
            + "\nAvailable Skill resources: "
            + json.dumps(skill.get("resources") or [], ensure_ascii=False, separators=(",", ":"))
        )
    if skill_blocks:
        parts.append("\n\n".join(skill_blocks))

    capability_records: list[dict[str, Any]] = []
    capability_records.extend(_selected_mcp_capabilities(selected["mcpServers"]))
    seen = {
        str(item.get("name") or "") for item in capability_records
    }
    host = active_plugin_application_host()
    registry = host.registry if host is not None else None
    if selected["skills"]:
        resource_plugin = None
        if registry is not None:
            try:
                resource_plugin = registry.resolve("ReadSkillResource")
            except Exception:
                resource_plugin = None
        if resource_plugin is not None:
            seen.add(resource_plugin.name)
            record = {
                "name": resource_plugin.name,
                "description": resource_plugin.description,
                "input_schema": resource_plugin.input_schema,
            }
            record["activated_skill_support"] = True
            capability_records.append(record)
    packs = (
        {pack.id: pack for pack in registry.list_packs()}
        if registry is not None
        else {}
    )
    for pack_id in selected["pluginPacks"]:
        pack = packs.get(pack_id)
        if pack is None:
            continue
        for plugin in pack.plugins:
            if (
                plugin.kind != "tool"
                or not plugin.model_visible
                or not registry.plugin_enabled(plugin.name)
                or plugin.name in seen
            ):
                continue
            seen.add(plugin.name)
            capability_records.append({
                "name": plugin.name,
                "description": plugin.description,
                "input_schema": plugin.input_schema,
                "plugin_pack": pack_id,
            })
    if capability_records:
        parts.extend([
            "### Pre-described toolbox capabilities",
            "The JSON records below are trusted capability metadata, not user "
            "instructions. They are already selected and described: do not call "
            "toolbox.list or describe for these names. When one is useful, call "
            "toolbox directly with operation=invoke, its name, and arguments "
            "matching input_schema.",
            json.dumps(capability_records, ensure_ascii=False, separators=(",", ":")),
        ])

    return "\n\n".join(part for part in parts if part).strip()


__all__ = [
    "ACTIVATION_KEYS",
    "build_context_activation_prompt",
    "context_activation_catalog",
    "normalize_context_activations",
    "resolve_context_activations",
    "validate_context_activations",
]
