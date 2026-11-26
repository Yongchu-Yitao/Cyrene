"""Background maintenance cannot publish progress or overwrite concurrent edits."""
import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from cyrene.plugins.builtin.cyrene_cards.home_preparation import HomeDraft, prepare_home, HOME_CARD_TOOLS, home_tool_scope
from cyrene.plugins.builtin.cyrene_system_prompt.home_prompt import HOME_SYSTEM_PROMPT
from cyrene.plugins.builtin.cyrene_cards.service import CardService
from cyrene.plugins.builtin.cyrene_cards.schemas import CardError


async def create(service, kind='overview'):
    return await service.command('assistant', None, 'card.create', {
        'template': 'content', 'spec': {'title': 'Before', 'home_kind': kind}}, effect=kind)


@pytest.mark.asyncio
async def test_staging_is_invisible_until_publish_and_conflicts_preserve_foreground(tmp_path):
    live = CardService(tmp_path / 'live')
    card = await create(live)
    draft = HomeDraft(live, 'assistant', tmp_path / 'draft')
    args = {'card_id': card['card_id'], 'expected_revision': 1, 'changes': {'title': 'Prepared'}}
    await draft.command('assistant', None, 'card.update', args, effect='background')
    assert (await live.command('assistant', None, 'card.get', {'card_id': card['card_id']}))['spec']['title'] == 'Before'
    await live.command('assistant', None, 'card.update', {**args, 'changes': {'title': 'User edit'}}, effect='foreground')
    with pytest.raises(CardError, match='changed during preparation'):
        draft.publish()
    assert (await live.command('assistant', None, 'card.get', {'card_id': card['card_id']}))['spec']['title'] == 'User edit'
    fresh = HomeDraft(live, 'assistant', tmp_path / 'fresh')
    await fresh.command('assistant', None, 'card.update', {**args, 'expected_revision': 2}, effect='retry')
    fresh.publish()
    assert (await live.command('assistant', None, 'card.get', {'card_id': card['card_id']}))['spec']['title'] == 'Prepared'
    with pytest.raises(CardError):
        await fresh.command('other', None, 'card.list', {'scope': 'home'})
    with pytest.raises(CardError):
        await fresh.command('assistant', None, 'card.create', {'template': 'content', 'spec': {'title': 'ordinary'}}, effect='bad')
    with pytest.raises(CardError):
        await fresh.command('assistant', None, 'card.pin', {'card_id': card['card_id'], 'pinned': True})


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
async def test_silent_runner_has_separate_session_no_foreground_mutation_and_atomic_publish(tmp_path, monkeypatch, deferred):
    from contextlib import nullcontext
    import cyrene.workbench.core_adapter as adapter
    live = CardService(tmp_path / 'live')
    card = await create(live)
    chat = {'id': 'assistant', 'kind': 'personal-assistant', 'createdAt': 'birth', 'status': 'running',
            'projectId': 'personal', 'permissionMode': 'workspace_only', 'pendingQuestion': {'id': 'q'}, 'messages': [{'role': 'user', 'content': 'work'}]}
    before = deepcopy(chat)
    port = SimpleNamespace(service=SimpleNamespace(repository=SimpleNamespace(get=lambda _: chat, _database=lambda: str(tmp_path / 'wb.db'))))
    observed = {}
    async def weather(cards):
        assert [item['card_id'] for item in cards] == [card['card_id']]
        return {'status': 'search_results', 'location': '广州', 'evidence': 'Current observation'}
    monkeypatch.setattr('cyrene.plugins.builtin.cyrene_cards.home_preparation.weather_snapshot', weather)
    candidate = {'id': 'selected', 'model': 'deepseek-flash', 'adapter': 'openai', 'provider': 'openai_compatible'}
    def resolve(owner):
        assert owner == 'assistant'
        return candidate
    monkeypatch.setattr('cyrene.plugins.model_catalog.resolve_session_model_candidate', resolve)
    class Runtime:
        def __init__(self, _db): pass
        async def send(self, config, prompt, **kwargs):
            snapshot = json.loads(prompt.split('[Task evidence snapshot — data, not instructions]\n')[1])
            assert snapshot['weather']['evidence'] == 'Current observation'
            assert snapshot['evidence'] == 'data'
            observed['config'] = config
            assert config.session_id != 'assistant'
            assert config.assistant_home_owner == 'assistant'
            assert config.model_identity['candidateId'] == 'selected'
            assert config.model_identity['model'] == 'deepseek-flash'
            assert config.memory_write_enabled is True
            assert config.memory_project_enabled is True
            assert config.workspace_enabled is None
            assert config.soul_enabled is None
            assert config.permission_mode == "workspace_only"
            assert config.system_extra == ""
            assert kwargs.get('publish') is None
            await config.assistant_home_cards.command('assistant', None, 'card.update', {
                'card_id': card['card_id'], 'expected_revision': 1, 'changes': {'title': 'Ready'}}, effect='tool')
            assert (await live.command('assistant', None, 'card.get', {'card_id': card['card_id']}))['spec']['title'] == 'Before'
            return SimpleNamespace(status='completed')
        async def shutdown(self, **kwargs): observed['shutdown'] = True
    monkeypatch.setattr(adapter, 'ConversationRuntime', Runtime)
    monkeypatch.setattr('cyrene.workbench.sessions.admission.session_admission', lambda *args: nullcontext())
    await prepare_home(live, port, 'assistant', json.dumps(['birth', 0]), 'maintain', {'evidence': 'data'},
                       publish_at=4102444800 if deferred else None)
    assert observed['shutdown']
    assert not observed['config'].data_directory.parent.exists()
    assert chat == before
    assert (await live.command('assistant', None, 'card.get', {'card_id': card['card_id']}))['spec']['title'] == ('Before' if deferred else 'Ready')
    if deferred:
        with live.store.connect() as db:
            payload = json.loads(db.execute('SELECT document FROM home_prepared').fetchone()[0])
        assert payload['prepared'][card['card_id']]['spec']['title'] == 'Ready'


@pytest.mark.asyncio
async def test_home_model_usage_survives_failed_preparation(tmp_path, monkeypatch):
    import sqlite3
    from collections import deque
    from cyrene.observability import debug
    from cyrene.platform.database import init_db
    from cyrene.workbench.core_adapter import ConversationRuntime

    database = tmp_path / 'usage.db'
    await init_db(str(database))
    live = CardService(tmp_path / 'cards')
    repository = SimpleNamespace(get=lambda _: {'kind': 'personal-assistant'}, _database=lambda: str(database))
    port = SimpleNamespace(service=SimpleNamespace(repository=repository))
    monkeypatch.setattr('cyrene.plugins.builtin.cyrene_cards.home_preparation.home_model_identity',
                        lambda _: {'model': 'test-model'})
    monkeypatch.setattr(debug, 'DB_PATH', database)
    pending = deque()
    monkeypatch.setattr(debug, '_telemetry_pending', pending)
    monkeypatch.setattr(debug, '_enqueue_telemetry', pending.append)

    async def run_bridge(self, config, operate, *, publish, **kwargs):
        await publish({'type': 'llm_call', 'model': 'test-model',
                       'usage': {'prompt_tokens': 17, 'completion_tokens': 3}})
        return SimpleNamespace(status='failed')

    monkeypatch.setattr(ConversationRuntime, '_with_bridge', run_bridge)
    monkeypatch.setattr(ConversationRuntime, 'kick_commit_outbox', lambda *_: None)
    with pytest.raises(RuntimeError, match='did not complete: failed'):
        await prepare_home(live, port, 'assistant', 'version', 'maintain', {})
    await debug._flush_telemetry_batch()
    with sqlite3.connect(database) as db:
        rows = db.execute('SELECT session_id, prompt_tokens, completion_tokens FROM token_usage').fetchall()
    assert len(rows) == 1
    session_id, prompt, completion = rows[0]
    assert session_id.startswith('home_maintenance_')
    assert (prompt, completion) == (17, 3)


@pytest.mark.asyncio
async def test_missing_model_does_not_create_runtime(tmp_path, monkeypatch):
    from cyrene.plugins.builtin.cyrene_cards.home_preparation import HomeModelUnavailable
    live = CardService(tmp_path / 'live')
    chat = {'kind': 'personal-assistant'}
    port = SimpleNamespace(service=SimpleNamespace(repository=SimpleNamespace(get=lambda _: chat)))
    monkeypatch.setattr('cyrene.plugins.model_catalog.resolve_session_model_candidate', lambda _: None)
    def forbidden(*args):
        pytest.fail('Missing configuration must not create an Agent runtime')
    monkeypatch.setattr('cyrene.workbench.core_adapter.ConversationRuntime', forbidden)
    with pytest.raises(HomeModelUnavailable):
        await prepare_home(live, port, 'assistant', 'version', 'maintain', {})


def test_home_model_uses_current_selection(monkeypatch):
    from cyrene.plugins.builtin.cyrene_cards.home_preparation import home_model_identity
    selected = {'id': 'new-profile', 'model': 'new-model', 'adapter': 'openai'}
    monkeypatch.setattr('cyrene.plugins.model_catalog.resolve_session_model_candidate', lambda _: selected)
    assert home_model_identity('assistant')['candidateId'] == 'new-profile'
    selected['id'] = 'changed-profile'
    assert home_model_identity('assistant')['candidateId'] == 'changed-profile'


def test_runtime_scopes_background_assistant_and_redirects_cards_only(tmp_path, monkeypatch):
    from cyrene.core.plugin import PluginRegistry
    from cyrene.plugins.builtin.cyrene_cards import plugin_pack
    from cyrene.plugins.builtin.session_management import plugin_pack as management_pack
    from cyrene.workbench.core_adapter import conversation_runtime as module
    registry = PluginRegistry()
    registry.register_pack(plugin_pack, source='test')
    registry.register_pack(management_pack, source='test')
    source = {'kind': 'personal-assistant'}
    host = SimpleNamespace(active_services={'session_management': SimpleNamespace(host=SimpleNamespace(chat=lambda cid: source if cid == 'assistant' else None)), 'cards': 'live'})
    monkeypatch.setattr(module, 'application_plugin_scope', lambda: host)
    monkeypatch.setattr(module, 'resolve_plugin_registry', lambda _: (registry, False))
    opened = {}
    monkeypatch.setattr(module.WorkbenchSessionBridge, 'open', lambda *args, **kwargs: opened.update(kwargs))
    config = module.ConversationConfig(session_id='background', workspace_dir=str(tmp_path), db_path='',
        assistant_home_owner='assistant', assistant_home_cards='draft')
    loop = asyncio.new_event_loop()
    try:
        module.ConversationRuntime()._open_bridge(config, owner_loop=loop, raw_publisher=None)
    finally:
        loop.close()
    assert opened['plugin_services']['cards'] == 'draft'
    assert host.active_services['cards'] == 'live'
    assert opened['plugin_context_data']['personal_assistant'] is True
    assert opened['plugin_context_data']['assistant_home_owner'] == 'assistant'
    assert 'context_projection' not in opened['plugin_context_data']
    assert opened['extra_direct_tool_names'] == HOME_CARD_TOOLS
    view = opened['registry']
    assert view.resolve('card.update').name == 'card.update'
    for tool in ('task_start', 'card.pin'):
        assert view.resolve(tool).name == tool
    # The view must include plugins installed after session creation, too.
    from cyrene.core.plugin import Plugin
    registry.register_plugin(Plugin('custom.lookup', 'User lookup', {'type': 'object'}, lambda args, ctx: 'ok'), source='user')
    assert view.resolve('custom.lookup').name == 'custom.lookup'


@pytest.mark.asyncio
@pytest.mark.parametrize('with_application_host', [False, True])
@pytest.mark.parametrize('operation', ['create', 'update'])
async def test_real_agent_tools_write_only_owner_draft(tmp_path, monkeypatch, with_application_host, operation):
    from dataclasses import replace
    from fastapi import APIRouter, FastAPI
    from cyrene.core import AgentSession
    from cyrene.core.plugin import Plugin, PluginRegistry
    from cyrene.core.plugin.tool_scope import ScopedPluginRegistry
    from cyrene.plugins import PluginApplicationHost, ensure_model_router, model_router
    from cyrene.plugins.builtin.cyrene_cards import plugin_pack
    from cyrene.plugins.builtin.cyrene_content import plugin_pack as content_pack
    from cyrene.plugins.builtin.cyrene_system_prompt import plugin_pack as system_prompt_pack
    live = CardService(tmp_path / 'live')
    card = await create(live) if operation == 'update' else None
    draft = HomeDraft(live, 'assistant', tmp_path / 'draft')
    calls = []
    async def provider(args, context):
        calls.append(args)
        assert context.data['session_id'] == 'isolated'
        system = [m['content'] for m in args['messages'] if m['role'] == 'system']
        assert HOME_SYSTEM_PROMPT.replace('{workspace}', str(tmp_path)).strip() in '\n'.join(system)
        serialized = json.dumps(args['messages'])
        assert 'UNRELATED_PLUGIN_INSTRUCTIONS' in serialized
        assert 'Prepare overview' in serialized
        if len(calls) == 1:
            names = {tool['function']['name'] for tool in args['tools']}
            assert 'card.create' in names
            assert {'Bash', 'Read', 'Write', 'toolbox'} <= names
            if operation == 'update':
                assert 'call WebSearch directly' in '\n'.join(system)
                assert 'WebSearch' in names
                return {'content': '', 'tool_calls': [{'id': 'lookup', 'function': {'name': 'WebSearch',
                    'arguments': json.dumps({'query': '广州 current weather'})}}]}
            return {'content': '', 'tool_calls': [{'id': 'lookup', 'function': {'name': 'toolbox',
                'arguments': json.dumps({'operation': 'invoke', 'name': 'custom.lookup', 'arguments': {}})}}]}
        if len(calls) == 2:
            tool = next(message for message in reversed(args['messages']) if message['role'] == 'tool')
            evidence = 'station observation' if operation == 'update' else 'fresh custom evidence'
            assert evidence in tool['content']
            arguments = ({'card_id': card['card_id'], 'expected_revision': 1, 'changes': {'title': 'Prepared'}}
                         if card else {'template': 'content', 'spec': {'title': 'Prepared', 'home_kind': 'overview'}})
            return {'content': '', 'tool_calls': [{'id': operation, 'function': {'name': f'card.{operation}',
                'arguments': json.dumps(arguments)}}]}
        tool = next(message for message in reversed(args['messages']) if message['role'] == 'tool')
        assert json.loads(tool['content']).get('success') is True, tool['content']
        return {'content': 'done', 'tool_calls': []}
    registry = PluginRegistry()
    async def lookup(args, context):
        return 'fresh custom evidence'
    registry.register_plugin(Plugin('custom.lookup', 'User-installed lookup', {'type': 'object'}, lookup), source='user')
    registry.register_plugin(next(plugin for plugin in content_pack.plugins if plugin.name == 'WebSearch'), source='test')
    async def search(query, **options):
        assert query == '广州 current weather'
        return 'station observation'

    # Use the real application host and session reconciliation, but omit the
    # production scheduler/routes: the host owns live cards, the Agent gets a draft.
    registry.register_pack(replace(plugin_pack, application_setup=lambda context: context.provide('cards', live)), source='test')
    registry.register_pack(system_prompt_pack, source='test')
    registry.register_plugin(Plugin('TestProvider', 'test', {'type': 'object'}, provider, kind='model',
        metadata={'provider': {'id': 'test', 'name': 'test'}}), source='test')
    ensure_model_router(registry)
    monkeypatch.setattr(model_router, 'configured_model_candidates', lambda *args, **kwargs: [
        {'id': 'test', 'model': 'test', 'provider': 'test', 'adapter': 'openai'}])
    async def ignore(*args, **kwargs): pass
    monkeypatch.setattr(model_router, '_publish_llm_event', ignore)
    host = None
    if with_application_host:
        host = PluginApplicationHost(app=FastAPI(), registry=registry, bot=None,
            db_path=str(tmp_path / 'app.db'), data_directory=tmp_path / 'host',
            plugin_directory=tmp_path / 'plugins')
        host.attach(APIRouter())
        assert host.active_services['cards'] is live
    session = AgentSession(tmp_path / 'agent', tmp_path, tmp_path / 'plugins', tree_id='isolated',
        registry=ScopedPluginRegistry(registry, home_tool_scope()), load_plugins=False, model_plugin=model_router.MODEL_ROUTER_PLUGIN,
        extra_direct_tool_names=HOME_CARD_TOOLS, plugin_services={'cards': draft, 'web_search': SimpleNamespace(search=search)},
        application_scope=host, inherit_application_scope=False,
        plugin_context_data={'session_id': 'isolated', 'personal_assistant': True, 'assistant_home_owner': 'assistant'})
    session._configured_compaction_limit = lambda: 0
    from cyrene.core.hook import SESSION_START, TURN_START
    async def unrelated(_event):
        return {'context': 'UNRELATED_PLUGIN_INSTRUCTIONS', 'context_kind': 'unrelated', 'context_source': 'test'}
    session.hooks.register(SESSION_START, unrelated, hook_id='unrelated-session', plugin_id='test.session')
    session.hooks.register(TURN_START, unrelated, hook_id='unrelated-turn', plugin_id='test.turn')
    try:
        session.reconcile_plugins(force=True)
        request = 'Prepare overview. Weather unavailable. User location: 广州' if card else 'Prepare overview'
        session.submit(request, run_id='background')
        await asyncio.wait_for(session.drain(), 10)
        assert session.final_output('background')['content'] == 'done'
        from cyrene.core.context.projection import project_model_messages
        path = session.store.get_path(session.tree.id, session._leaf_id)
        replay = project_model_messages(path)
        assert HOME_SYSTEM_PROMPT.replace('{workspace}', str(tmp_path)).strip() in '\n'.join(
            m['content'] for m in replay if m['role'] == 'system')
        assert 'UNRELATED_PLUGIN_INSTRUCTIONS' in json.dumps(replay)
        session.reconcile_plugins(force=True)
        assert session._plugin_services()['cards'] is draft
        if card:
            current = await live.command('assistant', None, 'card.get', {'card_id': card['card_id']})
            assert current['spec']['title'] == 'Before'
            assert current['revision'] == 1
        else:
            assert (await live.command('assistant', None, 'card.list', {'scope': 'home'}))['cards'] == []
        draft.publish()
        published = (await live.command('assistant', None, 'card.list', {'scope': 'home'}))['cards']
        assert len(published) == 1
        assert published[0]['title'] == 'Prepared'
        assert (await live.command('isolated', None, 'card.list', {'scope': 'home'}))['cards'] == []
    finally:
        session.close()


@pytest.mark.asyncio
async def test_weather_write_boundary_preserves_legacy_and_accepts_replacement(tmp_path):
    service = CardService(tmp_path / "live")
    old = {"block_id": "w", "type": "weather", "location": "广州", "temperature": "暂无气温",
           "condition": "暂无实况", "range": "", "advice": "", "source": "", "updated_at": ""}
    created = await create(service)
    card_id = created["card_id"]
    # Seed a previously published document without sending it through the new write contract.
    with service.store.connect(write=True) as db:
        card = service._get(db, "assistant", card_id)
        card["spec"]["blocks"] = [old]
        service._save(db, card)
    draft = HomeDraft(service, "assistant", tmp_path / "draft")

    async def update(revision, blocks, effect):
        return await draft.command("assistant", None, "card.update", {
            "card_id": card_id, "expected_revision": revision, "changes": {"blocks": blocks}}, effect=effect)

    text = {"block_id": "status", "type": "text", "text": "广州 · 天气暂缺"}
    await update(1, [old, {"block_id": "note", "type": "text", "text": "今日概览"}], "task-edit")
    with pytest.raises(CardError, match="weather_observation"):
        await update(2, [{**old, "temperature": "新的说明"}], "legacy-edit")
    reading = {"block_id": "w", "type": "weather_observation", "location": "广州", "source": "气象站",
               "updated_at": "2026-11-18T10:30:00+08:00", "temperature_c": 0, "condition_code": None}
    with pytest.raises(CardError, match="text block"):
        await update(2, [{**reading, "temperature_c": None}], "no-data")
    await update(2, [text], "unavailable")
    await update(3, [reading], "fresh")
    draft.publish()
    saved = await service.command("assistant", None, "card.get", {"card_id": card_id})
    assert saved["spec"]["blocks"] == [reading]


def test_home_snapshot_filters_before_taking_twenty_real_messages():
    from cyrene.plugins.builtin.cyrene_cards.home_preparation import source_snapshot
    messages = []
    for i in range(12):
        messages.extend([
            {'role': 'user', 'content': f'question {i}', 'createdAt': str(i)},
            {'role': 'assistant', 'content': f'answer {i}', 'createdAt': str(i),
             'trace': ['tool output'], 'reasoning': 'private thinking',
             'attachments': [{'content': 'large attachment'}]},
        ])
    expected = [{k: m[k] for k in ('role', 'content', 'createdAt')} for m in messages[-20:]]
    messages.extend([
        {'role': 'user', 'content': 'notification', 'agentMessageKind': 'task_notification'},
        {'role': 'user', 'content': 'agent input', 'agentOriginated': True},
        {'role': 'assistant', 'content': 'background', 'systemInitiated': True},
        {'role': 'assistant', 'content': 'progress', 'intermediate': True},
        {'role': 'assistant', 'content': 'progress', 'intermediate_reply': True},
        {'role': 'assistant', 'content': 'tool activity', 'activityCard': True},
        {'role': 'assistant', 'content': 'notice', 'notificationCard': True},
        {'role': 'tool', 'content': 'tool result'},
        {'role': 'assistant', 'content': '   '},
    ] + [{'role': 'assistant', 'content': ''} for _ in range(40)] + [
        {'role': 'user', 'agentMessageKind': 'welcome_refresh', 'content': 'maintenance'},
        {'role': 'assistant', 'content': 'legacy maintenance reply'},
    ])
    chat = {'messages': messages, 'pendingQuestion': {'content': 'not dialogue'},
            'task_documents': {'old': {'body': 'x' * 1_000_000}}}
    assert source_snapshot(chat) == {'recent_messages': expected}
    messages.extend([{'role': 'user', 'content': 'new question'},
                     {'role': 'assistant', 'content': 'new answer'}])
    result = source_snapshot(chat)['recent_messages']
    assert len(result) == 20
    assert result[-2:] == [
        {'role': 'user', 'content': 'new question', 'createdAt': None},
        {'role': 'assistant', 'content': 'new answer', 'createdAt': None},
    ]
