"""One replaceable reference mounted through the normal ContextTree Hook path."""
import json

from cyrene.core.hook import MODEL_START


def setup_import_context(context):
    tasks = context.services.get("task_contexts")
    if tasks is None or not context.data.get("personal_assistant"):
        return

    async def mount(event):
        imported = tasks.read().get("session_management_import")
        if not imported:
            return {}
        path = context.tree.get_path(context.tree_id, event.payload["node_id"])
        previous = next((node.value for node in reversed(path)
                         if node.value.get("role") == "context"
                         and node.value.get("context_kind") == "session_management_import"), None)
        if previous and previous.get("content") == imported["content"] and previous.get("context_lifecycle") == "reference":
            return {}
        return {"context": imported["content"], "context_kind": "session_management_import",
                "context_source": "session_management.import", "context_lifecycle": "reference"}

    hook_id = "session-management-import"
    plugin_id = "session_management.import"
    if hook_id in {hook.id for hook in context.hooks.list()}:
        context.hooks.bind_plugin(plugin_id, mount, replace=True)
    else:
        context.hooks.register(MODEL_START, mount, hook_id=hook_id,
                              plugin_id=plugin_id, root_only=True, failure_policy="closed")


def remove_legacy_imports(body):
    """Remove only complete old plugin snapshots, retaining surrounding notes."""
    marker = "[Imported session context snapshot]\nThe following blocks are reference data from another managed session. "
    position = 0
    while (start := body.find(marker, position)) >= 0:
        json_start = body.find("\n{", start)
        if json_start < 0:
            break
        try:
            value, length = json.JSONDecoder().raw_decode(body[json_start + 1:])
        except ValueError:
            position = start + len(marker)
            continue
        if not isinstance(value, dict) or "session_id" not in value or "blocks" not in value:
            position = start + len(marker)
            continue
        end = json_start + 1 + length
        body = body[:start].rstrip() + "\n\n" + body[end:].lstrip()
        position = start
    return body.strip()
