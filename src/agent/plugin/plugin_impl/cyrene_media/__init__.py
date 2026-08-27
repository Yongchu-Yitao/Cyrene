"""Editable Cyrene media Plugin pack."""

from agent.plugin import Plugin, PluginPack

from . import start_media_generation

_FUNCTION = start_media_generation.TOOL_DEF["function"]
plugin_pack = PluginPack(
    id="cyrene_media",
    description="Start asynchronous media generation.",
    plugins=(
        Plugin(
            name=str(_FUNCTION["name"]),
            description=str(_FUNCTION.get("description") or ""),
            input_schema=dict(
                _FUNCTION.get("parameters")
                or {"type": "object", "properties": {}}
            ),
            handler=start_media_generation.handler,
            allow_parallel=True,
            timeout_seconds=180.0,
            metadata={
                **start_media_generation.TOOL_METADATA,
                "main_only": True,
            },
        ),
    ),
)

__all__ = ["plugin_pack"]
