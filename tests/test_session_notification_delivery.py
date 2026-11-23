"""Durable notifications must not wait behind unrelated device I/O."""

import asyncio

import pytest

from cyrene.plugins.builtin.session_management.service import SessionManagementService
from test_session_management import Host


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_operation", ["refresh", "delivery"])
async def test_notice_arriving_during_remote_io_is_delivered_before_io_finishes(tmp_path, blocked_operation):
    host = Host(tmp_path)
    service = SessionManagementService(tmp_path / "plugin", host)
    host.create("a", "old-remote", "Old remote task", str(tmp_path))
    host.chats["old-remote"]["workDevice"] = {"device_id": "offline-peer"}
    service.store.register("old-remote", "a")
    host.create("a", "local-result", "Current task", str(tmp_path))
    service.store.register("local-result", "a")
    entered, cancelled, delivered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    async def blocked(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def notify(owner, target, event_id, payload):
        calls.append(event_id)
        await asyncio.sleep(0)  # Let competing explicit drains try the same notice.
        delivered.set()
        return True

    host.notify = notify
    if blocked_operation == "refresh":
        host.refresh_remote = blocked
    else:
        host.deliver = blocked
        service.store.enqueue("a", "old-remote", "input", {"content": "continue", "version": "0", "attachments": []})

    await service.start()
    try:
        await asyncio.wait_for(entered.wait(), 2)
        if blocked_operation == "delivery":
            assert service.lock.locked()
        # This is a new completion during the remote wait, not a startup notice.
        await asyncio.to_thread(service.store.notify, "completion", "a", "local-result",
                                {"owner_version": "0", "event": {"status": "completed"}})
        await asyncio.wait_for(delivered.wait(), 2)
        await asyncio.gather(service.notify_tasks(), service.notify_tasks())
        assert calls == ["completion"]
        assert not service.store.notifications()
        assert not cancelled.is_set()
        assert host.chats["old-remote"]["workDevice"]["device_id"] == "offline-peer"
    finally:
        await asyncio.wait_for(service.stop(), 2)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_notification_consumer_retries_and_replays_after_shutdown(tmp_path):
    host = Host(tmp_path)
    service = SessionManagementService(tmp_path / "plugin", host)
    first_attempt, retry, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    async def notify(owner, target, event_id, payload):
        calls.append(event_id)
        if len(calls) == 1:
            first_attempt.set()
            return False  # Owner is awaiting user input; receipt must stay pending.
        retry.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    host.notify = notify
    service.store.notify("completion", "a", "task", {"event": {"status": "completed"}})
    await service.start()
    try:
        await asyncio.wait_for(first_attempt.wait(), 2)
        await asyncio.wait_for(retry.wait(), 3)
        assert len(service.store.notifications()) == 1
    finally:
        await asyncio.wait_for(service.stop(), 2)
    assert cancelled.is_set()

    delivered = asyncio.Event()

    async def accept(owner, target, event_id, payload):
        assert event_id == "completion"
        delivered.set()
        return True

    host.notify = accept
    restarted = SessionManagementService(tmp_path / "plugin", host)
    await restarted.start()
    try:
        await asyncio.wait_for(delivered.wait(), 2)
        await restarted.notify_tasks()
        assert not restarted.store.notifications()
    finally:
        await restarted.stop()
