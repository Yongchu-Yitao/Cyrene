from copy import deepcopy
import re

import pytest

from cyrene.model.tool_names import ToolNames


def test_aliases_are_stable_distinct_bounded_and_non_mutating():
    names = ['schedule.create', 'schedule_create', '工具/查询', 'a' * 100, 'cyrene_fn_literal', 'Read']
    arguments = {'tools': [{'type': 'function', 'function': {'name': name}} for name in names]}
    original = deepcopy(arguments)
    codec = ToolNames()
    encoded = [t['function']['name'] for t in codec.request(arguments)['tools']]
    assert len(set(encoded)) == len(names)
    assert all(re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name) for name in encoded)
    assert encoded[1] == 'schedule_create' and encoded[-1] == 'Read'
    assert [ToolNames().encode(name) for name in names] == encoded
    assert arguments == original
    result = codec.response({'tool_calls': [{'name': name, 'arguments': {'name': name}} for name in encoded]})
    assert [call['name'] for call in result['tool_calls']] == names
    assert [call['arguments']['name'] for call in result['tool_calls']] == encoded


def test_history_forced_choice_and_returned_wire_calls_roundtrip():
    codec = ToolNames()
    args = {'tools': [{'type': 'function', 'function': {'name': 'schedule.create'}}],
            'tool_choice': {'type': 'function', 'function': {'name': 'schedule.create'}},
            'messages': [{'role': 'assistant', 'content': 'schedule.create', 'tool_calls': [
                {'id': 'x', 'function': {'name': 'schedule.create', 'arguments': '{}'}}]},
                {'role': 'tool', 'tool_call_id': 'x', 'name': 'schedule.create', 'content': 'ok'}]}
    wire = codec.request(args)
    alias = wire['tools'][0]['function']['name']
    assert wire['tool_choice']['function']['name'] == alias
    assert wire['messages'][0]['tool_calls'][0]['function']['name'] == alias
    assert wire['messages'][1]['name'] == alias
    assert wire['messages'][0]['content'] == 'schedule.create'
    result = codec.response({'tool_calls': wire['messages'][0]['tool_calls']})
    assert result['tool_calls'] == args['messages'][0]['tool_calls']


@pytest.mark.asyncio
async def test_shared_provider_boundary_restores_calls(monkeypatch):
    from cyrene.plugins.builtin.cyrene_model import _shared
    from cyrene.plugins.builtin.cyrene_model.deepseek import DEEPSEEK_PROVIDER
    from cyrene.core.plugin import PluginContext
    async def wire(arguments, context, provider):
        alias = arguments['tools'][0]['function']['name']
        assert alias != 'schedule.create'
        return {'content': '', 'tool_calls': [{'id': 'call', 'function': {'name': alias, 'arguments': '{}'}}]}
    monkeypatch.setattr(_shared, '_complete_model_wire', wire)
    result = await _shared.complete_model({'model': 'deepseek-flash', 'messages': [{'role': 'user', 'content': 'hi'}],
        'tools': [{'type': 'function', 'function': {'name': 'schedule.create'}}]}, PluginContext(), DEEPSEEK_PROVIDER)
    assert result['tool_calls'][0]['function']['name'] == 'schedule.create'
