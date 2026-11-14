"""Tool result storage is exercised through actual session/model transitions."""
import asyncio
import json

import pytest

from cyrene.core.plugin import Plugin, PluginPack, PluginRegistry
from cyrene.core.session import AgentSession
from cyrene.plugins.builtin.cyrene_content import tool_result_store as storage
from cyrene.core.context.tool_results import project_tool_messages


def make_session(tmp_path, model, tools=()):
    registry = PluginRegistry()
    registry.register_pack(PluginPack('test', 'test', (
        Plugin('MiniMax', 'model', {'type': 'object'}, model, kind='model'), *tools,
    )), source='test')
    (tmp_path / 'plugins').mkdir(exist_ok=True)
    session = AgentSession(
        tmp_path / 'data', tmp_path, tmp_path / 'plugins', registry=registry,
        tree_id='result-session', load_plugins=False, inherit_application_scope=False,
        plugin_services={'tool_results': storage.ToolResultStore()},
    )
    session._configured_compaction_limit = lambda: 100_000
    return session


def test_parallel_direct_and_nested_results_are_bounded_and_replayed(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, '_RESULT_ROOT', tmp_path / 'results')
    raw = '论文证据' * 15_000
    received = []

    async def model(arguments, context):
        received.append(arguments['messages'])
        if len(received) == 1:
            return {'content': '', 'tool_calls': [
                {'id': 'direct', 'name': 'Fetch', 'arguments': {}},
                {'id': 'nested', 'name': 'toolbox', 'arguments': {'operation': 'invoke', 'name': 'NestedFetch', 'arguments': {}}},
            ]}
        return {'content': 'done', 'tool_calls': []}

    async def fetch(arguments, context):
        return raw

    async def nested(arguments, context):
        return {'name': 'WebFetch', 'result': raw}

    tools = tuple(Plugin(name, name, {'type': 'object'}, handler, allow_parallel=True)
                  for name, handler in [('Fetch', fetch), ('NestedFetch', nested)])
    session = make_session(tmp_path, model, tools)
    try:
        session.submit('Read the evidence', run_id='run')
        asyncio.run(session.drain())
        assert len(received) == 2
        messages = [m for m in received[-1] if m.get('role') == 'tool']
        assert {m['tool_call_id'] for m in messages} == {'direct', 'nested'}
        assert sum(storage._token_count(m['content']) for m in messages) <= 2000
        refs = []
        for message in messages:
            envelope = json.loads(message['content'])
            assert envelope['truncated']
            refs.append(envelope['content_ref'])
            restored = storage._resolve_reference(envelope['content_ref'], session_id='result-session').read_text()
            assert raw in restored
        leaf_id = session.snapshot()['leaf_id']
        first_replay = session._messages(leaf_id)
        assert session._messages(leaf_id) == first_replay
        assert len(list((tmp_path / 'results').rglob('*.txt'))) == 2
    finally:
        session.close()

    # Persisted history still contains the complete results. Reopening must
    # apply the same guard before the next model request, with stable refs.
    restored_session = make_session(tmp_path, model, tools)
    try:
        assert restored_session._messages(leaf_id) == first_replay
        with pytest.raises(storage.ToolResultReferenceError):
            storage.read_tool_result(refs[0], session_id='another-session')
    finally:
        restored_session.close()


def test_historical_error_and_paged_result_are_bounded_without_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, '_RESULT_ROOT', tmp_path / 'results')
    messages = [
        {'role': 'tool', 'tool_call_id': 'error', 'name': 'Fetch',
         'content': json.dumps({'success': False, 'error': '错误' * 20_000}, ensure_ascii=False)},
        {'role': 'tool', 'tool_call_id': 'page', 'name': 'read_tool_result',
         'content': json.dumps({'content': '正文' * 20_000}, ensure_ascii=False)},
    ]
    original = json.dumps(messages)
    projected = project_tool_messages(messages, service=storage.ToolResultStore(),
                                      session_id='session', context_limit_tokens=100_000)
    assert sum(storage._token_count(m['content']) for m in projected) <= 2000
    assert json.dumps(messages) == original
    for message in projected:
        ref = json.loads(message['content'])['content_ref']
        page = json.loads(storage.read_tool_result(ref, session_id='session', limit=100))
        assert len(page['content']) <= 100
        assert page['has_more']


def test_small_results_keep_exact_content(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, '_RESULT_ROOT', tmp_path / 'results')
    messages = [{'role': 'tool', 'tool_call_id': 'small', 'name': 'Read', 'content': 'small'}]
    assert project_tool_messages(messages, service=storage.ToolResultStore(),
                                 session_id='session', context_limit_tokens=100_000) == messages
    assert not (tmp_path / 'results').exists()
