"""Unified slash-command catalog for the built-in Cyrene Agent."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from cyrene.workbench.application.commands import BUILTIN_COMMAND_IDS

_GROUP_BY_ACTIVATION = {
    "mcpServers": "mcp",
    "skills": "skill",
    "pluginPacks": "pluginPack",
}
_PREFIX_BY_ACTIVATION = {
    "mcpServers": "mcp",
    "skills": "skill",
    "pluginPacks": "plugin",
}


def _composer_context_service():
    from cyrene.core.plugin import application_plugin_service

    service = application_plugin_service("composer_context")
    if service is None:
        raise RuntimeError(
            "Required Plugin application service is unavailable: composer_context"
        )
    return service


def _encoded(value: Any) -> str:
    return quote(str(value or "").strip(), safe="._-")


def _builtin_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": command_id,
            "labelKey": f"workbenchChat.command.{command_id}.label",
            "descKey": f"workbenchChat.command.{command_id}.desc",
            "label": command_id,
            "description": "",
            "group": "workflow",
            "source": "builtin",
        }
        for command_id in BUILTIN_COMMAND_IDS
    ]


def _activation_catalog(
    *, excluded_plugin_packs: frozenset[str] = frozenset()
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    catalog = _composer_context_service().catalog()
    for kind, group in _GROUP_BY_ACTIVATION.items():
        prefix = _PREFIX_BY_ACTIVATION[kind]
        for item in catalog[kind]:
            if not bool(item.get("enabled")):
                continue
            identity = str(item.get("id") or "").strip()
            if not identity:
                continue
            if kind == "pluginPacks" and identity in excluded_plugin_packs:
                continue
            command_id = f"{prefix}:{_encoded(identity)}"
            result.append({
                "id": command_id,
                "label": str(item.get("name") or identity),
                "description": str(item.get("description") or item.get("status") or ""),
                "group": group,
                "source": "context",
                "activation": {"kind": kind, "id": identity},
                "i18n": dict(item.get("i18n") or {}),
            })
    return result


def _plugin_workflow_catalog() -> list[dict[str, Any]]:
    """Read first-class workflow commands contributed by operational packs."""

    from cyrene.core.plugin import application_plugin_scope

    host = application_plugin_scope()
    contributions = (
        host.workbench_contributions()
        if host is not None and hasattr(host, "workbench_contributions")
        else {}
    )
    result: list[dict[str, Any]] = []
    commands = contributions.get("commands", ()) if isinstance(contributions, dict) else ()
    for raw in commands:
        if not isinstance(raw, dict):
            continue
        command_id = str(raw.get("id") or "").strip()
        if command_id:
            result.append(dict(raw))
    return result


def _workflow_owned_pack_ids(commands: list[dict[str, Any]]) -> frozenset[str]:
    return frozenset(
        str(item.get("pack_id") or "").strip()
        for item in commands
        if isinstance(item.get("workflow"), dict)
        and str(item.get("pack_id") or "").strip()
    )


def local_slash_command_catalog() -> list[dict[str, Any]]:
    """Return built-in workflows and currently enabled context commands."""

    workflows = _plugin_workflow_catalog()
    workflow_packs = _workflow_owned_pack_ids(workflows)
    return [
        *_builtin_catalog(),
        *workflows,
        *_activation_catalog(excluded_plugin_packs=workflow_packs),
    ]


async def slash_command_catalog(project_id: str = "") -> list[dict[str, Any]]:
    del project_id
    return local_slash_command_catalog()


async def resolve_slash_command(
    command_id: str, project_id: str = ""
) -> dict[str, Any] | None:
    del project_id
    target = str(command_id or "").strip().lstrip("/")
    if not target:
        return None
    if target == "深度反思":
        target = "deep-reflect"
    if target in BUILTIN_COMMAND_IDS:
        return next(item for item in _builtin_catalog() if item["id"] == target)
    workflows = _plugin_workflow_catalog()
    workflow_packs = _workflow_owned_pack_ids(workflows)
    local = [
        *workflows,
        *_activation_catalog(excluded_plugin_packs=workflow_packs),
    ]
    return next((item for item in local if item["id"] == target), None)


__all__ = [
    "local_slash_command_catalog",
    "resolve_slash_command",
    "slash_command_catalog",
]
