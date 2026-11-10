"""Core-owned capacity context, mounted through the lifecycle Hook protocol."""
from ...hook import MODEL_START
from ...context.capacity import WARNING_RATIO, prepare_capacity, threshold
from ...context.compaction import messages_token_estimate
from ...context.tasks import project_tasks
from copy import deepcopy

_HOOK_ID = "core-context-capacity-model-start"
_PLUGIN_ID = "core.context_capacity.mount"
_CATALOG_HOOK_ID = "core-context-catalog-capacity-model-start"
_CATALOG_PLUGIN_ID = "core.context_catalog_capacity.mount"


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

    async def remind_catalog(event):
        trigger = context.tree.get_node(context.tree_id, event.payload["node_id"])
        runtime = event.runtime if isinstance(event.runtime, dict) else {}
        prepared = runtime.get("prepared_model_input") or session._prepare_model_input(trigger)
        limit = session._configured_compaction_limit()
        if not limit:
            return {}
        async with session.task_contexts.serial():
            active = session.task_contexts.read().get("active")
            if not active:
                return {}
            path = context.tree.get_path(context.tree_id, trigger.id)
            kinds = {node.value.get("context_kind") for node in path}
            # The higher reminder covers both levels if input jumps past 50%.
            for percent in (50, 30):
                kind = f"task_catalog_capacity.{active}.{percent}"
                if kind in kinds:
                    return {}
                if prepared.compaction_tokens * 100 >= limit * percent:
                    return {
                        "context": (
                            f"[Task catalog capacity: {percent}% threshold reached; "
                            f"estimated input {prepared.compaction_tokens} / {limit} tokens, including tools] "
                            "Review the task context catalog with edit_context_catalog. "
                            "Shorten outdated titles/summaries and archive completed or explicitly shelved "
                            "inactive contexts. Preserve ongoing goals. Search archived entries before "
                            "creating duplicates. Archive only after checkpointing and leaving an active "
                            "context. Archiving reduces catalog size; long active history may still need "
                            "compaction. Continue the user's request."
                        ),
                        "context_kind": kind,
                        "context_source": _CATALOG_PLUGIN_ID,
                    }
            return {}

    for hook_id, plugin_id, handler in (
        (_HOOK_ID, _PLUGIN_ID, mount),
        (_CATALOG_HOOK_ID, _CATALOG_PLUGIN_ID, remind_catalog),
    ):
        if any(h.id == hook_id for h in context.hooks.list()):
            context.hooks.bind_plugin(plugin_id, handler, replace=True)
        else:
            context.hooks.register(MODEL_START, handler, plugin_id=plugin_id,
                                   hook_id=hook_id, root_only=True, failure_policy="closed")
