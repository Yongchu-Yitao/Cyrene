"""One replaceable reference mounted through the normal ContextTree Hook path."""
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
