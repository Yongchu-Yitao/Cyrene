"""MiniMax asynchronous video and synchronous music adapters."""

from __future__ import annotations

import asyncio
import base64
import binascii
import mimetypes
from pathlib import Path
import time
from typing import Any
from urllib.parse import urlparse

from cyrene.media.models import MediaProviderError, MediaProviderResult
from cyrene.media.providers.base import MediaProvider, ProgressCallback, emit_progress
from cyrene.media.providers.helpers import (
    api_url,
    artifact_from_bytes,
    artifact_from_url,
    bounded_float,
    bounded_int,
    configured_download_limit,
    first_string,
    read_reference,
    reference_as_url,
    reference_roles,
    request_json,
    request_references,
    request_value,
    require_api_key,
)


_VIDEO_SUCCESS = frozenset({"success", "succeeded", "completed", "done"})
_VIDEO_FAILURE = frozenset({"fail", "failed", "error", "cancelled", "canceled", "expired"})


def _minimax_versioned_url(base_url: str, version: str, path: str) -> str:
    normalized = str(base_url or "").rstrip("/")
    for suffix in ("/v1", "/v2"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    relative = str(path or "").lstrip("/")
    return api_url(normalized, f"{version}/{relative}")


def _minimax_url(base_url: str, path: str) -> str:
    return _minimax_versioned_url(base_url, "v1", path)


def _minimax_v2_url(base_url: str, path: str) -> str:
    return _minimax_versioned_url(base_url, "v2", path)


async def _minimax_request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    try:
        return await request_json(method, url, **kwargs)
    except MediaProviderError as exc:
        # MiniMax documents HTTP 529 as its overloaded response. The shared
        # HTTP helper intentionally has a conservative retry list, so preserve
        # this provider-specific transient classification here.
        if exc.code == "minimax_http_529":
            raise MediaProviderError(str(exc), retryable=True, code=exc.code) from exc
        raise


def _api_error(payload: dict[str, Any], operation: str) -> None:
    base_resp = payload.get("base_resp")
    if not isinstance(base_resp, dict):
        return
    code = base_resp.get("status_code", 0)
    if str(code) in {"0", "", "None"}:
        return
    message = str(base_resp.get("status_msg") or payload.get("error_message") or "unknown MiniMax error")
    retryable = any(marker in message.lower() for marker in ("rate", "busy", "capacity", "timeout", "internal", "overload"))
    raise MediaProviderError(f"MiniMax {operation} failed: {message}", retryable=retryable, code=f"minimax_{code}")


def _video_status(payload: dict[str, Any]) -> str:
    value = payload.get("status") or payload.get("state")
    for key in ("task", "data"):
        nested = payload.get(key)
        if not value and isinstance(nested, dict):
            value = nested.get("status") or nested.get("state")
    return str(value or "unknown").strip().lower()


def _media_class(reference: Any, role: str) -> str:
    if role in {"first_frame", "last_frame", "subject", "character", "reference_image"}:
        role_class = "image"
    elif role in {"audio", "reference_audio"}:
        role_class = "audio"
    elif role in {"video", "reference_video"}:
        role_class = "video"
    else:
        role_class = ""
    raw = str(reference.get("path") or reference.get("url") or "") if isinstance(reference, dict) else str(reference or "")
    explicit_mime = str(reference.get("mime_type") or reference.get("content_type") or "") if isinstance(reference, dict) else ""
    if explicit_mime:
        mime = explicit_mime
    elif raw.startswith("data:"):
        mime = raw[5:].split(";", 1)[0]
    else:
        mime = mimetypes.guess_type(Path(urlparse(raw).path).name)[0] or ""
    detected = "audio" if mime.startswith("audio/") else "video" if mime.startswith("video/") else "image" if mime.startswith("image/") else ""
    if role_class and detected and role_class != detected:
        raise MediaProviderError(
            f"MiniMax reference role {role!r} does not match the {detected} input.",
            code="minimax_invalid_reference_role",
        )
    return detected or role_class or "image"


def _direct_video_url(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("video_url", "download_url", "url"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
        for key in ("task", "content", "file", "data", "result", "output"):
            found = _direct_video_url(payload.get(key))
            if found:
                return found
    if isinstance(payload, (list, tuple)):
        for item in payload:
            found = _direct_video_url(item)
            if found:
                return found
    return ""


def _h3_duration(value: Any) -> int:
    """Validate H3's integer 4-15 second contract without silently clamping."""

    if value is None or value == "":
        return 5
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MediaProviderError(
            "MiniMax-H3 duration must be an integer from 4 to 15 seconds.",
            code="minimax_invalid_duration",
        ) from exc
    if not number.is_integer() or not 4 <= number <= 15:
        raise MediaProviderError(
            "MiniMax-H3 duration must be an integer from 4 to 15 seconds.",
            code="minimax_invalid_duration",
        )
    return int(number)


def _hailuo_duration(value: Any, model: str) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MediaProviderError(
            f"{model} duration must be 6 or 10 seconds.",
            code="minimax_invalid_duration",
        ) from exc
    if not number.is_integer() or int(number) not in {6, 10}:
        raise MediaProviderError(
            f"{model} duration must be 6 or 10 seconds.",
            code="minimax_invalid_duration",
        )
    return int(number)


def _require_single_output(request: dict[str, Any], label: str) -> None:
    value = request.get("number_of_outputs")
    if value is None or value == "":
        return
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MediaProviderError(
            f"{label} returns one output per request.",
            code="minimax_unsupported_output_count",
        ) from exc
    if not number.is_integer() or int(number) != 1:
        raise MediaProviderError(
            f"{label} returns one output per request.",
            code="minimax_unsupported_output_count",
        )


class MiniMaxProvider(MediaProvider):
    name = "minimax"
    supported_kinds = frozenset({"video", "music"})

    async def generate(
        self,
        request: dict[str, Any],
        provider_settings: dict[str, Any],
        progress: ProgressCallback,
    ) -> MediaProviderResult:
        kind = str(request.get("kind") or "").strip().lower()
        if kind == "video":
            return await self._generate_video(request, provider_settings, progress)
        if kind == "music":
            return await self._generate_music(request, provider_settings, progress)
        raise MediaProviderError("MiniMax supports video and music jobs only.", code="unsupported_kind")

    async def _generate_video(
        self,
        request: dict[str, Any],
        settings: dict[str, Any],
        progress: ProgressCallback,
    ) -> MediaProviderResult:
        _require_single_output(request, "MiniMax video")
        task_id = str(request.get("_resume_provider_job_id") or "").strip()
        resume_state = request.get("_resume_provider_state")
        if not isinstance(resume_state, dict):
            resume_state = {}
        api_key = require_api_key(settings, "MiniMax")
        base_url = str((resume_state.get("base_url") if task_id else "") or settings.get("base_url") or "https://api.minimax.io")
        model = str(request.get("model") or settings.get("video_model") or "MiniMax-H3").strip()
        persisted_version = str(resume_state.get("api_version") or "").strip().lower()
        if task_id and persisted_version in {"v1", "v2"}:
            api_version = persisted_version
        else:
            api_version = "v2" if model.casefold() == "minimax-h3" else "v1"
        is_h3 = api_version == "v2"
        state_context = {"base_url": base_url, "api_version": api_version}
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        created: dict[str, Any] = {}
        direct_url = ""
        if task_id:
            await emit_progress(
                progress,
                "Resuming MiniMax video task",
                provider_job_id=task_id,
                state={**state_context, "status": "resuming"},
            )
        else:
            prompt = str(request.get("prompt") or "").strip()
            if not prompt:
                raise MediaProviderError("MiniMax video requires a prompt.", code="missing_prompt")
            prompt_limit = 7000 if is_h3 else 2000
            if len(prompt) > prompt_limit:
                raise MediaProviderError(
                    f"{model} video prompt exceeds {prompt_limit} characters.",
                    code="minimax_prompt_too_long",
                )
            references = request_references(request)
            roles = reference_roles(request, len(references))
            if is_h3:
                classified = [(reference, roles[index], _media_class(reference, roles[index])) for index, reference in enumerate(references)]
                has_explicit_frames = any(role in {"first_frame", "last_frame"} for _ref, role, _kind in classified)
                reference_mode = any(
                    media_kind in {"video", "audio"}
                    or role
                    in {
                        "subject",
                        "character",
                        "audio",
                        "video",
                        "reference_image",
                        "reference_video",
                        "reference_audio",
                    }
                    for _ref, role, media_kind in classified
                ) or (not has_explicit_frames and sum(kind == "image" for _ref, _role, kind in classified) > 2)
                if has_explicit_frames and reference_mode:
                    raise MediaProviderError(
                        "MiniMax-H3 cannot mix first/last frames with reference media.",
                        code="minimax_invalid_references",
                    )
                for frame_role in ("first_frame", "last_frame"):
                    if sum(role == frame_role for _ref, role, _kind in classified) > 1:
                        raise MediaProviderError(
                            f"MiniMax-H3 accepts at most one {frame_role.replace('_', '-')} image.",
                            code="minimax_invalid_references",
                        )
                if reference_mode:
                    counts = {media_kind: sum(kind == media_kind for _ref, _role, kind in classified) for media_kind in ("image", "video", "audio")}
                    if counts["image"] > 9 or counts["video"] > 3 or counts["audio"] > 3 or len(classified) > 12:
                        raise MediaProviderError(
                            "MiniMax-H3 reference mode accepts at most 9 images, 3 videos, 3 audio clips, and 12 files total.",
                            code="minimax_invalid_references",
                        )

                content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
                used_frame_roles: set[str] = {role for _reference, role, media_kind in classified if media_kind == "image" and role in {"first_frame", "last_frame"}}
                for reference, requested_role, media_kind in classified:
                    media_type = f"{media_kind}_url"
                    if requested_role in {"first_frame", "last_frame"}:
                        if media_kind != "image":
                            raise MediaProviderError(
                                "MiniMax-H3 first/last-frame inputs must be images.",
                                code="minimax_invalid_references",
                            )
                        role = requested_role
                    elif reference_mode:
                        role = {
                            "image": "reference_image",
                            "video": "reference_video",
                            "audio": "reference_audio",
                        }[media_kind]
                    else:
                        role = "first_frame" if "first_frame" not in used_frame_roles else "last_frame"
                        if role in used_frame_roles:
                            raise MediaProviderError(
                                "MiniMax-H3 supports at most two first/last-frame images.",
                                code="minimax_invalid_references",
                            )
                        used_frame_roles.add(role)
                    content.append(
                        {
                            "type": media_type,
                            media_type: {"url": reference_as_url(reference)},
                            "role": role,
                        }
                    )

                resolution = str(request.get("resolution") or "2K").upper()
                if resolution not in {"768P", "2K"}:
                    raise MediaProviderError(
                        "MiniMax-H3 resolution must be 768P or 2K.",
                        code="minimax_invalid_resolution",
                    )
                payload: dict[str, Any] = {
                    "model": model,
                    "content": content,
                    "resolution": resolution,
                    "duration": _h3_duration(request.get("duration")),
                }
                requested_ratio = request.get("aspect_ratio") or request_value(request, "ratio")
                ratio = str(requested_ratio or "").strip()
                valid_ratios = {"adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}
                if ratio and ratio not in valid_ratios:
                    raise MediaProviderError(
                        "MiniMax-H3 ratio must be adaptive, 21:9, 16:9, 4:3, 1:1, 3:4, or 9:16.",
                        code="minimax_invalid_ratio",
                    )
                if has_explicit_frames or (references and not reference_mode):
                    payload["ratio"] = "adaptive"
                elif reference_mode:
                    payload["ratio"] = ratio or "adaptive"
                else:
                    if ratio == "adaptive":
                        raise MediaProviderError(
                            "MiniMax-H3 text-to-video requires a concrete ratio, not adaptive.",
                            code="minimax_invalid_ratio",
                        )
                    payload["ratio"] = ratio or "16:9"
            else:
                payload = {"model": model, "prompt": prompt}
                normalized_model = model.casefold()
                legacy_resolution = str(request.get("resolution") or "").upper()
                if request.get("duration") is not None and request.get("duration") != "":
                    if normalized_model in {
                        "minimax-hailuo-2.3",
                        "minimax-hailuo-2.3-fast",
                        "minimax-hailuo-02",
                    }:
                        legacy_duration = _hailuo_duration(request.get("duration"), model)
                        if legacy_duration == 10 and legacy_resolution == "1080P":
                            raise MediaProviderError(
                                f"{model} supports 10-second output at 768P only.",
                                code="minimax_invalid_duration",
                            )
                    else:
                        legacy_duration = bounded_int(request.get("duration"), 6, 1, 30)
                    payload["duration"] = legacy_duration
                if legacy_resolution:
                    if normalized_model in {
                        "minimax-hailuo-2.3",
                        "minimax-hailuo-2.3-fast",
                    } and legacy_resolution not in {"768P", "1080P"}:
                        raise MediaProviderError(
                            f"{model} resolution must be 768P or 1080P.",
                            code="minimax_invalid_resolution",
                        )
                    if normalized_model == "minimax-hailuo-02" and legacy_resolution not in {
                        "512P",
                        "768P",
                        "1080P",
                    }:
                        raise MediaProviderError(
                            f"{model} resolution must be 512P, 768P, or 1080P.",
                            code="minimax_invalid_resolution",
                        )
                    payload["resolution"] = legacy_resolution
                for key in ("prompt_optimizer", "fast_pretreatment"):
                    value = request_value(request, key)
                    if value is not None and value != "":
                        payload[key] = value
                subject_images: list[str] = []
                image_index = 0
                for index, reference in enumerate(references):
                    role = roles[index]
                    media_class = _media_class(reference, role)
                    if role == "reference" and media_class == "image":
                        role = "first_frame" if image_index == 0 else "last_frame" if image_index == 1 else "reference"
                        image_index += 1
                    if role == "first_frame":
                        payload["first_frame_image"] = reference_as_url(reference)
                    elif role == "last_frame":
                        payload["last_frame_image"] = reference_as_url(reference)
                    elif role in {"subject", "character"}:
                        subject_images.append(reference_as_url(reference))
                    else:
                        raise MediaProviderError(
                            "MiniMax v1 video supports first/last-frame images or S2V-01 subject images only; use MiniMax-H3 for reference image, video, or audio inputs.",
                            code="minimax_unsupported_reference",
                        )
                if subject_images:
                    if "first_frame_image" in payload or "last_frame_image" in payload:
                        raise MediaProviderError(
                            "MiniMax v1 cannot mix subject references with first/last frames.",
                            code="minimax_invalid_references",
                        )
                    if normalized_model != "s2v-01":
                        raise MediaProviderError(
                            "MiniMax v1 subject references require model S2V-01; use MiniMax-H3 for general reference generation.",
                            code="minimax_invalid_model_for_reference",
                        )
                    payload["subject_reference"] = [{"type": "character", "image": subject_images}]
                if "last_frame_image" in payload:
                    if "first_frame_image" not in payload:
                        raise MediaProviderError(
                            "MiniMax v1 last-frame generation also requires a first-frame image.",
                            code="minimax_invalid_references",
                        )
                    if normalized_model != "minimax-hailuo-02":
                        raise MediaProviderError(
                            "MiniMax v1 last-frame generation requires model MiniMax-Hailuo-02.",
                            code="minimax_invalid_model_for_reference",
                        )
                    if legacy_resolution == "512P":
                        raise MediaProviderError(
                            "MiniMax-Hailuo-02 first/last-frame generation does not support 512P.",
                            code="minimax_invalid_resolution",
                        )
            await emit_progress(progress, "Submitting MiniMax video generation")
            created = await _minimax_request_json(
                "POST",
                _minimax_v2_url(base_url, "video_generation") if is_h3 else _minimax_url(base_url, "video_generation"),
                provider="MiniMax",
                headers=headers,
                payload=payload,
                timeout_seconds=bounded_float(settings.get("request_timeout_seconds"), 120.0, 15.0, 300.0),
            )
            _api_error(created, "video submission")
            task_id = first_string(created, ("task_id", "taskId", "id"))
            direct_url = _direct_video_url(created)
            if not task_id and not direct_url:
                raise MediaProviderError("MiniMax returned no video task ID.", retryable=True, code="minimax_missing_task_id")
            if task_id:
                await emit_progress(
                    progress,
                    "MiniMax video task queued",
                    provider_job_id=task_id,
                    state={**state_context, "status": "queued"},
                )

        result = created
        if task_id and not direct_url:
            total_timeout = bounded_float(
                request_value(request, "generation_timeout_seconds", settings.get("generation_timeout_seconds")),
                1800.0,
                60.0,
                7200.0,
            )
            poll_seconds = bounded_float(settings.get("poll_interval_seconds"), 10.0, 3.0, 60.0)
            deadline = time.monotonic() + total_timeout
            previous = ""
            while time.monotonic() < deadline:
                remaining = max(0.1, deadline - time.monotonic())
                result = await _minimax_request_json(
                    "GET",
                    _minimax_v2_url(base_url, f"query/video_generation/{task_id}") if is_h3 else _minimax_url(base_url, "query/video_generation"),
                    provider="MiniMax",
                    headers=headers,
                    params=None if is_h3 else {"task_id": task_id},
                    timeout_seconds=min(60.0, remaining),
                )
                _api_error(result, "video status")
                current = _video_status(result)
                if current != previous:
                    await emit_progress(
                        progress,
                        f"MiniMax video task: {current}",
                        provider_job_id=task_id,
                        state={**state_context, "status": current},
                    )
                    previous = current
                if current in _VIDEO_SUCCESS:
                    break
                if current in _VIDEO_FAILURE:
                    message = first_string(result, ("error_message", "message", "reason", "code")) or current
                    # A retry would resume this same terminal task ID, so it
                    # cannot recover the generation. Transport/status failures
                    # remain retryable through _minimax_request_json.
                    raise MediaProviderError(f"MiniMax video generation failed: {message}", code=f"minimax_video_{current}")
                await asyncio.sleep(min(poll_seconds, max(0.1, deadline - time.monotonic())))
            else:
                raise MediaProviderError("MiniMax video generation timed out.", retryable=True, code="minimax_video_timeout")
            direct_url = _direct_video_url(result)

        file_id = first_string(result, ("file_id", "fileId"))
        if not is_h3 and not direct_url and file_id:
            retrieved = await _minimax_request_json(
                "GET",
                _minimax_url(base_url, "files/retrieve"),
                provider="MiniMax",
                headers=headers,
                params={"file_id": file_id},
                timeout_seconds=60.0,
            )
            _api_error(retrieved, "video retrieval")
            direct_url = _direct_video_url(retrieved)
        if not direct_url:
            raise MediaProviderError("MiniMax completed without a downloadable video.", retryable=True, code="minimax_empty_output")
        artifact = await artifact_from_url(
            direct_url,
            prefix="minimax-video",
            index=1,
            max_bytes=configured_download_limit(settings),
            timeout_seconds=300.0,
        )
        await emit_progress(
            progress,
            "Downloaded MiniMax video",
            provider_job_id=task_id,
            state={**state_context, "status": "succeeded"},
        )
        task_details = result.get("task") if isinstance(result.get("task"), dict) else result
        return MediaProviderResult(
            artifacts=[artifact],
            provider_job_id=task_id,
            metadata={
                "provider": self.name,
                "model": model,
                "file_id": file_id,
                "width": task_details.get("video_width"),
                "height": task_details.get("video_height"),
                "resolution": task_details.get("resolution"),
                "duration": task_details.get("duration"),
                "ratio": task_details.get("ratio"),
                "usage": task_details.get("usage") if isinstance(task_details.get("usage"), dict) else {},
            },
        )

    async def _generate_music(
        self,
        request: dict[str, Any],
        settings: dict[str, Any],
        progress: ProgressCallback,
    ) -> MediaProviderResult:
        _require_single_output(request, "MiniMax music")
        prompt = str(request.get("prompt") or "").strip()
        lyrics = str(request.get("lyrics") or "").strip()
        instrumental = bool(request.get("is_instrumental") or request_value(request, "is_instrumental", False))
        if not prompt and not lyrics:
            raise MediaProviderError("MiniMax music requires a prompt or lyrics.", code="missing_prompt")
        api_key = require_api_key(settings, "MiniMax")
        base_url = str(settings.get("base_url") or "https://api.minimax.io")
        model = str(request.get("model") or settings.get("music_model") or "music-3.0").strip()
        audio_format = str(request.get("output_format") or request_value(request, "audio_format", "mp3") or "mp3").lower()
        audio_setting = request_value(request, "audio_setting")
        if not isinstance(audio_setting, dict):
            audio_setting = {
                "sample_rate": bounded_int(request_value(request, "sample_rate"), 44100, 16000, 48000),
                "bitrate": bounded_int(request_value(request, "bitrate"), 256000, 32000, 320000),
                "format": audio_format,
            }
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "lyrics": lyrics,
            "stream": False,
            "output_format": "url",
            "audio_setting": audio_setting,
            "is_instrumental": instrumental,
        }
        optimizer = request_value(request, "lyrics_optimizer")
        if optimizer is None:
            optimizer = bool(prompt and not lyrics and not instrumental)
        payload["lyrics_optimizer"] = bool(optimizer)
        references = request_references(request)
        if references:
            reference = references[0]
            if isinstance(reference, dict) and str(reference.get("url") or "").startswith("https://"):
                payload["audio_url"] = str(reference["url"])
            elif isinstance(reference, str) and reference.startswith("https://"):
                payload["audio_url"] = reference
            else:
                raw, _mime, _filename = read_reference(reference)
                payload["audio_base64"] = base64.b64encode(raw).decode("ascii")
        await emit_progress(progress, "Submitting MiniMax music generation")
        response = await request_json(
            "POST",
            _minimax_url(base_url, "music_generation"),
            provider="MiniMax",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            payload=payload,
            timeout_seconds=bounded_float(settings.get("music_timeout_seconds"), 900.0, 30.0, 1800.0),
        )
        _api_error(response, "music generation")
        data = response.get("data") if isinstance(response.get("data"), dict) else response
        audio_value = str(data.get("audio_url") or data.get("url") or data.get("audio") or "").strip()
        if not audio_value:
            raise MediaProviderError("MiniMax returned no generated music.", retryable=True, code="minimax_empty_output")
        if audio_value.startswith(("http://", "https://")):
            artifact = await artifact_from_url(
                audio_value,
                prefix="minimax-music",
                index=1,
                max_bytes=configured_download_limit(settings),
                timeout_seconds=300.0,
            )
        else:
            try:
                raw_audio = bytes.fromhex(audio_value)
            except ValueError:
                try:
                    raw_audio = base64.b64decode(audio_value, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise MediaProviderError("MiniMax returned invalid encoded audio.", code="minimax_invalid_audio") from exc
            if len(raw_audio) > configured_download_limit(settings):
                raise MediaProviderError("MiniMax music output exceeds the configured download limit.", code="output_too_large")
            content_type = {"mp3": "audio/mpeg", "wav": "audio/wav", "flac": "audio/flac", "m4a": "audio/mp4"}.get(audio_format, "audio/mpeg")
            artifact = artifact_from_bytes(raw_audio, prefix="minimax-music", index=1, content_type=content_type)
        await emit_progress(progress, "Downloaded MiniMax music")
        return MediaProviderResult(
            artifacts=[artifact],
            provider_job_id=str(response.get("trace_id") or response.get("id") or ""),
            metadata={
                "provider": self.name,
                "model": model,
                "extra_info": response.get("extra_info") if isinstance(response.get("extra_info"), dict) else {},
            },
        )


__all__ = ["MiniMaxProvider"]
