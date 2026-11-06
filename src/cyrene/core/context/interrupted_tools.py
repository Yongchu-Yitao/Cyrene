"""Complete interrupted model tool handshakes without changing stored history."""

from collections.abc import Mapping, Sequence
from typing import Any


def _missing_results(value, result_node):
    recorded = {item.get("call_id") for item in (result_node.value.get("results", []) if result_node else [])}
    return [
        {"call_id": call["id"], "name": call.get("name", ""), "success": False,
         "value": None, "error": "Run stopped before a tool result was recorded; execution outcome is unknown. Check side effects before retrying."}
        for call in value.get("tool_calls", [])
        if call.get("id") and call["id"] not in recorded
    ]


def settle_interrupted_tool_calls(path: Sequence[Any]) -> list[Any]:
    """Close interrupted tool handshakes in model input, never execution history.

    Cancellation can interrupt a batch before its result node is committed.
    Preserve real results and describe missing outcomes without inventing success
    or implying that side effects are safe to repeat.
    """
    from dataclasses import replace

    terminated = {
        str(node.value.get("run_id") or "") for node in path
        if isinstance(node.value, Mapping)
        and (node.value.get("cancelled") is True or node.value.get("error") is True)
    } - {""}
    if not terminated:
        return list(path)
    results = {
        node.parent_id: node for node in path
        if isinstance(node.value, Mapping) and node.value.get("role") == "tool_results"
    }
    missing_by_call = {}
    for node in path:
        value = node.value
        if value.get("role") != "assistant" or value.get("run_id") not in terminated:
            continue
        result_node = results.get(node.id)
        missing = _missing_results(value, result_node)
        if missing:
            missing_by_call[node.id] = missing
    projected = []
    for node in path:
        if node.value.get("role") == "tool_results" and node.parent_id in missing_by_call:
            node = replace(node, value={**node.value, "results": [
                *node.value.get("results", []), *missing_by_call[node.parent_id],
            ]})
        projected.append(node)
        if node.id in missing_by_call and node.id not in results:
            projected.append(replace(node, id=node.id + ":interrupted-results", parent_id=node.id, value={
                "role": "tool_results", "run_id": node.value.get("run_id"),
                "task_context_id": node.value.get("task_context_id"),
                "results": missing_by_call[node.id],
            }))
    return projected


