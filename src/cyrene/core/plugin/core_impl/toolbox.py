"""Stable model-facing gateway to the live Plugin registry."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..execution import PluginInvocationError, invoke_plugin, require_plugin_execution
from ..plugin import Plugin, PluginContext
from ..resource_effects import split_resource_reveal, workspace_resource_locations

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
            "description": "Plugin or pack name to describe; Plugin name to invoke.",
        },
        "names": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "maxItems": 20,
            "uniqueItems": True,
            "description": "Plugin or pack names to describe in one call.",
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

    @staticmethod
    def _agent_id(context: PluginContext) -> str:
        direct = str(context.data.get("agent_id") or "").strip()
        if direct:
            return direct
        run_context = context.data.get("run_context")
        if isinstance(run_context, Mapping):
            nested = str(run_context.get("agent_id") or "").strip()
            if nested:
                return nested
        return "main"

    def _deferred(
        self,
        name: str,
        failures: tuple[PluginLoadFailure, ...] = (),
        *,
        agent_id: str = "main",
    ) -> RegisteredPlugin:
        registered = self._registry.registered(name)
        if (
            registered.plugin.kind != "tool"
            or not registered.plugin.model_visible
            or registered.source == "core"
            or registered.plugin.agent_exposure != "discoverable"
            or not self._registry.plugin_accessible(name, agent_id=agent_id)
        ):
            raise ValueError(f"Plugin is not available through toolbox: {name}")
        failed_source = next(
            (
                failure
                for failure in failures
                if str(failure.path) == registered.source
            ),
            None,
        )
        if failed_source is not None:
            raise RuntimeError(
                f"Plugin source failed to refresh for {name!r}; refusing to use "
                f"the stale loaded version: {failed_source.error}"
            )
        return registered

    def _list(
        self,
        *,
        agent_id: str,
    ) -> tuple[list[str], list[dict[str, str]], list[str]]:
        packs: list[str] = []
        pack_descriptions: list[dict[str, str]] = []
        for pack in self._registry.list_packs():
            if self._registry.pack_source(pack.id) == "core":
                continue
            if any(
                plugin.kind == "tool"
                and plugin.model_visible
                and plugin.agent_exposure == "discoverable"
                and self._registry.plugin_accessible(
                    plugin.name,
                    agent_id=agent_id,
                )
                for plugin in pack.plugins
            ):
                packs.append(pack.id)
                pack_descriptions.append(
                    {
                        "name": pack.id,
                        "description": pack.description,
                    }
                )

        standalone_tools: list[str] = []
        for registered in self._registry.list_plugins():
            plugin = registered.plugin
            if (
                plugin.kind != "tool"
                or not plugin.model_visible
                or plugin.agent_exposure != "discoverable"
                or registered.pack_id is not None
                or registered.source == "core"
                or not self._registry.plugin_accessible(
                    plugin.name,
                    agent_id=agent_id,
                )
            ):
                continue
            standalone_tools.append(plugin.name)
        return packs, pack_descriptions, standalone_tools

    def _pack_plugins(self, name: str, *, agent_id: str) -> tuple[Plugin, ...]:
        for pack in self._registry.list_packs():
            if pack.id != name or self._registry.pack_source(pack.id) == "core":
                continue
            return tuple(
                plugin
                for plugin in pack.plugins
                if plugin.kind == "tool"
                and plugin.model_visible
                and plugin.agent_exposure == "discoverable"
                and self._registry.plugin_accessible(
                    plugin.name,
                    agent_id=agent_id,
                )
            )
        return ()

    def _describe(
        self,
        arguments: dict[str, Any],
        failures: tuple[PluginLoadFailure, ...],
        *,
        agent_id: str,
    ) -> list[dict[str, Any]]:
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
        described: set[str] = set()
        for requested_name in requested:
            pack_plugins = self._pack_plugins(requested_name, agent_id=agent_id)
            registered_plugins = (
                tuple(
                    self._deferred(
                        plugin.name,
                        failures,
                        agent_id=agent_id,
                    )
                    for plugin in pack_plugins
                )
                if pack_plugins
                else (
                    self._deferred(
                        requested_name,
                        failures,
                        agent_id=agent_id,
                    ),
                )
            )
            for registered in registered_plugins:
                plugin = registered.plugin
                if plugin.name in described:
                    continue
                described.add(plugin.name)
                descriptions.append(
                    {
                        "name": plugin.name,
                        "description": plugin.description,
                        "input_schema": plugin.model_input_schema(
                            allow_resource_reveal=agent_id == "main"
                        ),
                        "pack": registered.pack_id,
                    }
                )
        return descriptions

    async def _invoke(
        self,
        arguments: dict[str, Any],
        context: PluginContext,
        failures: tuple[PluginLoadFailure, ...],
        *,
        agent_id: str,
    ) -> dict[str, Any]:
        name = str(arguments.get("name") or "").strip()
        if not name:
            raise ValueError("toolbox invoke requires name")
        registered = self._deferred(name, failures, agent_id=agent_id)
        nested_arguments = arguments.get("arguments") or {}
        if not isinstance(nested_arguments, Mapping):
            raise TypeError("toolbox invoke arguments must be an object")
        nested_arguments, reveal = split_resource_reveal(
            nested_arguments,
            effects=registered.plugin.resource_effects,
            allow_reveal=agent_id == "main",
        )
        argument_repairs = tuple(
            dict(repair)
            for repair in require_plugin_execution().call.argument_repairs
        )
        nested_error: dict[str, Any] | None = None
        try:
            nested_value = await invoke_plugin(
                name,
                dict(nested_arguments),
                review=True,
                arguments_normalized=True,
                nested_arguments_normalized=True,
                argument_repairs=argument_repairs,
            )
        except PluginInvocationError as exc:
            nested_value = None
            nested_error = self._nested_error(name, exc)
        result: dict[str, Any] = {
            "operation": "invoke",
            "name": name,
            # Persist the live discovery identity with historical results.
            "pack": registered.pack_id,
            "result": nested_value,
        }
        if argument_repairs:
            result["argument_repairs"] = list(argument_repairs)
        if nested_error is not None:
            result["error"] = nested_error
        project_id = str(context.data.get("project_id") or "")
        if nested_error is None and registered.plugin.resource_effects and project_id:
            locations = (
                workspace_resource_locations(
                    registered.plugin.resource_effects,
                    nested_arguments,
                    workspace=context.workspace,
                    project_id=project_id,
                    phase="completed",
                )
                if context.workspace is not None
                else ()
            )
            if locations:
                result["presentation"] = {
                    "locations": list(locations),
                    "reveal": reveal,
                    "phase": "completed",
                }
        return result

    @staticmethod
    def _nested_error(name: str, exc: PluginInvocationError) -> dict[str, Any]:
        message = str(exc.result.error or exc)
        error = (
            exc.result.failure.as_dict()
            if exc.result.failure is not None
            else {
                "error_code": (
                    "plugin_invalid_arguments"
                    if "Invalid arguments" in message or "插件参数无效" in message
                    else "plugin_invocation_failed"
                ),
                "message": message,
                "retryable": False,
                "retry_scope": "never",
                "retry_after_ms": None,
                "circuit_scope": "none",
                "details": {},
            }
        )
        error["plugin"] = name
        error["code"] = (
            "invalid_arguments"
            if error.get("error_code") == "plugin_invalid_arguments"
            else str(error.get("error_code") or "plugin_invocation_failed")
        )
        return error

    async def __call__(
        self,
        arguments: dict[str, Any],
        context: PluginContext,
    ) -> dict[str, Any]:
        failures = self._registry.refresh()
        self._registry.refresh_customizations()
        refresh_errors = self._failure_values(failures)
        operation = str(arguments.get("operation") or "")
        agent_id = self._agent_id(context)

        if operation == "list":
            packs, pack_descriptions, standalone_tools = self._list(
                agent_id=agent_id
            )
            result: dict[str, Any] = {
                "operation": "list",
                "packs": packs,
                "pack_descriptions": pack_descriptions,
                "standalone_tools": standalone_tools,
            }
        elif operation == "describe":
            result = {
                "operation": "describe",
                "plugins": self._describe(
                    arguments,
                    failures,
                    agent_id=agent_id,
                ),
            }
        elif operation == "invoke":
            result = await self._invoke(
                arguments,
                context,
                failures,
                agent_id=agent_id,
            )
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
            "Gateway to deferred Plugins. Use list to inspect every current Plugin pack "
            "and standalone tool, describe to load current input schemas, and invoke "
            "to execute a Plugin."
        ),
        input_schema=_TOOLBOX_INPUT_SCHEMA,
        handler=_ToolboxHandler(registry),
        metadata={"read_only_gateway": True},
    )


__all__ = ["TOOLBOX_PLUGIN_NAME", "create_toolbox_plugin"]
