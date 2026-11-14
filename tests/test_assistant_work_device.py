"""Work-device selection, pinned execution, local results and model routing."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cyrene.plugins.builtin.cyrene_remote import task_execution as transport
from cyrene.plugins.builtin.session_management.remote_host import RemoteAwareHost, select_workspace
from cyrene.plugins.builtin.session_management.store import Store


@pytest.mark.parametrize('value,allowed', [('0.9.2.0', True), ('0.9.2.1', True), ('0.10.0', True),
    ('0.9.1.9', False), ('0.9.2.0rc1', False), ('', False), (None, False), ('unknown', False)])
def test_minimum_version(value, allowed):
    assert transport.compatible_version(value) is allowed


def device(identity='peer', root='/remote/work'):
    return {'device_id': identity, 'display_name': identity,
            'projects': [{'id': 'project', 'name': 'Work', 'workspace_path': root}]}


def test_workspace_paths_are_interpreted_on_the_execution_device():
    assert select_workspace(device(), '/remote/work/sub')['project_id'] == 'project'
    assert select_workspace(device(root=r'C:\Work'), r'C:\Work\sub')['workspace_path'] == r'C:\Work\sub'
    for path in ['/remote/other', '/remote/work-other', '/local/input', '/remote/work/../other']:
        with pytest.raises(ValueError):
            select_workspace(device(), path)


class Repository:
    def __init__(self):
        self.chats = {'assistant': {'id': 'assistant', 'kind': 'personal-assistant', 'projectId': 'assistant-scope',
                                   'model': 'controller-model', 'workDevice': device(), 'createdAt': 'now'}}
    def get(self, key):
        return deepcopy(self.chats.get(key))
    def insert(self, chat):
        self.chats[chat['id']] = deepcopy(chat)
        return deepcopy(chat)
    def mutate_one(self, key, fn):
        if key not in self.chats:
            return None
        fn(self.chats[key])
        return self.get(key)


def host_at(tmp_path):
    repo = Repository()
    service = SimpleNamespace(repository=repo, utc_now_iso=lambda: '2026-11-14T00:00:00Z',
        create_chat=lambda project, title, model='', **kw: {'projectId': project, 'title': title, 'model': model,
                                                           'messages': [], 'createdAt': 'now'})
    port = SimpleNamespace(service=service, run_manager=SimpleNamespace(get=lambda target: None,
        conversation_runtime=SimpleNamespace(context_checkpoint=lambda target: {'status': 'idle'})))
    host = RemoteAwareHost(lambda: port)
    host.task_store = Store(tmp_path / 'tasks.sqlite3')
    return host, repo


def test_new_tasks_snapshot_selection_and_main_assistant_stays_local(tmp_path):
    host, repo = host_at(tmp_path)
    first = host.create('assistant', 'one', 'First', '/remote/work')
    repo.chats['assistant']['workDevice'] = device('second')
    second = host.create('assistant', 'two', 'Second', '/remote/work')
    assert first['workDevice']['device_id'] == 'peer'
    assert second['workDevice']['device_id'] == 'second'
    assert host.create('assistant', 'one', 'Retry', '/remote/work')['workDevice']['device_id'] == 'peer'
    assert host.remote('assistant') is None
    assert host.status('assistant') == 'idle'
    assert not first.get('remoteDeviceIds')


@pytest.mark.asyncio
async def test_remote_delivery_uses_original_device_and_never_local_send(tmp_path, monkeypatch):
    host, repo = host_at(tmp_path)
    chat = host.create('assistant', 'task', 'Task', '/remote/work')
    repo.chats['assistant']['workDevice'] = device('second')
    calls = []
    async def request(peer, command, payload, **kw):
        calls.append((peer, command, payload, kw))
        return {'ok': True, 'accepted': True}
    from cyrene.plugins.builtin.session_management import remote_host
    monkeypatch.setattr(remote_host, 'task_request', lambda: request)
    monkeypatch.setattr('cyrene.plugins.model_catalog.configured_context_limit', lambda owner: 12345)
    payload = {'version': host.version(chat), 'content': 'Execute objective', 'attachments': []}
    assert await host.deliver('assistant', 'task', 'message-1', payload)
    assert calls[0][0:2] == ('peer', 'tasks.deliver')
    assert calls[0][2]['model_context_limit'] == 12345
    assert repo.chats['task']['messages'][0]['content'] == 'Execute objective'
    assert host.status('task') == 'running'
    async def offline(*a, **kw):
        raise ConnectionError('device offline')
    monkeypatch.setattr(remote_host, 'task_request', lambda: offline)
    with pytest.raises(ConnectionError):
        await host.deliver('assistant', 'task', 'message-2', payload)
    assert host.remote('task')['device_id'] == 'peer'


@pytest.mark.asyncio
async def test_results_and_pinned_evidence_survive_device_disconnection(tmp_path, monkeypatch):
    host, repo = host_at(tmp_path)
    host.create('assistant', 'task', 'Task', '/remote/work')
    snapshot = {'ok': True, 'chat_id': 'remote-task', 'status': 'completed',
        'chat': {'messages': [{'id': 'reply', 'role': 'assistant', 'content': 'done'}], 'pendingQuestion': None},
        'checkpoint': {'status': 'completed', 'node_id': 'final'},
        'blocks': {'revision': '1', 'blocks': [{'block_id': 'task:one', 'kind': 'task', 'content': 'evidence'}]},
        'result': {'status': 'completed', 'content': 'done', 'revision': 'r', 'final_reply_node_id': 'reply'},
        'notification': {'status': 'completed', 'node_id': 'final', 'final_reply': 'done'}}
    host.remote_call = AsyncMock(return_value=snapshot)
    monkeypatch.setattr('cyrene.workbench.chat.chat_events.publish_chat_changed', AsyncMock())
    await host.refresh_task('task')
    host.remote_call = AsyncMock(side_effect=ConnectionError('offline'))
    assert repo.chats['task']['messages'][0]['content'] == 'done'
    assert host.latest_result('task', 'final')['content'] == 'done'
    assert host.blocks('task')['blocks'][0]['content'] == 'evidence'
    host.task_store.save_remote_snapshot('task', {'status': 'running'})
    assert host.latest_result('task', 'final')['content'] == 'done'
    assert host.latest_result('task')['status'] == 'running'


@pytest.mark.asyncio
async def test_selection_requires_live_version_feature_grants_and_workspace(monkeypatch):
    peer = {'display_name': 'Peer', 'received_capabilities': list(transport.TASK_GRANTS)}
    monkeypatch.setattr(transport, 'application_plugin_service', lambda name: SimpleNamespace(store=SimpleNamespace(get_peer=lambda _: peer)))
    request = AsyncMock(side_effect=[{'app_version': '0.9.2.0', 'features': {transport.TASK_FEATURE: True}},
                                     {'projects': device()['projects']}])
    monkeypatch.setattr(transport, 'request', request)
    assert (await transport.describe_device('peer'))['version'] == '0.9.2.0'
    request.side_effect = [{'app_version': '0.9.1.9', 'features': {transport.TASK_FEATURE: True}}]
    with pytest.raises(ValueError, match='0.9.2.0'):
        await transport.describe_device('peer')
    peer['received_capabilities'] = ['chat:read']
    request.side_effect = [{'app_version': '0.9.2.0'}]
    with pytest.raises(ValueError, match='授权'):
        await transport.describe_device('peer')


@pytest.mark.asyncio
async def test_model_request_is_bound_to_task_owner_and_uses_local_config(tmp_path, monkeypatch):
    from cyrene.core.plugin import PluginRegistry
    from cyrene.core.plugin.runtime import PluginRuntime
    host, _ = host_at(tmp_path)
    host.create('assistant', 'task', 'Task', '/remote/work')
    host.task_store.register('task', 'assistant')
    host.task_store.save_remote_snapshot('task', {'status': 'running'})
    management = SimpleNamespace(host=host, store=host.task_store)
    monkeypatch.setattr(transport, 'application_plugin_service', lambda _: management)
    monkeypatch.setattr(transport, 'application_plugin_scope', lambda: SimpleNamespace(registry=PluginRegistry(), active_services={}))
    call = AsyncMock(return_value=SimpleNamespace(success=True, value={'text': 'model response'}))
    monkeypatch.setattr(PluginRuntime, 'call', call)
    payload = {'task_id': 'task', 'arguments': {'messages': [{'role': 'user', 'content': 'work'}]}}
    assert (await transport.controller_model('peer', payload))['response']['text'] == 'model response'
    assert call.call_args.args[2].data['session_id'] == 'assistant'
    with pytest.raises(ValueError, match='bound'):
        await transport.controller_model('other-peer', payload)
    host.task_store.save_remote_snapshot('task', {'status': 'completed'})
    with pytest.raises(ValueError, match='active execution'):
        await transport.controller_model('peer', payload)
    host.task_store.pause('task')
    host.port().service.repository.mutate_one('task', lambda c: c.update(workDeviceStopped=True))
    with pytest.raises(ValueError, match='stopped'):
        await transport.controller_model('peer', payload)


@pytest.mark.asyncio
async def test_router_delegates_without_using_remote_model_configuration():
    from cyrene.plugins.model_router import route_model_call
    from cyrene.core.plugin import PluginContext
    delegate = AsyncMock(return_value={'text': 'controller result'})
    result = await route_model_call({'messages': [{'role': 'user', 'content': 'task'}]}, PluginContext(services={'model_delegate': delegate}))
    assert result == {'text': 'controller result'}
    delegate.assert_awaited_once()


@pytest.mark.asyncio
async def test_remote_executor_reuses_task_harness_and_rejects_outside_workspace(tmp_path, monkeypatch):
    from cyrene.plugins.builtin.session_management.host import WorkbenchHost
    from cyrene.plugins.builtin.cyrene_remote import commands
    host, repo = host_at(tmp_path)
    root = tmp_path / 'remote-work'
    root.mkdir()
    port = host.port()
    port.agent_message_admitted = AsyncMock(return_value=False)
    executor = SimpleNamespace(chat=port,
        store=SimpleNamespace(get_peer=lambda _: {'granted_capabilities': list(transport.TASK_GRANTS)}),
        projects=SimpleNamespace(list_projects=AsyncMock(return_value=[{'id': 'project', 'workspacePath': str(root)}])))
    monkeypatch.setattr(commands, '_remote_project_workspace', lambda project: str(root))
    monkeypatch.setattr(transport, 'get_version', lambda: '0.9.2.0')
    delivered = AsyncMock(return_value=True)
    monkeypatch.setattr(WorkbenchHost, 'deliver', delivered)
    payload = {'task_id': 'local-task', 'message_id': 'input', 'content': 'objective', 'workspace_path': str(root),
               'model_context_limit': 64000}
    assert (await transport.execute(executor, 'controller', 'tasks.deliver', payload, 'project'))['accepted']
    target = transport.remote_target('controller', 'local-task')
    assert repo.chats[target]['remoteControllerDeviceId'] == 'controller'
    assert repo.chats[target]['remoteControllerTaskId'] == 'local-task'
    assert repo.chats[target]['remoteModelContextLimit'] == 64000
    assert repo.chats[target]['permissionMode'] == 'auto'
    assert delivered.call_args.args[1:3] == (target, 'input')
    port.agent_message_admitted.return_value = True
    await transport.execute(executor, 'controller', 'tasks.deliver', payload, 'project')
    assert delivered.await_count == 1
    with pytest.raises(ValueError, match='inside'):
        await transport.execute(executor, 'controller', 'tasks.deliver',
                                {**payload, 'task_id': 'outside', 'workspace_path': str(tmp_path)}, 'project')
    assert (await transport.execute(executor, 'stranger', 'tasks.snapshot', {'task_id': 'local-task'}, 'project'))['exists'] is False
    with pytest.raises(ValueError, match='belong'):
        await transport.execute(executor, 'controller', 'tasks.snapshot', {'task_id': 'local-task'}, 'other-project')


@pytest.mark.asyncio
async def test_task_transport_preserves_existing_directional_grants(tmp_path, monkeypatch):
    from cyrene.plugins.builtin.cyrene_remote.control import RemoteControlStore, RemoteGateway, InMemoryRemoteRelay
    monkeypatch.setenv('CYRENE_REMOTE_KEYRING', '0')
    controller = RemoteControlStore(str(tmp_path / 'controller.db'))
    peer = RemoteControlStore(str(tmp_path / 'peer.db'))
    for store in (controller, peer):
        store.update_settings(enabled=True, relay_url='', device_name='test')
    invitation = peer.create_pairing_invitation(project_scopes=['project'])
    accepted = controller.accept_pairing_invitation(invitation['invitation'])
    peer.complete_pairing_response(accepted['response'])
    relay = InMemoryRemoteRelay()
    received = []
    async def handler(sender, command, payload, project_id):
        received.append((sender, command, project_id))
        return {'ok': True, 'accepted': True}
    a = RemoteGateway(controller, relay, handler)
    b = RemoteGateway(peer, relay, handler)
    await a.start()
    await b.start()
    try:
        response = await a.request(peer.identity.device_id, command='tasks.deliver', project_id='project', payload={'task_id': 'task'})
        assert response['accepted']
        assert received[-1][1:] == ('tasks.deliver', 'project')
        with pytest.raises(PermissionError):
            await a.request(peer.identity.device_id, command='tasks.deliver', project_id='unshared')
        # Model callbacks are device-bound in the handler, not a broad reverse project grant.
        assert (await b.request(controller.identity.device_id, command='tasks.model', payload={'task_id': 'task'}))['ok']
        peer.update_peer_grant(controller.identity.device_id, capabilities=[], project_scopes=[])
        denied = await a.request(peer.identity.device_id, command='tasks.deliver', project_id='project')
        assert denied['ok'] is False
    finally:
        await a.stop()
        await b.stop()


@pytest.mark.asyncio
async def test_connection_retries_three_times_then_pauses_input_and_asks_once(tmp_path, monkeypatch):
    import json
    from cyrene.plugins.builtin.session_management import remote_host
    from cyrene.plugins.builtin.session_management.service import SessionManagementService
    host, _ = host_at(tmp_path)
    service = SessionManagementService(tmp_path / 'service', host)
    chat = host.create('assistant', 'task', 'Task', '/remote/work')
    service.store.register('task', 'assistant')
    service.store.enqueue('assistant', 'task', 'input', {
        'version': host.version(chat), 'content': 'objective', 'attachments': []})
    request = AsyncMock(side_effect=ConnectionError('offline'))
    sleep = AsyncMock()
    monkeypatch.setattr(remote_host, 'task_request', lambda: request)
    monkeypatch.setattr(remote_host.asyncio, 'sleep', sleep)
    monkeypatch.setattr('cyrene.plugins.model_catalog.configured_context_limit', lambda _: 32768)
    row = service.store.pending()[0]
    await service._deliver_message(row)
    assert request.await_count == 4  # initial attempt + three retries
    assert sleep.await_count == 3
    assert all(call.args[0] == 'peer' for call in request.call_args_list)
    assert service.store.message('input')['status'] == 'queued'
    assert service.store.session('task')['paused'] == 1
    notifications = service.store.notifications()
    assert len(notifications) == 1
    payload = json.loads(notifications[0]['payload'])
    assert payload['event']['recovery_action'] == 'ask_user'
    assert payload['event']['work_device']['device_id'] == 'peer'
    assert payload['event']['execution_state'] == 'unknown'
    host.port().dispatch_agent_message = AsyncMock()
    assert await host.notify('assistant', 'task', notifications[0]['id'], payload)
    text = host.port().dispatch_agent_message.call_args.args[1]
    assert 'Promptly call ask_user' in text
    assert 'does not prove the remote task stopped' in text
    service.store.acknowledge(notifications[0]['id'])
    # An error string changing, or reopening the durable store, must not repeat the question.
    service.store = Store(service.store.path)
    service.work_device_unavailable('assistant', 'task', 'connection timed out')
    assert service.store.notifications() == []
    # Reconnection clears only outage deduplication, not the user's paused queue.
    request.side_effect = None
    request.return_value = {'ok': True}
    await host.remote_call('task', 'tasks.snapshot')
    assert host.cached('task', 'connection_notice')['active']
    host.recovered('task')  # Only a confirmed snapshot/operation establishes recovery.
    assert service.store.session('task')['paused'] == 1
    service.work_device_unavailable('assistant', 'task', 'new outage')
    assert len(service.store.notifications()) == 1
    assert service.store.notifications()[0]['id'] != notifications[0]['id']


@pytest.mark.asyncio
async def test_reconnection_during_retry_does_not_notify_user(tmp_path, monkeypatch):
    from cyrene.plugins.builtin.session_management import remote_host
    host, _ = host_at(tmp_path)
    host.create('assistant', 'task', 'Task', '/remote/work')
    request = AsyncMock(side_effect=[ConnectionError('offline'), TimeoutError(),
                                    ConnectionError('offline'), {'ok': True, 'accepted': True}])
    monkeypatch.setattr(remote_host, 'task_request', lambda: request)
    monkeypatch.setattr(remote_host.asyncio, 'sleep', AsyncMock())
    result = await host.remote_call('task', 'tasks.deliver', {'message_id': 'same-input'}, key='same-key')
    assert result['accepted']
    assert request.await_count == 4
    assert all(call == request.call_args_list[0] for call in request.call_args_list)
    assert not host.task_store.notifications()
    request.reset_mock(side_effect=True)
    request.side_effect = ValueError('permission denied')
    with pytest.raises(ValueError, match='permission denied'):
        await host.remote_call('task', 'tasks.deliver')
    assert request.await_count == 1


@pytest.mark.asyncio
async def test_monitoring_disconnect_notifies_without_claiming_task_stopped(tmp_path):
    from cyrene.plugins.builtin.session_management.service import SessionManagementService
    host, _ = host_at(tmp_path)
    service = SessionManagementService(tmp_path / 'service', host)
    host.create('assistant', 'task', 'Task', '/remote/work')
    service.store.register('task', 'assistant')
    service.store.save_remote_snapshot('task', {'status': 'running', 'checkpoint': {'node_id': 'last'}})
    host.refresh_task = AsyncMock(side_effect=ConnectionError('three retries exhausted'))
    service.notify_tasks = AsyncMock()
    await host.refresh_remote(service)
    await host.refresh_remote(service)
    assert host.status('task') == 'running'
    assert host.checkpoint('task')['node_id'] == 'last'
    assert len(service.store.notifications()) == 1
    assert service.notify_tasks.await_count == 2


@pytest.mark.asyncio
async def test_device_inventory_is_local_and_keeps_offline_registered_peers(monkeypatch):
    settings = {'enabled': True, 'device_name': 'Mac', 'peers': [
        {'device_id': 'offline', 'display_name': 'Windows'}, {'device_id': 'online', 'display_name': 'Ubuntu'}]}
    remote = SimpleNamespace(projection=SimpleNamespace(settings=AsyncMock(return_value=settings)))
    monkeypatch.setattr(transport, 'application_plugin_service', lambda _: remote)
    probe = AsyncMock(side_effect=AssertionError('Inventory must not contact peers'))
    monkeypatch.setattr(transport, 'describe_device', probe)
    result = await transport.task_devices()
    assert [peer['device_id'] for peer in result['peers']] == ['offline', 'online']
    probe.assert_not_awaited()
    assert all(peer['task_state'] == 'checking' for peer in result['peers'])


@pytest.mark.asyncio
async def test_device_probe_bounds_entire_transport_and_marks_timeout_offline(monkeypatch):
    import asyncio
    cancelled = asyncio.Event()
    async def stuck_request(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    peer = {'display_name': 'Windows', 'received_capabilities': list(transport.TASK_GRANTS)}
    monkeypatch.setattr(transport, 'application_plugin_service', lambda _: SimpleNamespace(store=SimpleNamespace(get_peer=lambda _: peer)))
    monkeypatch.setattr(transport, 'DEVICE_CHECK_TIMEOUT', 0.02)
    monkeypatch.setattr(transport, 'request', stuck_request)
    result = await asyncio.wait_for(transport.check_task_device('offline'), timeout=0.5)
    assert result == {'device_id': 'offline', 'task_eligible': False, 'task_state': 'offline', 'task_error': '离线'}
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_offline_device_without_grants_is_still_shown_offline(monkeypatch):
    monkeypatch.setattr(transport, 'application_plugin_service', lambda _: SimpleNamespace(store=SimpleNamespace(get_peer=lambda _: {'display_name': 'Offline'})))
    monkeypatch.setattr(transport, 'request', AsyncMock(side_effect=ConnectionError('offline')))
    assert (await transport.check_task_device('peer'))['task_state'] == 'offline'
