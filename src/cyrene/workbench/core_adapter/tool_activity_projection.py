"""Reconstruct durable activity cards from ContextTree records."""
from collections.abc import Mapping
from typing import Any
import json

from cyrene.core.plugin.result_codec import decoded_plugin_value
from cyrene.workbench.projects.assistant import ASSISTANT_SCOPE_ID

_TRACE_SKIP_TOOLS = {
    "use_tools",
    "send_message",
    "update_plan_progress",
}



def tool_result_presentation(value: Any) -> dict[str, Any]:
    """Read structured presentation through direct and toolbox-wrapped results."""
    for _ in range(4):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                return {}
        if not isinstance(value, Mapping) or value.get("ok") is False:
            return {}
        presentation = value.get("presentation")
        if isinstance(presentation, Mapping):
            return {} if presentation.get("kind") == "plugin_view" else dict(presentation)
        value = value.get("result")
    return {}


def _created_schedule_receipt(result, value):
    name = result.get("name")
    if not result.get("success") or name not in {"schedule.create", "toolbox"}:
        return None
    payload = decoded_plugin_value(value)
    if name == "toolbox":
        if (not isinstance(payload, Mapping) or payload.get("operation") != "invoke"
                or payload.get("name") != "schedule.create" or payload.get("error")):
            return None
        payload = decoded_plugin_value(payload.get("result"))
    if not isinstance(payload, Mapping) or payload.get("ok") is not True:
        return None
    task = payload.get("task")
    if not isinstance(task, Mapping) or task.get("project_id") != ASSISTANT_SCOPE_ID:
        return None
    return {"kind": "schedule_created", "task_id": task.get("id"), "prompt": task.get("prompt"),
            "schedule_type": task.get("schedule_type"), "next_run": task.get("next_run")}


def result_presentation(result, value):
    """Bind inline views to the host-recorded tool owner before publishing or replay."""
    stored = result.get("presentation")
    base = dict(stored) if isinstance(stored, Mapping) and stored else tool_result_presentation(value)
    receipt = _created_schedule_receipt(result, value)
    if receipt is not None:
        return {**base, **receipt}
    if base.get("kind") == "schedule_created":
        return {}
    origin = result.get("plugin_origin")
    if not result.get("success") or not isinstance(origin, Mapping):
        return base
    value = decoded_plugin_value(value)
    if result.get("name") == "toolbox":
        if (not isinstance(value, Mapping) or value.get("operation") != "invoke"
                or value.get("error") or value.get("pack") != origin["pack_id"]):
            return base
        value = decoded_plugin_value(value.get("result"))
    if not isinstance(value, Mapping) or value.get("ok") is False:
        return base
    view = value.get("presentation")
    if not isinstance(view, Mapping) or view.get("kind") != "plugin_view":
        return base
    if not isinstance(view.get("view_id"), str) or not view["view_id"]:
        return base
    # The client resolves this ID against the owning pack's enabled frontend_views.
    return {**base, "kind": "plugin_view", "packId": origin["pack_id"],
            "projectId": origin["project_id"], "instanceId": origin["instance_id"],
            "viewId": view["view_id"], "state": view.get("state")}


def bash_exit_code(name: str, value: Any) -> int | None:
    """Read the process exit code separately from tool delivery status."""
    if name != "Bash":
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    code = value.get("exit_code") if isinstance(value, Mapping) else None
    return code if type(code) is int else None


def tool_completed_payload(data, value, status):
    name = str(data.get("name") or "")
    payload = {
        "toolCallId": str(data.get("call_id") or ""),
        "name": name,
        "status": status,
        "failed": not bool(data.get("success")),
        "outputSummary": data.get("value"),
        "error": str(data.get("error") or ""),
        "presentation": result_presentation(data, value),
    }
    if (exit_code := bash_exit_code(name, value)) is not None:
        payload["exitCode"] = exit_code
    return payload


def _bash_activity_identity(trace, node_id, value):
    if node_id and any(entry.get("tool") == "Bash" for entry in trace):
        return {"sourceId": node_id, "runId": str(value.get("run_id") or "")}
    return {}


def _tool_result_for_call(item, call_id, results):
    run_id = str(item["value"].get("run_id") or "")
    result = results.get((run_id, str(item.get("id") or ""), call_id))
    return results.get((run_id, "", call_id)) if result is None else result


def _apply_bash_result(entry, arguments, result):
    command = arguments.get("command") if isinstance(arguments, Mapping) else None
    if isinstance(command, str):
        entry["command"] = command
    if result is not None and (exit_code := bash_exit_code("Bash", result.get("value"))) is not None:
        entry["exitCode"] = exit_code


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
            **_bash_activity_identity(trace, node_id, value),
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
    result = _tool_result_for_call(item, call_id, results_by_call_id)
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
    if raw_name == "Bash":
        _apply_bash_result(entry, arguments, result)
    if result is not None and success:
        presentation = result_presentation(result, result.get("value"))
        if presentation:
            entry["presentation"] = presentation
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
    results_by_call_id: dict[tuple[str, str, str], Mapping[str, Any] | None] = {}
    for item in nodes:
        value = _eligible_value(item, "tool_results", requested_run_id, terminal_run_ids)
        if value is None:
            continue
        run_id = str(value.get("run_id") or "")
        parent_id = str(item.get("parent_id") or "")
        results = value.get("results")
        for result in results if isinstance(results, list) else ():
            if not isinstance(result, Mapping):
                continue
            call_id = str(result.get("call_id") or "").strip()
            if call_id:
                key = (run_id, parent_id, call_id)
                # A legacy result without an owner is usable only if unique.
                results_by_call_id[key] = None if not parent_id and key in results_by_call_id else result

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
