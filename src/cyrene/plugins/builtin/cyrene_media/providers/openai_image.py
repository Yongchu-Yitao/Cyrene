"""OpenAI GPT Image generation and editing adapter."""

from __future__ import annotations

from typing import Any

import httpx

from ..models import MediaProviderError, MediaProviderResult
from .base import MediaProvider, ProgressCallback, emit_progress
from .helpers import (
    api_url,
    artifact_from_bytes,
    artifact_from_url,
    bounded_float,
    bounded_int,
    configured_download_limit,
    decode_base64_media,
    json_payload,
    read_reference,
    request_references,
    request_value,
    require_api_key,
)


class OpenAIImageProvider(MediaProvider):
    name = "openai"
    supported_kinds = frozenset({"image"})

    async def generate(
        self,
        request: dict[str, Any],
        provider_settings: dict[str, Any],
        progress: ProgressCallback,
    ) -> MediaProviderResult:
        if str(request.get("kind") or "image").lower() != "image":
            raise MediaProviderError("OpenAI GPT Image only supports image jobs.", code="unsupported_kind")
        prompt = str(request.get("prompt") or "").strip()
        if not prompt:
            raise MediaProviderError("GPT Image requires a prompt.", code="missing_prompt")
        api_key = require_api_key(provider_settings, "OpenAI")
        base_url = str(provider_settings.get("base_url") or "https://api.openai.com/v1")
        model = str(request.get("model") or provider_settings.get("image_model") or "gpt-image-2").strip()
        references = request_references(request)
        if request.get("mask_path") and not references:
            raise MediaProviderError(
                "OpenAI image masks require at least one reference image.",
                code="openai_invalid_mask",
            )
        path = "images/edits" if references else "images/generations"
        await emit_progress(progress, "Submitting GPT Image edit" if references else "Submitting GPT Image generation")

        raw_count = request.get("number_of_outputs")
        try:
            numeric_count = float(1 if raw_count is None or raw_count == "" else raw_count)
        except (TypeError, ValueError) as exc:
            raise MediaProviderError(
                "OpenAI image number_of_outputs must be an integer from 1 to 10.",
                code="openai_invalid_output_count",
            ) from exc
        if not numeric_count.is_integer() or not 1 <= numeric_count <= 10:
            raise MediaProviderError(
                "OpenAI image number_of_outputs must be an integer from 1 to 10.",
                code="openai_invalid_output_count",
            )

        output_format = str(request.get("output_format") or "png").lower().replace("jpg", "jpeg")
        if output_format not in {"png", "jpeg", "webp"}:
            raise MediaProviderError(
                "OpenAI image output_format must be png, jpeg, or webp.",
                code="openai_invalid_output_format",
            )
        common: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "n": int(numeric_count),
            "size": str(request.get("size") or "1024x1024"),
            "quality": str(request.get("quality") or "auto"),
            "output_format": output_format,
        }
        for key in ("background", "moderation"):
            value = request_value(request, key)
            if value is not None and value != "":
                common[key] = value
        input_fidelity = request_value(request, "input_fidelity")
        if input_fidelity is not None and input_fidelity != "":
            if model.lower().startswith("gpt-image-2"):
                raise MediaProviderError(
                    "GPT Image 2 always uses high input fidelity and does not accept input_fidelity.",
                    code="openai_unsupported_parameter",
                )
            common["input_fidelity"] = input_fidelity
        compression = request_value(request, "output_compression")
        if compression is not None and compression != "":
            common["output_compression"] = bounded_int(compression, 100, 0, 100)

        headers = {"Authorization": f"Bearer {api_key}"}
        timeout_seconds = bounded_float(provider_settings.get("timeout_seconds"), 600.0, 30.0, 1800.0)
        timeout = httpx.Timeout(timeout_seconds, connect=min(20.0, timeout_seconds))
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                if references:
                    data = {key: str(value).lower() if isinstance(value, bool) else str(value) for key, value in common.items()}
                    files: list[tuple[str, tuple[str, bytes, str]]] = []
                    for index, reference in enumerate(references):
                        raw, mime_type, filename = read_reference(reference)
                        if not mime_type.lower().startswith("image/"):
                            raise MediaProviderError(
                                "OpenAI image references must be image files.",
                                code="openai_unsupported_reference",
                            )
                        field = "image" if len(references) == 1 else "image[]"
                        files.append((field, (filename or f"reference-{index}", raw, mime_type)))
                    # ``mask_path`` is a top-level, tool-normalized path. Never
                    # accept arbitrary filesystem paths from the free-form
                    # provider parameters object.
                    mask = request.get("mask_path")
                    if mask:
                        raw, mime_type, filename = read_reference(mask)
                        if not mime_type.lower().startswith("image/"):
                            raise MediaProviderError(
                                "OpenAI image masks must be image files.",
                                code="openai_unsupported_reference",
                            )
                        files.append(("mask", (filename or "mask.png", raw, mime_type)))
                    response = await client.post(api_url(base_url, path), headers=headers, data=data, files=files)
                else:
                    response = await client.post(
                        api_url(base_url, path),
                        headers={**headers, "Content-Type": "application/json"},
                        json=common,
                    )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise MediaProviderError(f"OpenAI image request failed: {exc}", retryable=True, code="openai_transport") from exc
        payload = json_payload(response, "OpenAI")
        entries = payload.get("data")
        if not isinstance(entries, list) or not entries:
            raise MediaProviderError("OpenAI returned no generated images.", retryable=True, code="openai_empty_output")

        artifacts = []
        revised_prompts: list[str] = []
        output_format = str(common["output_format"])
        content_type = "image/jpeg" if output_format == "jpeg" else f"image/{output_format}"
        download_limit = configured_download_limit(provider_settings)
        for index, entry in enumerate(entries, 1):
            if not isinstance(entry, dict):
                continue
            revised = str(entry.get("revised_prompt") or "").strip()
            if revised:
                revised_prompts.append(revised)
            encoded = str(entry.get("b64_json") or entry.get("b64") or "").strip()
            if encoded:
                raw_image = decode_base64_media(encoded, provider="OpenAI")
                if len(raw_image) > download_limit:
                    raise MediaProviderError("OpenAI image output exceeds the configured download limit.", code="output_too_large")
                artifacts.append(
                    artifact_from_bytes(
                        raw_image,
                        prefix="gpt-image",
                        index=index,
                        content_type=content_type,
                    )
                )
                continue
            url = str(entry.get("url") or "").strip()
            if url:
                artifacts.append(await artifact_from_url(url, prefix="gpt-image", index=index, max_bytes=download_limit))
        if not artifacts:
            raise MediaProviderError("OpenAI returned no usable generated images.", retryable=True, code="openai_empty_output")
        await emit_progress(progress, f"Downloaded {len(artifacts)} GPT Image output(s)")
        return MediaProviderResult(
            artifacts=artifacts,
            provider_job_id=str(payload.get("id") or payload.get("created") or ""),
            metadata={
                "provider": self.name,
                "model": model,
                "usage": payload.get("usage") if isinstance(payload.get("usage"), dict) else {},
                "revised_prompts": revised_prompts,
            },
        )


__all__ = ["OpenAIImageProvider"]
