"""Core-owned capacity context, mounted through the lifecycle Hook protocol."""
from ...hook import MODEL_START
from ...context.capacity import WARNING_RATIO, prepare_capacity, threshold
from ...context.compaction import messages_token_estimate
from ...context.tasks import project_tasks
from copy import deepcopy

_HOOK_ID = "core-context-capacity-model-start"
_PLUGIN_ID = "core.context_capacity.mount"


def setup_capacity(context):
    session = context.services.get("agent_session")
    if session is None:
        return

    async def mount(event):
        trigger = context.tree.get_node(context.tree_id, event.payload["node_id"])
        runtime = event.runtime if isinstance(event.runtime, dict) else {}
        prepared = runtime.get("prepared_model_input") or session._prepare_model_input(trigger)
        prepared = await prepare_capacity(session, trigger, prepared)
        runtime["prepared_model_input"] = prepared
        limit = threshold(session)
        if not limit or prepared.compaction_tokens < int(limit * WARNING_RATIO):
            return {}
        async with session.task_contexts.serial():
            state = session.task_contexts.read()
            path = context.tree.get_path(context.tree_id, trigger.id)
            kind = f"task_capacity.{state.get('active')}"
            # The mount itself survives task compaction. Inspect the current path,
            # not a permanent flag or a node on an abandoned retry branch.
            if any(n.value.get("context_kind") == kind for n in path):
                return {}
            public = deepcopy(state)
            public['active'] = None
            public_tokens = messages_token_estimate(project_tasks(path, public)) + prepared.tool_tokens
            if public_tokens >= int(limit * WARNING_RATIO):
                action = ('Public/fixed input still dominates after public task data offload. '
                          'Do not create empty contexts to reduce it. Read snapshots only in small sections; '
                          'avoid expanding large resources. Compaction remains the fallback.')
            else:
                action = ('Save a checkpoint with unfinished progress and evidence paths using unload_context, '
                          'then continue this same task in a new context without loading the full old segment. '
                          'This is continuation, not completion. Read older snapshots only as needed.')
            return {
                'context': f'[Context capacity: estimated {prepared.compaction_tokens} tokens; compaction threshold {limit}] {action}',
                'context_kind': kind,
                'context_source': _PLUGIN_ID,
            }

    if any(h.id == _HOOK_ID for h in context.hooks.list()):
        context.hooks.bind_plugin(_PLUGIN_ID, mount, replace=True)
    else:
        context.hooks.register(MODEL_START, mount, plugin_id=_PLUGIN_ID,
                               hook_id=_HOOK_ID, root_only=True, failure_policy="closed")
