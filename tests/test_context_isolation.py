"""Task-local conversation history across repeated real session transitions."""
from copy import deepcopy
import json

import pytest

from cyrene.core.plugin import Plugin, PluginPack
from cyrene.core.context.tasks import ARGUMENT_PREVIEW_CHARS, historical_assistant, saved_messages
from test_task_contexts import make_session, run


def test_round_trip_restores_order_without_other_task_prose_or_results(tmp_path):
    inputs = []
    ids = {}
    payload = 'A argument prefix ' + 'x' * 800 + 'ARGUMENT_TAIL'

    async def echo(arguments, context):
        return 'RESULT_SECRET_' + arguments['task']

    async def model(arguments, context):
        inputs.append(deepcopy(arguments['messages']))
        index = len(inputs)
        active = context.services['task_contexts'].read()['active']
        if index == 1:
            ids['a'] = active
            return {'content': 'A intro', 'tool_calls': [
                {'id': 'a-tool', 'name': 'Echo', 'arguments': {'task': 'A', 'payload': payload}}]}
        if index == 2:
            assert 'RESULT_SECRET_A' in str(arguments['messages'])
            assert 'ARGUMENT_TAIL' in str(arguments['messages'])
            return {'content': 'A conclusion'}
        if index in {3, 6, 9}:
            return {'content': '', 'tool_calls': [
                {'id': f'pause-{index}', 'name': 'unload_context', 'arguments': {'summary': 'unfinished; continue later'}}]}
        if index == 4:
            assert 'A intro' not in str(arguments['messages'])
            assert 'A original request' not in str(arguments['messages'])
            assert 'RESULT_SECRET_A' not in str(arguments['messages'])
            return {'content': 'B intro', 'tool_calls': [
                {'id': 'b-tool', 'name': 'Echo', 'arguments': {'task': 'B', 'payload': 'B arguments'}}]}
        if index == 5:
            ids['b'] = active
            assert 'RESULT_SECRET_B' in str(arguments['messages'])
            return {'content': 'B conclusion'}
        if index in {7, 10}:
            return {'content': '', 'tool_calls': [
                {'id': f'load-{index}', 'name': 'load_context',
                 'arguments': {'context_id': ids['a' if index == 7 else 'b']}}]}
        if index in {8, 11}:
            own, other = ('A', 'B') if index == 8 else ('B', 'A')
            rendered = str(arguments['messages'])
            assert f'{own} original request' in rendered
            assert f'{other} original request' not in rendered
            assert f'{other} intro' not in rendered
            assert f'{other} conclusion' not in rendered
            assert 'RESULT_SECRET_' not in rendered
            assert 'ARGUMENT_TAIL' not in rendered
            assert rendered.index(f'{own} intro') < rendered.index('Historical tool calls') < rendered.index(f'{own} conclusion')
            # Only the current management handshake has protocol tool calls.
            assert not any(call['name'] == 'Echo' for m in arguments['messages'] for call in m.get('tool_calls', []))
            return {'content': f'{own} resumed'}
        raise AssertionError(f'unexpected model call {index}')

    s = make_session(tmp_path, model)
    try:
        s.registry.register_pack(PluginPack('echo', 'test', (
            Plugin('Echo', 'test', {'type': 'object', 'properties': {'task': {'type': 'string'}, 'payload': {'type': 'string'}}},
                   echo, metadata={'agent_exposure': 'direct', 'permission_review': False}),
        )), source='test')
        for index, request in enumerate(['A original request', 'B original request', 'Resume A', 'Resume B']):
            s.submit(request, run_id=f'run-{index}')
            run(s.drain())
        assert len(inputs) == 11
        assert ids['a'] != ids['b']
        assert len(s.task_contexts.read()['documents']) == 2
        leaf = s._leaf_id
    finally:
        s.close()
    reopened = make_session(tmp_path)
    try:
        restored = str(reopened._messages(leaf))
        assert 'B intro' in restored and 'B conclusion' in restored
        assert 'A intro' not in restored and 'A original request' not in restored
        assert 'RESULT_SECRET_' not in restored
    finally:
        reopened.close()


@pytest.mark.parametrize('call_format', ['flat', 'function_object', 'function_json'])
def test_saved_calls_are_bounded_and_do_not_retain_results_or_reasoning(call_format):
    arguments = {'content': '文' * 1000}
    call = {'id': 'old', 'name': 'Write', 'arguments': arguments}
    if call_format != 'flat':
        call = {'id': 'old', 'type': 'function', 'function': {
            'name': 'Write', 'arguments': json.dumps(arguments, ensure_ascii=False)
            if call_format == 'function_json' else arguments}}
    raw = {'role': 'assistant', 'content': 'before call', 'reasoning_details': 'private reasoning',
           'tool_calls': [call]}
    result = historical_assistant(raw)
    assert result['content'].startswith('before call\n\n[Historical tool calls')
    assert 'reasoning_details' not in result and 'tool_calls' not in result
    record = json.loads(result['content'].split('\n')[-1])[0]
    assert record['name'] == 'Write'
    assert record['arguments_preview'].startswith('{"content": "文')
    assert len(record['arguments_preview']) == ARGUMENT_PREVIEW_CHARS
    assert record['arguments_preview'].endswith('…')
    saved = saved_messages([raw, {'role': 'tool', 'content': 'discard me'}, {'role': 'user', 'content': 'after call'}])
    assert saved == [result, {'role': 'user', 'content': 'after call'}]


def test_compacted_tool_tail_survives_unload_reload_and_reopen(tmp_path):
    from test_task_contexts import command

    s = make_session(tmp_path)
    try:
        active = s.task_contexts.ensure('a')
        command(s, 'replace_context', {'context_id': active, 'content': 'evidence ' * 5000}, 'notes')
        s.submit('continue A', run_id='r')
        run(s.drain())
        call = s.store.mount(s.tree.id, s._leaf_id, {
            'role': 'assistant', 'content': 'tail step', 'task_context_id': active, 'run_id': 'r',
            'tool_calls': [{'id': 't', 'name': 'Read', 'arguments': {'path': 'critical-evidence.txt'}}]})
        result = s.store.mount(s.tree.id, call.id, {
            'role': 'tool_results', 'task_context_id': active, 'run_id': 'r',
            'results': [{'call_id': 't', 'name': 'Read', 'success': True, 'value': 'TAIL_RESULT_SECRET'}]})

        class Gateway:
            async def complete(self, messages, **kwargs):
                return {'content': 'task evidence summary'}

        s._plugin_service_values['model'] = Gateway()
        node, outcome = run(s._compact_at_node(
            result, context_limit=0, force=True, reason='manual', resume_model=False))
        assert outcome['compacted']
        before = s.task_contexts.read()['documents'][active]['messages']
        assert any(c.get('function', {}).get('name') == 'Read'
                   for m in before for c in m.get('tool_calls', []))
        assert 'TAIL_RESULT_SECRET' in str(before)
        command(s, 'unload_context', {'summary': 'paused'}, 'pause')
        command(s, 'load_context', {'context_id': active}, 'load')
        restored = s._messages(node.id)
        tail = next(m for m in restored if m.get('content', '').startswith('tail step'))
        records = json.loads(tail['content'].split('\n')[-1])
        assert records == [{'name': 'Read', 'arguments_preview': '{"path": "critical-evidence.txt"}'}]
        assert 'TAIL_RESULT_SECRET' not in str(restored)
        assert not any(m.get('tool_calls') or m.get('role') == 'tool' for m in restored)
        leaf = node.id
    finally:
        s.close()

    reopened = make_session(tmp_path)
    try:
        assert reopened._messages(leaf) == restored
    finally:
        reopened.close()


def test_oversized_restore_snapshot_does_not_rehydrate_discarded_results(tmp_path):
    import json
    from pathlib import Path
    import pytest
    from cyrene.core.context.capacity import ContextCapacityError
    from test_task_contexts import command
    s = make_session(tmp_path)
    try:
        active = s.task_contexts.ensure('a')
        user = s.store.mount(s.tree.id, s._leaf_id, {'role': 'user', 'content': 'A request'})
        call = s.store.mount(s.tree.id, user.id, {'role': 'assistant', 'content': 'A step',
            'task_context_id': active, 'tool_calls': [{'id': 't', 'name': 'Read',
            'arguments': {'path': 'x' * 800 + 'ARGUMENT_TAIL'}}]})
        result = s.store.mount(s.tree.id, call.id, {'role': 'tool_results', 'task_context_id': active,
            'results': [{'call_id': 't', 'name': 'Read', 'value': 'DISCARDED_RESULT_SECRET'}]})
        s._leaf_id = result.id
        command(s, 'append_context', {'context_id': active, 'content': 'task notes ' * 10000}, 'notes')
        command(s, 'unload_context', {'summary': 'paused'}, 'pause')
        s._configured_compaction_limit = lambda: 10000
        with pytest.raises(ContextCapacityError) as error:
            command(s, 'load_context', {'context_id': active}, 'load')
        saved = Path(error.value.details['snapshot_path']).read_text()
        assert 'DISCARDED_RESULT_SECRET' not in saved
        assert 'ARGUMENT_TAIL' not in saved
        records = json.loads(saved)['execution_records']
        assert [record['id'] for record in records] == [user.id, call.id]
    finally:
        s.close()
