"""One replaceable reference mounted through the normal ContextTree Hook path."""
from cyrene.core.hook import MODEL_START


def setup_import_context(context):
    setup_work_device_context(context)
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


def setup_work_device_context(context):
    service = context.services.get("session_management")
    if service is None or not context.data.get("personal_assistant"):
        return

    async def mount(event):
        import json
        owner = str(context.data.get("session_id") or context.tree_id)
        device = (service.host.chat(owner) or {}).get("workDevice")
        content = ("[Assistant work device]\nYou run on the controller (local) device. "
                   "New task_start tasks run on the selected work device below, using your controller model configuration. "
                   "Use workspace_path from that device's shared workspaces; local paths do not exist there. "
                   "Existing tasks stay on their original device after selection changes. "
                   "Task conversation and returned results are saved locally; workspace files remain on the execution device. "
                   "If the transport reports an unavailable device after three retries, promptly use ask_user to ask what to do. Do not silently switch devices or retry execution before the user decides. Disconnection does not confirm that the remote task stopped.\n" +
                   json.dumps(device or {"device_id": "local"}, ensure_ascii=False))
        path = context.tree.get_path(context.tree_id, event.payload["node_id"])
        previous = next((n.value for n in reversed(path) if n.value.get("context_kind") == "assistant_work_device"), {})
        if previous.get("content") == content:
            return {}
        return {"context": content, "context_kind": "assistant_work_device",
                "context_source": "session_management.work_device", "context_lifecycle": "reference"}

    hook_id = "assistant-work-device"
    plugin_id = "session_management.work_device"
    if hook_id in {hook.id for hook in context.hooks.list()}:
        context.hooks.bind_plugin(plugin_id, mount, replace=True)
    else:
        context.hooks.register(MODEL_START, mount, hook_id=hook_id, plugin_id=plugin_id,
                              root_only=True, failure_policy="closed")
