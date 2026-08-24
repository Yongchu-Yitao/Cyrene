"""ByteDance/Volcengine Seedance asynchronous video adapter."""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path
import time
from typing import Any
from urllib.parse import urlparse

from cyrene.media.models import MediaProviderError, MediaProviderResult
from cyrene.media.providers.base import MediaProvider, ProgressCallback, emit_progress
from cyrene.media.providers.helpers import (
    api_url,
    artifact_from_url,
    bounded_float,
    configured_download_limit,
    first_string,
    reference_as_url,
    reference_roles,
    request_json,
    request_references,
    request_value,
    require_api_key,
)


_SUCCESS = frozenset({"success", "succeeded", "completed", "done"})
_FAILURE = frozenset({"failed", "fail", "error", "cancelled", "canceled", "expired"})


def _duration_for_model(value: Any, model: str) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MediaProviderError(
            "Seedance duration must be an integer number of seconds.",
            code="seedance_invalid_duration",
        ) from exc
    if not number.is_integer():
        raise MediaProviderError(
            "Seedance duration must be an integer number of seconds.",
            code="seedance_invalid_duration",
        )
    duration = int(number)
    normalized_model = model.casefold()
    if "seedance-2-0" in normalized_model or "seedance-2.0" in normalized_model:
        valid = duration == -1 or 4 <= duration <= 15
        expectation = "4 to 15 seconds, or -1"
    elif "seedance-1-5" in normalized_model or "seedance-1.5" in normalized_model:
        valid = duration == -1 or 4 <= duration <= 12
        expectation = "4 to 12 seconds, or -1"
    elif "seedance-1-0" in normalized_model or "seedance-1.0" in normalized_model:
        valid = 2 <= duration <= 12
        expectation = "2 to 12 seconds"
    else:
        valid = 1 <= duration <= 30
        expectation = "1 to 30 seconds"
    if not valid:
        raise MediaProviderError(
            f"{model or 'Seedance'} duration must be {expectation}.",
            code="seedance_invalid_duration",
        )
    return duration


def _reference_media_type(reference: Any, role: str) -> str:
    role_type = "audio_url" if role in {"audio", "reference_audio"} else "video_url" if role in {"video", "reference_video"} else ""
    raw = str(reference.get("path") or reference.get("url") or "") if isinstance(reference, dict) else str(reference or "")
    explicit_mime = str(reference.get("mime_type") or reference.get("content_type") or "") if isinstance(reference, dict) else ""
    if explicit_mime:
        mime = explicit_mime
    elif raw.startswith("data:"):
        mime = raw[5:].split(";", 1)[0]
    else:
        mime = mimetypes.guess_type(Path(urlparse(raw).path).name)[0] or ""
    detected = "audio_url" if mime.startswith("audio/") else "video_url" if mime.startswith("video/") else "image_url" if mime.startswith("image/") else ""
    if role_type and detected and role_type != detected:
        raise MediaProviderError(
            f"Seedance reference role {role!r} does not match the {detected.removesuffix('_url')} input.",
            code="seedance_invalid_reference_role",
        )
    return detected or role_type or "image_url"


def _reference_source(reference: Any) -> str:
    if isinstance(reference, dict):
        for key in ("url", "uri", "path"):
            if reference.get(key):
                return str(reference[key]).strip()
        return ""
    return str(reference or "").strip()


def _status(payload: dict[str, Any]) -> str:
    direct = payload.get("status") or payload.get("state")
    if direct:
        return str(direct).strip().lower()
    data = payload.get("data")
    if isinstance(data, dict):
        direct = data.get("status") or data.get("state")
        if direct:
            return str(direct).strip().lower()
    return "unknown"


def _output_urls(payload: Any) -> list[tuple[str, str]]:
    """Return generated videos without poster or last-frame side outputs."""
    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    def visit(value: Any, parent_key: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).lower()
                if normalized == "video_url":
                    candidate = child.get("url") if isinstance(child, dict) else child
                    if isinstance(candidate, str) and candidate.startswith(("http://", "https://")) and candidate not in seen:
                        seen.add(candidate)
                        results.append((candidate, "video"))
                if normalized == "url" and parent_key == "video_url":
                    if isinstance(child, str) and child.startswith(("http://", "https://")) and child not in seen:
                        seen.add(child)
                        results.append((child, "video"))
                visit(child, normalized)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child, parent_key)

    visit(payload)
    return results


class SeedanceProvider(MediaProvider):
    name = "seedance"
    supported_kinds = frozenset({"video"})

    async def generate(
        self,
        request: dict[str, Any],
        provider_settings: dict[str, Any],
        progress: ProgressCallback,
    ) -> MediaProviderResult:
        output_count = request.get("number_of_outputs")
        if output_count is not None and output_count != "":
            try:
                numeric_count = float(output_count)
            except (TypeError, ValueError) as exc:
                raise MediaProviderError(
                    "Seedance returns one video per task.",
                    code="seedance_unsupported_output_count",
                ) from exc
            if not numeric_count.is_integer() or int(numeric_count) != 1:
                raise MediaProviderError(
                    "Seedance returns one video per task.",
                    code="seedance_unsupported_output_count",
                )
        task_id = str(request.get("_resume_provider_job_id") or "").strip()
        resume_state = request.get("_resume_provider_state")
        if not isinstance(resume_state, dict):
            resume_state = {}
        api_key = require_api_key(provider_settings, "Seedance")
        base_url = str((resume_state.get("base_url") if task_id else "") or provider_settings.get("base_url") or "https://ark.cn-beijing.volces.com/api/v3")
        model = str(request.get("model") or provider_settings.get("video_model") or "doubao-seedance-2-0-260128").strip()
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        state_context = {"base_url": base_url}
        created: dict[str, Any] = {}
        if task_id:
            await emit_progress(
                progress,
                "Resuming Seedance task",
                provider_job_id=task_id,
                state={**state_context, "status": "resuming"},
            )
        else:
            prompt = str(request.get("prompt") or "").strip()
            if not prompt:
                raise MediaProviderError("Seedance requires a prompt.", code="missing_prompt")
            content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
            references = request_references(request)
            roles = reference_roles(request, len(references))
            image_index = 0
            for index, reference in enumerate(references):
                role = roles[index]
                media_type = _reference_media_type(reference, role)
                if media_type == "video_url" and not _reference_source(reference).startswith("https://"):
                    raise MediaProviderError(
                        "Seedance video references must use a public HTTPS URL; local video data URLs are not supported.",
                        code="seedance_unsupported_video_reference",
                    )
                if role in {"first_frame", "last_frame"} and media_type != "image_url":
                    raise MediaProviderError(
                        "Seedance first/last-frame references must be images.",
                        code="seedance_invalid_reference_role",
                    )
                if role == "audio":
                    role = "reference_audio"
                elif role == "subject":
                    role = "reference_image"
                if role == "reference":
                    if media_type == "image_url":
                        role = "first_frame" if image_index == 0 else "last_frame" if image_index == 1 else "reference_image"
                        image_index += 1
                    else:
                        role = "reference_video" if media_type == "video_url" else "reference_audio"
                content.append({"type": media_type, media_type: {"url": reference_as_url(reference)}, "role": role})
            payload: dict[str, Any] = {"model": model, "content": content}
            aspect_ratio = request.get("aspect_ratio") or request_value(request, "ratio")
            if aspect_ratio:
                payload["ratio"] = str(aspect_ratio)
            resolution = request.get("resolution")
            if resolution:
                payload["resolution"] = str(resolution)
            duration = request.get("duration")
            if duration is not None and duration != "":
                payload["duration"] = _duration_for_model(duration, model)
            for key in ("watermark", "generate_audio", "return_last_frame", "camera_fixed", "seed", "execution_expires_after"):
                value = request.get(key) if request.get(key) is not None else request_value(request, key)
                if value is not None and value != "":
                    payload[key] = value
            await emit_progress(progress, "Submitting Seedance video generation")
            created = await request_json(
                "POST",
                api_url(base_url, "contents/generations/tasks"),
                provider="Seedance",
                headers=headers,
                payload=payload,
                timeout_seconds=bounded_float(provider_settings.get("request_timeout_seconds"), 120.0, 15.0, 300.0),
            )
            if created.get("error"):
                raise MediaProviderError(f"Seedance rejected the request: {created.get('error')}", code="seedance_api_error")
            task_id = first_string(created, ("id", "task_id", "taskId"))
            if not task_id:
                raise MediaProviderError("Seedance returned no task ID.", retryable=True, code="seedance_missing_task_id")
            await emit_progress(
                progress,
                "Seedance task queued",
                provider_job_id=task_id,
                state={**state_context, "status": "queued"},
            )

        timeout_seconds = bounded_float(
            request_value(request, "generation_timeout_seconds", provider_settings.get("generation_timeout_seconds")),
            1800.0,
            60.0,
            7200.0,
        )
        poll_seconds = bounded_float(provider_settings.get("poll_interval_seconds"), 8.0, 2.0, 60.0)
        deadline = time.monotonic() + timeout_seconds
        result = created
        previous_status = ""
        while time.monotonic() < deadline:
            remaining = max(0.1, deadline - time.monotonic())
            result = await request_json(
                "GET",
                api_url(base_url, f"contents/generations/tasks/{task_id}"),
                provider="Seedance",
                headers=headers,
                timeout_seconds=min(60.0, remaining),
            )
            current = _status(result)
            if current != previous_status:
                await emit_progress(
                    progress,
                    f"Seedance task: {current}",
                    provider_job_id=task_id,
                    state={**state_context, "status": current},
                )
                previous_status = current
            if current in _SUCCESS:
                break
            if current in _FAILURE:
                message = first_string(result, ("message", "error_message", "error", "reason")) or current
                # Retrying a terminal remote task would only poll the same
                # failed task ID again; transient status/download failures are
                # classified separately by the HTTP helper.
                raise MediaProviderError(f"Seedance video generation failed: {message}", code=f"seedance_{current}")
            await asyncio.sleep(min(poll_seconds, max(0.1, deadline - time.monotonic())))
        else:
            raise MediaProviderError("Seedance video generation timed out.", retryable=True, code="seedance_timeout")

        urls = _output_urls(result)
        if not urls:
            raise MediaProviderError("Seedance completed without output media.", retryable=True, code="seedance_empty_output")
        artifacts = []
        max_bytes = configured_download_limit(provider_settings)
        for index, (url, kind) in enumerate(urls, 1):
            artifacts.append(await artifact_from_url(url, prefix=f"seedance-{kind}", index=index, max_bytes=max_bytes, timeout_seconds=300.0))
        await emit_progress(
            progress,
            f"Downloaded {len(artifacts)} Seedance output(s)",
            provider_job_id=task_id,
            state={**state_context, "status": "succeeded"},
        )
        return MediaProviderResult(
            artifacts=artifacts,
            provider_job_id=task_id,
            metadata={"provider": self.name, "model": model, "status": _status(result)},
        )


__all__ = ["SeedanceProvider"]
