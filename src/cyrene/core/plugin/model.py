"""Generic model service backed by one registered ``kind=model`` Plugin."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from .plugin import PluginContext
from .runtime import PluginRuntime


class RuntimeModelGateway:
    """Expose a model Plugin through the small service API used by core."""

    def __init__(self, runtime: PluginRuntime, plugin_name: str) -> None:
        self.runtime = runtime
        self.plugin_name = str(plugin_name)

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        context: PluginContext | None = None,
        **options: Any,
    ) -> dict[str, Any]:
        arguments = {"messages": [dict(message) for message in messages]}
        arguments.update(
            (name, value)
            for name, value in options.items()
            if value is not None and name not in {"caller", "session_id"}
        )
        invocation_context = context or PluginContext()
        context_data = dict(invocation_context.data)
        for name in ("caller", "session_id"):
            value = options.get(name)
            if value is not None:
                context_data[name] = value
        result = await self.runtime.call(
            self.plugin_name,
            arguments,
            replace(invocation_context, data=context_data),
        )
        if not result.success:
            raise RuntimeError(result.error or "Model Plugin call failed")
        if not isinstance(result.value, Mapping):
            raise RuntimeError("Model Plugin returned a non-object result")
        return dict(result.value)


__all__ = ["RuntimeModelGateway"]
