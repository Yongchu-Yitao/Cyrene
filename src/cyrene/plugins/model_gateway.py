"""Application- and session-level access to model Provider Plugins."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from .model_router import MODEL_ROUTER_PLUGIN, create_model_router_plugin
from .tool_call_parsers import (
    CODEX_OAUTH_TOOL_CALL_PARSER_PLUGIN,
    GENERIC_TOOL_CALL_PARSER_PLUGIN,
)
from cyrene.core.plugin.plugin import PluginContext
from cyrene.core.plugin.model import ModelGatewayError
from cyrene.core.plugin.registry import PluginRegistry, PluginRegistryError
from cyrene.core.plugin.runtime import PluginRuntime


def ensure_model_router(registry: PluginRegistry) -> None:
    """Install the model router and its parser Plugins as one core boundary."""

    required = (
        GENERIC_TOOL_CALL_PARSER_PLUGIN,
        CODEX_OAUTH_TOOL_CALL_PARSER_PLUGIN,
        create_model_router_plugin(),
    )
    registered_by_name = {
        item.plugin.name: item for item in registry.list_plugins()
    }
    for plugin in required:
        registered = registered_by_name.get(plugin.name)
        if registered is None:
            registry.register_plugin(plugin, source="core")
            continue
        if registered.source != "core":
            raise PluginRegistryError(
                f"Core model Plugin is shadowed by {registered.source}: {plugin.name}"
            )
        if registered.plugin.kind != plugin.kind:
            raise PluginRegistryError(
                f"Core model Plugin has the wrong kind: {plugin.name}"
            )


class PluginModelGateway:
    """Call the configured model route exclusively through PluginRuntime."""

    def __init__(
        self,
        registry: PluginRegistry,
        runtime: PluginRuntime | None = None,
    ) -> None:
        ensure_model_router(registry)
        self.registry = registry
        self.runtime = runtime or PluginRuntime(registry)

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
        route: str = "primary",
        caller: str = "auxiliary",
        session_id: str = "",
        model_identity: Mapping[str, Any] | None = None,
        context: PluginContext | None = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "messages": [dict(message) for message in messages],
            "route": str(route or "primary").strip().lower(),
        }
        for name, value in (
            ("tools", tools),
            ("tool_choice", tool_choice),
            ("max_tokens", max_tokens),
            ("temperature", temperature),
            ("response_format", response_format),
        ):
            if value is not None:
                arguments[name] = value

        data = {
            "caller": str(caller or "auxiliary"),
            "model_call_kind": "auxiliary",
            "model_route": arguments["route"],
            "session_id": str(session_id or ""),
        }
        if model_identity is not None:
            data["model_identity"] = dict(model_identity)
        if context is None:
            invocation_context = PluginContext(data=data)
        else:
            context_data = dict(context.data)
            context_data.pop("model_identity", None)
            invocation_context = replace(
                context,
                data={**context_data, **data},
            )
        result = await self.runtime.call(
            MODEL_ROUTER_PLUGIN,
            arguments,
            invocation_context,
        )
        if not result.success:
            raise ModelGatewayError(result)
        if not isinstance(result.value, Mapping):
            raise RuntimeError("Model Plugin returned a non-object result")
        return dict(result.value)


__all__ = ["PluginModelGateway", "ensure_model_router"]
