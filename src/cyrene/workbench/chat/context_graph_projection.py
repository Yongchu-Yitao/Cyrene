"""Pure rendering of context nodes and lineage for the graph read model."""
import hashlib
import json


def node_content(value):
    role = value.get('role')
    if role == 'tool_results':
        return json.dumps(value.get('results', []), ensure_ascii=False, indent=2)
    if role in {'context_compaction', 'context_reflection'}:
        return json.dumps(value.get('messages', []), ensure_ascii=False, indent=2)
    return str(value.get('content') or '')


def safe_boundary(node):
    v = node.value
    return (v.get('role') == 'assistant' and not v.get('tool_calls') and not v.get('error')
            and not v.get('cancelled') and (v.get('answer_complete') or v.get('session_end_complete')))


def append_task_nodes(graph_nodes, edges, task_state, cid, leaf, revision, active):
    if isinstance(task_state, dict):
        documents = {"shared": task_state.get("shared", {}), **task_state.get("documents", {})}
        for task_id, doc in documents.items():
            display_id = f"{cid}:task:{task_id}"
            graph_nodes.append({'id': display_id, 'nodeId': 'task:' + task_id, 'chatId': cid,
                'role': 'task', 'kind': task_id, 'preview': str(doc.get('body', ''))[:220],
                'canEdit': bool(safe_boundary(leaf)), 'canContinue': False, 'canFork': False,
                'revision': revision, 'active': active and (task_id == 'shared' or task_state.get('active') == task_id)})
            edges.append({'id': 'task:' + display_id, 'source': f'{cid}:{leaf.id}', 'target': display_id, 'kind': 'mount'})


def tool_projection(v):
    calls = v.get('tool_calls') if isinstance(v.get('tool_calls'), list) else []
    results = v.get('results') if v.get('role') == 'tool_results' and isinstance(v.get('results'), list) else []
    tool_phase = 'call' if calls else 'result' if v.get('role') == 'tool_results' else ''
    tool_items = calls if calls else results
    tool_names = list(dict.fromkeys(str(item.get('name') or '').strip() for item in tool_items if isinstance(item, dict) and item.get('name')))
    tool_preview = ' · '.join(tool_names[:3]) + (f' · +{len(tool_names) - 3}' if len(tool_names) > 3 else '')

    return tool_phase, tool_items, tool_names, tool_preview


def project_node(n, cid, visible_ids, active_ids, parents, revision, active):
    v = n.value
    content = node_content(v)
    tool_phase, tool_items, tool_names, tool_preview = tool_projection(v)
    pending = bool(v.get('pending_question'))
    return {'id': f'{cid}:{n.id}', 'nodeId': n.id, 'chatId': cid,
        'parentId': f'{cid}:{n.parent_id}' if n.parent_id in visible_ids else '',
        'internalCheckpoint': bool(v.get('context_graph_checkpoint')), 'contentHash': hashlib.sha256(json.dumps([content, v.get('context_lifecycle'), v.get('context_source'), v.get('context_graph_overrides')], sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        'role': v.get('role'), 'kind': v.get('context_kind', ''), 'preview': (tool_preview or content)[:220],
        'chars': len(content), 'updatedAt': n.updated_at.isoformat(), 'createdAt': n.created_at.isoformat(),
        'active': active and n.id in active_ids, 'currentPath': n.id in active_ids,
        'tip': n.id not in parents, 'historical': n.id not in active_ids,
        'canContinue': bool(safe_boundary(n)), 'canFork': v.get('role') == 'user' and not v.get('runtime_guidance') and not v.get('metadata', {}).get('agent_originated'),
        'canEdit': v.get('role') in {'user', 'assistant', 'system', 'context', 'tool_results', 'context_compaction', 'context_reflection'} and not pending and not v.get('tool_calls'),
        'toolGroup': bool(tool_phase), 'toolPhase': tool_phase, 'toolCount': len(tool_items), 'toolNames': tool_names,
        'pending': pending, 'revision': revision, 'runId': v.get('run_id', ''),
        'draft': bool(v.get('metadata', {}).get('graph_draft')), 'retry': bool(v.get('metadata', {}).get('retry')), 'manual': bool(v.get('manual_revision'))}


def append_lineage_edges(graph_nodes, edges, chats, links, origins):
    for cid, chat in chats.items():
        link = links.get(cid, {}).get('value', {})
        parent = link.get('parent')
        source_node = link.get('sourceNodeId') or origins.get(parent, {}).get(link.get('forkMessageId'))
        roots = [n for n in graph_nodes if n['chatId'] == cid and not n.get('parentId')]
        source = f'{parent}:{source_node}'
        if parent and roots:
            if source not in {n['id'] for n in graph_nodes}:
                source = 'missing:' + str(parent)
                if source not in {n['id'] for n in graph_nodes}:
                    graph_nodes.append({'id': source, 'role': 'origin', 'preview': '来源已删除或不可用', 'chatId': '', 'nodeId': '', 'canEdit': False})
            edges.append({'id': 'fork:' + cid, 'source': source, 'target': roots[0]['id'], 'kind': 'fork'})
