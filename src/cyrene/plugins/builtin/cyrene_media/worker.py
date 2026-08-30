"""Provider-neutral media job worker."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path
import tempfile
from typing import Any

import httpx
from PIL import Image

from .delivery import MediaDelivery
from .manager import MediaJobManager
from .models import MediaArtifact, MediaProviderError
from .providers.helpers import download_to_path, extension_for_mime
from .settings import get_media_settings
from cyrene.platform.attachments import (
    register_generated_attachment,
    register_generated_attachment_bytes,
    safe_attachment_filename,
)

logger = logging.getLogger(__name__)

_EXPECTED_MEDIA_PREFIX = {
    "image": "image/",
    "video": "video/",
    "music": "audio/",
}
_EXPECTED_MEDIA_EXTENSIONS = {
    "image": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif"},
    "video": {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".mpeg", ".mpg"},
    "music": {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"},
}


class _LeaseLost(RuntimeError):
    """The durable job is no longer owned by this worker."""


class MediaWorker:
    """Claims one durable job at a time and executes it outside the Agent run."""

    def __init__(self, manager: MediaJobManager, worker_id: str) -> None:
        self.manager = manager
        self.worker_id = str(worker_id)
        self.delivery = MediaDelivery(manager)
        self.current_job_id = ""

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self._reconcile_reports()
                jobs = await asyncio.to_thread(
                    self.manager.claim_jobs,
                    self.worker_id,
                    limit=1,
                    lease_seconds=180.0,
                )
                if not jobs:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=0.8)
                    except asyncio.TimeoutError:
                        pass
                    continue
                await self._process(jobs[0])
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("media worker %s iteration failed", self.worker_id)
                await asyncio.sleep(1.0)

    async def _reconcile_reports(self) -> None:
        for job in await asyncio.to_thread(self.manager.pending_reports, limit=20):
            try:
                await self.delivery.report(job)
            except Exception:
                logger.exception("failed to reconcile media job delivery %s", job.get("job_id"))

    async def _process(self, job: dict[str, Any]) -> None:
        from .providers import resolve_provider

        job_id = str(job.get("job_id") or "")
        token = str(job.get("lease_token") or "")
        self.current_job_id = job_id
        heartbeat = asyncio.create_task(self._heartbeat(job_id, token))
        generation: asyncio.Task[Any] | None = None
        try:
            settings = await asyncio.to_thread(get_media_settings)
            request = dict(job.get("request") or {})
            request["_media_job_id"] = job_id
            provider_name, provider = resolve_provider(
                str(job.get("provider") or request.get("provider") or "auto"),
                str(job.get("kind") or request.get("kind") or ""),
                settings,
                request,
            )
            provider_settings = dict((settings.get("providers") or {}).get(provider_name) or {})
            # Provider polling and remote-output downloads happen inside the
            # adapter, so carry the shared execution policy into that isolated
            # settings view without persisting synthetic provider fields.
            provider_settings.setdefault(
                "poll_interval_seconds",
                float(settings.get("poll_interval_seconds") or 3.0),
            )
            provider_settings.setdefault(
                "max_download_mb",
                int(settings.get("max_download_mb") or 256),
            )
            model = str(request.get("model") or job.get("model") or provider_settings.get(f"{job.get('kind')}_model") or "")
            request["model"] = model
            if str(job.get("provider_job_id") or ""):
                request["_resume_provider_job_id"] = str(job["provider_job_id"])
                request["_resume_provider_state"] = dict(job.get("provider_state") or {})
            assigned = await asyncio.to_thread(
                self.manager.assign_provider,
                job_id,
                token,
                provider=provider_name,
                model=model,
            )
            if not assigned:
                raise _LeaseLost

            async def progress(
                message: str,
                provider_job_id: str = "",
                state: dict[str, Any] | None = None,
            ) -> None:
                updated = await asyncio.to_thread(
                    self.manager.update_progress,
                    job_id,
                    token,
                    progress=str(message or "running"),
                    provider_job_id=str(provider_job_id or ""),
                    provider_state=state or {},
                )
                if not updated:
                    raise _LeaseLost

            if not await asyncio.to_thread(
                self.manager.heartbeat,
                job_id,
                token,
                lease_seconds=180.0,
            ):
                raise _LeaseLost

            generation = asyncio.create_task(
                provider.generate(request, provider_settings, progress),
                name=f"cyrene-media-provider-{job_id}",
            )
            done, _pending = await asyncio.wait(
                {generation, heartbeat},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat in done and not generation.done():
                heartbeat_error = heartbeat.exception()
                if heartbeat_error is not None:
                    raise heartbeat_error
                # Cancellation or lease recovery transferred ownership while
                # the remote provider was still running. Stop local polling /
                # downloading and let the durable row's current owner settle.
                generation.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await generation
                latest = await asyncio.to_thread(self.manager.get_job, job_id)
                if (
                    latest
                    and str(latest.get("status") or "")
                    in {
                        "succeeded",
                        "failed",
                        "cancelled",
                    }
                    and not str(latest.get("reported_at") or "")
                ):
                    await self.delivery.report(latest)
                return
            result = await generation
            attachments = []
            preferred_name = str(request.get("name") or "").strip()
            for index, artifact in enumerate(result.artifacts):
                if not await asyncio.to_thread(
                    self.manager.heartbeat,
                    job_id,
                    token,
                    lease_seconds=180.0,
                ):
                    raise _LeaseLost
                attachments.append(
                    await self._register_artifact(
                        artifact,
                        index=index,
                        total=len(result.artifacts),
                        expected_kind=str(job.get("kind") or ""),
                        preferred_name=preferred_name,
                        max_download_mb=int(settings.get("max_download_mb") or 256),
                    )
                )
            if not attachments:
                raise MediaProviderError("media provider returned no output")
            if not await asyncio.to_thread(
                self.manager.heartbeat,
                job_id,
                token,
                lease_seconds=180.0,
            ):
                raise _LeaseLost
            try:
                completed = await asyncio.to_thread(
                    self.manager.complete_job,
                    job_id,
                    token,
                    attachments=attachments,
                    provider_job_id=str(result.provider_job_id or ""),
                    provider_metadata=result.metadata,
                )
            except ValueError as exc:
                raise _LeaseLost from exc
            await self.delivery.report(completed)
        except asyncio.CancelledError:
            raise
        except _LeaseLost:
            latest = await asyncio.to_thread(self.manager.get_job, job_id)
            if (
                latest
                and str(latest.get("status") or "")
                in {
                    "succeeded",
                    "failed",
                    "cancelled",
                }
                and not str(latest.get("reported_at") or "")
            ):
                await self.delivery.report(latest)
        except MediaProviderError as exc:
            await self._fail(job_id, token, exc, exc.retryable, exc.code)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            await self._fail(job_id, token, exc, True, "network_error")
        except Exception as exc:
            await self._fail(job_id, token, exc, False, "worker_error")
        finally:
            if generation is not None and not generation.done():
                generation.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await generation
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            self.current_job_id = ""

    async def _fail(
        self,
        job_id: str,
        token: str,
        exc: BaseException,
        retryable: bool,
        code: str,
    ) -> None:
        try:
            failed = await asyncio.to_thread(
                self.manager.fail_job,
                job_id,
                token,
                str(exc),
                error_code=code,
                retryable=retryable,
            )
            if str(failed.get("status") or "") in {"failed", "cancelled"}:
                await self.delivery.report(failed)
        except ValueError:
            # Cancellation or lease recovery won the race; its owner now owns
            # the durable outcome and delivery.
            logger.info("media job %s lease changed before failure settlement", job_id)
        except Exception:
            logger.exception("failed to settle media job %s", job_id)

    async def _heartbeat(self, job_id: str, token: str) -> None:
        while True:
            await asyncio.sleep(30.0)
            owned = await asyncio.to_thread(
                self.manager.heartbeat,
                job_id,
                token,
                lease_seconds=180.0,
            )
            if not owned:
                return

    async def _register_artifact(
        self,
        artifact: MediaArtifact,
        *,
        index: int,
        total: int,
        expected_kind: str,
        preferred_name: str,
        max_download_mb: int,
    ) -> dict[str, Any]:
        byte_limit = max(10, min(max_download_mb, 1024)) * 1024 * 1024
        requested_name = preferred_name
        if requested_name and total > 1:
            requested_path = Path(requested_name)
            requested_name = f"{requested_path.stem or 'media'}-{index + 1}{requested_path.suffix}"
        name = safe_attachment_filename(
            requested_name or artifact.filename or f"media-{index + 1}.bin",
            "media",
        )
        if not Path(name).suffix and Path(artifact.filename).suffix:
            name += Path(artifact.filename).suffix
        if artifact.data is not None:
            self._validate_artifact_type(
                expected_kind,
                artifact.content_type,
                name,
            )
            name = self._normalize_artifact_name(
                name,
                artifact.content_type,
                expected_kind,
            )
            if len(artifact.data) > byte_limit:
                raise MediaProviderError(
                    "Generated media exceeds the configured download limit.",
                    code="output_too_large",
                )
            return await asyncio.to_thread(
                register_generated_attachment_bytes,
                artifact.data,
                display_name=name,
                content_type=artifact.content_type,
            )
        if artifact.path is not None:
            self._validate_artifact_type(
                expected_kind,
                artifact.content_type,
                name,
            )
            name = self._normalize_artifact_name(
                name,
                artifact.content_type,
                expected_kind,
            )
            artifact_path = Path(artifact.path).resolve()
            if not artifact_path.is_file():
                raise MediaProviderError(
                    "Generated media path is unavailable.",
                    code="missing_output",
                )
            if artifact_path.stat().st_size > byte_limit:
                raise MediaProviderError(
                    "Generated media exceeds the configured download limit.",
                    code="output_too_large",
                )
            if expected_kind == "image":
                await asyncio.to_thread(self._verify_image_path, artifact_path)
            return await asyncio.to_thread(
                register_generated_attachment,
                str(artifact_path),
                display_name=name,
            )
        if not artifact.url:
            raise MediaProviderError("media artifact has no bytes, path, or URL")
        suffix = Path(name).suffix or ".bin"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="cyrene-media-",
            suffix=suffix,
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        try:
            content_type, remote_name = await download_to_path(
                artifact.url,
                temporary_path,
                headers=artifact.headers,
                max_bytes=byte_limit,
            )
            display_name = name or safe_attachment_filename(remote_name, "media")
            server_type = str(content_type or "").split(";", 1)[0].lower()
            declared_type = str(artifact.content_type or "").split(";", 1)[0].lower()
            effective_type = server_type if server_type and server_type != "application/octet-stream" else declared_type or server_type
            self._validate_artifact_type(
                expected_kind,
                effective_type,
                display_name or remote_name,
            )
            display_name = self._normalize_artifact_name(
                display_name,
                effective_type,
                expected_kind,
            )
            if expected_kind == "image":
                await asyncio.to_thread(self._verify_image_path, temporary_path)
            return await asyncio.to_thread(
                register_generated_attachment,
                str(temporary_path),
                display_name=display_name,
            )
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _validate_artifact_type(
        expected_kind: str,
        content_type: str,
        filename: str,
    ) -> None:
        expected = str(expected_kind or "").strip().lower()
        prefix = _EXPECTED_MEDIA_PREFIX.get(expected)
        if prefix is None:
            raise MediaProviderError(
                "Generated artifact has an unsupported media kind.",
                code="invalid_output_type",
            )
        normalized_type = str(content_type or "").split(";", 1)[0].strip().lower()
        suffix = Path(str(filename or "")).suffix.lower()
        if normalized_type and normalized_type != "application/octet-stream":
            valid = normalized_type.startswith(prefix)
        else:
            valid = suffix in _EXPECTED_MEDIA_EXTENSIONS[expected]
        if not valid:
            raise MediaProviderError(
                f"Provider returned a non-{expected} artifact.",
                code="invalid_output_type",
            )

    @staticmethod
    def _normalize_artifact_name(
        filename: str,
        content_type: str,
        expected_kind: str,
    ) -> str:
        path = Path(filename)
        if path.suffix.lower() in _EXPECTED_MEDIA_EXTENSIONS[expected_kind]:
            return filename
        suffix = extension_for_mime(
            content_type,
            default={"image": ".png", "video": ".mp4", "music": ".mp3"}[expected_kind],
        )
        return f"{path.stem or 'media'}{suffix}"

    @staticmethod
    def _verify_image_path(path: Path) -> None:
        try:
            with Image.open(path) as image:
                width, height = int(image.width), int(image.height)
                if width <= 0 or height <= 0 or width * height > 80_000_000:
                    raise ValueError("unsafe image dimensions")
                image.verify()
        except Exception as exc:
            raise MediaProviderError(
                "Provider returned invalid image data.",
                code="invalid_output_type",
            ) from exc


__all__ = ["MediaWorker"]
