"""Ordered context bodies, using the execution tree's existing record values.

The tree remains the immutable audit trail; body is the editable, model-visible
sequence. Source IDs reconcile committed records without duplicating them on
restart. Text additions occupy a fixed position in that same sequence.
"""
from copy import deepcopy
import hashlib
import json


def text_entry(content, *, shared=False, after=None, entry_id=None):
    entry = {'message': {'role': 'user', 'content': (
        '[Shared task context data; sources and scope apply]\n' if shared else '[Task context data]\n'
    ) + content}}
    if after is not None:
        entry['after_node_id'] = after
    if entry_id:
        entry['id'] = entry_id
    return entry


def normalize_document(doc, *, shared=False):
    body = doc.get('body', [])
    if isinstance(body, str):
        body = [text_entry(body, shared=shared)] if body else []
    body.extend({'message': deepcopy(m)} for m in doc.pop('messages', []))
    doc['body'] = body
    return body


def normalize_state(state):
    normalize_document(state.setdefault('shared', {}), shared=True)
    for doc in state.get('documents', {}).values():
        normalize_document(doc)
    return state


def body_text(doc):
    """Readable complete body for inspectors/search/export (never model input)."""
    body = doc.get('body', [])
    if isinstance(body, str):
        return body
    return json.dumps(body, ensure_ascii=False, indent=2)


def sync_bodies(state, path):
    """Reconcile committed dialogue into its owning body's ordered records."""
    from .tasks import user_owners
    normalize_state(state)
    owners = user_owners(path, state)
    positions = {node.id: index for index, node in enumerate(path)}
    path_ids = set(positions)
    results = {node.parent_id: node.id for node in path if node.value.get("role") == "tool_results"}
    def position(entry):
        if "value" in entry:
            return positions.get(entry["id"], -1)
        if "after_node_id" in entry:
            anchor = entry["after_node_id"]
            return positions.get(results.get(anchor, anchor), -1) + .5
        return -1
    for key, doc in state.get('documents', {}).items():
        body = doc['body'] = [entry for entry in doc['body']
                              if 'value' not in entry or entry['id'] in path_ids]
        known = {entry['id']: entry for entry in body if 'value' in entry}
        covered = set(doc.get('covered', []))
        for node in path:
            value = node.value
            role = value.get('role')
            belongs = key in owners.get(node.id, ()) if role == 'user' else value.get('task_context_id') == key
            if role not in {'user', 'assistant', 'tool_results'} or not belongs or node.id in covered:
                continue
            record = {'id': node.id, 'parent_id': node.parent_id, 'value': deepcopy(value)}
            if node.id in known:
                if not known[node.id].get('edited'):
                    known[node.id].update(record)
                continue
            # append_context commits before its tool result is mounted. Keep the
            # protocol call/result pair together, then the newly appended text.
            insertion = next((i for i, entry in enumerate(body)
                              if position(entry) > positions[node.id]), len(body))
            body.insert(insertion, record)
            known[node.id] = record
    return state


def append_body(doc, content, *, shared=False, after=None, receipt=''):
    body = normalize_document(doc, shared=shared)
    entry_id = 'append_' + hashlib.sha256(receipt.encode()).hexdigest()[:32] if receipt else None
    if entry_id and any(entry.get('id') == entry_id for entry in body):
        return
    body.append(text_entry(content, shared=shared, after=after, entry_id=entry_id))


def replace_body(doc, content, *, shared=False, control_id=None):
    """Explicit replacement discards the editable body, not the audit trail."""
    body = normalize_document(doc, shared=shared)
    doc['covered'] = list(dict.fromkeys([*doc.get('covered', []),
                                       *(e['id'] for e in body if 'value' in e)]))
    if control_id is None and body and body[-1].get('value', {}).get('task_control'):
        control_id = body[-1].get('id')
    control = [entry for entry in body if entry.get('value', {}).get('task_control')
               and (entry.get('id') == control_id or entry.get('parent_id') == control_id)]
    doc['covered'] = [key for key in doc['covered'] if key not in {e.get('id') for e in control}]
    doc['body'] = ([text_entry(content, shared=shared)] if content else []) + control


def body_prefix(doc):
    return [deepcopy(e['message']) for e in normalize_document(doc)
            if 'message' in e and 'after_node_id' not in e]


def body_additions(doc, path):
    """Locate additions after their committed call/result, never before history."""
    ids = {n.id for n in path}
    results = {n.parent_id: n.id for n in path if n.value.get('role') == 'tool_results'}
    additions = {}
    for entry in normalize_document(doc):
        if 'message' not in entry or 'after_node_id' not in entry:
            continue
        anchor = entry['after_node_id']
        anchor = results.get(anchor, anchor)
        # Inactive edits may be anchored in another task; the full audit path
        # still provides a stable position even if that task is not projected.
        if anchor not in ids:
            continue
        additions.setdefault(anchor, []).append(deepcopy(entry['message']))
    return additions


def insert_additions(selected, path, documents):
    from .tasks import replace
    additions = {}
    for doc in documents:
        for anchor, messages in body_additions(doc, path).items():
            additions.setdefault(anchor, []).extend(messages)
    if not additions:
        return selected
    by_id = {}
    ids = {node.id for node in path}
    for node in selected:
        anchor = node.id
        if anchor not in ids:
            anchor = next((original.id for original in path
                           if getattr(original, 'created_at', None) == getattr(node, 'created_at', None)
                           and original.parent_id == node.parent_id), path[0].id)
        by_id.setdefault(anchor, []).append(node)
    result = []
    for node in path:
        result.extend(by_id.get(node.id, []))
        for index, message in enumerate(additions.get(node.id, [])):
            result.append(replace(node, id=f'{node.id}:body:{index}', value={
                'role': 'context_body_message', 'message': message}))
    return result


def edit_body(doc, content, *, shared=False):
    """Inspector edits accept the structured body; legacy text remains valid."""
    try:
        entries = json.loads(content)
    except (ValueError, TypeError):
        entries = None
    if not isinstance(entries, list):
        replace_body(doc, content, shared=shared)
        return
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('message', entry.get('value')), dict):
            raise ValueError('Context body must contain structured messages or execution records')
        value = entry.get('message', entry.get('value'))
        if value.get('role') not in {'user', 'assistant', 'tool', 'tool_results'}:
            raise ValueError('Invalid context body record role')
        if 'value' in entry and not isinstance(entry.get('id'), str):
            raise ValueError('Execution records require a source ID')
    previous = normalize_document(doc, shared=shared)
    retained = {e.get('id') for e in entries if 'value' in e}
    doc['covered'] = list(dict.fromkeys([*(key for key in doc.get('covered', []) if key not in retained),
        *(e['id'] for e in previous if 'value' in e and e['id'] not in retained)]))
    for entry in entries:
        if 'value' in entry:
            entry['edited'] = True
    doc['body'] = deepcopy(entries)
