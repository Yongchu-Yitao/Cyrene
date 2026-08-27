"""Helpers shared by the native memory Plugins."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from agent.plugin import Plugin, PluginContext


def create_tool(
    definition: Mapping[str, Any],
    handler: Any,
    *,
    allow_parallel: bool = False,
    timeout_seconds: float = 180.0,
) -> Plugin:
    function = definition.get("function")
    if not isinstance(function, Mapping):
        raise TypeError("memory tool definition must contain function")
    parameters = function.get("parameters") or {"type": "object", "properties": {}}
    if not isinstance(parameters, Mapping):
        raise TypeError("memory tool parameters must be an object")
    return Plugin(
        name=str(function.get("name") or ""),
        description=str(function.get("description") or ""),
        input_schema=deepcopy(dict(parameters)),
        handler=handler,
        allow_parallel=allow_parallel,
        timeout_seconds=timeout_seconds,
    )


def service(context: PluginContext):
    from .service import MEMORY_SERVICE_ID, MemoryService

    candidate = context.services.get(MEMORY_SERVICE_ID)
    if isinstance(candidate, MemoryService):
        return candidate
    # Direct PluginRuntime calls do not necessarily open an AgentSession. Keep
    # those calls useful without requiring an open AgentSession.
    return MemoryService.from_plugin_context(context)


__all__ = ["create_tool", "service"]
