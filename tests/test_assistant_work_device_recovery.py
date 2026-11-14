"""Cross-boundary regression coverage for remote task recovery and cache ownership."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_assistant_work_device import host_at
from cyrene.core.plugin import PluginContext, PluginRegistry
from cyrene.core.plugin.runtime import PluginRuntime
from cyrene.plugins.builtin.session_management import remote_host
from cyrene.plugins.builtin.session_management.service import SessionManagementService
from cyrene.plugins.builtin.cyrene_remote import task_execution as transport


def managed(tmp_path):
    host, repo = host_at(tmp_path)
    service = SessionManagementService(tmp_path / 'service', host)
    host.create('assistant', 'task', 'Task', '/remote/work')
    service.store.register('task', 'assistant')
    return host, repo, service


def final_snapshot(status='completed'):
    return {'ok': True, 'chat_id': 'remote', 'status': status,
        'chat': {'messages': [{'id': 'u', 'role': 'user', 'clientRequestId': 'input', 'content': 'go'},
                              {'id': 'a', 'role': 'assistant', 'content': 'done'}]},
        'checkpoint': {'status': status, 'node_id': 'final', 'run_id': 'run'},
        'result': {'status': status, 'content': 'done', 'revision': 'r', 'final_reply_node_id': 'a'},
        'blocks': {'blocks': []},
        'notification': {'status': status, 'node_id': 'final', 'run_id': 'run', 'final_reply': 'done'}}


def stub_model(monkeypatch, service):
    monkeypatch.setattr(transport, 'application_plugin_service', lambda _: service)
    monkeypatch.setattr(transport, 'application_plugin_scope', lambda: SimpleNamespace(registry=PluginRegistry(), active_services={}))
    model = AsyncMock(return_value=SimpleNamespace(success=True, value={'text': 'local model'}))
    monkeypatch.setattr(PluginRuntime, 'call', model)
    return model


@pytest.mark.asyncio
async def test_lost_first_delivery_receipt_keeps_monitoring_and_model_access(tmp_path, monkeypatch):
    host, repo, service = managed(tmp_path)
    service.loop, service.lock = asyncio.get_running_loop(), asyncio.Lock()
    service.notify_tasks = AsyncMock()
    monkeypatch.setattr('cyrene.plugins.model_catalog.configured_context_limit', lambda _: 32768)
    monkeypatch.setattr(remote_host.asyncio, 'sleep', AsyncMock())
    async def lost(*args, **kwargs):
        assert service.store.notifications() == []  # No early question during the retry budget.
        raise TimeoutError('reply lost')
    request = AsyncMock(side_effect=lost)
    monkeypatch.setattr(remote_host, 'task_request', lambda: request)
    service.store.enqueue('assistant', 'task', 'input', {'version': host.version(repo.get('task')), 'content': 'go', 'attachments': []})
    await service._deliver_message(service.store.pending()[0])
    assert request.await_count == 4
    assert service.store.session('task')['paused']
    assert host.status('task') == 'dispatching'
    assert (await service.list('assistant'))['sessions'][0]['status'] == 'unknown'
    assert len(service.store.notifications()) == 1
    model = stub_model(monkeypatch, service)
    assert (await transport.controller_model('peer', {'task_id': 'task', 'arguments': {'messages': []}}))['response']['text'] == 'local model'
    assert model.call_args.args[2].data['session_id'] == 'assistant'
    snapshot = final_snapshot()
    snapshot['admitted_message_ids'] = ['input']
    request.side_effect = [snapshot, {'ok': True, 'released': True}]
    monkeypatch.setattr('cyrene.workbench.chat.chat_events.publish_chat_changed', AsyncMock())
    await host.refresh_remote(service)
    assert service.store.message('input')['status'] == 'delivered'
    assert host.cached('task')['remote_released']
    assert host.latest_result('task', 'final')['content'] == 'done'
    assert request.call_args_list[-1].args[1] == 'tasks.release'


@pytest.mark.asyncio
async def test_completed_snapshot_replays_notification_after_publication_failure(tmp_path, monkeypatch):
    host, repo, service = managed(tmp_path)
    service.store.save_remote_snapshot('task', {'status': 'running'})
    host.remote_call = AsyncMock(side_effect=[final_snapshot(), {'ok': True, 'released': True}])
    monkeypatch.setattr('cyrene.workbench.chat.chat_events.publish_chat_changed', AsyncMock(side_effect=RuntimeError('publication interrupted')))
    await host.refresh_remote(service)
    assert host.status('task') == 'completed'
    await host.refresh_remote(service)
    import json
    events = [json.loads(row['payload'])['event'] for row in service.store.notifications()]
    assert sum(event['status'] == 'completed' for event in events) == 1
    assert host.cached('task')['remote_released']
    assert host.remote_call.call_args_list[-1].args[1] == 'tasks.release'


def test_incomplete_pinned_result_is_repaired_but_terminal_evidence_is_immutable(tmp_path):
    host, _, service = managed(tmp_path)
    early = final_snapshot()
    early['status'] = 'running'
    early['result'] = {'status': 'running', 'content': ''}
    service.store.save_remote_snapshot('task', early)
    service.store.save_remote_snapshot('task', final_snapshot())
    assert host.latest_result('task', 'final')['content'] == 'done'
    late = final_snapshot()
    late['result']['content'] = 'different turn'
    service.store.save_remote_snapshot('task', late)
    assert host.latest_result('task', 'final')['content'] == 'done'


@pytest.mark.asyncio
@pytest.mark.parametrize('failures', [1, 2, 3, 4])
async def test_model_callback_retries_then_marks_exhaustion_only(tmp_path, monkeypatch, failures):
    from cyrene.core.plugin.plugin import PluginExecutionError
    host, repo, service = managed(tmp_path)
    chat = repo.get('task')
    chat.update(remoteControllerDeviceId='controller', remoteControllerTaskId='parent-task')
    request = AsyncMock(side_effect=[TimeoutError('lost')] * failures + [{'ok': True, 'response': {'text': 'ok'}}])
    monkeypatch.setattr(transport, 'request', request)
    monkeypatch.setattr(transport.asyncio, 'sleep', AsyncMock())
    delegate = transport.model_delegate(chat, asyncio.get_running_loop(), repo)
    if failures == 4:
        with pytest.raises(PluginExecutionError) as failed:
            await delegate({'messages': []}, PluginContext())
        assert failed.value.failure.error_code == 'work_device_connection'
        assert failed.value.failure.retryable is False
        assert '3' in repo.get('task')['remoteModelConnectionError']
        assert request.await_count == 4
    else:
        assert (await delegate({'messages': []}, PluginContext()))['text'] == 'ok'
        assert not repo.get('task').get('remoteModelConnectionError')
        assert request.await_count == failures + 1
    assert len({call.kwargs['key'] for call in request.call_args_list}) == 1


@pytest.mark.asyncio
async def test_reverse_model_failure_asks_main_once_even_when_forward_link_works(tmp_path, monkeypatch):
    host, _, service = managed(tmp_path)
    service.store.save_remote_snapshot('task', {'status': 'running'})
    failed = final_snapshot('failed')
    failed['model_connection_error'] = 'model callback exhausted three retries'
    host.remote_call = AsyncMock(side_effect=[failed, {'ok': True, 'released': True}])
    monkeypatch.setattr('cyrene.workbench.chat.chat_events.publish_chat_changed', AsyncMock())
    await host.refresh_remote(service)
    await host.refresh_remote(service)
    import json
    events = [json.loads(row['payload'])['event'] for row in service.store.notifications()]
    assert len(events) == 1
    assert events[0]['phase'] == 'work_device_connection'
    assert events[0]['recovery_action'] == 'ask_user'
    assert events[0]['connection_retries'] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['tasks.stop', 'tasks.answer'])
async def test_pending_idempotent_request_is_retried_without_changing_key(tmp_path, monkeypatch, command):
    host, _, _ = managed(tmp_path)
    gate = SimpleNamespace(request=AsyncMock(side_effect=[TimeoutError(),
        {'ok': False, 'code': 'remote_command_in_progress', 'retryable': True},
        {'ok': False, 'code': 'remote_command_in_progress', 'retryable': True}, {'ok': True}]))
    monkeypatch.setattr(transport, 'gateway', lambda: gate)
    monkeypatch.setattr(remote_host, 'task_request', lambda: transport.request)
    monkeypatch.setattr(transport.asyncio, 'sleep', AsyncMock())
    assert (await host.remote_call('task', command, key='same-command'))['ok']
    assert gate.request.await_count == 4
    assert all(call.kwargs['idempotency_key'] == 'same-command' for call in gate.request.call_args_list)


def test_remote_workspace_never_authorizes_local_file_and_switch_does_not_move_old_task(tmp_path):
    host, repo, service = managed(tmp_path)
    root = tmp_path / 'same-path'
    root.mkdir()
    private = root / 'local-only.txt'
    private.write_text('local-only')
    repo.chats['task']['workspaceOverride'] = str(root)
    assert service.export_path('assistant', private) is None
    repo.chats['assistant']['workDevice'] = None
    assert host.create('assistant', 'task', 'replayed', '/remote/work')['workDevice']['device_id'] == 'peer'


@pytest.mark.asyncio
async def test_failed_stop_does_not_revoke_model_but_confirmed_stop_does(tmp_path, monkeypatch):
    host, repo, service = managed(tmp_path)
    service.store.save_remote_snapshot('task', {'status': 'running'})
    service.store.pause('task')
    stub_model(monkeypatch, service)
    host.remote_call = AsyncMock(side_effect=transport.WorkDeviceUnavailable('four attempts failed'))
    with pytest.raises(ConnectionError):
        await host.stop('task')
    assert (await transport.controller_model('peer', {'task_id': 'task', 'arguments': {'messages': []}}))['ok']
    host.remote_call = AsyncMock(return_value={'ok': True, 'stopped': True})
    host.refresh_task = AsyncMock()
    await host.stop('task')
    with pytest.raises(ValueError, match='stopped'):
        await transport.controller_model('peer', {'task_id': 'task', 'arguments': {'messages': []}})


@pytest.mark.asyncio
async def test_real_gateway_does_not_cache_retryable_or_not_yet_admitted_results(tmp_path, monkeypatch):
    from cyrene.plugins.builtin.cyrene_remote.control import RemoteControlStore, RemoteGateway, InMemoryRemoteRelay
    monkeypatch.setenv('CYRENE_REMOTE_KEYRING', '0')
    a, b = [RemoteControlStore(str(tmp_path / name)) for name in ('a.db', 'b.db')]
    for store in (a, b):
        store.update_settings(enabled=True, relay_url='', device_name='test')
    invitation = b.create_pairing_invitation(project_scopes=['project'])
    accepted = a.accept_pairing_invitation(invitation['invitation'])
    b.complete_pairing_response(accepted['response'])
    handler = AsyncMock(side_effect=[{'ok': False, 'retryable': True}, {'ok': True, 'accepted': False}, {'ok': True, 'accepted': True}])
    relay = InMemoryRemoteRelay()
    controller, worker = RemoteGateway(a, relay, AsyncMock()), RemoteGateway(b, relay, handler)
    await controller.start()
    await worker.start()
    try:
        replies = [await controller.request(b.identity.device_id, command='tasks.deliver', project_id='project',
                    payload={'task_id': 'task'}, idempotency_key='input') for _ in range(4)]
        assert replies[-1]['accepted']
        assert replies[-1]['duplicate']
        assert handler.await_count == 3
    finally:
        await controller.stop()
        await worker.stop()


@pytest.mark.asyncio
async def test_cleanup_uses_existing_deletion_and_keeps_workspace_files(tmp_path, monkeypatch):
    from cyrene.workbench.persistence import store
    from cyrene.workbench.chat.chat_repository import ChatRepository
    from cyrene.plugins.builtin.session_management.host import WorkbenchHost
    from cyrene.core.context import ContextStoreRouter, TreeNotFoundError
    from cyrene.workbench.core_adapter.chat_runtime import workbench_agent_data_directory
    from cyrene.platform import attachments
    import cyrene.core.plugin
    db = str(tmp_path / 'workbench.db')
    workspace = tmp_path / 'work'
    workspace.mkdir()
    output = workspace / 'result.txt'
    output.write_text('keep working file')
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    upload = uploads / 'input.txt'
    upload.write_text('transferred input')
    monkeypatch.setattr(attachments, 'UPLOADS_DIR', uploads)
    exports = tmp_path / 'exports'
    exports.mkdir()
    copied_output = exports / 'result-copy.txt'
    copied_output.write_text('cached result copy')
    monkeypatch.setattr(attachments, 'EXPORTS_DIR', exports)
    monkeypatch.setattr(cyrene.core.plugin, 'application_plugin_service', lambda _: None)
    chat = {'id': 'remote', 'kind': 'assistant-task', 'projectId': 'project', 'status': 'completed',
            'title': 'Task', 'workspaceOverride': str(workspace), 'createdAt': 'now',
            'messages': [{'id': 'u', 'role': 'user', 'clientRequestId': 'input', 'content': 'go',
                          'attachments': [{'path': str(upload)}]},
                         {'id': 'a', 'role': 'assistant', 'content': 'done',
                          'attachments': [{'path': str(copied_output)}, {'path': str(output)}]}]}
    store.write_chat_bundle(db, {'chats': [chat]}, lambda: {'chats': []})
    directory = workbench_agent_data_directory(db) / 'context'
    with ContextStoreRouter(directory) as router:
        router.create_tree({'role': 'system', 'content': 'remote execution cache'}, tree_id='remote')
    port = SimpleNamespace(service=SimpleNamespace(repository=ChatRepository(db)),
        run_manager=SimpleNamespace(get=lambda _: None, terminate=AsyncMock(),
            conversation_runtime=SimpleNamespace(delete_context=lambda _: None,
                context_checkpoint=lambda _: {'status': 'completed', 'node_id': 'final'})))
    host = WorkbenchHost(lambda: port)
    # New input must block cleanup of an earlier acknowledged result.
    stale = await transport.release_cache(port, host, 'remote', {'node_id': 'final', 'last_input_id': 'old'})
    assert stale['retryable']
    assert host.chat('remote')
    port.run_manager.terminate.assert_not_awaited()
    assert (await transport.release_cache(port, host, 'remote', {'node_id': 'final', 'last_input_id': 'input'}))['released']
    assert host.chat('remote') is None
    assert not upload.exists()
    assert not copied_output.exists()
    assert output.read_text() == 'keep working file'
    with ContextStoreRouter(directory) as router:
        with pytest.raises(TreeNotFoundError):
            router.get_tree('remote')


@pytest.mark.asyncio
async def test_snapshot_waits_for_run_finalization_and_reads_pinned_reply(tmp_path, monkeypatch):
    from cyrene.plugins.builtin.session_management.host import WorkbenchHost
    host, repo = host_at(tmp_path)
    target = transport.remote_target('controller', 'task')
    repo.chats[target] = {'id': target, 'projectId': 'project', 'remoteControllerDeviceId': 'controller',
                         'assistantOwnerSessionId': 'controller:controller', 'messages': []}
    run = SimpleNamespace(status='finishing', done=asyncio.Event())
    port = host.port()
    port.run_manager.get = lambda _: run
    port.service.public_chat_full = lambda chat: chat
    port.run_manager.conversation_runtime.context_checkpoint = lambda _: {'status': 'completed', 'node_id': 'final'}
    monkeypatch.setattr(WorkbenchHost, 'latest_result', lambda self, target, node=None: {'status': 'completed', 'content': 'done', 'node_id': node})
    monkeypatch.setattr(WorkbenchHost, 'blocks', lambda *a: {})
    monkeypatch.setattr(WorkbenchHost, 'notification', lambda *a: {'status': 'completed', 'node_id': 'final'})
    executor = SimpleNamespace(chat=port)
    early = await transport.execute(executor, 'controller', 'tasks.snapshot', {'task_id': 'task'}, 'project')
    assert early['status'] == 'running'
    assert early['result']['node_id'] == 'final'
    run.status = 'done'
    run.done.set()
    final = await transport.execute(executor, 'controller', 'tasks.snapshot', {'task_id': 'task'}, 'project')
    assert final['status'] == 'completed'


@pytest.mark.asyncio
async def test_explicit_retry_clears_old_model_outage_before_first_new_attempt(tmp_path, monkeypatch):
    host, repo, service = managed(tmp_path)
    service.store.save_remote_snapshot('task', {**final_snapshot('failed'),
        'model_connection_error': 'old outage', 'remote_released': True})
    service.work_device_unavailable('assistant', 'task', 'old outage')
    service.store.acknowledge(service.store.notifications()[0]['id'])
    monkeypatch.setattr('cyrene.plugins.model_catalog.configured_context_limit', lambda _: 32768)
    async def deliver(*args, **kwargs):
        assert not host.cached('task').get('model_connection_error')
        await host.capture_cached_notification(service, service.store.session('task'))
        assert not service.store.notifications()
        return {'ok': True, 'accepted': True}
    host.remote_call = AsyncMock(side_effect=deliver)
    await host.deliver('assistant', 'task', 'new-input', {'version': host.version(repo.get('task')), 'content': 'retry', 'attachments': []})
    assert not host.cached('task', 'connection_notice')['active']


def test_remote_model_routing_does_not_depend_on_local_assistant_plugin(tmp_path, monkeypatch):
    from cyrene.workbench.core_adapter import conversation_runtime as runtime_module
    from cyrene.workbench.chat.chat_repository import ChatRepository
    monkeypatch.setattr(runtime_module, 'application_plugin_scope', lambda: None)
    monkeypatch.setattr(runtime_module, 'resolve_plugin_registry', lambda _: (PluginRegistry(), True))
    monkeypatch.setattr(ChatRepository, 'get', lambda *_: {'id': 'remote', 'kind': 'assistant-task',
        'remoteControllerDeviceId': 'controller', 'remoteControllerTaskId': 'task', 'remoteModelContextLimit': 64000})
    opened = {}
    def open_bridge(*args, **kwargs):
        opened.update(kwargs)
        return object()
    monkeypatch.setattr(runtime_module.WorkbenchSessionBridge, 'open', open_bridge)
    config = runtime_module.ConversationConfig(session_id='remote', workspace_dir=str(tmp_path),
        db_path=str(tmp_path / 'workbench.db'), plugin_directory=tmp_path / 'plugins', data_directory=tmp_path / 'agent')
    loop = asyncio.new_event_loop()
    try:
        runtime_module.ConversationRuntime()._open_bridge(config, owner_loop=loop, raw_publisher=None)
    finally:
        loop.close()
    assert opened['plugin_services']['model_delegate']
    assert opened['plugin_services']['model_context_limit']() == 64000


@pytest.mark.asyncio
async def test_old_snapshot_and_cleanup_receipt_cannot_overwrite_new_dispatch(tmp_path, monkeypatch):
    host, repo, service = managed(tmp_path)
    service.store.save_remote_snapshot('task', {'status': 'running'})
    newer = {'status': 'dispatching', 'checkpoint': {}, 'remote_released': False}
    async def snapshot(*args, **kwargs):
        service.store.save_remote_snapshot('task', newer)
        return final_snapshot()
    host.remote_call = AsyncMock(side_effect=snapshot)
    monkeypatch.setattr('cyrene.workbench.chat.chat_events.publish_chat_changed', AsyncMock())
    await host.refresh_task('task')
    assert host.cached('task') == newer
    service.store.save_remote_snapshot('task', final_snapshot())
    async def release(*args, **kwargs):
        service.store.save_remote_snapshot('task', newer)
        return {'ok': True, 'released': True}
    host.remote_call = AsyncMock(side_effect=release)
    await host.refresh_remote(service)
    assert host.cached('task') == newer
