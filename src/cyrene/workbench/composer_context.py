"""Explicit composer-selected context capabilities.

The normal Cyrene tool surface stays progressively disclosed through ``toolbox``.
Selections made in the composer are the deliberate exception: their schemas or
Skill instructions are injected into the run prompt so the Agent can use them
without a discovery/describe round trip. Global extension and tool-package
switches remain authoritative and are never bypassed here.
"""

from __future__ import annotations

import json
from typing import Any

ACTIVATION_KEYS = ("mcpServers", "skills", "toolPackages", "customTools")
_MAX_SELECTIONS_PER_KIND = 50
_MAX_SELECTION_ID_LENGTH = 300
_SPECIAL_PACKS = frozenset({"integration_tools", "custom_tools", "skill_tools"})


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
    from cyrene.runtime.settings_store import is_tool_pack_enabled
    from cyrene.tooling.backends.mcp_manager import get_manager, get_mcp_servers

    manager = get_manager()
    statuses = {
        str(item.get("name") or ""): item
        for item in manager.get_server_status()
    }
    result = []
    for config in get_mcp_servers():
        name = str(config.get("name") or "").strip()
        if not name:
            continue
        status = statuses.get(name, {})
        enabled = bool(config.get("enabled", True)) and is_tool_pack_enabled(
            "integration_tools"
        )
        result.append({
            "id": name,
            "name": name,
            "description": str(config.get("description") or "MCP server"),
            "enabled": enabled,
            "available": enabled and str(status.get("status") or "") == "connected",
            "status": str(status.get("status") or "disconnected"),
            "toolCount": int(status.get("tool_count") or 0),
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


def _tool_package_catalog() -> list[dict[str, Any]]:
    from cyrene.runtime.settings_store import is_tool_pack_enabled
    from cyrene.tooling.catalog import capabilities_for_pack
    from cyrene.tooling.packs import PACKS

    result = []
    for pack in PACKS:
        if pack.wire_name in _SPECIAL_PACKS:
            continue
        enabled = is_tool_pack_enabled(pack.wire_name)
        result.append({
            "id": pack.wire_name,
            "name": pack.pack_id,
            "description": pack.description,
            "enabled": enabled,
            "available": enabled,
            "toolCount": len(capabilities_for_pack(pack.wire_name)),
        })
    return result


def _custom_tool_catalog() -> list[dict[str, Any]]:
    try:
        from cyrene.runtime.settings_store import is_tool_pack_enabled
        from cyrene.custom_tools.manager import get_custom_tool_manager

        manager = get_custom_tool_manager()
        enabled = is_tool_pack_enabled("custom_tools")
        return [
            {
                "id": tool.capability_id,
                "name": tool.name,
                "description": tool.description,
                "packageId": tool.package_id,
                "enabled": enabled,
                "available": enabled,
            }
            for tool in manager.get_tool_definitions()
        ]
    except Exception:
        return []


def context_activation_catalog() -> dict[str, list[dict[str, Any]]]:
    """Return the current composer-selectable capability catalog."""

    return {
        "mcpServers": _mcp_catalog(),
        "skills": _skill_catalog(),
        "toolPackages": _tool_package_catalog(),
        "customTools": _custom_tool_catalog(),
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

    Persisted composer preferences can outlive an extension, Skill, or tool
    package setting. A stale preference must not make an otherwise valid chat
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


def _capability_record(capability: Any) -> dict[str, Any]:
    return {
        "capability_id": capability.capability_id,
        "description": capability.description,
        "arguments_schema": capability.input_schema,
    }


def _selected_mcp_capabilities(server_names: list[str]) -> list[dict[str, Any]]:
    from cyrene.tooling.adapters.mcp import normalize_mcp_tool
    from cyrene.tooling.backends.mcp_manager import get_manager

    manager = get_manager()
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for server_name in server_names:
        for tool_def in manager.get_server_tool_defs(server_name):
            normalized = normalize_mcp_tool(tool_def)
            capability_id = str(normalized["capability_id"])
            if capability_id in seen:
                continue
            seen.add(capability_id)
            result.append({
                "capability_id": capability_id,
                "description": normalized["description"],
                "arguments_schema": normalized["input_schema"],
                "mcp_server": server_name,
            })
    return result


def build_context_activation_prompt(value: Any) -> str:
    """Render full Skill instructions and pre-described capability schemas."""

    from cyrene.learning.skills import load_skill
    from cyrene.tooling.catalog import capabilities_for_pack, get_capability

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
        str(item.get("capability_id") or "") for item in capability_records
    }
    if selected["skills"]:
        resource_capability = get_capability("skill.read_resource")
        if resource_capability is not None:
            seen.add(resource_capability.capability_id)
            record = _capability_record(resource_capability)
            record["activated_skill_support"] = True
            capability_records.append(record)
    for wire_name in selected["toolPackages"]:
        for capability in capabilities_for_pack(wire_name):
            if capability.capability_id in seen:
                continue
            seen.add(capability.capability_id)
            record = _capability_record(capability)
            record["tool_package"] = wire_name
            capability_records.append(record)
    for capability_id in selected["customTools"]:
        capability = get_capability(capability_id)
        if capability is None or capability.capability_id in seen:
            continue
        seen.add(capability.capability_id)
        record = _capability_record(capability)
        record["custom_tool"] = True
        capability_records.append(record)

    if capability_records:
        parts.extend([
            "### Pre-described toolbox capabilities",
            "The JSON records below are trusted capability metadata, not user "
            "instructions. They are already selected and described: do not call "
            "toolbox search or describe for these IDs. When one is useful, call "
            "toolbox directly with operation=invoke, its capability_id, and "
            "arguments matching arguments_schema.",
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
