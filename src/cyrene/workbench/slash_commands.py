"""Unified slash-command catalog for the built-in Cyrene Agent."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from agent.commands import BUILTIN_COMMAND_IDS

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


def _activation_catalog() -> list[dict[str, Any]]:
    from cyrene.workbench.composer_context import context_activation_catalog

    result: list[dict[str, Any]] = []
    catalog = context_activation_catalog()
    for kind, group in _GROUP_BY_ACTIVATION.items():
        prefix = _PREFIX_BY_ACTIVATION[kind]
        for item in catalog[kind]:
            if not bool(item.get("enabled")):
                continue
            identity = str(item.get("id") or "").strip()
            if not identity:
                continue
            command_id = f"{prefix}:{_encoded(identity)}"
            result.append({
                "id": command_id,
                "label": str(item.get("name") or identity),
                "description": str(item.get("description") or item.get("status") or ""),
                "group": group,
                "source": "context",
                "activation": {"kind": kind, "id": identity},
            })
    return result


def local_slash_command_catalog() -> list[dict[str, Any]]:
    """Return built-in workflows and currently enabled context commands."""

    return [*_builtin_catalog(), *_activation_catalog()]


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
    local = _activation_catalog()
    return next((item for item in local if item["id"] == target), None)


__all__ = [
    "local_slash_command_catalog",
    "resolve_slash_command",
    "slash_command_catalog",
]
