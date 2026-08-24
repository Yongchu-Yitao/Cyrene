"""Unified slash-command catalog for the built-in Cyrene Agent."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from cyrene.agent.commands import BUILTIN_COMMAND_IDS

_COMMAND_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:%-]{0,299}$")
_GROUP_BY_ACTIVATION = {
    "mcpServers": "mcp",
    "skills": "skill",
    "toolPackages": "toolPackage",
    "customTools": "customTool",
}
_PREFIX_BY_ACTIVATION = {
    "mcpServers": "mcp",
    "skills": "skill",
    "toolPackages": "pack",
    "customTools": "custom",
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


def _method_name(value: Any) -> str:
    if isinstance(value, dict) and isinstance(value.get("$method"), str):
        return str(value["$method"])
    return ""


async def _plugin_catalog(project_id: str) -> list[dict[str, Any]]:
    project_id = str(project_id or "").strip()
    if not project_id:
        return []
    from cyrene.plugins.manager import get_plugin_manager

    contributions = await get_plugin_manager().contributions(
        project_id, "cyrene.command"
    )
    reserved = {item["id"] for item in local_slash_command_catalog()}
    result: list[dict[str, Any]] = []
    for item in contributions:
        plugin_id = str(item.get("pluginId") or "").strip()
        contribution_id = str(item.get("id") or "").strip()
        requested = str(item.get("command") or contribution_id).strip().lstrip("/")
        prompt = str(item.get("prompt") or "").strip()
        prepare_method = _method_name(
            item.get("prepare") or item.get("execute") or item.get("run")
        )
        if not plugin_id or not contribution_id or (not prompt and not prepare_method):
            continue
        command_id = requested if _COMMAND_RE.fullmatch(requested) else ""
        if not command_id or command_id in reserved:
            command_id = f"plugin:{_encoded(plugin_id)}:{_encoded(contribution_id)}"
        reserved.add(command_id)
        result.append({
            "id": command_id,
            "label": str(item.get("title") or item.get("label") or contribution_id),
            "description": str(item.get("description") or ""),
            "group": "plugin",
            "source": "plugin",
            "pluginId": plugin_id,
            "contributionId": contribution_id,
            "prompt": prompt,
            "prepareMethod": prepare_method,
        })
    return result


async def slash_command_catalog(project_id: str = "") -> list[dict[str, Any]]:
    commands = [*local_slash_command_catalog(), *await _plugin_catalog(project_id)]
    return [
        {
            key: value
            for key, value in item.items()
            if key not in {"prompt", "prepareMethod"}
        }
        for item in commands
    ]


async def resolve_slash_command(
    command_id: str, project_id: str = ""
) -> dict[str, Any] | None:
    target = str(command_id or "").strip().lstrip("/")
    if not target:
        return None
    if target == "深度反思":
        target = "deep-reflect"
    if target in BUILTIN_COMMAND_IDS:
        return next(item for item in _builtin_catalog() if item["id"] == target)
    local = _activation_catalog()
    match = next((item for item in local if item["id"] == target), None)
    if match is not None:
        return match
    return next(
        (item for item in await _plugin_catalog(project_id) if item["id"] == target),
        None,
    )


async def prepare_plugin_command_prompt(
    descriptor: dict[str, Any], *, arguments: str, chat_id: str, project_id: str
) -> str:
    """Resolve one trusted enabled plugin command into an Agent instruction."""

    prompt = str(descriptor.get("prompt") or "").strip()
    method = str(descriptor.get("prepareMethod") or "").strip()
    if method:
        from cyrene.plugins.manager import get_plugin_manager

        result = await get_plugin_manager().call(
            str(descriptor.get("pluginId") or ""),
            project_id,
            method,
            {
                "commandId": str(descriptor.get("contributionId") or ""),
                "arguments": str(arguments or ""),
                "chatId": chat_id,
                "projectId": project_id,
            },
            timeout=30.0,
        )
        if isinstance(result, str):
            prompt = result.strip()
        elif isinstance(result, dict):
            prompt = str(result.get("prompt") or result.get("instruction") or "").strip()
    if not prompt:
        raise ValueError("Plugin command did not provide an Agent prompt")
    suffix = str(arguments or "").strip()
    return (
        "## User-invoked project plugin command\n"
        + prompt
        + ("\n\nCommand arguments from the user:\n" + suffix if suffix else "")
    )


__all__ = [
    "local_slash_command_catalog",
    "prepare_plugin_command_prompt",
    "resolve_slash_command",
    "slash_command_catalog",
]
