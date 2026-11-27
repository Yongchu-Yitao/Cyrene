"""Status projection retains recovery semantics without loading large histories."""
import json
import sqlite3

import pytest

from cyrene.core.context import ContextStoreRouter, ContextTreeStore
from cyrene.core.context.checkpoint_reads import checkpoint_snapshot
from cyrene.workbench.core_adapter.checkpoint import context_checkpoint_from_nodes
from cyrene.workbench.core_adapter.conversation_runtime import ConversationRuntime
from cyrene.workbench.chat.context_read_cache import ContextReadCache


@pytest.fixture
def state(tmp_path, monkeypatch):
    cache = ContextReadCache()
    monkeypatch.setattr('cyrene.workbench.chat.context_read_cache.CONTEXT_READ_CACHE', cache)
    runtime = ConversationRuntime()
    monkeypatch.setattr(runtime, '_state_root', lambda config=None: tmp_path)
    with ContextStoreRouter(tmp_path / 'context') as router:
        tree = router.create_tree({'role': 'system', 'content': 'x' * 1_000_000}, tree_id='task')
        user = router.mount(tree.id, tree.root_id, {'role': 'user', 'run_id': 'run', 'content': 'request'})
        yield router, tree, user, runtime
    cache.close()


@pytest.mark.parametrize('value', [
    {'role': 'assistant', 'answer_complete': True},
    {'role': 'assistant', 'session_end_complete': True},
    {'role': 'assistant', 'answer_complete': False},
    {'role': 'assistant', 'answer_complete': 1},
    {'role': 'assistant', 'cancelled': True, 'cancel_reason': 'process_restarted'},
    {'role': 'assistant', 'error': True, 'failure_kind': 'model_rate_limited'},
    {'role': 'assistant', 'error': True, 'failure_kind': 'model_rate_limited', 'retry_at': 123},
    {'role': 'context_reflection', 'resume_model': False},
    {'role': 'context_compaction', 'resume_model': True},
    {'role': 'tool_results', 'trigger_model': False, 'pending_question': {
        'id': 'q', 'text': 'Choose', 'options': ['yes', 'no'], 'plan': {'steps': ['one']}}},
])
def test_checkpoint_matches_full_tree_without_content(state, value, monkeypatch):
    router, tree, user, runtime = state
    router.mount(tree.id, user.id, {**value, 'run_id': 'run', 'content': 'y' * 1_000_000})
    full = context_checkpoint_from_nodes(router.get_subtree(tree.id, tree.root_id))
    nodes, committed = checkpoint_snapshot(router, tree.id)
    assert all('content' not in node.value for node in nodes)
    assert sum(len(json.dumps(node.value)) for node in nodes) < 1000
    assert context_checkpoint_from_nodes(nodes, committed) == full
    monkeypatch.setattr(ContextTreeStore, 'get_subtree', lambda *args: pytest.fail('full history read'))
    assert runtime.context_checkpoint(tree.id) == full
    assert runtime.context_checkpoint(tree.id) == full


def test_cross_connection_updates_commit_pointer_and_delete_invalidate_cache(state):
    router, tree, user, runtime = state
    answer = router.mount(tree.id, user.id, {'role': 'assistant', 'run_id': 'run', 'answer_complete': True})
    router.commit_state(tree.id, answer.id, 'run')
    assert runtime.context_checkpoint(tree.id)['node_id'] == answer.id
    with ContextStoreRouter(router.directory) as writer:
        retry = writer.mount(tree.id, user.id, {'role': 'user', 'run_id': 'retry', 'metadata': {'retry': True}})
        failed = writer.mount(tree.id, retry.id, {'role': 'assistant', 'run_id': 'retry', 'error': True})
        writer.mount(tree.id, failed.id, {'role': 'tool_results', 'run_id': 'retry'})
        # A failed retry and late output keep the committed branch selected.
        assert runtime.context_checkpoint(tree.id)['node_id'] == answer.id
        writer.commit_state(tree.id, failed.id, 'retry')
        assert runtime.context_checkpoint(tree.id)['status'] == 'failed'
        writer.delete_node(tree.id, retry.id, recursive=True)
        assert runtime.context_checkpoint(tree.id)['node_id'] == answer.id
        writer.update_node(tree.id, answer.id, {'role': 'assistant', 'run_id': 'run', 'cancelled': True})
        assert runtime.context_checkpoint(tree.id)['status'] == 'cancelled'
    assert runtime.context_checkpoints(['task', 'missing'])['missing'] is None


def test_projection_migrates_once_and_rolls_back_with_source(state):
    router, tree, user, runtime = state
    path = router.tree_database_path(tree.id)
    router.close()
    with sqlite3.connect(path) as db:
        for name in ('insert', 'update', 'delete'):
            db.execute(f'DROP TRIGGER checkpoint_node_{name}')
        db.execute('DROP TABLE context_checkpoint_nodes')
    # An existing database upgrades on first open, including old content.
    assert runtime.context_checkpoint(tree.id)['status'] == 'running'
    observer = sqlite3.connect(path)
    before = observer.execute('PRAGMA data_version').fetchone()[0]
    assert runtime.context_checkpoint(tree.id)['status'] == 'running'
    assert observer.execute('PRAGMA data_version').fetchone()[0] == before
    with sqlite3.connect(path) as writer:
        writer.execute('BEGIN')
        writer.execute('UPDATE context_nodes SET value_json=? WHERE node_id=?',
                       (json.dumps({'role': 'assistant', 'error': True}), user.id))
        assert json.loads(writer.execute('SELECT value_json FROM context_checkpoint_nodes WHERE node_id=?',
                                         (user.id,)).fetchone()[0])['error'] is True
        writer.rollback()
    assert runtime.context_checkpoint(tree.id)['status'] == 'running'
    observer.close()


def test_projection_does_not_turn_nonobject_values_into_dialogue(state):
    router, tree, user, runtime = state
    router.mount(tree.id, user.id, ['role', 'assistant'])
    assert runtime.context_checkpoint(tree.id)['node_id'] == user.id


def test_authorizations_match_original_run_id_normalization_across_branches(state):
    from cyrene.core.context.checkpoint_reads import run_authorization_nodes

    router, tree, user, _ = state
    for run_id in (42, "42", "042", 42.0, 2**64, True, False, 0, "0", 1e-7, None):
        router.mount(tree.id, tree.root_id, {"role": "user", "run_id": run_id})
    router.mount(tree.id, user.id, {"role": "context_reflection", "run_id": 42})
    router.mount(tree.id, user.id, {"role": "assistant", "run_id": "42"})
    history = sorted(router.get_subtree(tree.id, tree.root_id), key=lambda node: (node.created_at, node.id))
    for target in ("42", "042", "42.0", str(2**64), "True", "1", "False", "0", "1e-07", "missing"):
        expected = [n for n in history if n.value.get("role") in {"user", "context_reflection"}
                    and str(n.value.get("run_id") or "") == target]
        assert run_authorization_nodes(router, tree.id, target) == expected
