"""Tool implementation for GenerateImage."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from agent.plugin import Plugin, PluginContext
from agent.plugin.execution import invoke_plugin
from cyrene.model_runtime.image_generation import (
    ImageGenerationError,
    generate_image,
)
from .definitions import get_native_tool_def
from agent.plugin.native_runtime import json_result

logger = logging.getLogger(__name__)

TOOL_NAME = "GenerateImage"
TOOL_DEF = get_native_tool_def(TOOL_NAME)
TOOL_METADATA = {
    "read_only": False,
    "resource_keys": ("openai:image-generation", "fs:generated-image"),
    "requires_order": True,
}


async def _tool_generate_image(
    args: dict[str, Any],
    _context: PluginContext,
) -> str:
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return "Error: 'prompt' is required."
    try:
        generated = await generate_image(
            prompt=prompt,
            size=str(args.get("size") or "1024x1024"),
            quality=str(args.get("quality") or "medium"),
            output_format=str(args.get("output_format") or "png"),
        )
    except ImageGenerationError as exc:
        return f"Error: {exc}"
    except (OSError, RuntimeError, TimeoutError) as exc:
        logger.warning("Image generation failed: %s", exc)
        return f"Error: image generation failed: {exc}"

    display_name = str(args.get("name") or "").strip()
    if not display_name:
        display_name = generated.path.name
    try:
        delivered = await invoke_plugin(
            "send_file",
            {
                "path": str(generated.path),
                "name": display_name,
                "text": "Generated image.",
            },
            review=False,
        )
    finally:
        try:
            generated.path.unlink(missing_ok=True)
        except OSError:
            logger.debug(
                "Unable to remove temporary generated image %s",
                generated.path,
                exc_info=True,
            )

    try:
        payload = json.loads(delivered)
    except (TypeError, ValueError):
        return delivered
    if not isinstance(payload, dict):
        return delivered
    payload["generation"] = {
        "provider": generated.provider,
        "model": generated.model,
        **(
            {"revised_prompt": generated.revised_prompt}
            if generated.revised_prompt
            else {}
        ),
    }
    return json_result(payload)


handler = _tool_generate_image

_FUNCTION = TOOL_DEF.get("function")
if not isinstance(_FUNCTION, Mapping):
    raise TypeError("GenerateImage definition must contain a function object")
_INPUT_SCHEMA = _FUNCTION.get("parameters")
if not isinstance(_INPUT_SCHEMA, Mapping):
    raise TypeError("GenerateImage definition must contain an input schema")

plugin = Plugin(
    name=TOOL_NAME,
    description=str(_FUNCTION.get("description") or ""),
    input_schema=dict(_INPUT_SCHEMA),
    handler=handler,
    allow_parallel=False,
    timeout_seconds=420.0,
    metadata={
        **TOOL_METADATA,
        "main_only": True,
        "model_visible": False,
    },
)

__all__ = [
    "TOOL_NAME",
    "TOOL_DEF",
    "TOOL_METADATA",
    "handler",
    "plugin",
    "_tool_generate_image",
]
