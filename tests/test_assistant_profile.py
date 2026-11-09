from cyrene.core.plugin import PluginRegistry
from cyrene.core.plugin.core_impl.context import CONTEXT_PLUGINS
from cyrene.plugins.builtin import ask_user
from cyrene.plugins.builtin.cyrene_content import plugin_pack as content
from cyrene.plugins.builtin.cyrene_delivery import plugin_pack as delivery
from cyrene.plugins.builtin.session_management import TOOL_NAMES, plugin_pack as management
from cyrene.workbench.core_adapter.assistant_profile import personal_assistant_config, ASSISTANT_SCHEDULE_TOOL_NAMES
from cyrene.plugins.builtin.cyrene_schedule import plugin_pack as schedule
from cyrene.plugins.builtin.cyrene_cards import TOOL_NAMES as CARD_TOOL_NAMES, plugin_pack as cards
from cyrene.workbench.core_adapter.conversation_runtime import ConversationConfig


def test_assistant_profile_preserves_ordinary_config_and_resolves_real_tools():
    ordinary = ConversationConfig(session_id="assistant", workspace_dir="/tmp", db_path="/tmp/app.db")
    assistant = personal_assistant_config(ordinary)
    assert ordinary.extra_direct_tool_names == ()
    assert assistant == personal_assistant_config(assistant)
    assert set(assistant.extra_direct_tool_names) == {*TOOL_NAMES, *ASSISTANT_SCHEDULE_TOOL_NAMES, *CARD_TOOL_NAMES, "ask_user", "send_message", "send_file", "WebSearch"}
    registry = PluginRegistry()
    for pack in (management, content, delivery, schedule, cards):
        registry.register_pack(pack, source="test")
    assert ask_user.TOOL_NAME == "ask_user"
    from cyrene.core.plugin.tool_scope import ScopedPluginRegistry
    view = ScopedPluginRegistry(registry, assistant.tool_scope)
    for name in set(assistant.extra_direct_tool_names) - {"ask_user"}:
        assert view.resolve(name).name == name
    core_names = {d["function"]["name"] for d in registry.direct_tool_definitions()}
    assert {p.name for p in CONTEXT_PLUGINS} <= core_names
    assert not set(TOOL_NAMES) & core_names
    assert not set(CARD_TOOL_NAMES) & core_names


async def _mounted_prompt(personal):
    from types import SimpleNamespace
    from pathlib import Path
    from cyrene.plugins.builtin.cyrene_system_prompt.service import setup_system_prompt

    handlers = []
    hooks = SimpleNamespace(list=lambda: [], register=lambda event, handler, **kwargs: handlers.append(handler))
    setup_system_prompt(SimpleNamespace(data={"personal_assistant": personal}, workspace=Path("/workspace"), hooks=hooks))
    handler = handlers[0]
    mounted = await handler(None)
    assert mounted["context_position"] == "system"
    assert handler.session_start_cache_fingerprint(None) == mounted["context"]
    return mounted["context"]


def test_personal_prompt_uses_existing_hook_and_changes_fingerprint():
    import asyncio
    from cyrene.plugins.builtin.cyrene_system_prompt.system_prompt import SYSTEM_PROMPT
    from cyrene.plugins.builtin.cyrene_system_prompt.assistant_prompt import ASSISTANT_SYSTEM_PROMPT

    ordinary = asyncio.run(_mounted_prompt(False))
    assistant = asyncio.run(_mounted_prompt(True))
    assert ordinary == SYSTEM_PROMPT.strip()
    assert assistant == ASSISTANT_SYSTEM_PROMPT.strip()
    assert "toolbox" not in assistant.lower()
    assert "update_plan_progress" not in assistant
    assert asyncio.run(_mounted_prompt(False)) == ordinary
    assert ordinary != assistant


def test_assistant_two_turns_parse_and_execute_session_creation(tmp_path, monkeypatch):
    import asyncio
    import json
    from cyrene.core import AgentSession
    from cyrene.core.plugin import Plugin
    from cyrene.core.plugin.tool_scope import ScopedPluginRegistry
    from cyrene.plugins import ensure_model_router, model_router
    from cyrene.plugins.builtin.session_management.service import SessionManagementService
    from test_session_management import Host

    async def scenario():
        host = Host(tmp_path)
        service = SessionManagementService(tmp_path / 'management', host)
        await service.start()
        calls = []
        async def provider(args, context):
            calls.append(args)
            if len(calls) == 1:
                return {'content': '你好', 'tool_calls': []}
            if len(calls) == 2:
                names = {t['function']['name'] for t in args['tools']}
                assert 'task_start' in names
                assert not any(name.startswith('session_') for name in names)
                return {'content': '', 'tool_calls': [{'id': 'create-once', 'function': {
                    'name': 'task_start', 'arguments': json.dumps({
                        'title': '验证任务', 'workspace_path': str(tmp_path), 'message': '检查目录并汇报结果',
                    })}}]}
            result = next(m for m in reversed(args['messages']) if m['role'] == 'tool')
            assert result['tool_call_id'] == 'create-once'
            assert json.loads(result['content'])['success'] is True
            return {'content': '已创建对话', 'tool_calls': []}
        registry = PluginRegistry()
        for pack in (management, content, delivery, schedule, cards):
            registry.register_pack(pack, source='test')
        registry.register_plugin(ask_user.plugin, source='test')
        registry.register_plugin(Plugin('TestProvider', 'test', {'type': 'object'}, provider, kind='model',
                                       metadata={'provider': {'id': 'test', 'name': 'test'}}), source='test')
        ensure_model_router(registry)
        monkeypatch.setattr(model_router, 'configured_model_candidates', lambda *a, **k: [
            {'id': 'test', 'model': 'test', 'provider': 'test', 'adapter': 'openai'}])
        async def ignore(*args, **kwargs):
            pass
        monkeypatch.setattr(model_router, '_publish_llm_event', ignore)
        profile = personal_assistant_config(ConversationConfig(session_id='a', workspace_dir=str(tmp_path), db_path=''))
        session = AgentSession(tmp_path / 'agent', tmp_path, tmp_path / 'plugins', tree_id='a',
            registry=ScopedPluginRegistry(registry, profile.tool_scope), load_plugins=False,
            model_plugin=model_router.MODEL_ROUTER_PLUGIN,
            extra_direct_tool_names=profile.extra_direct_tool_names,
            plugin_services={'session_management': service})
        session._configured_compaction_limit = lambda: 0
        try:
            session.submit('你好', run_id='hello')
            await asyncio.wait_for(session.drain(), 5)
            assert session.final_output('hello')['content'] == '你好'
            session.submit('你新建一个对话试试。', run_id='create')
            await asyncio.wait_for(session.drain(), 5)
            assert session.final_output('create')['content'] == '已创建对话'
            assert len(calls) == 3
            assert len((await service.list('a'))['sessions']) == 1
        finally:
            session.close()
            await service.stop()
    asyncio.run(scenario())
