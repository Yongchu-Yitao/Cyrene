from __future__ import annotations

import json
from typing import Any

import pytest

from cyrene.media.delivery import MediaDelivery


@pytest.mark.asyncio
async def test_media_message_projects_attachments_idempotently_without_clearing_busy_chat(
    tmp_path,
    monkeypatch,
):
    from cyrene.agent import session as agent_session
    from cyrene.workbench import chat as chat_service
    from cyrene.workbench import chat_events

    chats_path = tmp_path / "workbench_chats.json"
    chats_path.write_text(
        json.dumps(
            {
                "chats": [
                    {
                        "id": "chat-media",
                        "projectId": "project-media",
                        "title": "Media test",
                        "status": "running",
                        "updatedAt": "2026-08-24T00:00:00+00:00",
                        "messages": [
                            {
                                "id": "user-1",
                                "role": "user",
                                "content": "make a video with music",
                                "createdAt": "2026-08-24T00:00:00+00:00",
                                "attachments": [
                                    {
                                        "id": "reference-image",
                                        "name": "reference.png",
                                        "content_type": "image/png",
                                        "size": 64,
                                        "kind": "image",
                                        "url": "/api/chat/upload/reference-image",
                                        "path": "/private/server/path/reference.png",
                                        "width": 320,
                                        "height": 180,
                                    },
                                    {
                                        "id": "reference-video",
                                        "name": "reference.mp4",
                                        "content_type": "video/mp4",
                                        "size": 256,
                                        "kind": "video",
                                        "url": "/api/chat/upload/reference-video",
                                        "path": "/private/server/path/reference.mp4",
                                    },
                                    {
                                        "id": "reference-mask",
                                        "name": "mask.png",
                                        "content_type": "image/png",
                                        "size": 48,
                                        "kind": "image",
                                        "url": "/api/chat/upload/reference-mask",
                                        "path": "/private/server/path/mask.png",
                                    },
                                ],
                            }
                        ],
                    },
                    {
                        "id": "chat-other",
                        "projectId": "project-media",
                        "title": "Other chat",
                        "status": "idle",
                        "updatedAt": "2026-08-24T00:00:00+00:00",
                        "messages": [
                            {
                                "id": "other-user",
                                "role": "user",
                                "content": "foreign reference",
                                "attachments": [
                                    {
                                        "id": "foreign-reference",
                                        "name": "foreign.png",
                                        "content_type": "image/png",
                                        "size": 32,
                                        "kind": "image",
                                        "url": "/api/chat/upload/foreign-reference",
                                    }
                                ],
                            }
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(chat_service, "_CHATS_STORE", chats_path)
    monkeypatch.setattr(chat_service, "_STORE_DB_PATH", "")
    monkeypatch.setattr(chat_service, "_CONFIGURED_CHATS_STORE", None)

    session_messages: list[dict[str, Any]] = []
    published: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def append_to_session(_chat_id: str, entry: dict[str, Any]) -> None:
        if not any(item.get("message_id") == entry.get("message_id") for item in session_messages):
            session_messages.append(dict(entry))

    async def publish(*args: Any, **kwargs: Any) -> None:
        published.append((args, kwargs))

    monkeypatch.setattr(agent_session, "append_message_to_session", append_to_session)
    monkeypatch.setattr(chat_events, "publish_chat_changed", publish)

    job = {
        "job_id": "media-job-1",
        "batch_id": "media-batch-1",
        "chat_id": "chat-media",
        "project_id": "project-media",
        "kind": "video",
        "provider": "google",
        "model": "gemini-omni-flash-preview",
        "status": "succeeded",
        "completed_at": "2026-08-24T00:01:00+00:00",
        "request": {
            "reference_attachment_ids": [
                "reference-image",
                "missing-from-this-chat",
                "foreign-reference",
                "reference-video",
                "reference-image",
            ],
            "mask_attachment_id": "reference-mask",
        },
    }
    attachments = [
        {
            "id": "generated.mp4",
            "name": "generated.mp4",
            "content_type": "video/mp4",
            "size": 1024,
            "kind": "video",
            "url": "/api/chat/export/generated.mp4",
            "path": "/private/server/path/generated.mp4",
        },
        {
            "id": "generated.mp3",
            "name": "generated.mp3",
            "content_type": "audio/mpeg",
            "size": 512,
            "kind": "audio",
            "url": "/api/chat/export/generated.mp3",
            "path": "/private/server/path/generated.mp3",
        },
    ]

    first = await chat_service.append_media_job_message(job, attachments)
    second = await chat_service.append_media_job_message(job, attachments)

    assert first == second
    assert first["id"] == "msg_media_media-job-1"
    assert first["attachments"] == [
        {
            "id": "generated.mp4",
            "name": "generated.mp4",
            "content_type": "video/mp4",
            "size": 1024,
            "kind": "video",
            "url": "/api/chat/export/generated.mp4",
        },
        {
            "id": "generated.mp3",
            "name": "generated.mp3",
            "content_type": "audio/mpeg",
            "size": 512,
            "kind": "audio",
            "url": "/api/chat/export/generated.mp3",
        },
    ]
    assert first["referenceAttachments"] == [
        {
            "id": "reference-image",
            "name": "reference.png",
            "content_type": "image/png",
            "size": 64,
            "kind": "image",
            "url": "/api/chat/upload/reference-image",
            "width": 320,
            "height": 180,
        },
        {
            "id": "reference-video",
            "name": "reference.mp4",
            "content_type": "video/mp4",
            "size": 256,
            "kind": "video",
            "url": "/api/chat/upload/reference-video",
        },
        {
            "id": "reference-mask",
            "name": "mask.png",
            "content_type": "image/png",
            "size": 48,
            "kind": "image",
            "url": "/api/chat/upload/reference-mask",
        },
    ]
    assert "missing-from-this-chat" not in json.dumps(first)
    assert "foreign-reference" not in json.dumps(first)
    assert "/private/server/path" not in json.dumps(first)

    stored = json.loads(chats_path.read_text(encoding="utf-8"))["chats"][0]
    assert stored["status"] == "running"
    projected = [message for message in stored["messages"] if message.get("mediaJob")]
    assert len(projected) == 1
    assert projected[0]["id"] == "msg_media_media-job-1"
    assert projected[0]["attachments"] == first["attachments"]
    assert projected[0]["referenceAttachments"] == first["referenceAttachments"]
    assert "/private/server/path" not in json.dumps(projected[0])
    assert len(session_messages) == 1
    assert session_messages[0]["message_id"] == "msg_media_media-job-1"
    assert session_messages[0]["attachments"] == first["attachments"]
    assert session_messages[0]["reference_attachments"] == first["referenceAttachments"]
    assert "/private/server/path" not in json.dumps(session_messages[0])
    assert len(published) == 2
    assert published[0][0][:3] == ("chat-media", "project-media", "media_attachment")
    assert published[0][1]["assistantMessages"][0]["attachments"] == first["attachments"]
    assert published[0][1]["assistantMessages"][0]["referenceAttachments"] == first["referenceAttachments"]


@pytest.mark.asyncio
async def test_media_delivery_marks_reported_only_after_visible_projection(monkeypatch):
    from cyrene.workbench import chat as chat_service

    order: list[str] = []

    class Manager:
        def mark_reported(self, job_id: str, *, delivery_error: str = "") -> None:
            assert job_id == "media-job-2"
            assert delivery_error == ""
            order.append("reported")

    async def append(job: dict[str, Any], attachments: list[dict[str, Any]]):
        assert job["job_id"] == "media-job-2"
        assert attachments == [{"id": "song.mp3"}]
        order.append("projected")
        return {"id": "msg_media_media-job-2"}

    monkeypatch.setattr(chat_service, "append_media_job_message", append)
    delivery = MediaDelivery(Manager())  # type: ignore[arg-type]

    delivered = await delivery.report(
        {
            "job_id": "media-job-2",
            "status": "succeeded",
            "attachments": [{"id": "song.mp3"}],
        }
    )

    assert delivered is True
    assert order == ["projected", "reported"]


@pytest.mark.asyncio
async def test_reclaimed_worker_resumes_remote_job_with_original_model_and_delivers_attachment(
    tmp_path,
    monkeypatch,
):
    from cyrene.media.manager import MediaJobManager
    from cyrene.media.models import MediaArtifact, MediaProviderResult
    from cyrene.media import providers
    from cyrene.media import worker as worker_module

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
