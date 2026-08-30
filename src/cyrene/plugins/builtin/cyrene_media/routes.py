"""Read and cancellation API for durable media jobs."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query
from .daemon import MediaDaemon
from .delivery import MediaDelivery
from .manager import MediaJobManager
from .settings import redact_media_secrets
from cyrene.workbench.http.errors import localized_error_response


_PUBLIC_JOB_FIELDS = (
    "job_id",
    "batch_id",
    "ordinal",
    "chat_id",
    "project_id",
    "kind",
    "provider",
    "model",
    "status",
    "attempts",
    "max_attempts",
    "progress",
    "error_code",
    "created_at",
    "updated_at",
    "started_at",
    "completed_at",
    "reported_at",
)
_PUBLIC_REQUEST_FIELDS = (
    "kind",
    "provider",
    "prompt",
    "negative_prompt",
    "lyrics",
    "model",
    "name",
    "reference_attachment_ids",
    "reference_roles",
    "mask_attachment_id",
    "size",
    "aspect_ratio",
    "resolution",
    "quality",
    "output_format",
    "duration",
    "number_of_outputs",
    "seed",
    "is_instrumental",
    "generate_audio",
)
_PUBLIC_BATCH_FIELDS = (
    "batch_id",
    "wake_id",
    "chat_id",
    "project_id",
    "created_at",
    "updated_at",
)
_PUBLIC_WAKE_FIELDS = (
    "wake_id",
    "batch_id",
    "status",
    "created_at",
    "ready_at",
    "delivered_at",
    "cancelled_at",
)


def _one_line(value: Any, limit: int) -> str:
    return " ".join(str(value or "").splitlines()).strip()[:limit]


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    from cyrene.platform.attachments import build_public_attachment_payload

    raw_request = job.get("request") if isinstance(job.get("request"), dict) else {}
    request = {key: raw_request[key] for key in _PUBLIC_REQUEST_FIELDS if key in raw_request}
    local_references = raw_request.get("reference_paths")
    remote_references = raw_request.get("reference_urls")
    reference_count = (len(local_references) if isinstance(local_references, list) else 0) + (len(remote_references) if isinstance(remote_references, list) else 0)
    if reference_count:
        request["reference_count"] = reference_count
    if raw_request.get("mask_path"):
        request["has_mask"] = True
    public = {key: job[key] for key in _PUBLIC_JOB_FIELDS if key in job}
    public["progress"] = _one_line(public.get("progress"), 500)
    public["error_code"] = _one_line(public.get("error_code"), 120)
    public["request"] = request
    public["attachments"] = [build_public_attachment_payload(item) for item in job.get("attachments") or [] if isinstance(item, dict)]
    return redact_media_secrets(public)


def _public_wake(wake: Any) -> dict[str, Any]:
    if not isinstance(wake, dict):
        return {}
    public = {key: wake[key] for key in _PUBLIC_WAKE_FIELDS if key in wake}
    raw_summary = wake.get("summary") if isinstance(wake.get("summary"), dict) else {}
    summary: dict[str, Any] = {key: raw_summary[key] for key in ("batch_id", "succeeded", "failed", "cancelled") if key in raw_summary}
    summary["jobs"] = [
        {
            key: item[key]
            for key in (
                "job_id",
                "kind",
                "provider",
                "model",
                "status",
                "attachment_ids",
                "error_code",
            )
            if key in item
        }
        for item in raw_summary.get("jobs") or []
        if isinstance(item, dict)
    ]
    if summary:
        public["summary"] = summary
    return redact_media_secrets(public)


def _public_batch(batch: dict[str, Any]) -> dict[str, Any]:
    return {
        **{key: batch[key] for key in _PUBLIC_BATCH_FIELDS if key in batch},
        "wake": _public_wake(batch.get("wake")),
        "jobs": [_public_job(job) for job in batch.get("jobs") or [] if isinstance(job, dict)],
    }


def _public_daemon_status(status: Any) -> dict[str, Any]:
    raw = status if isinstance(status, dict) else {}
    counts = raw.get("counts") if isinstance(raw.get("counts"), dict) else {}
    return redact_media_secrets(
        {
            "running": raw.get("running") is True,
            "worker_count": max(0, int(raw.get("worker_count") or 0)),
            "active_job_ids": [_one_line(item, 160) for item in raw.get("active_job_ids") or [] if str(item or "").strip()],
            "counts": {status_name: max(0, int(counts.get(status_name) or 0)) for status_name in ("queued", "claimed", "succeeded", "failed", "cancelled")},
        }
    )


def register_media_routes(
    router: APIRouter,
    *,
    manager: MediaJobManager,
    daemon: MediaDaemon,
) -> None:

    @router.get("/api/media/status")
    async def api_media_status():
        status = await asyncio.to_thread(daemon.status)
        return _public_daemon_status(status)

    @router.get("/api/media/jobs")
    async def api_media_jobs(
        chat_id: str = "",
        batch_id: str = "",
        limit: int = Query(default=50, ge=1, le=200),
    ):
        jobs = await asyncio.to_thread(
            manager.list_jobs,
            chat_id=chat_id,
            batch_id=batch_id,
            limit=limit,
        )
        return {"jobs": [_public_job(job) for job in jobs]}

    @router.get("/api/media/jobs/{job_id}")
    async def api_media_job(job_id: str):
        job = await asyncio.to_thread(manager.get_job, job_id)
        if not job:
            return localized_error_response(
                "Media job not found.",
                "未找到媒体任务。",
                404,
                "media_job_not_found",
            )
        return _public_job(job)

    @router.get("/api/media/batches/{batch_id}")
    async def api_media_batch(batch_id: str):
        batch = await asyncio.to_thread(manager.get_batch, batch_id)
        if not batch:
            return localized_error_response(
                "Media batch not found.",
                "未找到媒体批次。",
                404,
                "media_batch_not_found",
            )
        return _public_batch(batch)

    @router.post("/api/media/jobs/{job_id}/cancel")
    async def api_cancel_media_job(job_id: str):
        job = await asyncio.to_thread(manager.cancel_job, job_id)
        if not job:
            return localized_error_response(
                "Media job not found.",
                "未找到媒体任务。",
                404,
                "media_job_not_found",
            )
        if not str(job.get("reported_at") or ""):
            await MediaDelivery(manager).report(job)
            job = await asyncio.to_thread(manager.get_job, job_id) or job
        return _public_job(job)


__all__ = ["register_media_routes"]
