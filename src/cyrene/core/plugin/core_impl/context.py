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
                         "active_context_id": service.read().get("active"), "state_unchanged": True,
                         **getattr(exc, "details", {})},
            )) from exc
    return Plugin(
        name=name, description=description,
        input_schema={"type": "object", "properties": properties, "required": required, "additionalProperties": False},
        handler=handler, allow_parallel=False,
        metadata={"agent_exposure": "direct", "permission_review": False, "read_only": True,
                  "task_context_control": True, "requires_order": True},
    )


ID = {"type": "string", "description": "Exact context ID from the mounted catalog or a catalog search. shared is editable with append/replace only; it is always loaded."}
CONTENT = {"type": "string", "description": "Task document body; use paths for large source material. For shared, include evidence source, applicability and the actor constrained; do not invent facts or broaden the source's scope."}
TITLE = {"type": "string", "minLength": 1, "maxLength": 40, "description": "Stable goal/workstream title, at most 40 characters. Required for a new context; optional to name an existing untitled context."}
CHECKPOINT = {"type": "string", "minLength": 1, "description": "Checkpoint for the context being left: progress, decisions, unfinished work and next action; at most 200 characters (excess is head/tail truncated). Required when leaving an active context."}
CONTEXT_PLUGINS = (
    tool("switch_context", "Preferred atomic transition before progress messages or tools for an independent goal or a resumed workstream. Use an exact catalog ID to resume, or null plus title to create a context. Supply a checkpoint when leaving an active context. Failure preserves the original state. Related follow-ups stay in the current context. Call alone and wait for success.",
         {"context_id": {"type": ["string", "null"], "description": "Exact existing catalog ID, or null to create a new context. Never shared."}, "title": TITLE, "summary": CHECKPOINT}, ["context_id"]),
    tool("load_context", "Before progress messages or task-specific tools, resume an existing goal or independently resumable workstream using its exact catalog ID, restoring its messages and truncated historical calls in order without old tool results. Unload a different active context first. Never load shared, an unrelated placeholder or the already active context. Oversized loads fail without changing state: read the returned snapshot in small sections instead. Call alone and wait for success.", {"context_id": ID, "title": TITLE}, ["context_id"]),
    tool("unload_context", "Before progress messages or task-specific tools for an independent goal or workstream, checkpoint and unload the active context, even if its goal is complete. Also use for a continuation segment when warned about capacity. Save task messages in order; retain tool names and truncated arguments, discard results from restored context. Keep related follow-ups, corrections and tightly coupled steps together; delivery or waiting alone does not require unloading. Then load an existing context or start work to create one; after a capacity warning, continue in a new segment and read old evidence by path. Call alone and wait for success.",
         {"summary": {"type": "string", "minLength": 1, "description": "Required checkpoint, at most 200 characters: unsaved progress, decisions, unfinished work and next action. Excess is head/tail truncated."}}, ["summary"]),
    tool("append_context", "Save new task notes in any context without activating it. Use shared for new supported cross-task agreements with source and scope. Skip information already covered; keep local or uncertain conclusions in their task. To correct an existing agreement, use replace_context instead of appending contradictory text. Execution records are managed separately. Call alone and wait for success.", {"content": CONTENT, "context_id": ID}, ["content", "context_id"]),
    tool("replace_context", "Correct or update any context body without activating it. Supply the complete replacement; there is no revision history. For shared, replace outdated or incorrect agreements while preserving other valid information, sources and scope. Do not rewrite unchanged content or add unsupported requirements. Execution records are managed separately. Call alone and wait for success.", {"content": CONTENT, "context_id": ID}, ["content", "context_id"]),
    tool("edit_context_catalog", "Manage the current session's task context catalog. update edits a title or summary. archive hides an inactive task from the mounted catalog; checkpoint and switch or unload first if active. restore shows it without loading history. search finds IDs, titles and summaries, including archived tasks, with 10 entries per page. Search before creating duplicates. switch_context or load_context also cancels archival on successful activation. Call alone and wait for success.", {
        "action": {"type": "string", "enum": ["update", "archive", "restore", "search"]},
        "context_id": {"type": "string", "description": "Exact task context ID for update, archive or restore. Never shared."},
        "title": {**TITLE, "description": "Replacement catalog title for update."},
        "summary": {"type": "string", "minLength": 1, "maxLength": 200, "description": "Replacement catalog summary for update. A later unload checkpoint updates it."},
        "query": {"type": "string", "maxLength": 200, "description": "search only. Case-insensitive ID, title or summary substring. Empty or omitted lists entries."},
        "status": {"type": "string", "enum": ["all", "archived", "unarchived"], "description": "search only. Defaults to all."},
        "cursor": {"type": "string", "maxLength": 200, "description": "search only. next_cursor from the preceding page with the same query and status."},
    }, ["action"]),
)
