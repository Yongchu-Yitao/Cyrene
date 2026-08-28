"""Hidden infrastructure Plugin for the messaging-channel runtime."""

from __future__ import annotations

from typing import Any

from agent.plugin import Plugin, PluginContext

PLUGIN_NAME = "cyrene_channels.runtime"


def channel_runtime_status(
    _arguments: dict[str, Any],
    context: PluginContext,
) -> dict[str, Any]:
    """Return the live state owned by the channel application service."""

    service = context.services.get("channels")
    status = getattr(service, "status", None)
    if not callable(status):
        raise RuntimeError("PluginContext.services['channels'] is unavailable")
    value = status()
    if not isinstance(value, dict):
        raise RuntimeError("Messaging channel status must be an object")
    return {"ok": True, **value}


plugin = Plugin(
    name=PLUGIN_NAME,
    description="Provide the configured Telegram and WeChat channel runtime.",
    input_schema={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    handler=channel_runtime_status,
    allow_parallel=True,
    metadata={
        "model_visible": False,
        "required": True,
        "i18n": {
            "en": {
                "name": "Messaging channel runtime",
                "description": "Provide the configured Telegram and WeChat channel runtime.",
            },
            "zh": {
                "name": "消息渠道运行时",
                "description": "提供已配置的 Telegram 与微信消息渠道运行时。",
            },
        },
    },
)


__all__ = ["PLUGIN_NAME", "channel_runtime_status", "plugin"]
