"""Preserve user clarifications across one context transition, without moving them."""

import json


HANDOFF_KEY = "clarification_handoff"


def answered_clarification(value):
    pending = value.get("pending_question")
    if (value.get("role") == "tool_results" and isinstance(pending, dict)
            and pending.get("status") == "answered"
            and pending.get("kind") == "clarification"
            and not pending.get("permission") and pending.get("answer")):
        return pending
    return None


def transition_clarifications(state, previous, target, path):
    """Expire the previous handoff and carry only newly answered source questions.

    Call before pausing the source document. Its unloaded nodes are the durable
    boundary: reloading and leaving it again must not re-export old answers.
    An explicit unload followed by load is one logical source-to-target move.
    """
    if previous == target:
        return
    if previous is None:
        pending = state.get(HANDOFF_KEY)
        if pending and pending.get("target") is None:
            pending["target"] = target
        else:
            state.pop(HANDOFF_KEY, None)
        return
    state.pop(HANDOFF_KEY, None)
    archived = set(state["documents"][previous].get("unloaded_nodes", []))
    start = max((i for i, node in enumerate(path) if node.value.get("role") == "user"), default=0)
    nodes = [node.id for node in path[start:]
             if node.id not in archived and node.value.get("task_context_id") == previous
             and answered_clarification(node.value)]
    if nodes:
        state[HANDOFF_KEY] = {"source": previous, "target": target, "nodes": nodes}


def carried_clarifications(state):
    handoff = state.get(HANDOFF_KEY, {})
    return set(handoff.get("nodes", [])) if handoff.get("target") == state.get("active") else set()


def clarification_fallback(value, included_call_ids):
    """Render an answered question when its original tool exchange is hidden.

    Keep exact question/options/answer and source ownership. This is dialogue,
    not a tool call to execute or a new permission grant.
    """
    pending = answered_clarification(value)
    if pending is None or pending.get("call_id") in included_call_ids:
        return None
    return {"role": "user", "content":
            "[Recorded user clarification; belongs to the source context, not a new request]\n"
            + json.dumps({"source_context_id": value.get("task_context_id"),
                          "question": pending.get("text", ""),
                          "options": pending.get("options", []),
                          "answer": pending["answer"]}, ensure_ascii=False)}
