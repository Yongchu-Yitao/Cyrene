"""Hidden infrastructure Plugin for composer-selected session context."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cyrene.core.plugin import Plugin, PluginContext

PLUGIN_NAME = "cyrene_composer_context.mount"


def build_composer_context(
    *,
    data: Mapping[str, Any],
    workspace: Path | None,
    services: Mapping[str, Any],
) -> dict[str, str]:
    """Build the context fragment shared by the Plugin and TurnStart Hook."""

    service = services.get("composer_context")
    builder = getattr(service, "build_session_context", None)
    if not callable(builder):
        raise RuntimeError(
            "required composer_context application service is unavailable"
        )
    content = str(
        builder(data, workspace=workspace, services=services) or ""
    ).strip()
    return {
        "context": content,
        "context_kind": "composer_context",
        "context_source": "cyrene_composer_context",
    } if content else {}


def mount_composer_context(
    _arguments: dict[str, Any],
    context: PluginContext,
) -> dict[str, str]:
    """Execute the composer-context capability through the Plugin runtime."""

    return build_composer_context(
        data=context.data,
        workspace=context.workspace,
        services=context.services,
    )


plugin = Plugin(
    name=PLUGIN_NAME,
    description="Validate and mount context selected in the message composer.",
    input_schema={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    handler=mount_composer_context,
    metadata={
        "model_visible": False,
        "required": True,
        "i18n": {
            "en": {
                "name": "Mount composer context",
                "description": "Validate and mount context selected in the message composer.",
            },
            "zh": {
                "name": "挂载输入框上下文",
                "description": "校验并挂载在消息输入框中选择的上下文。",
            },
        },
    },
)


__all__ = [
    "PLUGIN_NAME",
    "build_composer_context",
    "mount_composer_context",
    "plugin",
]
