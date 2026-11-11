"""Idle scheduling must preserve durable delivery, leases and run boundaries."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from cyrene.platform.database_notifications import database_changes
from cyrene.plugins.builtin.cyrene_sessions.store import MessageStore


@pytest.mark.asyncio
async def test_commit_notifications_include_other_instances_and_threads_but_not_rollbacks(tmp_path):
    path = tmp_path / 'messages.db'
    store = MessageStore(path)
    with database_changes(path) as changed:
        other = MessageStore(path)
        await asyncio.to_thread(other.accept, 'a', 'b', 'hello', 'A', 'effect', 'v1')
        await asyncio.wait_for(changed.wait(), .5)
        changed.clear()
        with pytest.raises(ValueError), store.connect() as db:
            db.execute("UPDATE messages SET status='failed'")
            raise ValueError('rollback')
        await asyncio.sleep(.02)
        assert not changed.is_set()
        assert store.has_pending()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['messages', 'managed', 'cards'])
async def test_idle_queue_sleeps_until_commit_and_keeps_future_retries(tmp_path, kind):
    if kind == 'messages':
        from cyrene.plugins.builtin.cyrene_sessions.service import SessionMessagingService
        service = SessionMessagingService(tmp_path, lambda: None)
        store = service.store
        def insert():
            return store.accept('a', 'b', 'hello', 'A', 'effect', 'v1')
        table, status = 'messages', "status='queued'"
    elif kind == 'managed':
        from cyrene.plugins.builtin.session_management.service import SessionManagementService
        service = SessionManagementService(tmp_path, None)
        store = service.store
        store.register('b', 'a')
        def insert():
            return store.enqueue('a', 'b', 'm1', {})
        table, status = 'messages', "status='queued'"
    else:
        from cyrene.plugins.builtin.cyrene_cards.delivery import CardDelivery
        from cyrene.plugins.builtin.cyrene_cards.store import Store
        store = Store(tmp_path)
        service = CardDelivery(SimpleNamespace(store=store), lambda: None)
        def insert():
            with store.connect(write=True) as db:
                db.execute("INSERT INTO deliveries VALUES ('a', 'e', 'v', 'pending', '{}')")
        table, status = 'deliveries', "state='pending'"
    # Exercise the real scheduling/SQL while isolating external message dispatch.
    service.tick = AsyncMock()
    await service.start()
    try:
        await asyncio.sleep(.1)
        calls = service.tick.await_count
        await asyncio.sleep(1.1)
        assert service.tick.await_count == calls == 1
        await asyncio.to_thread(insert)
        for _ in range(50):
            if service.tick.await_count > calls:
                break
            await asyncio.sleep(.01)
        assert service.tick.await_count == calls + 1
        if kind != 'cards':
            with store.connect() as db:
                db.execute(f'UPDATE {table} SET next_attempt=99999999999 WHERE {status}')
            assert store.has_pending()  # Scheduled retry must never sleep indefinitely.
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_workspace_watchers_expire_only_after_last_run_and_reopen_fresh(tmp_path):
    from cyrene.workbench.chat.chat_application import WorkspaceChangeService
    root = tmp_path / 'workspace'
    root.mkdir()
    path = root / 'a.txt'
    path.write_text('before')
    service = WorkspaceChangeService(str(tmp_path / 'app.db'), Mock())
    service._idle_seconds = .04
    service.sync_generated_files = Mock()
    key = str(root.resolve())
    try:
        service.prewarm(root)
        before = await service.capture(root, 'one')
        second = await service.capture(root, 'two')
        index = service._indexes[key]
        await asyncio.sleep(.1)
        assert service._indexes[key] is index  # Active pins outlive the idle TTL.
        path.write_text('after')
        # Wait for the native watcher rather than relying on OS event latency.
        async with asyncio.timeout(2):
            while (await index.snapshot(settle=False)).files['a.txt'].text != 'after':
                await asyncio.sleep(.01)
        result = await service.finalize(chat_id='c', run_id='one', workspace_dir=root, before=before, status='completed')
        assert result['fileCount'] == 1
        assert result['attribution'] == 'overlapping'
        await asyncio.sleep(.1)
        assert key in service._indexes  # Another run still owns its baseline.
        await service.finalize(chat_id='c', run_id='two', workspace_dir=root, before=second, status='completed')
        await asyncio.sleep(.1)
        assert key not in service._indexes
        path.write_text('changed while unwatched')
        resumed = await service.capture(root, 'three')
        assert resumed.snapshot.files['a.txt'].text == 'changed while unwatched'
        assert service._indexes[key] is not index
    finally:
        await service.shutdown()
    assert not service._idle_indexes


@pytest.mark.asyncio
async def test_workspace_capture_cancellation_releases_pin(tmp_path, monkeypatch):
    from cyrene.workbench.chat.chat_application import WorkspaceChangeService
    service = WorkspaceChangeService('', Mock())
    service._idle_seconds = .01
    index = SimpleNamespace(snapshot=AsyncMock(side_effect=asyncio.CancelledError), close=AsyncMock())
    service._indexes[str(tmp_path.resolve())] = index
    with pytest.raises(asyncio.CancelledError):
        await service.capture(tmp_path)
    await asyncio.sleep(.05)
    assert not service._index_users
    index.close.assert_awaited_once()
    await service.shutdown()


@pytest.mark.asyncio
async def test_remote_cleanup_sleeps_without_sessions_and_wakes_on_admission(tmp_path):
    from cyrene.plugins.builtin.cyrene_remote_desktop.service import RemoteDesktopService
    service = RemoteDesktopService(str(tmp_path / 'remote.db'), tmp_path / 'data', remote_service=None)
    service._consume_forced_disconnects = AsyncMock()
    await service.start()
    try:
        await asyncio.sleep(.9)
        service._consume_forced_disconnects.assert_not_awaited()
        service._host_sessions['one'] = {'pending': True}
        service._host_sessions_changed.set()
        await asyncio.sleep(.05)
        service._consume_forced_disconnects.assert_awaited_once()
        service._host_sessions.clear()
    finally:
        await service.stop()
