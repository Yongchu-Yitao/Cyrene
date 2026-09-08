"""Task capacity policy; snapshots preserve data without invoking a model."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from .compaction import COMPACT_TRIGGER_RATIO, message_token_estimate, messages_token_estimate

WARNING_RATIO = 0.85
PUBLIC_BUDGET_RATIO = 0.5


class ContextCapacityError(ValueError):
    def __init__(self, details):
        self.details = details
        super().__init__(
            f"Context {details['context_id']} would exceed the compaction threshold "
            f"({details['estimated_tokens']} estimated tokens > {details['threshold_tokens']}). "
            f"Read its snapshot in small sections instead: {details['snapshot_path']}. "
            "Reading does not activate it. Keep current state; do not repeatedly retry this load."
        )


def threshold(session):
    return int(session._configured_compaction_limit() * COMPACT_TRIGGER_RATIO)


def snapshot(session, value, suffix):
    # Pretty JSON permits bounded line reads; content-addressing preserves old evidence.
    def readable(value):
        if isinstance(value, str) and len(value) > 4000:
            return {'text_chunks': [value[i:i + 4000] for i in range(0, len(value), 4000)],
                    'encoding': 'Concatenate text_chunks without separators to recover the exact text.'}
        if isinstance(value, dict):
            return {k: readable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [readable(v) for v in value]
        return value
    content = json.dumps(readable(value), ensure_ascii=False, indent=2, default=str)
    digest = hashlib.sha256(content.encode()).hexdigest()
    directory = session.store.artifact_directory(session.tree.id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{digest}.{suffix}.json"
    if not snapshot_valid(str(target)):
        temporary = target.with_suffix('.tmp')
        temporary.write_text(content, encoding='utf-8')
        temporary.replace(target)
    return str(target)


def snapshot_valid(location):
    if not location:
        return False
    path = Path(location)
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() == path.name.split(".", 1)[0]
    except OSError:
        return False


def body_digest(body):
    return hashlib.sha256(body.encode()).hexdigest()


def shared_reference(state):
    reference = state.get('shared_offload', {})
    body = state.get('shared', {}).get('body', '')
    if reference.get('body_digest') == body_digest(body) and snapshot_valid(reference.get('snapshot_path')):
        return reference.get('snapshot_path')
    return None


def check_load(service, state, target):
    from .tasks import project_tasks, historical_assistant, user_owners
    s = service.session
    limit = threshold(s)
    if not limit:
        return
    proposed = deepcopy(state)
    proposed['active'] = target
    path = s.store.get_path(s.tree.id, s._leaf_id)
    tools = list(s._direct_model_tool_definitions())
    estimated = messages_token_estimate(project_tasks(path, proposed))
    estimated += message_token_estimate({'role': 'system', 'tools': tools}) if tools else 0
    # Include the successful control receipt that the following request will carry.
    estimated += message_token_estimate({'role': 'tool', 'content': {
        'context_id': target, 'active_context_id': target, 'saved': True}})
    if estimated <= limit:
        return
    owners = user_owners(path, state)
    records = [{'id': n.id, 'value': historical_assistant(n.value) if n.value.get('role') == 'assistant'
                else {'role': 'user', 'content': n.value.get('content', '')}} for n in path
               if (n.value.get('role') == 'assistant' and n.value.get('task_context_id') == target)
               or (n.value.get('role') == 'user' and target in owners.get(n.id, set()))]
    location = snapshot(s, {'context_id': target, 'document': state['documents'][target],
                            'execution_records': records,
                            'note': 'Read-only evidence snapshot. Original sources retain their trust and scope.'}, 'context')
    raise ContextCapacityError({'context_id': target, 'estimated_tokens': estimated,
                                'threshold_tokens': limit, 'snapshot_path': location})


async def prepare_capacity(session, trigger, prepared):
    """Offload excessive public task data before plugin-owned capacity guidance."""
    from .tasks import project_tasks
    limit = threshold(session)
    service = session.task_contexts
    async with service.serial():
        state = service.read()
        reference = state.get('shared_offload', {})
        body = state['shared']['body']
        if reference.get('body_digest') == body_digest(body) and not shared_reference(state):
            # Repair from the authoritative body even below the warning threshold
            # or when the provider no longer reports a context window.
            location = snapshot(session, {'context_id': 'shared', 'body': body,
                                          'note': 'Shared task data; original sources and scope apply.'}, 'shared-body')
            state['shared_offload'] = {'body_digest': body_digest(body), 'snapshot_path': location}
            service.write(state)
            prepared = session._prepare_model_input(trigger)
        if not limit or prepared.compaction_tokens < int(limit * WARNING_RATIO):
            return prepared
        path = session.store.get_path(session.tree.id, trigger.id)
        public = deepcopy(state)
        public['active'] = None
        public_tokens = messages_token_estimate(project_tasks(path, public)) + prepared.tool_tokens
        changed = False
        if public_tokens > int(limit * PUBLIC_BUDGET_RATIO):
            before = deepcopy(state)
            body = state['shared']['body']
            if body and not shared_reference(state):
                location = snapshot(session, {'context_id': 'shared', 'body': body,
                                              'note': 'Shared task data; original sources and scope apply.'}, 'shared-body')
                state['shared_offload'] = {'body_digest': body_digest(body), 'snapshot_path': location}
            changed = state != before
        if changed:
            service.write(state)
    return session._prepare_model_input(trigger) if changed else prepared
