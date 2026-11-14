"""Bound tool messages through the host's result storage service."""

from collections.abc import Mapping


def project_tool_messages(messages, *, service, session_id, context_limit_tokens):
    project = getattr(service, "project_batch", None)
    if not callable(project):
        return messages
    projected = list(messages)
    start = 0
    while start < len(messages):
        if messages[start].get("role") != "tool":
            start += 1
            continue
        end = start + 1
        while end < len(messages) and messages[end].get("role") == "tool":
            end += 1
        batch = messages[start:end]
        # Include the whole serialized envelope: errors and nested toolbox
        # results consume context just as successful direct-tool output does.
        results = project(
            [(message.get("content", ""), message.get("name", ""),
              message.get("tool_call_id", "")) for message in batch],
            session_id=session_id,
            context_limit_tokens=context_limit_tokens,
        )
        if len(results) != len(batch):
            raise ValueError("Tool result projection changed the batch length")
        for index, result in enumerate(results, start):
            projected[index] = {**messages[index], "content": result.content}
        start = end
    return projected


def result_session_id(data: Mapping, tree_id: str) -> str:
    run_context = data.get("run_context")
    return str(data.get("session_id") or data.get("chat_id")
               or (run_context.get("session_id") if isinstance(run_context, Mapping) else "")
               or tree_id)
