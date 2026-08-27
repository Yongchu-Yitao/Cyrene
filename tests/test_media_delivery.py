from __future__ import annotations

import json
from typing import Any

import pytest

from agent.plugin.plugin_impl.cyrene_media.delivery import MediaDelivery


@pytest.mark.asyncio
async def test_reclaimed_worker_resumes_remote_job_with_original_model_and_delivers_attachment(
    tmp_path,
    monkeypatch,
):
    from agent.plugin.plugin_impl.cyrene_media.manager import MediaJobManager
    from agent.plugin.plugin_impl.cyrene_media.models import MediaArtifact, MediaProviderResult
    from agent.plugin.plugin_impl.cyrene_media import providers
    from agent.plugin.plugin_impl.cyrene_media import worker as worker_module

    manager = MediaJobManager(tmp_path / "media.sqlite3")
    batch = manager.create_batch(
        chat_id="chat-media",
        project_id="project-media",
        requests=[
            {
                "kind": "video",
                "prompt": "a crane crosses the lake",
                "provider": "seedance",
            }
        ],
        max_attempts=2,
    )
    first = manager.claim_jobs("worker-before-crash", limit=1, lease_seconds=30)[0]
    manager.assign_provider(
        first["job_id"],
        first["lease_token"],
        provider="seedance",
        model="seedance-original-model",
    )
    manager.update_progress(
        first["job_id"],
        first["lease_token"],
        progress="remote task queued",
        provider_job_id="seedance-remote-task-1",
        provider_state={"status": "queued", "percent": 20},
    )
    with manager._connect() as conn:
        conn.execute(
            "UPDATE media_jobs SET lease_until=0 WHERE job_id=?",
            (first["job_id"],),
        )
        conn.commit()
    reclaimed = manager.claim_jobs("worker-after-crash", limit=1, lease_seconds=30)[0]

    captured: dict[str, Any] = {}
    order: list[str] = []

    class Provider:
        async def generate(self, request, provider_settings, progress):
            captured["request"] = dict(request)
            captured["provider_settings"] = dict(provider_settings)
            order.append("provider_resumed")
            await progress(
                "remote task completed",
                "seedance-remote-task-1",
                {"status": "completed"},
            )
            return MediaProviderResult(
                artifacts=[
                    MediaArtifact(
                        filename="flight.mp4",
                        content_type="video/mp4",
                        data=b"video-bytes",
                    )
                ],
                provider_job_id="seedance-remote-task-1",
                metadata={"resumed": True},
            )

    def resolve_provider(
        requested: str,
        kind: str,
        _settings: dict[str, Any],
        _request: dict[str, Any] | None = None,
    ):
        assert requested == "seedance"
        assert kind == "video"
        return "seedance", Provider()

    monkeypatch.setattr(providers, "resolve_provider", resolve_provider)
    monkeypatch.setattr(
        worker_module,
        "get_media_settings",
        lambda: {
            "max_download_mb": 100,
            "poll_interval_seconds": 3,
            "providers": {
                "seedance": {
                    "enabled": True,
                    "video_model": "seedance-new-default",
                }
            },
        },
    )

    worker = worker_module.MediaWorker(manager, "worker-after-crash")

    async def register(artifact, **_kwargs: Any):
        assert artifact.data == b"video-bytes"
        order.append("attachment_registered")
        return {
            "id": "flight.mp4",
            "name": "flight.mp4",
            "content_type": "video/mp4",
            "size": len(artifact.data),
            "kind": "video",
            "url": "/api/chat/export/flight.mp4",
        }

    class Delivery:
        async def report(self, job: dict[str, Any]):
            assert job["status"] == "succeeded"
            assert job["attachments"][0]["id"] == "flight.mp4"
            assert manager.get_batch(batch["batch_id"])["wake"]["status"] == "watching"
            order.append("attachment_delivered")
            manager.mark_reported(job["job_id"])
            return True

    monkeypatch.setattr(worker, "_register_artifact", register)
    worker.delivery = Delivery()  # type: ignore[assignment]

    await worker._process(reclaimed)

    assert captured["request"]["_resume_provider_job_id"] == "seedance-remote-task-1"
    assert captured["request"]["_resume_provider_state"] == {
        "status": "queued",
        "percent": 20,
    }
    assert captured["request"]["model"] == "seedance-original-model"
    assert captured["provider_settings"]["video_model"] == "seedance-new-default"
    assert order == [
        "provider_resumed",
        "attachment_registered",
        "attachment_delivered",
    ]
    completed = manager.get_job(reclaimed["job_id"])
    assert completed["status"] == "succeeded"
    assert completed["provider_job_id"] == "seedance-remote-task-1"
    assert completed["reported_at"]
    assert manager.get_batch(batch["batch_id"])["wake"]["status"] == "ready"
