"""Branch graph uses real SQLite trees and the existing fork runtime."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from cyrene.core.context import ContextStoreRouter
from cyrene.workbench.chat.chat_repository import ChatRepository
from cyrene.workbench.chat.context_graph_service import ContextGraphService
from cyrene.workbench.chat.context_graph_store import GraphConflict
from cyrene.workbench.core_adapter.conversation_runtime import ConversationRuntime


@pytest.fixture
def graph_env(tmp_path):
    db = str(tmp_path / 'workbench.sqlite3')
    repository = ChatRepository(db)
    runtime = ConversationRuntime(db)
    runtime._state_root = lambda *args: tmp_path / 'data'
    service = SimpleNamespace(repository=repository, run_manager=SimpleNamespace(conversation_runtime=runtime),
        short_id=lambda prefix: prefix + '_' + uuid4().hex[:12], utc_now_iso=lambda: '2026-09-21T00:00:00+00:00',
        create_chat=lambda project, title, model: {'projectId': project, 'title': title, 'model': model},
        public_chat_full=lambda chat: dict(chat))
    directory = tmp_path / 'data' / 'context'
    with ContextStoreRouter(directory) as router:
        tree = router.create_tree({'role': 'system', 'content': 'system', 'permission_session_grants': ['old-grant']}, tree_id='source')
        user = router.mount(tree.id, tree.root_id, {'role': 'user', 'content': 'original question', 'run_id': 'r1', 'metadata': {'turn_id': 'message-1'}})
        answer = router.mount(tree.id, user.id, {'role': 'assistant', 'content': 'original answer', 'run_id': 'r1', 'answer_complete': True, 'session_end_complete': True})
        router.commit_state(tree.id, answer.id, 'r1')
    repository.insert({'id': 'source', 'projectId': 'project', 'title': 'Source', 'model': 'fake', 'messages': [
        {'id': 'message-1', 'role': 'user', 'content': 'original question'}, {'id': 'message-2', 'role': 'assistant', 'content': 'original answer'}]})
    graph = ContextGraphService(service, directory, db)
    return SimpleNamespace(graph=graph, service=service, directory=directory, user=user, answer=answer, root=tree.root_id)


def operation(env, node, mode='continue', **kwargs):
    return {'nodeId': node.id, 'mode': mode, 'operationId': uuid4().hex,
            'expectedRevision': env.graph.snapshot('source')[2], **kwargs}


def test_graph_read_includes_retry_and_committed_path(graph_env):
    e = graph_env
    with ContextStoreRouter(e.directory) as r:
        retry = r.mount('source', e.root, {'role': 'user', 'content': 'question', 'run_id': 'r2', 'metadata': {'retry': True, 'turn_id': 'message-1'}})
        failed = r.mount('source', retry.id, {'role': 'assistant', 'content': 'failed', 'run_id': 'r2', 'error': True})
    graph = e.graph.graph('source')
    assert graph['branches'][0]['leafId'] == e.answer.id
    assert next(n for n in graph['nodes'] if n['nodeId'] == failed.id)['historical']
    assert any(edge['kind'] == 'retry' for edge in graph['edges'])


def test_edit_forks_without_mutating_source_and_is_idempotent(graph_env):
    e = graph_env
    body = operation(e, e.user, 'edit', content='new question')
    result = e.graph.materialize('source', body)
    assert result['replay']
    assert result['chat']['messages'][-1]['content'] == 'new question'
    assert e.graph.materialize('source', body)['chat']['id'] == result['chat']['id']
    assert e.graph.detail('source', e.user.id)['content'] == 'original question'
    assert len(e.service.repository.read()['chats']) == 2
    with ContextStoreRouter(e.directory) as r:
        target = r.get_tree(result['chat']['id'])
        assert 'permission_session_grants' not in r.get_node(target.id, target.root_id).value
    with pytest.raises(GraphConflict):
        e.graph.materialize('source', {**body, 'content': 'different operation'})


def test_continuation_is_idle_and_contains_selected_history(graph_env):
    e = graph_env
    target = e.graph.materialize('source', operation(e, e.answer))['chat']
    with ContextStoreRouter(e.directory) as r:
        nodes = r.get_subtree(target['id'], e.root)
        leaf, run = r.committed_state(target['id'])
        assert leaf and run
        assert r.get_node(target['id'], leaf).value['session_end_complete']
        assert any(n.value.get('content') == 'original answer' for n in nodes)
    assert e.graph.graph(target['id'])['familyId'] == 'source'


def test_stale_revision_rejected_before_creating_chat(graph_env):
    e = graph_env
    body = operation(e, e.answer)
    with ContextStoreRouter(e.directory) as r:
        r.update_node('source', e.answer.id, {**e.answer.value, 'content': 'changed'})
    with pytest.raises(GraphConflict):
        e.graph.materialize('source', body)
    assert len(e.service.repository.read()['chats']) == 1


def test_view_cas_persists_across_instances(graph_env):
    e = graph_env
    store = e.graph.store
    assert store.write('view:source', {'collapsed': ['x']}, 0)['revision'] == 1
    with pytest.raises(GraphConflict):
        store.write('view:source', {}, 0)
    assert type(store)(store.db_path).read('view:source')['value']['collapsed'] == ['x']


def test_missing_source_keeps_family_identity(graph_env):
    e = graph_env
    child = e.graph.materialize('source', operation(e, e.answer))['chat']
    e.service.repository.write({'chats': [child]})
    graph = e.graph.graph(child['id'])
    assert graph['familyId'] == 'source'
    assert any(n['role'] == 'origin' for n in graph['nodes'])


def test_tool_boundary_cannot_be_continued(graph_env):
    e = graph_env
    with pytest.raises(ValueError, match='completed reply'):
        e.graph.materialize('source', operation(e, e.user))


def test_manual_reply_revision_updates_transcript_and_tree(graph_env):
    e = graph_env
    target = e.graph.materialize('source', operation(e, e.answer, 'edit', content='manual answer'))['chat']
    assert target['messages'][-1]['content'] == 'manual answer'
    detail = e.graph.detail(target['id'], e.answer.id)
    assert detail['content'] == 'manual answer'
    assert detail['value']['manual_revision']


def test_atomic_node_revision_rejects_second_writer(graph_env):
    e = graph_env
    with ContextStoreRouter(e.directory) as r:
        first = r.get_node('source', e.answer.id)
        r.update_node('source', first.id, {**first.value, 'content': 'writer one'}, expected_updated_at=first.updated_at.isoformat())
        with pytest.raises(ValueError, match='changed before'):
            r.update_node('source', first.id, {**first.value, 'content': 'writer two'}, expected_updated_at=first.updated_at.isoformat())
        assert r.get_node('source', first.id).value['content'] == 'writer one'


def test_task_document_edit_is_branch_local_and_replayable(graph_env):
    from cyrene.core.context.tasks import STATE_KEY, fork_task_state
    e = graph_env
    with ContextStoreRouter(e.directory) as r:
        root = r.get_node('source', e.root)
        value = dict(root.value)
        value[STATE_KEY] = {'active': None, 'documents': {}, 'shared': {'body': ''}, 'receipts': {}}
        r.update_node('source', e.root, value)
    body = operation(e, e.answer, 'edit', content='new shared contract')
    body['nodeId'] = 'task:shared'
    target = e.graph.materialize('source', body)['chat']['id']
    assert e.graph.detail(target, 'task:shared')['content'] == 'new shared contract'
    assert e.graph.detail('source', 'task:shared')['content'] == ''
    with ContextStoreRouter(e.directory) as r:
        leaf = r.committed_state(target)[0]
        assert fork_task_state(r.get_path(target, leaf))['shared']['body'] == 'new shared contract'


def test_saved_user_draft_reopens_idle_and_replays_only_once(graph_env, tmp_path):
    from test_task_contexts import make_session, run
    from cyrene.workbench.core_adapter.bridge import WorkbenchSessionBridge
    e = graph_env
    target = e.graph.materialize('source', operation(e, e.user, 'edit', content='draft request'))['chat']['id']
    session = make_session(tmp_path, tree_id=target)
    try:
        assert session.is_idle
        assert WorkbenchSessionBridge.has_graph_draft(SimpleNamespace(session=session))
        session.prepare_retry()
        session.submit('draft request', run_id='execute-draft', metadata={'retry': True, 'turn_id': 'draft-message'})
        run(session.drain())
        messages = session._messages(session.snapshot()['leaf_id'])
        assert sum(m.get('content') == 'draft request' for m in messages) == 1
    finally:
        session.close()


def test_reference_is_projected_and_revision_can_be_reverted(graph_env):
    e = graph_env
    target = e.graph.materialize('source', operation(e, e.answer, 'reference', content='reference evidence'))['chat']['id']
    nodes, leaf, _ = e.graph.snapshot(target)
    assert 'reference evidence' in str(e.graph.detail(target, leaf.id)['projection'])
    edited = e.graph.materialize('source', operation(e, e.answer, 'edit', content='changed'))['chat']['id']
    assert e.graph.detail(edited, e.answer.id)['previousContent'] == 'original answer'


def test_copy_question_uses_destination_history_without_old_answers(graph_env):
    e = graph_env
    body = operation(e, e.answer, 'graft', copiedFrom={'chatId': 'source', 'nodeId': e.user.id,
                     'revision': e.graph.snapshot('source')[2]})
    result = e.graph.materialize('source', body)
    assert result['replay']
    assert [m['content'] for m in result['chat']['messages']] == ['original question', 'original answer', 'original question']
    assert e.graph.materialize('source', body)['chat']['id'] == result['chat']['id']
    assert e.graph.detail('source', e.answer.id)['content'] == 'original answer'


def test_pending_question_cannot_be_copied_or_edited(graph_env):
    e = graph_env
    with ContextStoreRouter(e.directory) as r:
        pending = r.mount('source', e.answer.id, {'role': 'user', 'content': 'permission?', 'pending_question': {'id': 'q'}})
    for mode in ('fork', 'edit'):
        with pytest.raises(ValueError, match='pending question'):
            e.graph.materialize('source', operation(e, pending, mode, content='answer'))


def test_task_nodes_can_be_included_in_family_graph(graph_env):
    from cyrene.core.context.tasks import STATE_KEY
    e = graph_env
    with ContextStoreRouter(e.directory) as r:
        root = r.get_node('source', e.root)
        r.update_node('source', root.id, {**root.value, STATE_KEY: {'active': None, 'shared': {'body': 'shared'}, 'documents': {}, 'receipts': {}}})
    assert any(n['role'] == 'task' for n in e.graph.graph('source')['nodes'])


def test_interrupted_copy_is_cleaned_up_and_can_be_retried(graph_env, monkeypatch):
    e = graph_env
    body = operation(e, e.answer)
    insert = e.service.repository.insert
    monkeypatch.setattr(e.service.repository, 'insert', lambda _: (_ for _ in ()).throw(RuntimeError('injected crash')))
    with pytest.raises(RuntimeError, match='injected crash'):
        e.graph.materialize('source', body)
    monkeypatch.setattr(e.service.repository, 'insert', insert)
    e.graph.recover_incomplete_operations()
    target = e.graph.materialize('source', body)['chat']
    assert e.graph.materialize('source', body)['chat']['id'] == target['id']
    assert len(e.service.repository.read()['chats']) == 2


def test_copied_question_artifacts_survive_removing_source_files(graph_env):
    import shutil
    e = graph_env
    with ContextStoreRouter(e.directory) as r:
        artifacts = r.artifact_directory('source')
        artifacts.mkdir(parents=True, exist_ok=True)
        attachment = artifacts / 'proof.txt'
        attachment.write_text('evidence')
    chat = e.service.repository.get('source')
    chat['messages'][0]['attachments'] = [{'path': str(attachment), 'name': 'proof.txt'}]
    e.service.repository.write_one(chat)
    body = operation(e, e.answer, 'graft', copiedFrom={'chatId': 'source', 'nodeId': e.user.id, 'revision': e.graph.snapshot('source')[2]})
    target = e.graph.materialize('source', body)['chat']
    copied_path = target['messages'][-1]['attachments'][0]['path']
    shutil.rmtree(artifacts)
    from pathlib import Path
    assert Path(copied_path).read_text() == 'evidence'
    assert str(artifacts) not in copied_path


def test_future_context_override_survives_provider_refresh(graph_env, tmp_path):
    from test_task_contexts import make_session, run
    e = graph_env
    with ContextStoreRouter(e.directory) as r:
        mount = r.mount('source', e.answer.id, {'role': 'context', 'content': 'provider original', 'context_kind': 'memory', 'context_lifecycle': 'session', 'run_id': 'r1'})
    target = e.graph.materialize('source', operation(e, mount, 'edit', content='branch memory', scope='future'))['chat']['id']
    session = make_session(tmp_path, tree_id=target)
    try:
        mounts = session._graph_mount_overrides([{'kind': 'memory', 'content': 'provider refreshed', 'lifecycle': 'session'}], 'session')
        assert mounts[0]['content'] == 'branch memory'
        session.submit('next question', run_id='next')
        run(session.drain())
        messages = session._messages(session.snapshot()['leaf_id'])
        assert 'branch memory' in str(messages)
        assert e.graph.detail('source', mount.id)['content'] == 'provider original'
    finally:
        session.close()


def test_graph_http_contract_conflicts_and_validation(graph_env):
    from fastapi import FastAPI, APIRouter
    from fastapi.testclient import TestClient
    from cyrene.workbench.http.workbench.chat_routes.context_graph_routes import register_context_graph_routes
    e = graph_env
    router = APIRouter()
    register_context_graph_routes(router, SimpleNamespace(service=e.service, db_path=e.graph.store.db_path,
        conversation_context=SimpleNamespace(agent_states=SimpleNamespace(context_directory=e.directory))))
    app = FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        base = '/api/workbench/chats/source/context-graph'
        assert client.get(base).status_code == 200
        detail = client.get(base + '/nodes/' + e.answer.id).json()
        assert detail['content'] == 'original answer'
        assert client.patch(base + '/view', json={'value': {'collapsed': []}, 'expectedRevision': 0}).status_code == 200
        assert client.patch(base + '/view', json={'value': {}, 'expectedRevision': 0}).status_code == 409
        assert client.patch(base + '/metadata', json={'value': {'notes': []}, 'expectedRevision': 0}).status_code == 400
        assert client.post(base + '/branches', content='{bad', headers={'content-type':'application/json'}).status_code == 400
        payload = operation(e, e.answer, 'edit', content='http revision')
        response = client.post(base + '/branches', json=payload)
        assert response.status_code == 200
        assert response.json()['chat']['messages'][-1]['content'] == 'http revision'


def test_user_revision_exposes_original_content_for_inverse_revision(graph_env):
    e = graph_env
    target = e.graph.materialize('source', operation(e, e.user, 'edit', content='revised user'))['chat']['id']
    draft = next(n for n in e.graph.graph(target)['nodes'] if n.get('chatId') == target and n.get('draft'))
    assert e.graph.detail(target, draft['nodeId'])['previousContent'] == 'original question'
