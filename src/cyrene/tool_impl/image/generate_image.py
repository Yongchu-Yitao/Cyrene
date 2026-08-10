"""Tool implementation for GenerateImage."""

from __future__ import annotations

import json
from typing import Any

from cyrene.model_runtime.image_generation import (
    ImageGenerationError,
    generate_image,
)
from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_api import json_result, logger

TOOL_NAME = "GenerateImage"
TOOL_DEF = get_native_tool_def(TOOL_NAME)
TOOL_METADATA = {
    "read_only": False,
    "resource_keys": ("openai:image-generation", "fs:generated-image"),
    "requires_order": True,
}


async def _tool_generate_image(
    args: dict[str, Any],
    bot: Any,
    chat_id: int,
    db_path: str,
    notify_state: dict[str, bool] | None,
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
        # Resolve the already-registered public delivery handler at execution
        # time. Image tooling must not import another concrete tool module.
        from cyrene.tooling.catalog import TOOL_HANDLERS

        send_file = TOOL_HANDLERS.get("send_file")
        if send_file is None:
            raise RuntimeError("send_file delivery is unavailable")
        delivered = await send_file(
            {
                "path": str(generated.path),
                "name": display_name,
                "text": "Generated image.",
            },
            bot,
            chat_id,
            db_path,
            notify_state,
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

__all__ = [
    "TOOL_NAME",
    "TOOL_DEF",
    "TOOL_METADATA",
    "handler",
    "_tool_generate_image",
]
