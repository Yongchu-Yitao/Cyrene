"""Google Gemini image and Veo video adapter using the official google-genai SDK."""

from __future__ import annotations

import asyncio
import base64
import binascii
import mimetypes
from io import BytesIO
from pathlib import Path
import time
from typing import Any, Callable

from ..models import MediaArtifact, MediaProviderError, MediaProviderResult
from .base import MediaProvider, ProgressCallback, emit_progress
from .helpers import (
    artifact_from_bytes,
    bounded_float,
    configured_download_limit,
    read_reference,
    reference_roles,
    request_references,
    request_value,
    require_api_key,
)


def _google_modules() -> tuple[Any, Any]:
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise MediaProviderError(
            "Google media support requires the google-genai package.",
            code="google_sdk_missing",
        ) from exc
    return genai, types


def _google_error(operation: str, exc: Exception) -> MediaProviderError:
    message = str(exc or "unknown error")[:1600]
    lowered = message.lower()
    raw_status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    try:
        status = int(raw_status)
    except (TypeError, ValueError):
        status = 0
    retryable = status in {408, 409, 429, 500, 502, 503, 504} or any(
        marker in lowered
        for marker in (
            "429",
            "rate limit",
            "resource exhausted",
            "timeout",
            "timed out",
            "deadline exceeded",
            "temporarily unavailable",
            "connection reset",
            "unavailable",
            "internal",
            "500",
            "502",
            "503",
            "504",
        )
    )
    code = "google_rate_limit" if status == 429 or "429" in lowered or "resource exhausted" in lowered else "google_api_error"
    return MediaProviderError(f"Google {operation} failed: {message}", retryable=retryable, code=code)


async def _sdk_call(function: Callable[..., Any], *args: Any, timeout: float, **kwargs: Any) -> Any:
    try:
        return await asyncio.wait_for(asyncio.to_thread(function, *args, **kwargs), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise MediaProviderError("Google SDK request timed out.", retryable=True, code="google_timeout") from exc


def _dump(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    dumper = getattr(value, "model_dump", None)
    if callable(dumper):
        try:
            result = dumper(exclude_none=True)
            return result if isinstance(result, dict) else {}
        except Exception:
            return {}
    return {}


def _parts(response: Any) -> list[Any]:
    direct = getattr(response, "parts", None)
    if isinstance(direct, list):
        return direct
    candidates = getattr(response, "candidates", None) or []
    result: list[Any] = []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        result.extend(getattr(content, "parts", None) or [])
    return result


def _part_bytes(part: Any) -> tuple[bytes, str] | None:
    inline = getattr(part, "inline_data", None)
    if inline is not None:
        value = getattr(inline, "data", None)
        mime_type = str(getattr(inline, "mime_type", None) or "image/png")
        if isinstance(value, bytes):
            return value, mime_type
        if isinstance(value, bytearray):
            return bytes(value), mime_type
        if isinstance(value, str):
            try:
                return base64.b64decode(value, validate=True), mime_type
            except (ValueError, binascii.Error):
                pass
    as_image = getattr(part, "as_image", None)
    if callable(as_image):
        try:
            image = as_image()
            stream = BytesIO()
            image.save(stream, format="PNG")
            return stream.getvalue(), "image/png"
        except Exception:
            return None
    return None


def _video_bytes(client: Any, generated: Any) -> tuple[bytes, str]:
    video = getattr(generated, "video", None) or generated
    for source in (video, generated):
        value = getattr(source, "video_bytes", None) or getattr(source, "data", None)
        if isinstance(value, bytes):
            return value, str(getattr(source, "mime_type", None) or "video/mp4")
        if isinstance(value, bytearray):
            return bytes(value), str(getattr(source, "mime_type", None) or "video/mp4")
    downloaded = client.files.download(file=video)
    if isinstance(downloaded, bytes):
        return downloaded, str(getattr(video, "mime_type", None) or "video/mp4")
    if isinstance(downloaded, bytearray):
        return bytes(downloaded), str(getattr(video, "mime_type", None) or "video/mp4")
    if hasattr(downloaded, "read"):
        value = downloaded.read()
        if isinstance(value, bytes):
            return value, str(getattr(video, "mime_type", None) or "video/mp4")
    value = getattr(video, "video_bytes", None) or getattr(video, "data", None)
    if isinstance(value, bytes):
        return value, str(getattr(video, "mime_type", None) or "video/mp4")
    raise MediaProviderError("Google Veo completed but the SDK returned no video bytes.", retryable=True, code="google_empty_output")


def _whole_number(value: Any, *, message: str, code: str) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MediaProviderError(message, code=code) from exc
    if not number.is_integer():
        raise MediaProviderError(message, code=code)
    return int(number)


def _veo_duration(value: Any, model: str) -> int:
    duration = _whole_number(
        value,
        message="Google Veo duration must be a supported whole number of seconds.",
        code="google_invalid_duration",
    )
    normalized_model = model.lower()
    if "veo-2" in normalized_model:
        allowed = {5, 6, 8}
    elif "veo-3.1" in normalized_model:
        allowed = {4, 6, 8}
    elif "veo-3" in normalized_model:
        allowed = {8}
    else:
        # Custom aliases still receive a strict union of Google's documented
        # Veo durations instead of silently clamping an unsupported request.
        allowed = {4, 5, 6, 8}
    if duration not in allowed:
        choices = ", ".join(str(item) for item in sorted(allowed))
        raise MediaProviderError(
            f"{model or 'Google Veo'} duration must be one of: {choices} seconds.",
            code="google_invalid_duration",
        )
    return duration


class GoogleMediaProvider(MediaProvider):
    name = "google"
    supported_kinds = frozenset({"image", "video"})

    async def generate(
        self,
        request: dict[str, Any],
        provider_settings: dict[str, Any],
        progress: ProgressCallback,
    ) -> MediaProviderResult:
        kind = str(request.get("kind") or "").strip().lower()
        if kind == "image":
            return await self._generate_image(request, provider_settings, progress)
        if kind == "video":
            model = str(request.get("model") or provider_settings.get("video_model") or "gemini-omni-flash-preview").strip()
            if "omni" in model.lower():
                return await self._generate_omni_video(
                    request,
                    provider_settings,
                    progress,
                    model=model,
                )
            return await self._generate_veo_video(request, provider_settings, progress)
        raise MediaProviderError("Google media supports image and video jobs only.", code="unsupported_kind")

    def _client(self, api_key: str, timeout_seconds: float) -> tuple[Any, Any]:
        genai, types = _google_modules()
        http_options = types.HttpOptions(timeout=int(timeout_seconds * 1000))
        return genai.Client(api_key=api_key, http_options=http_options), types

    async def _generate_image(
        self,
        request: dict[str, Any],
        settings: dict[str, Any],
        progress: ProgressCallback,
    ) -> MediaProviderResult:
        prompt = str(request.get("prompt") or "").strip()
        if not prompt:
            raise MediaProviderError("Gemini image generation requires a prompt.", code="missing_prompt")
        output_count = _whole_number(
            1 if request.get("number_of_outputs") is None or request.get("number_of_outputs") == "" else request.get("number_of_outputs"),
            message="Gemini image generation currently returns one output per request.",
            code="google_unsupported_output_count",
        )
        if output_count != 1:
            raise MediaProviderError(
                "Gemini image generation currently returns one output per request.",
                code="google_unsupported_output_count",
            )
        api_key = require_api_key(settings, "Google")
        model = str(request.get("model") or settings.get("image_model") or "gemini-3.1-flash-image").strip()
        request_timeout = bounded_float(settings.get("request_timeout_seconds"), 600.0, 30.0, 1800.0)
        client, types = self._client(api_key, request_timeout)
        try:
            parts = [types.Part.from_text(text=prompt)]
            for reference in request_references(request):
                data, mime_type, _filename = read_reference(reference)
                if not mime_type.lower().startswith("image/"):
                    raise MediaProviderError(
                        "Gemini image references must be image files.",
                        code="google_unsupported_reference",
                    )
                parts.append(types.Part.from_bytes(data=data, mime_type=mime_type))
            contents = [types.Content(role="user", parts=parts)]
            image_config: dict[str, Any] = {}
            aspect_ratio = request.get("aspect_ratio")
            if aspect_ratio:
                image_config["aspect_ratio"] = str(aspect_ratio)
            image_size = request.get("resolution") or request.get("size")
            if image_size:
                image_config["image_size"] = str(image_size)
            config: dict[str, Any] = {"response_modalities": ["TEXT", "IMAGE"]}
            if image_config:
                config["image_config"] = image_config
            await emit_progress(progress, "Submitting Gemini image generation")
            try:
                response = await _sdk_call(
                    client.models.generate_content,
                    model=model,
                    contents=contents,
                    config=config,
                    timeout=request_timeout + 10.0,
                )
            except MediaProviderError:
                raise
            except Exception as exc:
                raise _google_error("image generation", exc) from exc
            artifacts = []
            maximum = configured_download_limit(settings)
            for index, part in enumerate(_parts(response), 1):
                output = _part_bytes(part)
                if output is None:
                    continue
                data, mime_type = output
                if len(data) > maximum:
                    raise MediaProviderError("Google image output exceeds the configured download limit.", code="output_too_large")
                artifacts.append(artifact_from_bytes(data, prefix="gemini-image", index=index, content_type=mime_type))
            if not artifacts:
                raise MediaProviderError("Gemini returned no generated image data.", retryable=True, code="google_empty_output")
            await emit_progress(progress, f"Received {len(artifacts)} Gemini image output(s)")
            return MediaProviderResult(
                artifacts=artifacts,
                provider_job_id=str(getattr(response, "response_id", None) or ""),
                metadata={
                    "provider": self.name,
                    "model": model,
                    "model_version": str(getattr(response, "model_version", None) or ""),
                    "usage": _dump(getattr(response, "usage_metadata", None)),
                    "text": str(getattr(response, "text", None) or "")[:4000],
                },
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    await asyncio.to_thread(close)
                except Exception:
                    pass

    async def _generate_omni_video(
        self,
        request: dict[str, Any],
        settings: dict[str, Any],
        progress: ProgressCallback,
        *,
        model: str,
    ) -> MediaProviderResult:
        prompt = str(request.get("prompt") or "").strip()
        if not prompt:
            raise MediaProviderError(
                "Gemini Omni video generation requires a prompt.",
                code="missing_prompt",
            )
        requested_output_count = request.get("number_of_outputs")
        output_count = _whole_number(
            1 if requested_output_count is None or requested_output_count == "" else requested_output_count,
            message="Gemini Omni currently returns one video per interaction.",
            code="google_unsupported_output_count",
        )
        if output_count != 1:
            raise MediaProviderError(
                "Gemini Omni currently returns one video per interaction.",
                code="google_unsupported_output_count",
            )
        api_key = require_api_key(settings, "Google")
        request_timeout = bounded_float(
            settings.get("request_timeout_seconds"),
            180.0,
            30.0,
            600.0,
        )
        client, _types = self._client(api_key, request_timeout)
        try:
            interaction_id = str(request.get("_resume_provider_job_id") or "").strip()
            # The durable interaction id is sufficient to resume. Re-reading
            # or re-uploading the original references here would duplicate a
            # remote upload after a worker lease recovery.
            references = [] if interaction_id else request_references(request)
            roles = reference_roles(request, len(references))
            interaction_inputs: list[dict[str, Any]] = []
            video_paths: list[Path] = []
            image_count = 0
            for index, reference in enumerate(references):
                role = roles[index]
                if role == "last_frame":
                    raise MediaProviderError(
                        "Gemini Omni does not support last-frame interpolation; use Veo 3.1.",
                        code="google_unsupported_reference",
                    )
                if isinstance(reference, dict):
                    raw = str(reference.get("path") or reference.get("url") or reference.get("uri") or "").strip()
                else:
                    raw = str(reference or "").strip()
                if raw.startswith("data:"):
                    mime_type = raw[5:].split(";", 1)[0].lower()
                else:
                    mime_type = (mimetypes.guess_type(Path(raw).name)[0] or "").lower()
                if role in {"audio", "reference_audio"} or mime_type.startswith("audio/"):
                    raise MediaProviderError(
                        "Gemini Omni does not currently support uploaded audio references.",
                        code="google_unsupported_reference",
                    )
                is_video = role == "reference_video" or mime_type.startswith("video/")
                if is_video:
                    if raw.startswith(("http://", "https://", "data:")):
                        raise MediaProviderError(
                            "Gemini Omni video editing requires a local video file for the Google Files API.",
                            code="google_unsupported_reference",
                        )
                    path = Path(raw).expanduser().resolve()
                    if not path.is_file():
                        raise MediaProviderError(
                            "Gemini Omni video reference is unavailable.",
                            code="missing_reference",
                        )
                    video_paths.append(path)
                    continue
                data, mime_type, _filename = read_reference(reference)
                if not mime_type.lower().startswith("image/"):
                    raise MediaProviderError(
                        "Gemini Omni references must be images or one local video.",
                        code="google_unsupported_reference",
                    )
                image_count += 1
                interaction_inputs.append(
                    {
                        "type": "image",
                        "data": base64.b64encode(data).decode("ascii"),
                        "mime_type": mime_type,
                    }
                )
            if len(video_paths) > 1:
                raise MediaProviderError(
                    "Gemini Omni supports at most one video reference.",
                    code="google_unsupported_reference",
                )
            if video_paths and image_count:
                raise MediaProviderError(
                    "Gemini Omni video editing cannot mix a video with image references.",
                    code="google_invalid_references",
                )

            if request.get("negative_prompt"):
                prompt += "\nAvoid: " + str(request["negative_prompt"]).strip()
            if request.get("generate_audio") is False:
                prompt += "\nDo not include an audio track."
            interaction_state: dict[str, Any] = {"api_kind": "interactions"}
            if video_paths:
                await emit_progress(progress, "Uploading Google video reference")
                try:
                    uploaded = await _sdk_call(
                        client.files.upload,
                        file=str(video_paths[0]),
                        timeout=request_timeout + 10.0,
                    )
                except MediaProviderError:
                    raise
                except Exception as exc:
                    raise _google_error("video reference upload", exc) from exc
                upload_deadline = time.monotonic() + bounded_float(
                    settings.get("upload_timeout_seconds"),
                    900.0,
                    60.0,
                    3600.0,
                )
                while True:
                    state_value = getattr(uploaded, "state", None)
                    state_name = str(getattr(state_value, "name", None) or state_value or "").split(".")[-1].strip().lower()
                    if state_name in {"active", "ready", "succeeded"}:
                        break
                    if state_name in {"failed", "cancelled", "expired"}:
                        raise MediaProviderError(
                            "Google Files API failed to process the video reference.",
                            code="google_reference_upload_failed",
                        )
                    if time.monotonic() >= upload_deadline:
                        raise MediaProviderError(
                            "Google video reference processing timed out.",
                            retryable=True,
                            code="google_reference_upload_timeout",
                        )
                    await asyncio.sleep(5.0)
                    uploaded = await _sdk_call(
                        client.files.get,
                        name=str(getattr(uploaded, "name", None) or ""),
                        timeout=request_timeout + 10.0,
                    )
                uploaded_uri = str(getattr(uploaded, "uri", None) or "")
                if not uploaded_uri:
                    raise MediaProviderError(
                        "Google Files API returned no video URI.",
                        retryable=True,
                        code="google_reference_upload_failed",
                    )
                interaction_inputs.append({"type": "document", "uri": uploaded_uri})
                interaction_state["uploaded_file"] = str(getattr(uploaded, "name", None) or "")

            interaction_inputs.append({"type": "text", "text": prompt})
            response_format: dict[str, Any] = {
                "type": "video",
                "delivery": "uri",
            }
            aspect_ratio = str(request.get("aspect_ratio") or "").strip()
            if aspect_ratio:
                if aspect_ratio not in {"16:9", "9:16"}:
                    raise MediaProviderError(
                        "Gemini Omni video aspect ratio must be 16:9 or 9:16.",
                        code="google_invalid_aspect_ratio",
                    )
                response_format["aspect_ratio"] = aspect_ratio
            resolution = str(request.get("resolution") or "").strip().lower()
            if resolution:
                if resolution not in {"360p", "720p", "1080p", "4k"}:
                    raise MediaProviderError(
                        "Gemini Omni video resolution must be 360p, 720p, 1080p, or 4k.",
                        code="google_invalid_resolution",
                    )
                response_format["resolution"] = resolution
            duration = request.get("duration")
            if duration is not None and duration != "":
                numeric_duration = _whole_number(
                    duration,
                    message=("Gemini Omni video duration must be a whole number between 1 and 600 seconds."),
                    code="google_invalid_duration",
                )
                if not 1 <= numeric_duration <= 600:
                    raise MediaProviderError(
                        "Gemini Omni video duration must be a whole number between 1 and 600 seconds.",
                        code="google_invalid_duration",
                    )
                response_format["duration"] = f"{numeric_duration}s"
            output_format = str(request.get("output_format") or "").strip().lower()
            if output_format and output_format not in {"mp4", "video/mp4"}:
                raise MediaProviderError(
                    "Gemini Omni video output format is MP4.",
                    code="google_invalid_output_format",
                )
            if video_paths:
                task = "edit"
            elif image_count > 1 or any(role in {"subject", "reference_image"} for role in roles):
                task = "reference_to_video"
            elif image_count:
                task = "image_to_video"
            else:
                task = "text_to_video"

            if interaction_id:
                await emit_progress(
                    progress,
                    "Resuming Gemini Omni video interaction",
                    provider_job_id=interaction_id,
                    state={**interaction_state, "status": "resuming"},
                )
                try:
                    interaction = await _sdk_call(
                        client.interactions.get,
                        interaction_id,
                        timeout=request_timeout + 10.0,
                    )
                except MediaProviderError:
                    raise
                except Exception as exc:
                    raise _google_error("Omni interaction status", exc) from exc
            else:
                await emit_progress(progress, "Submitting Gemini Omni video generation")
                try:
                    interaction = await _sdk_call(
                        client.interactions.create,
                        model=model,
                        input=(prompt if len(interaction_inputs) == 1 else interaction_inputs),
                        response_format=response_format,
                        generation_config={"video_config": {"task": task}},
                        background=True,
                        store=True,
                        stream=False,
                        timeout=request_timeout + 10.0,
                    )
                except MediaProviderError:
                    raise
                except Exception as exc:
                    raise _google_error("Omni video submission", exc) from exc
                interaction_id = str(getattr(interaction, "id", None) or "")
                if not interaction_id:
                    raise MediaProviderError(
                        "Gemini Omni returned no interaction ID.",
                        retryable=True,
                        code="google_missing_interaction",
                    )
                await emit_progress(
                    progress,
                    "Gemini Omni video interaction queued",
                    provider_job_id=interaction_id,
                    state={**interaction_state, "status": str(getattr(interaction, "status", None) or "queued")},
                )

            deadline = time.monotonic() + bounded_float(
                request_value(
                    request,
                    "generation_timeout_seconds",
                    settings.get("generation_timeout_seconds"),
                ),
                1800.0,
                60.0,
                7200.0,
            )
            poll_seconds = bounded_float(
                settings.get("poll_interval_seconds"),
                5.0,
                2.0,
                60.0,
            )
            terminal_failures = {
                "failed",
                "cancelled",
                "incomplete",
                "budget_exceeded",
            }
            while True:
                status = str(getattr(interaction, "status", None) or "").lower()
                if status == "completed":
                    break
                if status in terminal_failures:
                    raise MediaProviderError(
                        f"Gemini Omni video interaction ended with status {status}.",
                        code=f"google_omni_{status}",
                    )
                if time.monotonic() >= deadline:
                    raise MediaProviderError(
                        "Gemini Omni video generation timed out.",
                        retryable=True,
                        code="google_video_timeout",
                    )
                await asyncio.sleep(min(poll_seconds, max(0.1, deadline - time.monotonic())))
                try:
                    interaction = await _sdk_call(
                        client.interactions.get,
                        interaction_id,
                        timeout=request_timeout + 10.0,
                    )
                except MediaProviderError:
                    raise
                except Exception as exc:
                    raise _google_error("Omni interaction status", exc) from exc
                await emit_progress(
                    progress,
                    f"Gemini Omni video interaction: {str(getattr(interaction, 'status', None) or 'processing')}",
                    provider_job_id=interaction_id,
                    state={**interaction_state, "status": str(getattr(interaction, "status", None) or "processing")},
                )

            output_video = getattr(interaction, "output_video", None)
            if output_video is None:
                raise MediaProviderError(
                    "Gemini Omni completed without video output.",
                    retryable=True,
                    code="google_empty_output",
                )
            content_type = str(getattr(output_video, "mime_type", None) or "video/mp4")
            if not content_type.lower().startswith("video/"):
                raise MediaProviderError(
                    "Gemini Omni returned a non-video output.",
                    code="google_invalid_output",
                )
            encoded = getattr(output_video, "data", None)
            maximum = configured_download_limit(settings)
            if isinstance(encoded, str) and encoded:
                try:
                    data = base64.b64decode(encoded, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise MediaProviderError(
                        "Gemini Omni returned invalid video data.",
                        code="google_invalid_output",
                    ) from exc
                if len(data) > maximum:
                    raise MediaProviderError(
                        "Gemini Omni video exceeds the configured download limit.",
                        code="output_too_large",
                    )
                artifact = artifact_from_bytes(
                    data,
                    prefix="gemini-omni-video",
                    index=1,
                    content_type=content_type,
                )
            else:
                uri = str(getattr(output_video, "uri", None) or "")
                if not uri.startswith("https://"):
                    raise MediaProviderError(
                        "Gemini Omni returned no downloadable video.",
                        retryable=True,
                        code="google_empty_output",
                    )
                artifact = MediaArtifact(
                    filename="gemini-omni-video-1.mp4",
                    content_type=content_type,
                    url=uri,
                    headers={"x-goog-api-key": api_key},
                )
            return MediaProviderResult(
                artifacts=[artifact],
                provider_job_id=interaction_id,
                metadata={
                    "provider": self.name,
                    "model": model,
                    "api_kind": "interactions",
                    "status": "completed",
                    "usage": _dump(getattr(interaction, "usage", None)),
                },
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    await asyncio.to_thread(close)
                except Exception:
                    pass

    async def _generate_veo_video(
        self,
        request: dict[str, Any],
        settings: dict[str, Any],
        progress: ProgressCallback,
    ) -> MediaProviderResult:
        api_key = require_api_key(settings, "Google")
        model = str(request.get("model") or settings.get("video_model") or "veo-3.1-generate-preview").strip()
        request_timeout = bounded_float(settings.get("request_timeout_seconds"), 180.0, 30.0, 600.0)
        client, types = self._client(api_key, request_timeout)
        try:
            operation_name = str(request.get("_resume_provider_job_id") or "").strip()
            resumed = bool(operation_name)
            if operation_name:
                operation = types.GenerateVideosOperation(name=operation_name)
                await emit_progress(
                    progress,
                    "Resuming Google Veo task",
                    provider_job_id=operation_name,
                    state={"status": "resuming"},
                )
            else:
                prompt = str(request.get("prompt") or "").strip()
                if not prompt:
                    raise MediaProviderError("Google Veo requires a prompt.", code="missing_prompt")
                refs = request_references(request)
                roles = reference_roles(request, len(refs))
                decoded_references: list[tuple[Any, str]] = []
                for index, reference in enumerate(refs):
                    data, mime_type, _filename = read_reference(reference)
                    if roles[index] in {
                        "audio",
                        "reference_audio",
                        "video",
                        "reference_video",
                    } or not mime_type.lower().startswith("image/"):
                        raise MediaProviderError(
                            "Google Veo reference inputs must be images.",
                            code="google_unsupported_reference",
                        )
                    decoded_references.append((types.Image(image_bytes=data, mime_type=mime_type), roles[index]))

                has_explicit_frames = any(role in {"first_frame", "last_frame"} for _image, role in decoded_references)
                asset_mode = any(role in {"subject", "reference_image"} for _image, role in decoded_references) or (not has_explicit_frames and len(decoded_references) > 2)
                if has_explicit_frames and asset_mode:
                    raise MediaProviderError(
                        "Google Veo cannot mix first/last frames with asset reference images.",
                        code="google_invalid_references",
                    )
                first_image = None
                last_image = None
                asset_references = []
                for image, role in decoded_references:
                    if asset_mode:
                        asset_references.append(types.VideoGenerationReferenceImage(image=image, reference_type="asset"))
                    elif role == "last_frame":
                        if last_image is not None:
                            raise MediaProviderError(
                                "Google Veo accepts at most one last-frame image.",
                                code="google_invalid_references",
                            )
                        last_image = image
                    else:
                        if role == "first_frame" and first_image is not None:
                            raise MediaProviderError(
                                "Google Veo accepts at most one first-frame image.",
                                code="google_invalid_references",
                            )
                        if first_image is None:
                            first_image = image
                        elif last_image is None:
                            last_image = image
                        else:
                            raise MediaProviderError(
                                "Google Veo frame interpolation accepts at most two images.",
                                code="google_invalid_references",
                            )
                if len(asset_references) > 3:
                    raise MediaProviderError(
                        "Google Veo accepts at most three asset reference images.",
                        code="google_invalid_references",
                    )
                if asset_references and "veo-3.1" not in model.lower():
                    raise MediaProviderError(
                        "Asset reference images require a Veo 3.1 model.",
                        code="google_unsupported_reference",
                    )
                if last_image is not None and first_image is None:
                    raise MediaProviderError(
                        "Google Veo last-frame interpolation also requires a first-frame image.",
                        code="google_invalid_references",
                    )

                requested_output_count = request.get("number_of_outputs")
                output_count = _whole_number(
                    1 if requested_output_count is None or requested_output_count == "" else requested_output_count,
                    message="Google Veo number_of_outputs must be a supported whole number.",
                    code="google_unsupported_output_count",
                )
                maximum_outputs = 2 if "veo-2" in model.lower() else 1
                if not 1 <= output_count <= maximum_outputs:
                    raise MediaProviderError(
                        f"{model or 'Google Veo'} supports 1" + (" or 2" if maximum_outputs == 2 else "") + " output video(s) per request.",
                        code="google_unsupported_output_count",
                    )
                config_values: dict[str, Any] = {
                    "number_of_videos": output_count,
                }
                mapping = {
                    "aspect_ratio": request.get("aspect_ratio"),
                    "resolution": request.get("resolution"),
                    "negative_prompt": request.get("negative_prompt"),
                    "seed": request.get("seed"),
                    "person_generation": request_value(request, "person_generation"),
                    "enhance_prompt": request_value(request, "enhance_prompt"),
                    "generate_audio": request.get("generate_audio") if request.get("generate_audio") is not None else request_value(request, "generate_audio"),
                }
                for key, value in mapping.items():
                    if value is not None and value != "":
                        config_values[key] = value
                aspect_ratio = str(config_values.get("aspect_ratio") or "").strip()
                if aspect_ratio and aspect_ratio not in {"16:9", "9:16"}:
                    raise MediaProviderError(
                        "Google Veo aspect ratio must be 16:9 or 9:16.",
                        code="google_invalid_aspect_ratio",
                    )
                resolution = str(config_values.get("resolution") or "").strip().lower()
                if resolution and resolution not in {"720p", "1080p", "4k"}:
                    raise MediaProviderError(
                        "Google Veo resolution must be 720p, 1080p, or 4k.",
                        code="google_invalid_resolution",
                    )
                if request.get("duration") is not None and request.get("duration") != "":
                    config_values["duration_seconds"] = _veo_duration(
                        request.get("duration"),
                        model,
                    )
                duration_seconds = config_values.get("duration_seconds")
                requires_eight_seconds = bool(asset_references) or resolution in {
                    "1080p",
                    "4k",
                }
                if requires_eight_seconds and duration_seconds not in {None, 8}:
                    raise MediaProviderError(
                        "Google Veo requires an 8-second duration for asset references, 1080p, and 4k output.",
                        code="google_invalid_duration",
                    )
                if requires_eight_seconds and duration_seconds is None:
                    config_values["duration_seconds"] = 8
                if last_image is not None:
                    config_values["last_frame"] = last_image
                if asset_references:
                    config_values["reference_images"] = asset_references
                config = types.GenerateVideosConfig(**config_values)
                kwargs: dict[str, Any] = {"model": model, "prompt": prompt, "config": config}
                if first_image is not None:
                    kwargs["image"] = first_image
                await emit_progress(progress, "Submitting Google Veo video generation")
                try:
                    operation = await _sdk_call(client.models.generate_videos, timeout=request_timeout + 10.0, **kwargs)
                except MediaProviderError:
                    raise
                except Exception as exc:
                    raise _google_error("Veo submission", exc) from exc
                operation_name = str(getattr(operation, "name", None) or "")
                if not operation_name:
                    raise MediaProviderError(
                        "Google Veo returned no operation name.",
                        retryable=True,
                        code="google_missing_operation",
                    )
                await emit_progress(progress, "Google Veo task queued", provider_job_id=operation_name, state={"status": "queued"})
            total_timeout = bounded_float(
                request_value(request, "generation_timeout_seconds", settings.get("generation_timeout_seconds")),
                1800.0,
                60.0,
                7200.0,
            )
            poll_seconds = bounded_float(settings.get("poll_interval_seconds"), 10.0, 3.0, 60.0)
            deadline = time.monotonic() + total_timeout
            first_poll = True
            while not bool(getattr(operation, "done", False)) and time.monotonic() < deadline:
                # A reconstructed operation has no status payload, so query it
                # immediately. Newly submitted operations retain the provider's
                # recommended delay before their first status request.
                if not (resumed and first_poll):
                    await asyncio.sleep(min(poll_seconds, max(0.1, deadline - time.monotonic())))
                first_poll = False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    operation = await _sdk_call(
                        client.operations.get,
                        operation,
                        timeout=min(request_timeout + 10.0, max(0.1, remaining)),
                    )
                except MediaProviderError:
                    raise
                except Exception as exc:
                    raise _google_error("Veo status check", exc) from exc
                await emit_progress(progress, "Google Veo task processing", provider_job_id=operation_name, state={"status": "processing"})
            if not bool(getattr(operation, "done", False)):
                raise MediaProviderError("Google Veo video generation timed out.", retryable=True, code="google_video_timeout")
            error = getattr(operation, "error", None)
            if error:
                raise MediaProviderError(f"Google Veo generation failed: {str(error)[:1600]}", code="google_video_failed")
            response = getattr(operation, "response", None) or getattr(operation, "result", None)
            generated_videos = getattr(response, "generated_videos", None) or []
            if not generated_videos:
                raise MediaProviderError("Google Veo completed without generated videos.", retryable=True, code="google_empty_output")
            artifacts = []
            maximum = configured_download_limit(settings)
            for index, generated in enumerate(generated_videos, 1):
                try:
                    data, mime_type = await _sdk_call(_video_bytes, client, generated, timeout=300.0)
                except MediaProviderError:
                    raise
                except Exception as exc:
                    raise _google_error("Veo download", exc) from exc
                if len(data) > maximum:
                    raise MediaProviderError("Google Veo output exceeds the configured download limit.", code="output_too_large")
                artifacts.append(artifact_from_bytes(data, prefix="google-veo", index=index, content_type=mime_type))
            await emit_progress(progress, f"Downloaded {len(artifacts)} Google Veo output(s)", provider_job_id=operation_name)
            return MediaProviderResult(
                artifacts=artifacts,
                provider_job_id=operation_name,
                metadata={"provider": self.name, "model": model, "operation": operation_name},
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    await asyncio.to_thread(close)
                except Exception:
                    pass


__all__ = ["GoogleMediaProvider"]
