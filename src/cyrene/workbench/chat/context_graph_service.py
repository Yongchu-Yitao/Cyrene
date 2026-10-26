"""Read the entire conversation lineage and materialize explicit history paths.

Graph reads never instantiate an Agent or execute hooks. Content edits create
independent histories; the execution tree is not rewired by canvas gestures.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import threading
from collections import OrderedDict
from uuid import uuid4
from copy import deepcopy
from dataclasses import replace

from cyrene.core.context import ContextStoreRouter, TreeNotFoundError
from cyrene.core.context.graph_reads import graph_version, graph_snapshot
from cyrene.core.context.paths import DIALOGUE_ROLES, context_path, select_context_leaf
from cyrene.core.context.projection import project_model_messages
from cyrene.core.context.tasks import STATE_KEY, fork_task_state
from cyrene.workbench.chat.context_graph_store import ContextGraphStore, GraphConflict
from cyrene.workbench.chat.context_graph_projection import (
    node_content, safe_boundary, append_task_nodes, project_node, append_lineage_edges,
)


def rebase_artifact_refs(value, source, target):
    if isinstance(value, str):
        return value.replace(source, target)
    if isinstance(value, list):
        return [rebase_artifact_refs(item, source, target) for item in value]
    if isinstance(value, dict):
        return {key: rebase_artifact_refs(item, source, target) for key, item in value.items()}
    return value


def tree_revision(nodes):
    return hashlib.sha256('|'.join(sorted(f'{n.id}:{n.updated_at.isoformat()}' for n in nodes)).encode()).hexdigest()


class ContextGraphService:
    def __init__(self, service, context_directory, db_path):
        self.service = service
        self.directory = context_directory
        self.store = ContextGraphStore(db_path)
        self._graph_lock = threading.RLock()
        self._graph_cache = OrderedDict()
        self._graph_cache_bytes = 0
        self.recover_incomplete_operations()

    def recover_incomplete_operations(self):
        for suffix, entry in self.store.entries('operation:').items():
            value = entry['value']
            if value.get('phase') != 'prepared' or not value.get('sourceId'):
                continue
            try:
                with self.store.operation_gate(value['sourceId']):
                    current = self.store.read('operation:' + suffix)
                    if current['value'].get('phase') != 'prepared':
                        continue
                    target = self.service.repository.get(value['targetId'])
                    if not target:
                        self.service.run_manager.conversation_runtime.delete_context(value['targetId'])
                    self.store.write('operation:' + suffix, {**value, 'phase': 'committed' if target else 'interrupted'}, current['revision'])
            except GraphConflict:
                continue

    def chat(self, chat_id, *, metadata_only=False):
        read = self.service.repository.get_metadata if metadata_only else self.service.repository.get
        chat = read(chat_id)
        if not chat:
            raise LookupError('Conversation not found')
        from cyrene.agents.builtin import normalize_agent_binding
        if not normalize_agent_binding(chat.get('agent')).is_builtin:
            raise ValueError('此 Agent 不支持本地上下文导图 / Unsupported agent')
        return chat

    def family(self, chat_id):
        chat = self.chat(chat_id, metadata_only=True)
        chats = {c['id']: c for c in self.service.repository.read_summaries()['chats']
                 if c.get('projectId') == chat.get('projectId')}
        links = self.store.entries('lineage:')
        # Persist known origins before source chats can be removed. Reads only
        # migrate metadata, never alter conversation history or execute hooks.
        for cid, c in chats.items():
            if cid not in links:
                value = {'parent': c.get('forkedFromChatId', ''), 'forkMessageId': c.get('forkedAtMessageId', '')}
                try:
                    links[cid] = self.store.write('lineage:' + cid, value, 0)
                except GraphConflict:
                    links[cid] = self.store.read('lineage:' + cid)
        def root(cid):
            seen = set()
            while cid not in seen:
                seen.add(cid)
                parent = links.get(cid, {}).get('value', {}).get('parent')
                if not parent:
                    return cid
                cid = parent
            return min(seen)
        family_id = root(chat_id)
        return family_id, {cid: c for cid, c in chats.items() if root(cid) == family_id}, links

    def snapshot(self, chat_id, *, router=None):
        if router is None:
            with ContextStoreRouter(self.directory) as opened:
                return self.snapshot(chat_id, router=opened)
        self.chat(chat_id, metadata_only=True)
        nodes, committed_leaf, _version = graph_snapshot(router, chat_id)
        leaf = select_context_leaf(nodes, committed_leaf)
        return nodes, leaf, tree_revision(nodes)

    def _graph_inputs(self, chat_id, router):
        family_id, chats, links = self.family(chat_id)
        records = self.store.read_many([*('branch:' + cid for cid in chats), 'view:' + family_id])
        metadata = {cid: records['branch:' + cid] for cid in chats}
        view = records['view:' + family_id]
        links = {cid: links[cid] for cid in chats if cid in links}
        versions = {}
        for cid in chats:
            try:
                versions[cid] = graph_version(router, cid)
            except TreeNotFoundError:
                versions[cid] = None
        # Include ordering, non-tree metadata and the active conversation.
        # Tree versions are transactionally updated, never timestamp guesses.
        return family_id, chats, links, metadata, view, versions, tuple(chats)

    def graph_document(self, chat_id):
        """Return immutable JSON plus a representation revision for HTTP reads."""
        with self._graph_lock, ContextStoreRouter(self.directory) as router:
            inputs = self._graph_inputs(chat_id, router)
            cached = self._graph_cache.get(chat_id)
            if cached is not None and cached[0] == inputs:
                self._graph_cache.move_to_end(chat_id)
                return cached[1], cached[2]
            graph = self._build_graph(chat_id, router, inputs)
            revision = uuid4().hex
            graph['graphRevision'] = revision
            encoded = json.dumps(graph, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            # Trees live in separate databases. Cache only if all dependencies
            # remained stable throughout the read; never label a mixed read as
            # a reusable snapshot. A changing graph is still served fresh.
            stable = inputs == self._graph_inputs(chat_id, router)
            old = self._graph_cache.pop(chat_id, None)
            if old is not None:
                self._graph_cache_bytes -= len(old[1]) + old[3]
            # Keys retain only metadata, but notes can be large too. Account
            # for them as well as the immutable response (Unicode worst case).
            key_size = 4 * len(json.dumps(inputs, ensure_ascii=False))
            weight = len(encoded) + key_size
            if stable and weight <= 8 * 1024 * 1024:
                while self._graph_cache and (len(self._graph_cache) >= 4 or self._graph_cache_bytes + weight > 8 * 1024 * 1024):
                    _, evicted = self._graph_cache.popitem(last=False)
                    self._graph_cache_bytes -= len(evicted[1]) + evicted[3]
                self._graph_cache[chat_id] = (inputs, encoded, revision, key_size)
                self._graph_cache_bytes += weight
            return encoded, revision

    def graph(self, chat_id):
        # Public callers get independent dictionaries; cached data cannot be
        # corrupted by graph consumers or branch-edit code.
        encoded, _revision = self.graph_document(chat_id)
        return json.loads(encoded)

    def _build_graph(self, chat_id, router, inputs):
        family_id, chats, links, metadata, view, _versions, _order = inputs
        graph_nodes, edges, branches = [], [], []
        origins = {}
        for cid, chat in chats.items():
            meta = metadata.get(cid, {'revision': 0, 'value': {}})
            try:
                nodes, leaf, revision = self.snapshot(cid, router=router)
            except (TreeNotFoundError, ValueError):
                branches.append({'id': cid, 'chatId': cid, 'title': chat.get('title', ''), 'empty': True, **meta})
                continue
            active_path = context_path(nodes, leaf.id)
            active_ids = {n.id for n in active_path}
            visible = [n for n in nodes if isinstance(n.value, dict) and n.value.get('role') in DIALOGUE_ROLES]
            visible_ids = {n.id for n in visible}
            parents = {n.parent_id for n in visible}
            origins[cid] = {str(n.value.get('metadata', {}).get('turn_id', '')): n.id for n in visible if n.value.get('role') == 'user'}
            branches.append({'id': cid, 'chatId': cid, 'title': chat.get('title', ''), 'leafId': leaf.id,
                             'replay': bool(chat.get('contextGraphReplay')), 'config': {key: chat.get(key) for key in ('model', 'reasoningEffort', 'agent', 'contextActivations')}, 'treeRevision': revision, 'displayLeafId': leaf.parent_id if leaf.value.get('context_graph_checkpoint') else leaf.id, 'active': cid == chat_id, **meta})
            append_task_nodes(graph_nodes, edges, active_path[0].value.get(STATE_KEY), cid, leaf, revision, cid == chat_id)
            for n in visible:
                v = n.value
                graph_nodes.append(project_node(n, cid, visible_ids, active_ids, parents, revision, cid == chat_id))
                if n.parent_id in visible_ids:
                    kind = 'mount' if v.get('role') == 'context' else 'retry' if v.get('metadata', {}).get('retry') else 'sequence'
                    edges.append({'id': f'{cid}:{n.parent_id}>{n.id}', 'source': f'{cid}:{n.parent_id}', 'target': f'{cid}:{n.id}', 'kind': kind})
        append_lineage_edges(graph_nodes, edges, chats, links, origins)
        return {'familyId': family_id, 'activeChatId': chat_id, 'nodes': graph_nodes, 'edges': edges,
                'branches': branches, 'view': view}

    def detail(self, chat_id, node_id):
        nodes, leaf, revision = self.snapshot(chat_id)
        if node_id.startswith('task:'):
            state = context_path(nodes, leaf.id)[0].value.get(STATE_KEY) or {}
            task_id = node_id[5:]
            doc = state.get('shared') if task_id == 'shared' else state.get('documents', {}).get(task_id)
            if doc is None:
                raise LookupError('Task document not found')
            record = self.store.read('revision:' + chat_id)['value']
            previous = record.get('beforeContent') if record.get('nodeId') == node_id and record.get('mode') == 'edit' else None
            return {'previousContent': previous, 'revisionSource': record, 'content': str(doc.get('body', '')), 'role': 'task', 'format': 'text',
                    'revision': revision, 'value': doc, 'projection': project_model_messages(context_path(nodes, leaf.id))}
        node = next((n for n in nodes if n.id == node_id), None)
        if node is None:
            raise LookupError('Node not found')
        path = context_path(nodes, node_id)
        if node_id != leaf.id:
            state = fork_task_state(path)
            if state is not None:
                root_value = deepcopy(path[0].value)
                root_value[STATE_KEY] = state
                path[0] = replace(path[0], value=root_value)
        revision_record = self.store.read('revision:' + chat_id)['value']
        previous = revision_record.get('beforeContent') if revision_record.get('targetNodeId', revision_record.get('nodeId')) == node_id and revision_record.get('mode') in {'edit', 'remove'} else None
        return {'updatedAt': node.updated_at.isoformat(), 'previousContent': previous, 'revisionSource': revision_record, 'content': node_content(node.value), 'role': node.value.get('role'),
                'format': 'json' if node.value.get('role') in {'tool_results', 'context_compaction', 'context_reflection'} else 'text',
                'revision': revision, 'value': node.value, 'projection': project_model_messages(path)}

    def materialize(self, source_id, body):
        with self.store.operation_gate(source_id):
            return self._materialize(source_id, body)

    def _materialize(self, source_id, body):
        source_chat = self.chat(source_id)
        op = str(body.get('operationId') or '')
        if not op or len(op) > 128:
            raise ValueError('operationId is required')
        key = 'operation:' + source_id + ':' + op
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        saved = self.store.read(key)
        if saved['revision']:
            if saved['value'].get('fingerprint') != fingerprint:
                raise GraphConflict('Operation ID was reused for a different request')
            target = self.service.repository.get(saved['value']['targetId'])
            if target:
                return {'chat': self.service.public_chat_full(target), 'replay': saved['value'].get('replay', False)}
            # A prepared operation from a crashed process is safely rebuilt.
        nodes, selected_leaf, revision = self.snapshot(source_id)
        if body.get('expectedRevision') != revision:
            raise GraphConflict('源上下文已变化，请刷新 / Source revision changed')
        node_id = str(body.get('nodeId') or '')
        task_id = node_id[5:] if node_id.startswith('task:') else ''
        if task_id:
            if not safe_boundary(selected_leaf):
                raise ValueError('任务文档修订需要已完成的对话边界 / Wait for a completed turn')
            node_id = selected_leaf.id
        node = next((n for n in nodes if n.id == node_id), None)
        if node is None:
            raise LookupError('Node not found')
        if body.get('scope', 'snapshot') not in {'snapshot', 'future'}:
            raise ValueError('Invalid edit scope')
        mode = body.get('mode', 'continue')
        if mode not in {'continue', 'fork', 'edit', 'reference', 'remove', 'graft'}:
            raise ValueError('Unknown branch operation')
        role = 'task' if task_id else node.value.get('role')
        if task_id and mode != 'edit':
            raise ValueError('Task documents support explicit content revisions only')
        if mode == 'continue' and not safe_boundary(node):
            raise ValueError('请选择已完成的回复作为续聊边界 / Select a completed reply')
        if mode == 'fork' and (role != 'user' or node.value.get('runtime_guidance') or node.value.get('metadata', {}).get('agent_originated')):
            raise ValueError('请选择用户消息 / Select a user message')
        if mode == 'remove' and role != 'context':
            raise ValueError('Only context mounts may be removed')
        if mode == 'reference' and not safe_boundary(node):
            raise ValueError('References require a completed reply boundary')
        copied = None
        if mode == 'graft':
            if not safe_boundary(node):
                raise ValueError('Copy destination must be a completed reply')
            copied = body.get('copiedFrom')
            if not isinstance(copied, dict):
                raise ValueError('Copy source is required')
            copy_detail = self.detail(str(copied.get('chatId', '')), str(copied.get('nodeId', '')))
            if copy_detail['revision'] != copied.get('revision'):
                raise GraphConflict('Copy source changed; refresh and retry')
            copy_value = copy_detail['value']
            if copy_value.get('role') != 'user' or copy_value.get('runtime_guidance') or copy_value.get('pending_question') or (copy_value.get('metadata') or {}).get('agent_originated'):
                raise ValueError('Only ordinary user questions can be copied')
        content = copy_detail['content'] if copied else body.get('content', node_content(node.value))
        if not isinstance(content, str) or len(content) > 1_000_000:
            raise ValueError('Invalid content')
        if mode in {'edit', 'fork'} and role == 'user' and not content.strip():
            raise ValueError('User message cannot be empty')
        if mode in {'edit', 'fork', 'remove'} and node.value.get('pending_question'):
            raise ValueError('待答请求不能复制或修订 / Resolve the pending question first')
        if mode == 'edit' and role not in {'user', 'assistant', 'system', 'context', 'tool_results', 'context_compaction', 'context_reflection', 'task'}:
            raise ValueError('Unsupported editable role')
        if mode == 'edit' and node.value.get('tool_calls'):
            raise ValueError('请选择完整工具结果组，不能截断待执行工具 / Incomplete tool group')
        if mode == 'edit' and role in {'tool_results', 'context_compaction', 'context_reflection'}:
            parsed = json.loads(content)
            if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
                raise ValueError('Expected a JSON object array')
            if role == 'tool_results':
                original_ids = [str(item.get('call_id') or '') for item in node.value.get('results', [])]
                replacement_ids = [str(item.get('call_id') or '') for item in parsed]
                if sorted(original_ids) != sorted(replacement_ids):
                    raise ValueError('Tool result IDs must preserve the complete call group')
        path = context_path(nodes, node_id)
        replay = mode == 'graft' or (mode in {'fork', 'edit'} and role == 'user')
        target_id = saved['value']['targetId'] if saved['revision'] else self.service.short_id('chat')
        journal = {'sourceId': source_id, 'targetId': target_id, 'fingerprint': fingerprint, 'replay': replay, 'phase': 'prepared', 'nodeId': body.get('nodeId'), 'beforeContent': self.detail(source_id, str(body.get('nodeId')))['content'], 'content': content, 'mode': mode, 'scope': body.get('scope', 'snapshot')}
        saved = self.store.write(key, journal, saved['revision'])
        runtime = self.service.run_manager.conversation_runtime
        if runtime.has_context(target_id):
            runtime.delete_context(target_id)
        draft_message_id = self.service.short_id('msg') if replay else ''
        include = not replay or mode == 'graft'
        # Root editing copies the root as the complete prefix.
        try:
            runtime.fork_context(source_id, target_id, source_leaf_id=node_id,
                                 boundary_node_id=node_id, include_boundary=include)
            with ContextStoreRouter(self.directory) as router:
                copied_attachments = {}
                if copied:
                    copy_chat = self.chat(str(copied['chatId']))
                    copy_turn = (copy_value.get('metadata') or {}).get('turn_id')
                    original_copy = next((m for m in copy_chat.get('messages', []) if m.get('id') == copy_turn), {})
                    source_files = router.artifact_directory(str(copied['chatId']))
                    target_files = router.artifact_directory(target_id) / ('copied-' + hashlib.sha256(str(copied['chatId']).encode()).hexdigest()[:12])
                    if source_files.exists():
                        shutil.copytree(source_files, target_files, dirs_exist_ok=True)
                    copied_attachments = rebase_artifact_refs({k: original_copy[k] for k in ('attachments', 'agentAttachments') if k in original_copy}, str(source_files) + '/', str(target_files) + '/')
                target_tree = router.get_tree(target_id)
                last_id = path[-2].id if replay and mode != 'graft' else node_id
                if task_id:
                    root = router.get_node(target_id, target_tree.root_id)
                    rv = deepcopy(root.value)
                    state = rv.get(STATE_KEY) or {}
                    doc = state.get('shared') if task_id == 'shared' else state.get('documents', {}).get(task_id)
                    if doc is None:
                        raise ValueError('Task document is unavailable at this boundary')
                    doc['body'] = content
                    router.update_node(target_id, root.id, rv)
                    audit = router.mount(target_id, last_id, {'role': 'context', 'content': '',
                        'context_kind': 'task_revision', 'context_graph_task_edit': {'contextId': task_id, 'content': content},
                        'manual_revision': {'sourceTreeId': source_id, 'operationId': op}})
                    last_id = audit.id
                if mode in {'edit', 'remove'} and not replay and not task_id:
                    target_node = router.get_node(target_id, node_id)
                    value = deepcopy(target_node.value)
                    value['manual_revision'] = {'sourceTreeId': source_id, 'sourceNodeId': node_id, 'operationId': op}
                    field = 'results' if role == 'tool_results' else 'messages' if role in {'context_compaction', 'context_reflection'} else 'content'
                    value[field] = json.loads(content) if field != 'content' else '' if mode == 'remove' else content
                    router.update_node(target_id, node_id, value)
                    if role == 'context' and body.get('scope') == 'future':
                        root = router.get_node(target_id, target_tree.root_id)
                        rv = deepcopy(root.value)
                        overrides = dict(rv.get('context_graph_overrides') or {})
                        overrides[str(value.get('context_kind') or 'context')] = {'content': value[field], 'lifecycle': value.get('context_lifecycle', 'session'), 'nodeId': node_id}
                        rv['context_graph_overrides'] = overrides
                        router.update_node(target_id, root.id, rv)
                if mode == 'reference':
                    kind = 'branch_reference.' + op
                    run_id = next((str(n.value.get('run_id')) for n in reversed(path) if n.value.get('role') == 'user' and n.value.get('run_id')), '')
                    ref = router.mount(target_id, last_id, {'role': 'context', 'content': content, 'run_id': run_id,
                        'context_kind': kind, 'context_source': 'context_graph', 'context_lifecycle': 'session'})
                    last_id = ref.id
                    root = router.get_node(target_id, target_tree.root_id)
                    rv = deepcopy(root.value)
                    rv.setdefault('context_graph_overrides', {})[kind] = {'content': content, 'lifecycle': 'session', 'nodeId': ref.id}
                    router.update_node(target_id, root.id, rv)
                if replay:
                    draft_node = router.mount(target_id, last_id, {'role': 'user', 'content': content,
                        'run_id': 'graph-draft-' + op, 'trigger_model': False,
                        'metadata': {'turn_id': draft_message_id, 'public_user_message': content, 'graph_draft': True, **({'public_attachments': copied_attachments.get('attachments', [])} if copied else {})}})
                    last_id = draft_node.id
                    journal['targetNodeId'] = draft_node.id
                marker = router.mount(target_id, last_id, {'role': 'assistant', 'content': '',
                    'run_id': 'graph-' + op, 'answer_complete': True, 'session_end_complete': True,
                    'manual_revision': True, 'context_graph_checkpoint': True})
                router.commit_state(target_id, marker.id, 'graph-' + op)
            now = self.service.utc_now_iso()
            chat = self.service.create_chat(str(source_chat.get('projectId') or ''),
                                            str(body.get('title') or ((source_chat.get('title') or '对话') + (' · 修订' if mode in {'edit', 'remove'} else ' · 分支'))),
                                            str(source_chat.get('model') or ''))
            chat['id'] = target_id
            for field in ('agent', 'workspaceOverride', 'soulActive', 'workspaceActive', 'shortTermMemoryActive', 'projectMemoryActive', 'projectMemorySnapshot', 'contextActivations', 'remoteDeviceIds', 'reasoningEffort', 'modelSelectionId', 'permissionMode'):
                if field in source_chat:
                    chat[field] = deepcopy(source_chat[field])
            chat.update({'forkedFromChatId': source_id, 'forkMessage': content[:80], 'updatedAt': now})
            transcript = []
            for n in path:
                if replay and mode != 'graft' and n.id == node_id:
                    break
                v = n.value
                if v.get('role') not in {'user', 'assistant'} or not v.get('content'):
                    continue
                metadata = v.get('metadata') or {}
                entry = {'id': str((metadata.get('turn_id') if v['role'] == 'user' else '') or self.service.short_id('msg')), 'role': v['role'],
                         'content': metadata.get('public_user_message') or str(v.get('content') or ''),
                         'createdAt': n.created_at.isoformat(), 'contextNodeId': n.id, 'runId': v.get('run_id', '')}
                if metadata.get('public_attachments'):
                    entry['attachments'] = deepcopy(metadata['public_attachments'])
                if n.id == node_id and mode == 'edit' and not task_id:
                    entry['content'] = content
                    entry['manualRevision'] = True
                transcript.append(entry)
            turn_id = str(node.value.get('metadata', {}).get('turn_id') or '')
            if turn_id and mode != 'graft':
                chat['forkedAtMessageId'] = turn_id
            if replay:
                original_chat = self.chat(str(copied['chatId'])) if copied else source_chat
                original_turn = str((copy_value.get('metadata') or {}).get('turn_id', '')) if copied else turn_id
                original = next((m for m in original_chat.get('messages', []) if m.get('id') == original_turn), {})
                user = {'id': draft_message_id, 'role': 'user', 'content': content, 'createdAt': now}
                for field in ('attachments', 'agentAttachments'):
                    if field in original and not copied:
                        user[field] = deepcopy(original[field])
                if copied:
                    user.update(copied_attachments)
                transcript.append(user)
            with ContextStoreRouter(self.directory) as router:
                source_artifacts = str(router.artifact_directory(source_id)) + '/'
                target_artifacts = str(router.artifact_directory(target_id)) + '/'
            chat['messages'] = rebase_artifact_refs(transcript, source_artifacts, target_artifacts)
            chat['completedTurnCount'] = sum(1 for m in transcript if m['role'] == 'assistant')
            chat['contextGraphReplay'] = replay
            family_id, _, _ = self.family(source_id)
            self.store.write('lineage:' + target_id, {'parent': source_id, 'sourceNodeId': node_id, 'familyId': family_id, 'kind': mode}, self.store.read('lineage:' + target_id)['revision'])
            if copied and self.snapshot(str(copied['chatId']))[2] != copied['revision']:
                raise GraphConflict('Copy source changed while copying')
            latest_revision = self.snapshot(source_id)[2]
            if latest_revision != revision:
                raise GraphConflict('Source changed while copying; refresh and retry')
            self.store.write('revision:' + target_id, journal, self.store.read('revision:' + target_id)['revision'])
            self.service.repository.insert(chat)
            journal['phase'] = 'committed'
            self.store.write(key, journal, saved['revision'])
            return {'chat': self.service.public_chat_full(chat), 'replay': replay}
        except Exception:
            if not self.service.repository.get(target_id):
                runtime.delete_context(target_id)
            raise
