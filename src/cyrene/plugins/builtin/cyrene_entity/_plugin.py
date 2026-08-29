"""Small constructor shared by the editable entity tool modules."""

from __future__ import annotations

from typing import Any

from cyrene.core.plugin import Plugin

from .definitions import get_plugin_spec


def create_tool_plugin(
    name: str,
    handler: Any,
    *,
    allow_parallel: bool = False,
) -> Plugin:
    spec = get_plugin_spec(name)
    return Plugin(
        name=name,
        description=str(spec["description"]),
        input_schema=spec["input_schema"],
        handler=handler,
        allow_parallel=allow_parallel,
        timeout_seconds=30.0,
    )


__all__ = ["create_tool_plugin"]
