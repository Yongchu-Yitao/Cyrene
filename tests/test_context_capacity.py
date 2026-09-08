"""Capacity boundaries preserve task identity, evidence and append-only input."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from cyrene.core.context.capacity import ContextCapacityError
from cyrene.core.hook import MODEL_START
from test_task_contexts import make_session, run, command


def prepare(s):
    node = s.store.get_node(s.tree.id, s._leaf_id)
    contributions = run(s.hooks.model_start_mounts({'node_id': node.id}))
    for item in contributions:
        node = s.store.mount(s.tree.id, node.id, {
            'role': 'context', 'content': item['context'],
            'context_kind': item['context_kind'], 'context_source': item['context_source'],
            'context_lifecycle': 'model', 'run_id': node.value.get('run_id', ''),
            'trigger_model': False,
        })
        s._leaf_id = node.id
    return s._prepare_model_input(node)


def warnings(s):
    return [n for n in s.store.get_path(s.tree.id, s._leaf_id)
            if str(n.value.get('context_kind', '')).startswith('task_capacity.')]


def text(value):
    return ''.join(value['text_chunks']) if isinstance(value, dict) else value


def user(s, content, run_id):
    node = s.store.mount(s.tree.id, s._leaf_id, {'role': 'user', 'content': content, 'run_id': run_id})
    s._leaf_id = node.id
    return node


def test_warning_once_and_stable_prefix_when_task_continues(tmp_path):
    s = make_session(tmp_path)
    try:
        user(s, 'continue the implementation', 'r')
        active = s.task_contexts.ensure('a')
        command(s, 'append_context', {'context_id': active, 'content': 'working evidence ' * 2400}, 'body')
        initial = s._prepare_model_input(s.store.get_node(s.tree.id, s._leaf_id))
        s._configured_compaction_limit = lambda: int(initial.compaction_tokens / .55)
        first = prepare(s)
        assert 'Context capacity:' in first.messages[-1]['content']
        assert first.messages[:-1] == initial.messages
        state = s.task_contexts.read()
        assert len(warnings(s)) == 1
        assert s.hooks.list(MODEL_START)
        assert prepare(s).messages == first.messages
        assert s.task_contexts.read() == state
        n = s.store.mount(s.tree.id, s._leaf_id, {'role': 'assistant', 'content': 'continuing',
                          'task_context_id': active, 'run_id': 'r'})
        s._leaf_id = n.id
        assert prepare(s).messages[:len(first.messages)] == first.messages
        assert len(warnings(s)) == 1
    finally:
        s.close()


def test_oversized_load_preserves_state_and_exports_complete_evidence(tmp_path):
    s = make_session(tmp_path)
    try:
        user(s, 'task A', 'r')
        active = s.task_contexts.ensure('a')
        body = 'old evidence ' * 5000
        command(s, 'append_context', {'context_id': active, 'content': body}, 'body')
        command(s, 'unload_context', {'summary': 'A unfinished; continue analysis'}, 'pause')
        before = s.task_contexts.read()
        s._configured_compaction_limit = lambda: 10000
        with pytest.raises(ContextCapacityError) as error:
            command(s, 'load_context', {'context_id': active}, 'too-large')
        assert s.task_contexts.read() == before
        info = error.value.details
        assert info['estimated_tokens'] > info['threshold_tokens'] == 6000
        snapshot = json.loads(Path(info['snapshot_path']).read_text())
        assert snapshot['context_id'] == active
        assert text(snapshot['document']['body']) == body
        assert snapshot['document']['summary'] == 'A unfinished; continue analysis'
        assert 'execution_records' in snapshot
        s._configured_compaction_limit = lambda: 1000000
        command(s, 'load_context', {'context_id': active}, 'fits')
        assert s.task_contexts.read()['active'] == active
    finally:
        s.close()


def test_shared_offload_keeps_task_dialogue_and_shared_edits(tmp_path):
    s = make_session(tmp_path)
    try:
        old = 'historical public material ' * 1500
        shared = 'common contract evidence ' * 900
        user(s, old, 'old')
        s.task_contexts.ensure('a')
        user(s, 'current exact request', 'new')
        command(s, 'append_context', {'context_id': 'shared', 'content': shared}, 'shared')
        before = s.task_contexts.read()
        s._configured_compaction_limit = lambda: 16000
        prepared = prepare(s)
        state = s.task_contexts.read()
        assert state['active'] == before['active']
        assert state['shared']['body'] == shared
        assert 'current exact request' in str(prepared.messages)
        assert old[:300] in str(prepared.messages)
        assert shared[:300] not in str(prepared.messages)
        original = Path(state['shared_offload']['snapshot_path'])
        assert text(json.loads(original.read_text())['body']) == shared
        assert not state.get('shared_snapshot')
        assert prepare(s).messages == prepared.messages
        command(s, 'append_context', {'context_id': 'shared', 'content': 'new explicit constraint'}, 'edit')
        assert 'new explicit constraint' in str(s._messages(s._leaf_id))
        prepare(s)
        current = s.task_contexts.read()
        assert current['shared_offload']['snapshot_path'] != str(original)
        assert original.exists()
        assert text(json.loads(Path(current['shared_offload']['snapshot_path']).read_text())['body']).endswith('new explicit constraint')
    finally:
        s.close()


def test_no_capacity_changes_below_warning_or_without_limit(tmp_path):
    s = make_session(tmp_path)
    try:
        user(s, 'small task', 'r')
        initial = s._prepare_model_input(s.store.get_node(s.tree.id, s._leaf_id))
        before = deepcopy(s.task_contexts.read())
        assert prepare(s).messages == initial.messages
        assert s.task_contexts.read() == before
        s._configured_compaction_limit = lambda: 1000000
        assert prepare(s).messages == initial.messages
        assert s.task_contexts.read() == before
    finally:
        s.close()


def test_model_receives_warning_and_continues_in_new_segment(tmp_path):
    seen = []

    async def model(arguments, context):
        seen.append(deepcopy(arguments['messages']))
        if len(seen) == 1:
            assert 'Context capacity:' in str(arguments['messages'])
            return {'content': '', 'tool_calls': [{'id': 'pause', 'name': 'unload_context',
                    'arguments': {'summary': 'Implementation unfinished; continue from saved task evidence.'}}]}
        return {'content': 'continuing the implementation', 'tool_calls': []}

    s = make_session(tmp_path, model)
    try:
        original = s.task_contexts.ensure('original')
        body = 'implementation evidence ' * 2000
        command(s, 'append_context', {'context_id': original, 'content': body}, 'body')
        initial = s._prepare_model_input(s.store.get_node(s.tree.id, s._leaf_id))
        s._configured_compaction_limit = lambda: int(initial.compaction_tokens / .55)
        s.submit('Continue this implementation.', run_id='continue')
        run(s.drain())
        state = s.task_contexts.read()
        assert len(seen) == 2
        assert len(state['documents']) == 2
        assert state['active'] != original
        assert state['documents'][original]['body'] == body
        assert state['documents'][original]['summary'].startswith('Implementation unfinished')
        assert not any(n.value.get('role') == 'context_compaction'
                       for n in s.store.get_subtree(s.tree.id, s.tree.root_id))
    finally:
        s.close()


def test_capacity_mount_survives_compacted_anchor_and_switches(tmp_path):
    s = make_session(tmp_path)
    try:
        user(s, 'task', 'r')
        active = s.task_contexts.ensure('a')
        command(s, 'append_context', {'context_id': active, 'content': 'working evidence ' * 2400}, 'body')
        call = s.store.mount(s.tree.id, s._leaf_id, {
            'role': 'assistant', 'content': '', 'task_context_id': active, 'run_id': 'r',
            'tool_calls': [{'id': 'read', 'name': 'Read', 'arguments': {'path': 'example'}}],
        })
        result = s.store.mount(s.tree.id, call.id, {
            'role': 'tool_results', 'task_context_id': active, 'run_id': 'r',
            'results': [{'call_id': 'read', 'value': 'evidence'}],
        })
        s._leaf_id = result.id
        initial = s._prepare_model_input(result)
        s._configured_compaction_limit = lambda: int(initial.compaction_tokens / .55)
        assert prepare(s).messages[:-1] == initial.messages
        class Gateway:
            async def complete(self, messages, **kwargs):
                return {'content': 'compacted task evidence'}
        s._plugin_service_values['model'] = Gateway()
        trigger = s.store.get_node(s.tree.id, s._leaf_id)
        node, outcome = run(s._compact_at_node(trigger, context_limit=0, force=True,
                                               reason='manual', resume_model=False))
        assert outcome['compacted']
        s._leaf_id = node.id
        assert call.id in s.task_contexts.read()['documents'][active]['covered']
        assert 'Context capacity:' in str(prepare(s).messages)
        assert len(warnings(s)) == 1
        command(s, 'unload_context', {'summary': 'continue in new segment'}, 'pause')
        s.task_contexts.ensure('next')
        assert 'Context capacity:' not in str(prepare(s).messages)
    finally:
        s.close()


def test_blocked_snapshot_excludes_abandoned_branch(tmp_path):
    s = make_session(tmp_path)
    try:
        root = user(s, 'task', 'r')
        active = s.task_contexts.ensure('a')
        abandoned = s.store.mount(s.tree.id, root.id, {
            'role': 'assistant', 'content': 'abandoned evidence', 'task_context_id': active,
        })
        chosen = s.store.mount(s.tree.id, root.id, {
            'role': 'assistant', 'content': 'chosen evidence', 'task_context_id': active,
        })
        s._leaf_id = chosen.id
        command(s, 'append_context', {'context_id': active, 'content': 'evidence ' * 10000}, 'body')
        command(s, 'unload_context', {'summary': 'paused'}, 'pause')
        before = s.task_contexts.read()
        s._configured_compaction_limit = lambda: 10000
        with pytest.raises(ContextCapacityError) as error:
            command(s, 'load_context', {'context_id': active}, 'load')
        records = json.loads(Path(error.value.details['snapshot_path']).read_text())['execution_records']
        assert chosen.id in {record['id'] for record in records}
        assert abandoned.id not in {record['id'] for record in records}
        assert s.task_contexts.read() == before
    finally:
        s.close()


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_shared_snapshot_repairs_without_capacity_pressure(tmp_path, damage):
    s = make_session(tmp_path)
    try:
        user(s, 'task', 'r')
        body = 'common constraints with sources ' * 2000
        command(s, 'append_context', {'context_id': 'shared', 'content': body}, 'body')
        s._configured_compaction_limit = lambda: 10000
        prepare(s)
        path = Path(s.task_contexts.read()['shared_offload']['snapshot_path'])
        original = path.read_bytes()
        if damage == 'missing':
            path.unlink()
        else:
            path.write_text('corrupt')
        # Projection never hides authoritative constraints behind an invalid path.
        assert body in str(s._messages(s._leaf_id))
        s._configured_compaction_limit = lambda: 0
        prepared = prepare(s)
        assert path.read_bytes() == original
        assert body not in str(prepared.messages)
        assert str(path) in str(prepared.messages)
    finally:
        s.close()
