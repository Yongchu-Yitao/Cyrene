"""Stable model-facing gateway to the live Plugin registry."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..plugin import Plugin, PluginContext

if TYPE_CHECKING:
    from ..registry import PluginLoadFailure, PluginRegistry, RegisteredPlugin


TOOLBOX_PLUGIN_NAME = "toolbox"

_TOOLBOX_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": ["list", "describe", "invoke"],
        },
        "name": {
            "type": "string",
            "minLength": 1,
            "description": "Plugin name to describe or invoke.",
        },
        "names": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "maxItems": 20,
            "uniqueItems": True,
            "description": "Plugin names to describe in one call.",
        },
        "arguments": {
            "type": "object",
            "description": "Arguments for the current Plugin schema.",
        },
    },
    "required": ["operation"],
    "additionalProperties": False,
}


class _ToolboxHandler:
    def __init__(self, registry: PluginRegistry) -> None:
        self._registry = registry

    @staticmethod
    def _failure_values(failures: tuple[PluginLoadFailure, ...]) -> list[dict[str, str]]:
        return [
            {"path": str(failure.path), "error": failure.error}
            for failure in failures
        ]

    def _deferred(self, name: str) -> RegisteredPlugin:
        registered = self._registry.registered(name)
        if registered.plugin.kind != "tool" or registered.source == "core":
            raise ValueError(f"Plugin is not available through toolbox: {name}")
        return registered

    @staticmethod
    def _tool_summary(plugin: Plugin) -> dict[str, str]:
        return {
            "name": plugin.name,
            "description": plugin.description,
        }

    def _list(self) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        packs: list[dict[str, Any]] = []
        for pack in self._registry.list_packs():
            if self._registry.pack_source(pack.id) == "core":
                continue
            tools = [
                self._tool_summary(plugin)
                for plugin in pack.plugins
                if plugin.kind == "tool"
            ]
            if tools:
                packs.append(
                    {
                        "id": pack.id,
                        "description": pack.description,
                        "tools": tools,
                    }
                )

        standalone_tools: list[dict[str, str]] = []
        for registered in self._registry.list_plugins():
            plugin = registered.plugin
            if (
                plugin.kind != "tool"
                or registered.pack_id is not None
                or registered.source == "core"
            ):
                continue
            standalone_tools.append(self._tool_summary(plugin))
        return packs, standalone_tools

    def _describe(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        requested: list[str] = []
        name = str(arguments.get("name") or "").strip()
        if name:
            requested.append(name)
        for item in arguments.get("names") or ():
            normalized = str(item).strip()
            if normalized and normalized not in requested:
                requested.append(normalized)
        if not requested:
            raise ValueError("toolbox describe requires name or names")

        descriptions: list[dict[str, Any]] = []
        for plugin_name in requested:
            registered = self._deferred(plugin_name)
            plugin = registered.plugin
            descriptions.append(
                {
                    "name": plugin.name,
                    "description": plugin.description,
                    "input_schema": deepcopy(dict(plugin.input_schema)),
                    "pack": registered.pack_id,
                }
            )
        return descriptions

    async def __call__(
        self,
        arguments: dict[str, Any],
        context: PluginContext,
    ) -> dict[str, Any]:
        failures = self._registry.refresh()
        refresh_errors = self._failure_values(failures)
        operation = str(arguments.get("operation") or "")

        if operation == "list":
            packs, standalone_tools = self._list()
            result: dict[str, Any] = {
                "operation": "list",
                "packs": packs,
                "standalone_tools": standalone_tools,
            }
        elif operation == "describe":
            result = {
                "operation": "describe",
                "plugins": self._describe(arguments),
            }
        elif operation == "invoke":
            name = str(arguments.get("name") or "").strip()
            if not name:
                raise ValueError("toolbox invoke requires name")
            self._deferred(name)
            nested_arguments = arguments.get("arguments") or {}
            if not isinstance(nested_arguments, Mapping):
                raise TypeError("toolbox invoke arguments must be an object")

            # Resolve again inside Runtime so validation always uses the Plugin
            # and input_schema that are current for this invocation.
            from ..runtime import PluginRuntime

            nested = await PluginRuntime(self._registry).call(
                name,
                dict(nested_arguments),
                replace(context, hooks=None),
            )
            if not nested.success:
                raise RuntimeError(nested.error)
            result = {
                "operation": "invoke",
                "name": name,
                "result": nested.value,
            }
        else:  # The Runtime schema normally rejects this before execution.
            raise ValueError(f"unsupported toolbox operation: {operation}")

        if refresh_errors:
            result["refresh_errors"] = refresh_errors
        return result


def create_toolbox_plugin(registry: PluginRegistry) -> Plugin:
    """Bind the fixed toolbox protocol to one live PluginRegistry."""

    return Plugin(
        name=TOOLBOX_PLUGIN_NAME,
        description=(
            "Gateway to deferred Plugins. Use list to inspect every current tool pack "
            "and standalone tool, describe to load current input schemas, and invoke "
            "to execute a Plugin."
        ),
        input_schema=_TOOLBOX_INPUT_SCHEMA,
        handler=_ToolboxHandler(registry),
    )


__all__ = ["TOOLBOX_PLUGIN_NAME", "create_toolbox_plugin"]
