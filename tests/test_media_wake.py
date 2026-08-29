from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from cyrene.plugins.builtin.cyrene_media.wake import MediaWakeBridge


class _WakeManager:
    def __init__(self) -> None:
        self.claim_count = 0
        self.settled: list[tuple[str, str, str]] = []
        self.heartbeats: list[tuple[str, str, float]] = []

    def claim_wake(self, _consumer_id: str, *, lease_seconds: float) -> dict[str, Any] | None:
        self.claim_count += 1
        if self.claim_count != 1:
            return None
        return {
            "wake_id": "media-wake-1",
            "batch_id": "media-batch-1",
            "chat_id": "chat-1",
            "project_id": "project-1",
            "prompt": "attachments are visible",
            "note": "continue",
            "summary": {"succeeded": 1},
            "lease_token": "wake-lease-1",
        }

    def settle_wake(self, wake_id: str, lease_token: str, outcome: str) -> dict[str, Any]:
        self.settled.append((wake_id, lease_token, outcome))
        return {"status": outcome}

    def heartbeat_wake(
        self,
        wake_id: str,
        lease_token: str,
        *,
        lease_seconds: float,
    ) -> bool:
        self.heartbeats.append((wake_id, lease_token, lease_seconds))
        return True


async def _wait_until_settled(manager: _WakeManager) -> None:
    for _ in range(100):
        if manager.settled:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("media wake was not settled")


@pytest.mark.asyncio
async def test_media_wake_busy_chat_releases_lease_without_dispatching():
    manager = _WakeManager()
    bridge = MediaWakeBridge(manager)  # type: ignore[arg-type]
    dispatched: list[dict[str, Any]] = []

    async def dispatch(payload: dict[str, Any]):
        dispatched.append(payload)
        return "started"

    bridge.configure(dispatcher=dispatch, is_busy=lambda chat_id: chat_id == "chat-1")
    await bridge.start()
    try:
        await _wait_until_settled(manager)
    finally:
        await bridge.stop()

    assert dispatched == []
    assert manager.settled == [("media-wake-1", "wake-lease-1", "release")]


@pytest.mark.asyncio
async def test_media_wake_started_run_is_settled_as_delivered():
    manager = _WakeManager()
    bridge = MediaWakeBridge(manager)  # type: ignore[arg-type]
    dispatched: list[dict[str, Any]] = []

    async def dispatch(payload: dict[str, Any]):
        dispatched.append(payload)
        return "started"

    bridge.configure(dispatcher=dispatch, is_busy=lambda _chat_id: False)
    await bridge.start()
    try:
        await _wait_until_settled(manager)
    finally:
        await bridge.stop()

    assert dispatched == [
        {
            "wake_id": "media-wake-1",
            "batch_id": "media-batch-1",
            "chat_id": "chat-1",
            "project_id": "project-1",
            "prompt": "attachments are visible",
            "note": "continue",
            "summary": {"succeeded": 1},
            "source": "media_job",
        }
    ]
    assert manager.settled == [("media-wake-1", "wake-lease-1", "delivered")]


@pytest.mark.asyncio
async def test_long_media_wake_dispatch_renews_claim_until_dispatch_finishes(
    monkeypatch,
):
    from cyrene.plugins.builtin.cyrene_media import wake as wake_module

    manager = _WakeManager()
    bridge = MediaWakeBridge(manager)  # type: ignore[arg-type]
    heartbeat_seen = threading.Event()
    original_heartbeat = manager.heartbeat_wake
    real_sleep = asyncio.sleep

    def heartbeat(*args: Any, **kwargs: Any) -> bool:
        owned = original_heartbeat(*args, **kwargs)
        heartbeat_seen.set()
        return owned

    async def fast_sleep(_seconds: float) -> None:
        await real_sleep(0)

    async def dispatch(_payload: dict[str, Any]):
        observed = await asyncio.to_thread(heartbeat_seen.wait, 1.0)
        assert observed is True
        return "started"

    manager.heartbeat_wake = heartbeat  # type: ignore[method-assign]
    monkeypatch.setattr(wake_module.asyncio, "sleep", fast_sleep)
    bridge.configure(dispatcher=dispatch)

    outcome = await bridge._dispatch_with_heartbeat(
        {"wake_id": "media-wake-long", "chat_id": "chat-1"},
        wake_id="media-wake-long",
        lease_token="wake-lease-long",
    )

    assert outcome == "started"
    assert manager.heartbeats
    assert all(heartbeat == ("media-wake-long", "wake-lease-long", 45.0) for heartbeat in manager.heartbeats)
