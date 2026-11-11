"""Task result reads must be bounded, owned, and tied to the requested execution."""

from types import SimpleNamespace

import pytest

from cyrene.plugins.builtin.session_management import plugin_pack
from cyrene.plugins.builtin.session_management.host import WorkbenchHost
from cyrene.plugins.builtin.session_management.inspection import content_page
from test_session_management import Host, create, service_at
from test_task_contexts import make_session


@pytest.mark.asyncio
async def test_inspection_directory_tail_boundaries_and_revision(tmp_path):
    host = Host(tmp_path)
    host.snapshot['blocks'][0]['content'] = '甲乙🙂丙丁'
    host.snapshot['blocks'][1]['content'] = ''
    service = await service_at(tmp_path, host)
    try:
        target = (await create(service))['session_id']
        base = {'session_id': target}
        directory = await service.blocks('a', base)
        assert directory['blocks'][0]['content_length'] == 5
        assert directory['blocks'][1]['is_empty'] is True
        args = {**base, 'block_ids': ['task:one'], 'anchor': 'end', 'content_limit': 3}
        tail = (await service.blocks('a', args))['blocks'][0]
        assert tail['content'] == '🙂丙丁'
        assert (tail['content_offset'], tail['content_end']) == (2, 5)
        assert tail['has_before'] and not tail['has_after']
        assert tail['next_content_offset'] is None
        for offset, status in [(5, 'end_of_content'), (999999999, 'out_of_range')]:
            page = (await service.blocks('a', {**base, 'block_ids': ['task:one'],
                                               'content_offset': offset}))['blocks'][0]
            assert page['status'] == status and page['content'] == ''
            if status == 'out_of_range':
                assert page['suggested_tail_offset'] == 0
                assert page['content_end'] is None
        empty = (await service.blocks('a', {**base, 'block_ids': ['task:two']}))['blocks'][0]
        assert empty['status'] == 'empty'
        first = (await service.blocks('a', {**base, 'block_ids': ['task:one'], 'content_limit': 2}))['blocks'][0]
        assert first['next_content_offset'] == 2 and first['truncated']
        host.snapshot['revision'] = 'r2'
        stale = await service.blocks('a', {**base, 'block_ids': ['task:one'],
                                           'content_offset': 2, 'expected_revision': 'r1'})
        assert stale['status'] == 'stale_revision' and 'blocks' not in stale
        with pytest.raises(ValueError, match='not owned'):
            await service.blocks('b', {**base, 'view': 'latest_result'})
        for extra in [
            {'view': 'content'}, {'anchor': 'end'},
            {'view': 'latest_result', 'block_ids': ['task:one']},
            {'block_ids': ['task:one'], 'anchor': 'end', 'content_offset': 0},
            {'node_id': 'some-node'},
        ]:
            with pytest.raises(ValueError):
                await service.blocks('a', {**base, **extra})
    finally:
        await service.stop()


@pytest.mark.parametrize('args', [{'content_limit': 0}, {'content_limit': 20001},
                                  {'content_offset': -1}, {'content_offset': True}])
def test_page_rejects_invalid_ranges(args):
    with pytest.raises(ValueError):
        content_page('data', args)


def real_host(tmp_path, session):
    host = WorkbenchHost(lambda: SimpleNamespace(context=SimpleNamespace(
        conversation_context=SimpleNamespace(agent_states=SimpleNamespace(
            context_directory=tmp_path / 'data' / 'context')))))
    host.status = lambda _: 'idle'
    host.chat = lambda _: {'title': 'Task'}
    return host


def test_final_reply_is_pinned_bounded_and_never_progress(tmp_path):
    session = make_session(tmp_path)
    host = real_host(tmp_path, session)
    try:
        active = session.task_contexts.ensure('task')
        old = session.store.mount(session.tree.id, session.tree.root_id, {
            'role': 'assistant', 'run_id': 'old', 'content': 'OLD',
            'task_context_id': active, 'answer_complete': True})
        terminal = session.store.mount(session.tree.id, old.id, {
            'role': 'assistant', 'run_id': 'completed', 'content': '甲' * 7000,
            'task_context_id': active, 'session_end_complete': True})
        original = host.latest_result(session.tree.id, terminal.id)
        assert original['status'] == 'completed' and original['content'] == '甲' * 7000
        event = host.notification(session.tree.id, terminal.id)
        assert event['final_reply'] == '甲' * 6000
        assert event['final_reply_length'] == 7000 and event['final_reply_truncated']
        assert event['final_reply_next_offset'] == 6000
        progress = session.store.mount(session.tree.id, terminal.id, {
            'role': 'assistant', 'run_id': 'next', 'content': 'Working',
            'task_context_id': active, 'tool_calls': [{'id': 'c1', 'name': 'Bash', 'arguments': {}}]})
        latest = host.latest_result(session.tree.id)
        assert latest['status'] == 'running' and latest['content'] == ''
        assert host.latest_result(session.tree.id, terminal.id) == original
        assert host.latest_result(session.tree.id, 'missing')['status'] == 'not_found'
        failed = session.store.mount(session.tree.id, progress.id, {
            'role': 'assistant', 'run_id': 'next', 'content': 'ERROR', 'error': True})
        assert host.latest_result(session.tree.id, failed.id)['content'] == ''
        assert host.latest_result(session.tree.id)['status'] == 'failed'
        cancelled = session.store.mount(session.tree.id, failed.id, {
            'role': 'assistant', 'run_id': 'cancelled', 'cancelled': True})
        assert host.latest_result(session.tree.id, cancelled.id)['status'] == 'cancelled'
        host.status = lambda _: 'running'
        assert host.latest_result(session.tree.id)['status'] == 'running'
        assert host.latest_result(session.tree.id, terminal.id) == original
    finally:
        session.close()


@pytest.mark.asyncio
async def test_latest_result_service_pages_exact_reply_and_exposes_state(tmp_path):
    session = make_session(tmp_path)
    host = Host(tmp_path)
    source = real_host(tmp_path, session)
    host.latest_result = lambda _target, node_id: source.latest_result(session.tree.id, node_id)
    # The latest-result path must not serialize all task execution blocks.
    host.blocks = lambda _: pytest.fail('unexpected full task block projection')
    service = await service_at(tmp_path, host)
    try:
        target = (await create(service))['session_id']
        active = session.task_contexts.ensure('task')
        terminal = session.store.mount(session.tree.id, session.tree.root_id, {
            'role': 'assistant', 'run_id': 'r1', 'content': 'abcdef',
            'task_context_id': active, 'answer_complete': True})
        args = {'session_id': target, 'view': 'latest_result', 'content_limit': 2}
        first = await service.blocks('a', args)
        assert first['status'] == 'ok' and first['execution_status'] == 'completed'
        assert first['content'] == 'ab' and first['node_id'] == terminal.id
        next_args = {**args, 'node_id': first['node_id'], 'expected_revision': first['revision'],
                     'content_offset': first['next_content_offset']}
        next_page = await service.blocks('a', next_args)
        assert next_page['content'] == 'cd'
        stale = await service.blocks('a', {**next_args, 'expected_revision': 'old'})
        assert stale['status'] == 'stale_revision' and 'content' not in stale
        source.status = lambda _: 'awaiting_user'
        waiting = await service.blocks('a', args)
        assert waiting['status'] == 'awaiting_user' and 'content' not in waiting
        assert (await service.blocks('a', next_args))['content'] == 'cd'
        empty = session.store.mount(session.tree.id, terminal.id, {
            'role': 'assistant', 'run_id': 'empty', 'content': '', 'answer_complete': True})
        assert (await service.blocks('a', {**args, 'node_id': empty.id}))['status'] == 'empty'
        inspect_tool = next(p for p in plugin_pack.plugins if p.name == 'task_inspect')
        assert inspect_tool.input_schema['properties']['view']['enum'] == ['directory', 'content', 'latest_result']
    finally:
        await service.stop()
        session.close()
