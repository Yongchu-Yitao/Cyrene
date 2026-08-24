from __future__ import annotations

import pytest

from cyrene.media.manager import MediaJobManager


def _batch(
    manager: MediaJobManager,
    *,
    requests: list[dict[str, str]] | None = None,
    idempotency_key: str = "",
) -> dict[str, object]:
    return manager.create_batch(
        chat_id="chat-media",
        project_id="project-media",
        requests=requests or [{"kind": "image", "prompt": "a paper crane"}],
        wake_note="continue after every result is visible",
        idempotency_key=idempotency_key,
    )


def test_media_batch_idempotency_and_expired_job_lease_recovery(tmp_path):
    manager = MediaJobManager(tmp_path / "media.sqlite3")

    created = _batch(manager, idempotency_key="tool-call-1")
    duplicate = _batch(manager, idempotency_key="tool-call-1")

    assert created["status"] == "queued"
    assert created["wake_status"] == "watching"
    assert duplicate == {**created, "status": "existing"}
    assert duplicate["wake_status"] == "watching"
    with pytest.raises(ValueError, match="idempotency"):
        _batch(
            manager,
            requests=[{"kind": "music", "prompt": "a different request"}],
            idempotency_key="tool-call-1",
        )
    assert len(manager.list_jobs(chat_id="chat-media")) == 1

    first = manager.claim_jobs("worker-a", limit=1, lease_seconds=30)[0]
    assert first["status"] == "claimed"
    assert first["attempts"] == 1
    assert manager.claim_jobs("worker-b", limit=1) == []
    assert manager.heartbeat(first["job_id"], "not-the-owner") is False
    assert manager.heartbeat(first["job_id"], first["lease_token"], lease_seconds=30) is True

    # Simulate a worker process disappearing after it acquired the durable row.
    with manager._connect() as conn:
        conn.execute(
            "UPDATE media_jobs SET lease_until=0 WHERE job_id=?",
            (first["job_id"],),
        )
        conn.commit()

    reclaimed = manager.claim_jobs("worker-b", limit=1, lease_seconds=30)[0]
    assert reclaimed["job_id"] == first["job_id"]
    assert reclaimed["attempts"] == 2
    assert reclaimed["lease_token"] != first["lease_token"]
    with pytest.raises(ValueError, match="lease is no longer owned"):
        manager.complete_job(first["job_id"], first["lease_token"], attachments=[])

    completed = manager.complete_job(
        reclaimed["job_id"],
        reclaimed["lease_token"],
        attachments=[{"id": "crane.png", "path": "/managed/crane.png"}],
    )
    assert completed["status"] == "succeeded"


def test_wake_requires_every_job_terminal_and_reported_and_duplicate_report_is_safe(
    tmp_path,
):
    manager = MediaJobManager(tmp_path / "media.sqlite3")
    batch = _batch(
        manager,
        requests=[
            {"kind": "image", "prompt": "a paper crane", "provider": "openai"},
            {"kind": "video", "prompt": "the crane takes flight", "provider": "google"},
        ],
    )
    claimed = {job["job_id"]: job for job in manager.claim_jobs("worker", limit=8, lease_seconds=30)}
    image_id, video_id = batch["job_ids"]

    manager.complete_job(
        image_id,
        claimed[image_id]["lease_token"],
        attachments=[{"id": "crane.png", "path": "/managed/crane.png"}],
    )
    manager.mark_reported(image_id)
    assert manager.get_batch(batch["batch_id"])["wake"]["status"] == "watching"
    assert manager.claim_wake("web") is None

    manager.fail_job(
        video_id,
        claimed[video_id]["lease_token"],
        "provider response contained private diagnostics",
        error_code="provider_timeout",
    )
    assert manager.get_batch(batch["batch_id"])["wake"]["status"] == "watching"
    assert manager.claim_wake("web") is None

    manager.mark_reported(video_id)
    wake = manager.claim_wake("web", lease_seconds=10)
    assert wake is not None
    assert wake["batch_id"] == batch["batch_id"]
    assert wake["summary"]["succeeded"] == 1
    assert wake["summary"]["failed"] == 1
    assert "already been added to the visible chat" in wake["prompt"]
    assert "provider_timeout" in wake["prompt"]
    assert "private diagnostics" not in wake["prompt"]

    # A daemon reconciliation pass can report a row again. It must not clear a
    # wake lease that has already been claimed by the Web process.
    manager.mark_reported(image_id)
    still_claimed = manager.get_batch(batch["batch_id"])["wake"]
    assert still_claimed["status"] == "claimed"
    assert still_claimed["lease_token"] == wake["lease_token"]

    settled = manager.settle_wake(wake["wake_id"], wake["lease_token"], "delivered")
    assert settled["status"] == "delivered"
    assert manager.claim_wake("another-web") is None


def test_expired_claim_at_attempt_limit_fails_instead_of_reclaiming_forever(tmp_path):
    manager = MediaJobManager(tmp_path / "media.sqlite3")
    batch = manager.create_batch(
        chat_id="chat-media",
        project_id="project-media",
        requests=[{"kind": "video", "prompt": "a crane flying over water"}],
        max_attempts=1,
    )
    claimed = manager.claim_jobs("worker-a", limit=1, lease_seconds=30)[0]
    manager.update_progress(
        claimed["job_id"],
        claimed["lease_token"],
        progress="remote task queued",
        provider_job_id="remote-video-task-1",
        provider_state={"status": "queued"},
    )

    with manager._connect() as conn:
        conn.execute(
            "UPDATE media_jobs SET lease_until=0 WHERE job_id=?",
            (claimed["job_id"],),
        )
        conn.commit()

    assert manager.claim_jobs("worker-b", limit=1, lease_seconds=30) == []
    failed = manager.get_job(claimed["job_id"])
    assert failed["status"] == "failed"
    assert failed["attempts"] == 1
    assert failed["provider_job_id"] == "remote-video-task-1"
    assert failed["completed_at"]
    assert failed["error_code"]
    assert failed["reported_at"] == ""
    assert [job["job_id"] for job in manager.pending_reports()] == batch["job_ids"]


def test_claimed_wake_heartbeat_requires_owner_and_extends_lease(tmp_path):
    manager = MediaJobManager(tmp_path / "media.sqlite3")
    batch = _batch(manager)
    job = manager.claim_jobs("worker", limit=1, lease_seconds=30)[0]
    manager.complete_job(
        job["job_id"],
        job["lease_token"],
        attachments=[{"id": "crane.png", "path": "/managed/crane.png"}],
    )
    manager.mark_reported(job["job_id"])
    wake = manager.claim_wake("web-a", lease_seconds=10)
    original_expiry = wake["lease_until"]

    assert (
        manager.heartbeat_wake(
            wake["wake_id"],
            "wrong-owner",
            lease_seconds=60,
        )
        is False
    )
    assert (
        manager.heartbeat_wake(
            wake["wake_id"],
            wake["lease_token"],
            lease_seconds=60,
        )
        is True
    )

    refreshed = manager.get_batch(batch["batch_id"])["wake"]
    assert refreshed["status"] == "claimed"
    assert refreshed["lease_token"] == wake["lease_token"]
    assert refreshed["lease_until"] > original_expiry
    assert manager.claim_wake("web-b", lease_seconds=10) is None
