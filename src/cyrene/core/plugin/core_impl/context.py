"""Always-visible task context management tools in the fixed core pack."""
from ..plugin import Plugin, PluginExecutionError, PluginFailure


def tool(name, description, properties, required):
    async def handler(arguments, context):
        service = context.services.get("task_contexts")
        if service is None:
            raise RuntimeError("Task contexts require an AgentSession")
        node = context.tree.get_node(context.tree_id, context.node_id)
        calls = node.value.get("tool_calls", [])
        if len(calls) != 1 or calls[0].get("name") != name:
            raise ValueError("Context tools must be called directly in their own batch")
        try:
            return await service.execute(name, arguments, f"{context.node_id}:{name}")
        except (ValueError, OSError) as exc:
            # Invalid IDs, state transitions and unreadable evidence do not
            # disable the tool for the rest of the turn. The agent may correct
            # the request or restore the evidence and retry.
            raise PluginExecutionError(PluginFailure(
                error_code="context_operation_failed", message=str(exc),
                retryable=True, retry_scope="different_arguments",
                details={"context_id": arguments.get("context_id"),
                         "active_context_id": service.read().get("active"), "state_unchanged": True},
            )) from exc
    return Plugin(
        name=name, description=description,
        input_schema={"type": "object", "properties": properties, "required": required, "additionalProperties": False},
        handler=handler, allow_parallel=False,
        metadata={"agent_exposure": "direct", "permission_review": False, "read_only": True,
                  "task_context_control": True, "requires_order": True},
    )


ID = {"type": "string", "description": "Exact context ID from the always-visible catalog. shared is editable with append/replace only; it is always loaded."}
CONTENT = {"type": "string", "description": "Task document body; use paths for large source material."}
CONTEXT_PLUGINS = (
    tool("load_context", "Load an existing task context. Unload another active context first. Call alone.", {"context_id": ID}, ["context_id"]),
    tool("unload_context", "Save a checkpoint and unload the active context. Call alone. Subsequent task work creates a context unless you load one.",
         {"summary": {"type": "string", "minLength": 1, "description": "Required checkpoint, at most 200 characters: unsaved progress, decisions, unfinished work and next action. Excess is head/tail truncated."}}, ["summary"]),
    tool("append_context", "Append body text to any context, including shared, without activating it. For shared, preserve source references and applicability. Call alone.", {"content": CONTENT, "context_id": ID}, ["content", "context_id"]),
    tool("replace_context", "Overwrite any context body, including shared, without activating it; preserve valid shared agreements, source references and applicability. No revision history. Execution records are managed separately. Call alone.", {"content": CONTENT, "context_id": ID}, ["content", "context_id"]),
)
