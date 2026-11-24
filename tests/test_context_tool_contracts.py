"""All context tools: state transitions, protocol prefix stability and replay."""
from copy import deepcopy
import json

import pytest

from cyrene.core.context.tasks import fork_task_state, task_messages
from cyrene.model.protocol_adapters import prepare_request
from test_task_contexts import make_session, command, run


CASES = [
    ('append_active', 'append_context', 'active', {'content': 'new evidence'}, True, False),
    ('append_shared', 'append_context', 'shared', {'content': 'new agreement'}, True, False),
    ('append_inactive', 'append_context', 'inactive', {'content': 'other evidence'}, True, True),
    ('replace_active', 'replace_context', 'active', {'content': 'replacement'}, False, False),
    ('replace_shared', 'replace_context', 'shared', {'content': 'replacement'}, False, False),
    ('replace_inactive', 'replace_context', 'inactive', {'content': 'replacement'}, True, True),
    ('search', 'search_context', None, {'query': 'question'}, True, True),
    ('catalog_search', 'edit_context_catalog', None, {'action': 'search'}, True, True),
    ('catalog_update', 'edit_context_catalog', 'inactive', {'action': 'update', 'title': 'Renamed'}, False, False),
    ('catalog_noop', 'edit_context_catalog', 'active', {'action': 'update', 'title': 'B'}, True, True),
    ('archive', 'edit_context_catalog', 'inactive', {'action': 'archive'}, False, False),
    ('restore', 'edit_context_catalog', 'inactive', {'action': 'restore'}, False, False),
    ('unload', 'unload_context', None, {'summary': 'paused'}, False, False),
    ('load', 'load_context', 'inactive', {}, False, False),
    ('load_same', 'load_context', 'active', {}, True, True),
    ('switch_resume', 'switch_context', 'inactive', {'summary': 'paused'}, False, False),
    ('switch_new', 'switch_context', 'new', {'summary': 'paused', 'title': 'C'}, False, False),
]


def payload(messages, tools, adapter):
    if adapter == 'openai_chat':
        from cyrene.plugins.builtin.cyrene_model._shared import _openai_payload
        from cyrene.plugins.builtin.cyrene_model.openai_compatible import OPENAI_COMPATIBLE_PROVIDER
        return _openai_payload({'messages': messages, 'tools': tools}, OPENAI_COMPATIBLE_PROVIDER, 'beam-free')
    return prepare_request(adapter, api_key='local-test', model='test-model', messages=messages,
                           tools=tools, max_tokens=None, stream=True, response_format=None).payload


def without_cache_markers(value):
    # Anthropic intentionally moves its final cache breakpoint to the new end.
    # The marker is cache metadata, not a change in model-visible prompt content.
    if isinstance(value, dict):
        return {k: without_cache_markers(v) for k, v in value.items() if k != 'cache_control'}
    if isinstance(value, list):
        return [without_cache_markers(v) for v in value]
    return value


@pytest.mark.parametrize('case,tool,target,args,prefix,identical', CASES, ids=[c[0] for c in CASES])
def test_all_context_tools_state_and_wire_prefix(tmp_path, case, tool, target, args, prefix, identical):
    s = make_session(tmp_path)
    try:
        s.submit('question A', run_id='a')
        run(s.drain())
        a = s.task_contexts.read()['active']
        command(s, 'switch_context', {'context_id': None, 'title': 'B', 'summary': 'A paused'}, 'setup-b')
        s.submit('question B', run_id='b')
        run(s.drain())
        b = s.task_contexts.read()['active']
        command(s, 'replace_context', {'context_id': 'shared', 'content': 'existing agreement'}, 'setup-shared')
        if case == 'restore':
            command(s, 'edit_context_catalog', {'action': 'archive', 'context_id': a}, 'setup-archive')
        if case == 'load':
            command(s, 'unload_context', {'summary': 'B paused'}, 'setup-unload')
        before_state = s.task_contexts.read()
        before = s._messages(s._leaf_id)
        tools = deepcopy(s._direct_model_tool_definitions())
        arguments = dict(args)
        if target is not None:
            arguments['context_id'] = {'active': b, 'inactive': a, 'shared': 'shared', 'new': None}[target]
        receipt = command(s, tool, arguments, 'operation')
        after_state = s.task_contexts.read()
        after = s._messages(s._leaf_id)
        assert (after[:len(before)] == before) is prefix
        if identical:
            assert before == after
        assert s._direct_model_tool_definitions() == tools
        if tool in {'append_context', 'replace_context', 'search_context', 'edit_context_catalog'}:
            assert after_state['active'] == before_state['active']
        if tool in {'append_context', 'replace_context'}:
            changed_target = arguments['context_id']
            for key in (a, b):
                if key != changed_target:
                    assert after_state['documents'][key] == before_state['documents'][key]
        if tool == 'unload_context':
            assert after_state['active'] is None
        if tool in {'load_context', 'switch_context'}:
            assert after_state['active'] == receipt['context_id']
        for adapter, field in [('openai_chat', 'messages'), ('openai_responses', 'input'),
                               ('anthropic', 'messages'), ('gemini', 'contents')]:
            old = without_cache_markers(payload(before, tools, adapter))
            new = without_cache_markers(payload(after, tools, adapter))
            assert old.get('tools') == new.get('tools')
            assert (new[field][:len(old[field])] == old[field]) is prefix, (case, adapter)
        # Retrying committed mutations must not append a second record or switch again.
        if tool != 'search_context' and not (tool == 'edit_context_catalog' and args.get('action') == 'search'):
            assert command(s, tool, arguments, 'operation') == receipt
            assert s._messages(s._leaf_id) == after
    finally:
        s.close()


@pytest.mark.parametrize('tool,args', [
    ('append_context', {'context_id': 'missing', 'content': 'x'}),
    ('replace_context', {'context_id': 'missing', 'content': 'x'}),
    ('load_context', {'context_id': 'missing'}),
    ('load_context', {'context_id': 'shared'}),
    ('switch_context', {'context_id': 'missing', 'summary': 'leave'}),
    ('switch_context', {'context_id': None, 'title': 'new'}),
    ('search_context', {'query': ''}),
    ('edit_context_catalog', {'context_id': 'shared', 'action': 'archive'}),
])
def test_failed_tools_leave_body_and_request_unchanged(tmp_path, tool, args):
    s = make_session(tmp_path)
    try:
        s.submit('original request', run_id='r')
        run(s.drain())
        state = s.task_contexts.read()
        messages = s._messages(s._leaf_id)
        with pytest.raises(ValueError):
            command(s, tool, args, 'invalid')
        assert s.task_contexts.read() == state
        assert s._messages(s._leaf_id) == messages
    finally:
        s.close()


def test_replace_replay_retains_own_tool_receipt(tmp_path):
    calls = []
    async def model(arguments, context):
        calls.append(deepcopy(arguments['messages']))
        if len(calls) == 2:
            return {'tool_calls': [{'id': 'replace', 'name': 'replace_context', 'arguments': {
                'context_id': s.task_contexts.read()['active'], 'content': 'replacement evidence'}}]}
        return {'content': 'answer'}
    s = make_session(tmp_path, model)
    try:
        s.submit('old question', run_id='old')
        run(s.drain())
        s.submit('replace previous context', run_id='new')
        run(s.drain())
        assert len(calls) == 3
        path = s.store.get_path(s.tree.id, s._leaf_id)
        index = next(i for i, n in enumerate(path) if n.value.get('role') == 'tool_results')
        path = path[:index + 1]
        state = fork_task_state(path)
        from cyrene.core.context.tasks import project_tasks
        projected = project_tasks(path, state)
        assert any(m.get('tool_call_id') == 'replace' for m in projected)
        assert 'replacement evidence' in str(projected)
        assert 'old question' not in str(projected)
    finally:
        s.close()


def test_unload_drops_compacted_tool_results_without_losing_append(tmp_path):
    s = make_session(tmp_path)
    try:
        active = s.task_contexts.ensure('a')
        state = s.task_contexts.read()
        state['documents'][active]['body'] = [
            {'message': {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': 'old', 'type': 'function', 'function': {'name': 'Read', 'arguments': '{}'}}]}},
            {'message': {'role': 'tool', 'tool_call_id': 'old', 'content': 'old full tool result'}}]
        s.task_contexts.write(state)
        command(s, 'append_context', {'context_id': active, 'content': 'retained evidence'}, 'append')
        command(s, 'unload_context', {'summary': 'paused'}, 'unload')
        command(s, 'load_context', {'context_id': active}, 'load')
        projected = s._messages(s._leaf_id)
        assert not any(m['role'] == 'tool' for m in projected)
        assert 'old full tool result' not in str(projected)
        assert 'retained evidence' in str(projected)
    finally:
        s.close()


def test_task_export_retires_historical_images(tmp_path):
    s = make_session(tmp_path)
    try:
        node = s.store.mount(s.tree.id, s._leaf_id, {'role': 'user', 'content': 'old image',
            'run_id': 'old', 'metadata': {'input_images': [{'path': '/test/old.png'}]}})
        s._leaf_id = node.id
        active = s.task_contexts.ensure('a')
        command(s, 'unload_context', {'summary': 'paused'}, 'unload')
        command(s, 'load_context', {'context_id': active}, 'load')
        node = s.store.mount(s.tree.id, s._leaf_id, {'role': 'user', 'content': 'new question', 'run_id': 'new'})
        s._leaf_id = node.id
        messages = task_messages(s.store.get_path(s.tree.id, node.id), s.task_contexts.read(), active)
        assert 'cyrene_local_image' not in json.dumps(messages)
        assert '/test/old.png' in str(messages)
    finally:
        s.close()


@pytest.mark.parametrize('tool', ['load_context', 'switch_context'])
def test_same_context_transition_does_not_evict_images_or_change_prefix(tmp_path, tool):
    s = make_session(tmp_path)
    try:
        image = s.store.mount(s.tree.id, s._leaf_id, {'role': 'user', 'content': 'image question',
            'run_id': 'old', 'metadata': {'input_images': [{'path': '/test/old.png'}]}})
        s._leaf_id = image.id
        active = s.task_contexts.ensure('a')
        later = s.store.mount(s.tree.id, image.id, {'role': 'user', 'content': 'continue', 'run_id': 'new'})
        s._leaf_id = later.id
        before = s._messages(later.id)
        assert 'cyrene_local_image' in json.dumps(before)
        command(s, tool, {'context_id': active}, 'same-context')
        assert s._messages(later.id) == before
        assert image.id not in s.task_contexts.read().get('retired_image_nodes', [])
    finally:
        s.close()
