"""ByteDance/Volcengine Seedream image adapter."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..models import MediaProviderError, MediaProviderResult
from .base import MediaProvider, ProgressCallback, emit_progress
from .helpers import (
    api_url,
    artifact_from_bytes,
    artifact_from_url,
    bounded_float,
    configured_download_limit,
    decode_base64_media,
    read_reference,
    reference_as_url,
    request_json,
    request_references,
    request_value,
    require_api_key,
)


class SeedreamProvider(MediaProvider):
    name = "seedream"
    supported_kinds = frozenset({"image"})

    async def generate(
        self,
        request: dict[str, Any],
        provider_settings: dict[str, Any],
        progress: ProgressCallback,
    ) -> MediaProviderResult:
        prompt = str(request.get("prompt") or "").strip()
        if not prompt:
            raise MediaProviderError("Seedream requires a prompt.", code="missing_prompt")
        api_key = require_api_key(provider_settings, "Seedream")
        model = str(request.get("model") or provider_settings.get("image_model") or "doubao-seedream-5-0-260128").strip()
        raw_count = request.get("number_of_outputs")
        try:
            numeric_count = float(1 if raw_count is None or raw_count == "" else raw_count)
        except (TypeError, ValueError) as exc:
            raise MediaProviderError(
                "Seedream number_of_outputs must be an integer from 1 to 15.",
                code="seedream_invalid_output_count",
            ) from exc
        if not numeric_count.is_integer() or not 1 <= numeric_count <= 15:
            raise MediaProviderError(
                "Seedream number_of_outputs must be an integer from 1 to 15.",
                code="seedream_invalid_output_count",
            )
        output_count = int(numeric_count)
        output_format = str(request.get("output_format") or "png").lower().replace("jpg", "jpeg")
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "response_format": str(request_value(request, "response_format", "url") or "url"),
            "size": str(request.get("size") or request.get("resolution") or "2K"),
            "output_format": output_format,
            "stream": False,
        }
        references = request_references(request)
        if references:
            for reference in references:
                raw = str(reference.get("url") or reference.get("uri") or reference.get("path") or "").strip() if isinstance(reference, dict) else str(reference or "").strip()
                if raw.startswith("https://"):
                    mime_type = mimetypes.guess_type(Path(urlparse(raw).path).name)[0] or ""
                else:
                    _data, mime_type, _filename = read_reference(reference)
                if mime_type and not mime_type.lower().startswith("image/"):
                    raise MediaProviderError(
                        "Seedream references must be image files.",
                        code="seedream_unsupported_reference",
                    )
            encoded = [reference_as_url(value) for value in references]
            payload["image"] = encoded[0] if len(encoded) == 1 else encoded
        if output_count > 1:
            payload["sequential_image_generation"] = str(request_value(request, "sequential_image_generation", "auto") or "auto")
            payload["sequential_image_generation_options"] = {"max_images": output_count}
        for key in ("watermark", "seed"):
            value = request_value(request, key)
            if value is not None and value != "":
                payload[key] = value
        optimize_options = request_value(request, "optimize_prompt_options")
        if isinstance(optimize_options, dict):
            payload["optimize_prompt_options"] = dict(optimize_options)
        await emit_progress(progress, "Submitting Seedream image generation")
        timeout_seconds = bounded_float(provider_settings.get("timeout_seconds"), 600.0, 30.0, 1800.0)
        response = await request_json(
            "POST",
            api_url(str(provider_settings.get("base_url") or "https://ark.cn-beijing.volces.com/api/v3"), "images/generations"),
            provider="Seedream",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        if response.get("error"):
            error = response.get("error")
            message = str(error.get("message") or error.get("code") or error) if isinstance(error, dict) else str(error)
            raise MediaProviderError(f"Seedream generation failed: {message}", retryable=False, code="seedream_api_error")
        entries = response.get("data")
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list) or not entries:
            raise MediaProviderError("Seedream returned no generated images.", retryable=True, code="seedream_empty_output")
        artifacts = []
        max_bytes = configured_download_limit(provider_settings)
        content_type = "image/jpeg" if output_format in {"jpg", "jpeg"} else f"image/{output_format}"
        for index, entry in enumerate(entries, 1):
            if not isinstance(entry, dict):
                continue
            encoded = str(entry.get("b64_json") or entry.get("b64") or "").strip()
            if encoded:
                raw_image = decode_base64_media(encoded, provider="Seedream")
                if len(raw_image) > max_bytes:
                    raise MediaProviderError("Seedream image output exceeds the configured download limit.", code="output_too_large")
                artifacts.append(
                    artifact_from_bytes(
                        raw_image,
                        prefix="seedream",
                        index=index,
                        content_type=content_type,
                    )
                )
                continue
            url = str(entry.get("url") or entry.get("image_url") or "").strip()
            if url:
                artifacts.append(await artifact_from_url(url, prefix="seedream", index=index, max_bytes=max_bytes))
        if not artifacts:
            raise MediaProviderError("Seedream returned no usable generated images.", retryable=True, code="seedream_empty_output")
        await emit_progress(progress, f"Downloaded {len(artifacts)} Seedream output(s)")
        return MediaProviderResult(
            artifacts=artifacts,
            provider_job_id=str(response.get("id") or response.get("request_id") or ""),
            metadata={
                "provider": self.name,
                "model": model,
                "usage": response.get("usage") if isinstance(response.get("usage"), dict) else {},
            },
        )


__all__ = ["SeedreamProvider"]
