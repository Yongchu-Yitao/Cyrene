"""Structured completion protocol for scheduler-initiated proactive turns."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from cyrene.core.plugin import PluginContext
from cyrene.core.plugin.context import run_context_value


TOOL_NAME = "finish_proactive"


@dataclass(frozen=True, slots=True)
class ProactiveOutcome:
    decision: Literal["deliver", "suppress"]
    report: str = ""
    valid: bool = True
    error: str = ""


def finish_proactive(
    arguments: dict[str, Any],
    context: PluginContext,
) -> dict[str, str]:
    """Validate and persist one model-authored proactive delivery decision."""

    if str(run_context_value(context, "conversation_source", "")) != "scheduler":
        raise ValueError("finish_proactive is available only in scheduler runs")

    decision = str(arguments.get("decision") or "").strip().lower()
    report = str(arguments.get("report") or "").strip()
    if decision == "deliver":
        if not report:
            raise ValueError("deliver requires a non-empty report")
    elif decision == "suppress":
        if report:
            raise ValueError("suppress requires an empty report")
    else:
        raise ValueError("decision must be deliver or suppress")
    return {"decision": decision, "report": report}


def outcome_from_result(result: Any) -> ProactiveOutcome:
    """Read the sole successful finish decision from a durable run snapshot.

    Final assistant prose is deliberately not inspected. A missing, malformed,
    or ambiguous protocol result fails closed so it can never create a public
    conversation by accident.
    """

    snapshot = getattr(result, "snapshot", None)
    run_id = str(getattr(result, "run_id", "") or "")
    if not isinstance(snapshot, Mapping) or not run_id:
        return ProactiveOutcome(
            decision="suppress",
            valid=False,
            error="missing proactive run snapshot",
        )

    successful: list[Mapping[str, Any]] = []
    nodes = snapshot.get("nodes")
    for node in nodes if isinstance(nodes, list) else ():
        if not isinstance(node, Mapping):
            continue
        value = node.get("value")
        if not isinstance(value, Mapping):
            continue
        if (
            str(value.get("role") or "") != "tool_results"
            or str(value.get("run_id") or "") != run_id
        ):
            continue
        records = value.get("results")
        for record in records if isinstance(records, list) else ():
            if (
                isinstance(record, Mapping)
                and str(record.get("name") or "") == TOOL_NAME
                and record.get("success") is True
                and isinstance(record.get("value"), Mapping)
            ):
                successful.append(record["value"])

    if len(successful) != 1:
        return ProactiveOutcome(
            decision="suppress",
            valid=False,
            error=(
                "missing successful finish_proactive result"
                if not successful
                else "multiple successful finish_proactive results"
            ),
        )

    value = successful[0]
    decision = str(value.get("decision") or "").strip().lower()
    report = str(value.get("report") or "").strip()
    if decision == "deliver" and report:
        return ProactiveOutcome(decision="deliver", report=report)
    if decision == "suppress" and not report:
        return ProactiveOutcome(decision="suppress")
    return ProactiveOutcome(
        decision="suppress",
        valid=False,
        error="invalid finish_proactive result",
    )


__all__ = [
    "ProactiveOutcome",
    "TOOL_NAME",
    "finish_proactive",
    "outcome_from_result",
]
