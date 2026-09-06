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
CONTENT = {"type": "string", "description": "Task document body; use paths for large source material. For shared, include evidence source, applicability and the actor constrained; do not invent facts or broaden the source's scope."}
CONTEXT_PLUGINS = (
    tool("load_context", "Resume an earlier goal by restoring its saved task context before answering or using its evidence. Match the goal to the catalog's exact ID; unload a different active context first. Never load an unrelated context as a placeholder for a new goal. Do not call for the already active task or shared. Call alone and wait for success.", {"context_id": ID}, ["context_id"]),
    tool("unload_context", "Save a checkpoint and pause the active task before starting an independent goal or loading an earlier one, including text-only work. Do not unload merely to save, finish, report results or wait for the user. Do not call for the first task, acknowledgments, or explanations, corrections and tests serving the current goal. Subsequent task work creates a context unless you load one. Call alone and wait for success.",
         {"summary": {"type": "string", "minLength": 1, "description": "Required checkpoint, at most 200 characters: unsaved progress, decisions, unfinished work and next action. Excess is head/tail truncated."}}, ["summary"]),
    tool("append_context", "Save new task notes in any context without activating it. Use shared for new supported cross-task agreements with source and scope. Skip information already covered; keep local or uncertain conclusions in their task. To correct an existing agreement, use replace_context instead of appending contradictory text. Execution records are managed separately. Call alone and wait for success.", {"content": CONTENT, "context_id": ID}, ["content", "context_id"]),
    tool("replace_context", "Correct or update any context body without activating it. Supply the complete replacement; there is no revision history. For shared, replace outdated or incorrect agreements while preserving other valid information, sources and scope. Do not rewrite unchanged content or add unsupported requirements. Execution records are managed separately. Call alone and wait for success.", {"content": CONTENT, "context_id": ID}, ["content", "context_id"]),
)
