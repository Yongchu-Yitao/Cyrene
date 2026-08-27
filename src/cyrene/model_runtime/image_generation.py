"""OpenAI OAuth image generation for Cyrene tools."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import uuid
from typing import Any

from PIL import Image

from agent.plugin.model_catalog import candidate_provider_id
from cyrene.config import DATA_DIR

_MAX_IMAGE_BYTES = 30 * 1024 * 1024
_OUTPUT_FORMATS = {"png", "jpeg", "webp"}
_POPULAR_SIZES = {"1024x1024", "1536x1024", "1024x1536"}
_QUALITY_VALUES = {"low", "medium", "high", "auto"}
_DEFAULT_GENERATION_TIMEOUT_SECONDS = 180.0
_HIGH_QUALITY_GENERATION_TIMEOUT_SECONDS = 300.0


class ImageGenerationError(RuntimeError):
    """A user-actionable image generation failure."""


@dataclass(frozen=True, slots=True)
class GeneratedImage:
    path: Path
    provider: str
    model: str
    revised_prompt: str = ""


def _primary_candidate() -> dict[str, Any]:
    from agent.plugin import active_plugin_service

    service = active_plugin_service("model_configuration")
    models = service.candidates_for_route("primary") if service is not None else []
    if not models:
        raise ImageGenerationError(
            "No primary model is configured. Configure OpenAI OAuth first."
        )
    candidate = dict(models[0])
    candidate["provider"] = candidate_provider_id(candidate)
    candidate["model"] = str(
        candidate.get("model") or candidate.get("name") or ""
    ).strip()
    return candidate


def _decode_provider_image(
    value: str,
    *,
    saved_path: str = "",
) -> bytes:
    raw = str(value or "").strip()
    if raw.startswith("data:"):
        _, separator, raw = raw.partition(",")
        if not separator:
            raise ImageGenerationError("The image provider returned an invalid data URL.")
    if raw:
        if len(raw) > ((_MAX_IMAGE_BYTES * 4) // 3) + 16:
            raise ImageGenerationError(
                "The generated image exceeded the 30 MB size limit."
            )
        try:
            decoded = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ImageGenerationError(
                "The image provider returned invalid base64 image data."
            ) from exc
        if len(decoded) > _MAX_IMAGE_BYTES:
            raise ImageGenerationError(
                "The generated image exceeded the 30 MB size limit."
            )
        return decoded
    if saved_path:
        path = Path(saved_path).expanduser().resolve()
        if not path.is_file():
            raise ImageGenerationError(
                "The image provider returned a missing generated-image path."
            )
        if path.stat().st_size > _MAX_IMAGE_BYTES:
            raise ImageGenerationError(
                "The generated image exceeded the 30 MB size limit."
            )
        return path.read_bytes()
    raise ImageGenerationError("The image provider returned no image data.")


def _validated_image_format(image_bytes: bytes) -> str:
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            detected = str(image.format or "").lower()
            image.verify()
    except Exception as exc:
        raise ImageGenerationError(
            "The image provider returned data that is not a valid image."
        ) from exc
    if detected == "jpg":
        detected = "jpeg"
    if detected not in _OUTPUT_FORMATS:
        raise ImageGenerationError(
            f"The generated image format is unsupported: {detected or 'unknown'}."
        )
    return detected


def _persist_temp_image(image_bytes: bytes, detected_format: str) -> Path:
    output_dir = DATA_DIR / "generated_images"
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".jpg" if detected_format == "jpeg" else f".{detected_format}"
    path = output_dir / f"generated-{uuid.uuid4().hex}{suffix}"
    path.write_bytes(image_bytes)
    return path


async def _generate_with_codex(
    candidate: dict[str, Any],
    *,
    prompt: str,
    size: str,
    quality: str,
    output_format: str,
    timeout: float,
) -> tuple[bytes, str, str]:
    from cyrene.model_runtime.codex_provider import get_codex_provider

    provider = get_codex_provider()
    if not await provider.image_generation_capability():
        raise ImageGenerationError(
            "Image generation is unavailable for the connected OpenAI account."
        )
    result = await provider.generate_image(
        prompt=prompt,
        model=str(candidate.get("model") or ""),
        size=size,
        quality=quality,
        output_format=output_format,
        timeout=timeout,
    )
    image_bytes = _decode_provider_image(
        str(result.get("result") or ""),
        saved_path=str(result.get("savedPath") or result.get("saved_path") or ""),
    )
    return (
        image_bytes,
        str(candidate.get("model") or ""),
        str(result.get("revisedPrompt") or result.get("revised_prompt") or ""),
    )


async def generate_image(
    *,
    prompt: str,
    size: str = "1024x1024",
    quality: str = "medium",
    output_format: str = "png",
    timeout: float | None = None,
) -> GeneratedImage:
    clean_prompt = str(prompt or "").strip()
    if not clean_prompt:
        raise ImageGenerationError("An image prompt is required.")
    if len(clean_prompt) > 32_000:
        raise ImageGenerationError("The image prompt is too long.")
    clean_size = str(size or "1024x1024").strip().lower()
    if clean_size not in _POPULAR_SIZES:
        raise ImageGenerationError(f"Unsupported image size: {clean_size}.")
    clean_quality = str(quality or "medium").strip().lower()
    if clean_quality not in _QUALITY_VALUES:
        raise ImageGenerationError(f"Unsupported image quality: {clean_quality}.")
    generation_timeout = (
        float(timeout)
        if timeout is not None
        else (
            _HIGH_QUALITY_GENERATION_TIMEOUT_SECONDS
            if clean_quality == "high"
            else _DEFAULT_GENERATION_TIMEOUT_SECONDS
        )
    )
    if generation_timeout <= 0:
        raise ImageGenerationError("Image generation timeout must be positive.")
    clean_format = str(output_format or "png").strip().lower()
    if clean_format == "jpg":
        clean_format = "jpeg"
    if clean_format not in _OUTPUT_FORMATS:
        raise ImageGenerationError(f"Unsupported image format: {clean_format}.")

    candidate = _primary_candidate()
    provider = str(candidate.get("provider") or "")
    if provider != "codex_oauth":
        raise ImageGenerationError(
            "Image generation is available only when the primary model uses "
            "OpenAI OAuth."
        )
    image_bytes, model, revised_prompt = await _generate_with_codex(
        candidate,
        prompt=clean_prompt,
        size=clean_size,
        quality=clean_quality,
        output_format=clean_format,
        timeout=generation_timeout,
    )

    detected_format = _validated_image_format(image_bytes)
    return GeneratedImage(
        path=_persist_temp_image(image_bytes, detected_format),
        provider=provider,
        model=model,
        revised_prompt=revised_prompt,
    )


__all__ = [
    "GeneratedImage",
    "ImageGenerationError",
    "generate_image",
]
