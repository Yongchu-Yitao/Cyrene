"""Reconstruct durable activity cards from ContextTree records."""
from collections.abc import Mapping
from typing import Any
import json

_TRACE_SKIP_TOOLS = {
    "use_tools",
    "send_message",
    "update_plan_progress",
}


def _trace_preview(value: Any, *, limit: int = 400) -> str:
    """Return a bounded, display-only summary for one durable tool entry."""

    if value is None:
        return ""
    if isinstance(value, Mapping):
        for key in ("result", "message", "error", "output"):
            preferred = value.get(key)
            if isinstance(preferred, (str, int, float, bool)) and str(preferred):
                return str(preferred)[:limit]
    if isinstance(value, str):
        return value[:limit]
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:limit]
    except (TypeError, ValueError):
        return str(value)[:limit]


def _trace_display_name(name: str, arguments: Any) -> str:
    """Expose the invoked Plugin name instead of the generic toolbox wrapper."""

    if name != "toolbox" or not isinstance(arguments, Mapping):
        return name
    operation = str(arguments.get("operation") or "").strip()
    target = str(arguments.get("name") or "").strip()
    if operation == "invoke" and target:
        return target
    return ".".join(part for part in ("toolbox", operation) if part) or name


def project_tool_activity_messages(
    snapshot: Mapping[str, Any],
    run_id: str = "",
) -> tuple[dict[str, Any], ...]:
    """Project durable ContextTree tool nodes into Workbench activity cards.

    The ContextTree is the source of truth after the Agent-kernel rewrite.  A
    completed chat must therefore be reconstructible from these nodes without
    depending on the browser's temporary SSE state or the removed legacy Agent
    session file.
    """

    raw_nodes = snapshot.get("nodes")
    nodes = (
        [item for item in raw_nodes if isinstance(item, Mapping)]
        if isinstance(raw_nodes, list)
        else []
    )
    requested_run_id = str(run_id or "")
    terminal_run_ids = _terminal_run_ids(nodes)
    results_by_call_id = _tool_results_by_call(nodes, requested_run_id, terminal_run_ids)

    activities: list[dict[str, Any]] = []
    for item in nodes:
        value = _eligible_value(item, "assistant", requested_run_id, terminal_run_ids)
        if value is None:
            continue
        calls = value.get("tool_calls")
        calls = calls if isinstance(calls, list) else []
        stored_reviews = value.get("permission_reviews")
        reviews = stored_reviews if isinstance(stored_reviews, list) else []
        if not calls and not reviews:
            continue
        trace = _activity_trace(item, calls, reviews, results_by_call_id)
        reasoning = str(
            value.get("reasoning") or value.get("reasoning_content") or ""
        )
        if not trace and not reasoning.strip():
            continue
        node_id = str(item.get("id") or "")
        activity: dict[str, Any] = {
            "id": f"activity_{node_id}" if node_id else f"activity_{len(activities)}",
            "role": "assistant",
            "content": "",
            "createdAt": str(item.get("created_at") or ""),
            "activityCard": True,
            "reasoning": reasoning,
            "trace": trace,
            "intermediate": True,
        }
        model = str(value.get("model") or "").strip()
        if model:
            activity["model"] = model
        activities.append(activity)
    return tuple(activities)



def _tool_trace_entry(item, index, call, results_by_call_id):
    if not isinstance(call, Mapping):
        return None
    raw_name = str(call.get("name") or "").strip()
    if not raw_name or raw_name in _TRACE_SKIP_TOOLS:
        return None
    arguments = call.get("arguments")
    call_id = str(call.get("id") or "").strip()
    if not call_id:
        call_id = f"{str(item.get('id') or 'assistant')}:{index}"
    result = results_by_call_id.get(call_id)
    success = bool(result.get("success")) if result is not None else True
    preview_source: Any = arguments
    if result is not None:
        preview_source = (
            result.get("value")
            if success
            else result.get("error") or result.get("value")
        )
    entry: dict[str, Any] = {
        "kind": "tool",
        "toolCallId": call_id,
        "text": _trace_display_name(raw_name, arguments),
        "tool": raw_name,
        "status": "completed" if success else "failed",
        "failed": not success,
    }
    preview = _trace_preview(preview_source)
    if preview:
        entry["preview"] = preview
    if result is None:
        entry["inferredCompletion"] = True
    return entry


def _permission_trace_entry(item, review_index, review):
    if not isinstance(review, Mapping):
        return None
    decisions = [
        decision
        for decision in review.get("decisions") or ()
        if isinstance(decision, Mapping)
    ]
    approved = bool(review.get("approved"))
    preview_parts = []
    for decision in decisions:
        tool = str(decision.get("tool") or "").strip()
        rationale = str(decision.get("rationale") or "").strip()
        detail = " · ".join(part for part in (tool, rationale) if part)
        if detail:
            preview_parts.append(detail)
    return {
        "kind": "permission",
        "toolCallId": str(
            review.get("id")
            or f"permission:{str(item.get('id') or 'assistant')}:{review_index}"
        ),
        "text": (
            "Permission review approved"
            if approved
            else "Permission review denied"
        ),
        "preview": "; ".join(preview_parts)[:240],
        "status": "completed" if approved else "failed",
        "failed": not approved,
    }


def _tool_results_by_call(nodes, requested_run_id, terminal_run_ids):
    results_by_call_id: dict[str, Mapping[str, Any]] = {}
    for item in nodes:
        value = _eligible_value(item, "tool_results", requested_run_id, terminal_run_ids)
        if value is None:
            continue
        results = value.get("results")
        for result in results if isinstance(results, list) else ():
            if not isinstance(result, Mapping):
                continue
            call_id = str(result.get("call_id") or "")
            if call_id:
                results_by_call_id[call_id] = result

    return results_by_call_id


def _eligible_value(item, role, requested_run_id, terminal_run_ids):
    value = item.get("value")
    if not isinstance(value, Mapping) or value.get("role") != role:
        return None
    node_run_id = str(value.get("run_id") or "")
    if requested_run_id and node_run_id != requested_run_id:
        return None
    if not requested_run_id and node_run_id not in terminal_run_ids:
        return None
    return value


def _terminal_run_ids(nodes):
    return {
        str(value.get("run_id") or "")
        for item in nodes
        for value in [item.get("value")]
        if isinstance(value, Mapping)
        and value.get("role") == "assistant"
        and str(value.get("run_id") or "")
        and (
            value.get("session_end_complete") is True
            or value.get("answer_complete") is True
            or value.get("error") is True
            or value.get("cancelled") is True
        )
    }


def _activity_trace(item, calls, reviews, results_by_call_id):
    trace: list[dict[str, Any]] = []
    for index, call in enumerate(calls):
        entry = _tool_trace_entry(item, index, call, results_by_call_id)
        if entry is not None:
            trace.append(entry)
    for review_index, review in enumerate(reviews):
        entry = _permission_trace_entry(item, review_index, review)
        if entry is not None:
            trace.append(entry)
    return trace
