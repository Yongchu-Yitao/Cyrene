"""Background maintenance cannot publish progress or overwrite concurrent edits."""
import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from cyrene.plugins.builtin.cyrene_cards.home_preparation import HomeDraft, prepare_home, HOME_TOOLS, HOME_CONTEXT_PROJECTION, home_tool_scope
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
            'projectId': 'personal', 'pendingQuestion': {'id': 'q'}, 'messages': [{'role': 'user', 'content': 'work'}]}
    before = deepcopy(chat)
    port = SimpleNamespace(service=SimpleNamespace(repository=SimpleNamespace(get=lambda _: chat, _database=lambda: str(tmp_path / 'wb.db'))))
    observed = {}
    candidate = {'id': 'selected', 'model': 'deepseek-flash', 'adapter': 'openai', 'provider': 'openai_compatible'}
    def resolve(owner):
        assert owner == 'assistant'
        return candidate
    monkeypatch.setattr('cyrene.plugins.model_catalog.resolve_session_model_candidate', resolve)
    class Runtime:
        def __init__(self, _db): pass
        async def send(self, config, prompt, **kwargs):
            observed['config'] = config
            assert config.session_id != 'assistant'
            assert config.assistant_home_owner == 'assistant'
            assert config.model_identity['candidateId'] == 'selected'
            assert config.model_identity['model'] == 'deepseek-flash'
            assert config.memory_write_enabled is False
            assert config.system_extra == ""
            await kwargs['publish']({'type': 'tool.started'})
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
    from cyrene.core.plugin import PluginRegistry, PluginUnavailableError
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
    assert opened['plugin_context_data']['context_projection'] == HOME_CONTEXT_PROJECTION
    assert opened['extra_direct_tool_names'] == HOME_TOOLS
    view = opened['registry']
    assert view.resolve('card.update').name == 'card.update'
    for tool in ('task_start', 'card.pin', 'switch_context'):
        with pytest.raises(PluginUnavailableError):
            view.resolve(tool)


@pytest.mark.asyncio
async def test_real_agent_tools_write_only_owner_draft(tmp_path, monkeypatch):
    from cyrene.core import AgentSession
    from cyrene.core.plugin import Plugin, PluginRegistry
    from cyrene.core.plugin.tool_scope import ScopedPluginRegistry
    from cyrene.plugins import ensure_model_router, model_router
    from cyrene.plugins.builtin.cyrene_cards import plugin_pack
    from cyrene.plugins.builtin.cyrene_system_prompt import plugin_pack as system_prompt_pack
    live = CardService(tmp_path / 'live')
    draft = HomeDraft(live, 'assistant', tmp_path / 'draft')
    calls = []
    async def provider(args, context):
        calls.append(args)
        assert context.data['session_id'] == 'isolated'
        system = [m['content'] for m in args['messages'] if m['role'] == 'system']
        assert system == [HOME_SYSTEM_PROMPT.strip()]
        serialized = json.dumps(args['messages'])
        assert 'UNRELATED_PLUGIN_INSTRUCTIONS' not in serialized
        assert 'task_context_catalog' not in serialized
        assert 'Prepare overview' in serialized
        if len(calls) == 1:
            names = {tool['function']['name'] for tool in args['tools']}
            assert 'card.create' in names
            assert not names & {'send_message', 'task_start', 'card.pin', 'switch_context'}
            return {'content': '', 'tool_calls': [{'id': 'create', 'function': {'name': 'card.create',
                'arguments': json.dumps({'template': 'content', 'spec': {'title': 'Prepared', 'home_kind': 'overview'}})}}]}
        tool = next(message for message in reversed(args['messages']) if message['role'] == 'tool')
        assert json.loads(tool['content']).get('success') is True, tool['content']
        return {'content': 'done', 'tool_calls': []}
    registry = PluginRegistry()
    registry.register_pack(plugin_pack, source='test')
    registry.register_pack(system_prompt_pack, source='test')
    registry.register_plugin(Plugin('TestProvider', 'test', {'type': 'object'}, provider, kind='model',
        metadata={'provider': {'id': 'test', 'name': 'test'}}), source='test')
    ensure_model_router(registry)
    monkeypatch.setattr(model_router, 'configured_model_candidates', lambda *args, **kwargs: [
        {'id': 'test', 'model': 'test', 'provider': 'test', 'adapter': 'openai'}])
    async def ignore(*args, **kwargs): pass
    monkeypatch.setattr(model_router, '_publish_llm_event', ignore)
    session = AgentSession(tmp_path / 'agent', tmp_path, tmp_path / 'plugins', tree_id='isolated',
        registry=ScopedPluginRegistry(registry, home_tool_scope(registry)), load_plugins=False, model_plugin=model_router.MODEL_ROUTER_PLUGIN,
        extra_direct_tool_names=HOME_TOOLS[:4], plugin_services={'cards': draft},
        plugin_context_data={'session_id': 'isolated', 'personal_assistant': True, 'assistant_home_owner': 'assistant',
                             'context_projection': HOME_CONTEXT_PROJECTION})
    session._configured_compaction_limit = lambda: 0
    from cyrene.core.hook import SESSION_START, TURN_START
    async def unrelated(_event):
        return {'context': 'UNRELATED_PLUGIN_INSTRUCTIONS', 'context_kind': 'unrelated', 'context_source': 'test'}
    session.hooks.register(SESSION_START, unrelated, hook_id='unrelated-session', plugin_id='test.session')
    session.hooks.register(TURN_START, unrelated, hook_id='unrelated-turn', plugin_id='test.turn')
    try:
        session.submit('Prepare overview', run_id='background')
        await asyncio.wait_for(session.drain(), 10)
        assert session.final_output('background')['content'] == 'done'
        from cyrene.core.context.projection import project_model_messages
        path = session.store.get_path(session.tree.id, session._leaf_id)
        replay = project_model_messages(path)
        assert [m['content'] for m in replay if m['role'] == 'system'] == [HOME_SYSTEM_PROMPT.strip()]
        assert 'UNRELATED_PLUGIN_INSTRUCTIONS' not in json.dumps(replay)
        assert (await live.command('assistant', None, 'card.list', {'scope': 'home'}))['cards'] == []
        draft.publish()
        assert len((await live.command('assistant', None, 'card.list', {'scope': 'home'}))['cards']) == 1
        assert (await live.command('isolated', None, 'card.list', {'scope': 'home'}))['cards'] == []
    finally:
        session.close()
