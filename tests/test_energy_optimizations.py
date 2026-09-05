"""Event scheduling, structural sharing, and lightweight plan retrieval."""
import asyncio
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cyrene.plugins.builtin.cyrene_media.manager import MediaJobManager
from cyrene.plugins.builtin.cyrene_media.worker import MediaWorker
from cyrene.workbench.workspaces.workspace_changes import (
    WorkspaceFileState, WorkspaceSnapshot, capture_workspace_snapshot,
)


def test_workspace_index_shares_untouched_shards_and_preserves_baselines(tmp_path):
    root = tmp_path.resolve()
    (root / 'changed').write_text('new')
    state = WorkspaceFileState(0, 1, '', 'x')
    before = WorkspaceSnapshot(root, {f'f{i}': state for i in range(10000)}, '')
    after = capture_workspace_snapshot(root, previous=before, changed_paths={str(root / 'changed')})
    assert len(before.files) == 10000
    assert 'changed' not in before.files
    assert after.files['changed'].size == 3
    assert after.files.text_count == before.files.text_count  # initial fixture exceeds the text cap
    assert sum(a is b for a, b in zip(before.files._buckets, after.files._buckets)) == 255
    builder = after.files.edit()
    del builder['f1']
    frozen = builder.freeze()
    builder['f2'] = WorkspaceFileState(0, 5, '', 'hello')
    assert frozen['f2'] is state
    assert before.files['f1'] is state
    assert frozen.text_count == 9999


@pytest.mark.asyncio
async def test_idle_workers_do_not_poll_and_external_process_enqueue_wakes_them(tmp_path):
    manager = MediaJobManager(tmp_path / 'jobs.db')
    claims = 0
    original = manager.claim_jobs
    def claim(*args, **kwargs):
        nonlocal claims
        claims += 1
        return original(*args, **kwargs)
    manager.claim_jobs = claim
    stop = asyncio.Event()
    processed = asyncio.Event()
    class Worker(MediaWorker):
        async def _process(self, job):
            processed.set()
            stop.set()
    workers = [asyncio.create_task(Worker(manager, str(i)).run(stop)) for i in range(3)]
    try:
        await asyncio.sleep(0.3)  # allow initial schema and native watch notifications to settle
        initial = claims
        await asyncio.sleep(1.0)
        assert claims == initial
        script = """import sys
from cyrene.plugins.builtin.cyrene_media.manager import MediaJobManager
MediaJobManager(sys.argv[1]).create_batch(chat_id='c', project_id='p', requests=[{'kind':'image','prompt':'test'}])
"""
        await asyncio.to_thread(subprocess.run, [sys.executable, '-c', script, str(manager.db_path)], check=True)
        await asyncio.wait_for(processed.wait(), 2)
    finally:
        stop.set()
        await asyncio.gather(*workers)
    assert not manager.changes._listeners
    assert manager.changes._task is None


@pytest.mark.asyncio
async def test_job_deadline_wakes_without_any_new_write(tmp_path):
    manager = MediaJobManager(tmp_path / 'jobs.db')
    manager.create_batch(chat_id='c', project_id='p', requests=[{'kind':'image','prompt':'test'}])
    due = time.time() + 0.5
    with manager._connect() as conn:
        conn.execute('UPDATE media_jobs SET available_at=?', (due,))
    stop = asyncio.Event()
    processed = []
    class Worker(MediaWorker):
        async def _process(self, job):
            processed.append(time.time())
            stop.set()
    await asyncio.wait_for(Worker(manager, 'worker').run(stop), 3)
    assert due <= processed[0] < due + 1


def test_abandoned_leases_and_delivery_failures_have_deadlines(tmp_path):
    manager = MediaJobManager(tmp_path / 'jobs.db')
    batch = manager.create_batch(chat_id='c', project_id='p', requests=[{'kind':'image','prompt':'test'}])
    job = manager.claim_jobs('old', lease_seconds=30)[0]
    assert 28 < manager.next_job_delay() <= 31
    manager.complete_job(job['job_id'], job['lease_token'], attachments=[])
    assert manager.next_job_delay() == 0.8
    manager.mark_reported(job['job_id'])
    assert manager.next_job_delay() is None
    assert manager.next_wake_delay() <= 0.01
    wake = manager.claim_wake('old', lease_seconds=45)
    assert 43 < manager.next_wake_delay() <= 46
    assert wake['batch_id'] == batch['batch_id']


@pytest.mark.asyncio
async def test_plan_endpoint_never_reads_transcript(tmp_path):
    from fastapi import APIRouter
    from cyrene.workbench.http.workbench.chat_routes.detail_routes import _register_get_route
    service = MagicMock()
    chat = {'id':'c', 'projectId':'p', 'activePlan':{'steps':[{'title':'keep'}]}}
    service.repository.get_metadata.return_value = chat
    service.repository.get.side_effect = AssertionError('plan must not hydrate transcript')
    runtime = MagicMock()
    runtime.find_project_lightweight.return_value = None
    router = APIRouter()
    _register_get_route(router, SimpleNamespace(service=service, runtime=lambda:runtime))
    endpoint = next(route.endpoint for route in router.routes if route.path.endswith('/plan'))
    result = await endpoint('c')
    assert result == {'plan':chat['activePlan']}
    service.repository.get.assert_not_called()
    service.repository.get_metadata.assert_called_once_with('c')


@pytest.mark.asyncio
async def test_media_pool_reacts_to_settings_and_worker_exit_without_periodic_reads(tmp_path, monkeypatch):
    from cyrene.plugins.builtin.cyrene_media import daemon as module
    monkeypatch.setattr(module, 'DATA_DIR', tmp_path)
    settings = {'max_parallel_jobs': 2}
    reads = 0
    def read_settings():
        nonlocal reads
        reads += 1
        return settings
    monkeypatch.setattr(module, 'get_media_settings', read_settings)
    daemon = module.MediaDaemon(MediaJobManager(tmp_path / 'jobs.db'))
    async def until(predicate):
        async with asyncio.timeout(2):
            while not predicate():
                await asyncio.sleep(0.01)
    await daemon.start()
    try:
        await asyncio.sleep(0.3)
        initial = reads
        await asyncio.sleep(0.2)
        assert reads == initial
        settings['max_parallel_jobs'] = 1
        (tmp_path / 'config.enc').write_bytes(b'configuration-changed')
        await until(lambda: len(daemon._workers) == 1)
        old = next(iter(daemon._tasks.values()))
        old.cancel()
        await until(lambda: len(daemon._tasks) == 1 and next(iter(daemon._tasks.values())) is not old)
    finally:
        await daemon.stop()
