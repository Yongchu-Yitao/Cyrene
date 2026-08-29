"""Durable contracts owned by the subagent Plugin pack.

The Agent kernel deliberately knows nothing about worker modes or discussion
budgets.  This module is the serialization boundary for those pack-local
concepts, so a manager can be closed and reconstructed without losing a lease,
completion evidence, or a shared discussion counter.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from cyrene.runtime import settings_store


EXECUTION_MODE = "execution"
DISCUSSION_MODE = "discussion"
SUMMARY_MODE = "summary"
DISCUSSION_ROLES = frozenset({"moderator", "participant"})
TERMINAL_STATUSES = frozenset(
    {"done", "failed", "cancelled", "timeout", "incomplete"}
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalized_mode(mode: str = "", role: str = "") -> str:
    normalized_role = str(role or "").strip().lower()
    if normalized_role in DISCUSSION_ROLES:
        return DISCUSSION_MODE
    normalized = str(mode or EXECUTION_MODE).strip().lower()
    if normalized not in {EXECUTION_MODE, DISCUSSION_MODE, SUMMARY_MODE}:
        raise ValueError("mode must be 'execution' or 'discussion'")
    return normalized


def normalized_criteria(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError("success_criteria must be an array of strings")
    result: list[str] = []
    for raw in value:
        criterion = str(raw or "").strip()
        if criterion and criterion not in result:
            result.append(criterion)
    if len(result) > 20:
        raise ValueError("success_criteria may contain at most 20 items")
    return result


def _integer_setting(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(settings_store.get(name, default))
    except (TypeError, ValueError, OverflowError):
        value = default
    return max(minimum, min(maximum, value))


def _number_setting(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(settings_store.get(name, default))
    except (TypeError, ValueError, OverflowError):
        value = default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True, slots=True)
class ExecutionLimits:
    max_tool_calls: int
    max_wall_seconds: int
    no_progress_turns: int
    checkpoint_calls: int
    max_cost_usd: float
    max_context_tokens: int

    @classmethod
    def current(cls) -> "ExecutionLimits":
        return cls(
            max_tool_calls=_integer_setting(
                "subagent_execution_max_tool_calls", 200, 1, 5000
            ),
            max_wall_seconds=_integer_setting(
                "subagent_execution_max_wall_seconds", 1800, 30, 86400
            ),
            no_progress_turns=_integer_setting(
                "subagent_execution_no_progress_turns", 3, 1, 20
            ),
            checkpoint_calls=_integer_setting(
                "subagent_execution_checkpoint_calls", 20, 1, 500
            ),
            max_cost_usd=_number_setting(
                "subagent_execution_max_cost_usd", 5.0, 0.0, 1000.0
            ),
            max_context_tokens=_integer_setting(
                "subagent_execution_max_context_tokens", 0, 0, 4_000_000
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_tool_calls": self.max_tool_calls,
            "max_wall_seconds": self.max_wall_seconds,
            "no_progress_turns": self.no_progress_turns,
            "checkpoint_calls": self.checkpoint_calls,
            "max_cost_usd": self.max_cost_usd,
            "max_context_tokens": self.max_context_tokens,
        }


@dataclass(frozen=True, slots=True)
class DiscussionLimits:
    max_rounds: int
    max_messages_per_agent: int
    max_total_messages: int
    max_message_chars: int
    max_wall_seconds: int
    max_tool_calls: int
    no_new_info_rounds: int

    @classmethod
    def current(cls, per_agent_override: int | None = None) -> "DiscussionLimits":
        configured_per_agent = _integer_setting(
            "subagent_discussion_max_messages_per_agent", 4, 1, 50
        )
        if per_agent_override is not None:
            configured_per_agent = min(
                configured_per_agent,
                max(1, min(50, int(per_agent_override))),
            )
        return cls(
            max_rounds=_integer_setting(
                "subagent_discussion_max_rounds", 5, 1, 50
            ),
            max_messages_per_agent=configured_per_agent,
            max_total_messages=_integer_setting(
                "subagent_discussion_max_total_messages", 20, 1, 500
            ),
            max_message_chars=_integer_setting(
                "subagent_discussion_max_message_chars", 2000, 100, 20000
            ),
            max_wall_seconds=_integer_setting(
                "subagent_discussion_max_wall_seconds", 600, 30, 86400
            ),
            max_tool_calls=_integer_setting(
                "subagent_discussion_max_tool_calls", 50, 1, 1000
            ),
            no_new_info_rounds=_integer_setting(
                "subagent_discussion_no_new_info_rounds", 2, 1, 20
            ),
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "max_rounds": self.max_rounds,
            "max_messages_per_agent": self.max_messages_per_agent,
            "max_total_messages": self.max_total_messages,
            "max_message_chars": self.max_message_chars,
            "max_wall_seconds": self.max_wall_seconds,
            "max_tool_calls": self.max_tool_calls,
            "no_new_info_rounds": self.no_new_info_rounds,
        }


@dataclass(slots=True)
class SubagentMetrics:
    model_turns: int = 0
    tool_calls: int = 0
    lease_tool_calls: int = 0
    no_progress_turns: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    context_compactions: int = 0
    discussion_rounds: int = 0
    messages: int = 0

    @classmethod
    def from_value(cls, value: Any) -> "SubagentMetrics":
        raw = value if isinstance(value, Mapping) else {}
        integer_fields = {
            "model_turns", "tool_calls", "lease_tool_calls",
            "no_progress_turns", "prompt_tokens", "completion_tokens",
            "total_tokens", "context_compactions", "discussion_rounds",
            "messages",
        }
        kwargs: dict[str, Any] = {}
        for name in integer_fields:
            try:
                kwargs[name] = max(0, int(raw.get(name) or 0))
            except (TypeError, ValueError, OverflowError):
                kwargs[name] = 0
        try:
            kwargs["estimated_cost_usd"] = max(
                0.0, float(raw.get("estimated_cost_usd") or 0.0)
            )
        except (TypeError, ValueError, OverflowError):
            kwargs["estimated_cost_usd"] = 0.0
        return cls(**kwargs)

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_turns": self.model_turns,
            "tool_calls": self.tool_calls,
            "lease_tool_calls": self.lease_tool_calls,
            "no_progress_turns": self.no_progress_turns,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 8),
            "context_compactions": self.context_compactions,
            "discussion_rounds": self.discussion_rounds,
            "messages": self.messages,
        }


@dataclass(slots=True)
class FinishRequest:
    completion_status: str = ""
    criteria_evidence: list[dict[str, str]] = field(default_factory=list)
    accepted: bool = False
    requested_at: str = ""

    @classmethod
    def from_value(cls, value: Any) -> "FinishRequest":
        raw = value if isinstance(value, Mapping) else {}
        evidence = [
            {
                "criterion": str(item.get("criterion") or "").strip(),
                "evidence": str(item.get("evidence") or "").strip(),
            }
            for item in raw.get("criteria_evidence", ())
            if isinstance(item, Mapping)
        ]
        return cls(
            completion_status=str(raw.get("completion_status") or ""),
            criteria_evidence=evidence,
            accepted=bool(raw.get("accepted")),
            requested_at=str(raw.get("requested_at") or ""),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "completion_status": self.completion_status,
            "criteria_evidence": [dict(item) for item in self.criteria_evidence],
            "accepted": self.accepted,
            "requested_at": self.requested_at,
        }


@dataclass(slots=True)
class SubagentRecord:
    agent_id: str
    tree_id: str
    task: str
    parent_agent_id: str
    round_id: str
    mode: str = EXECUTION_MODE
    role: str = ""
    success_criteria: list[str] = field(default_factory=list)
    discussion_id: str = ""
    discussion_max_messages: int | None = None
    use_secondary: bool = False
    current_run_id: str = ""
    status: str = "running"
    outcome: str = ""
    stop_reason: str = ""
    result: str = ""
    error: str = ""
    instruction_node_id: str = ""
    reported_node_id: str = ""
    waiting_node_id: str = ""
    authorization_request: str = ""
    spawn_effect_key: str = ""
    generation: int = 1
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    started_at: str = field(default_factory=utc_now)
    lease_started_at: str = field(default_factory=utc_now)
    finalization_reason: str = ""
    metrics: SubagentMetrics = field(default_factory=SubagentMetrics)
    finish: FinishRequest = field(default_factory=FinishRequest)
    seen_tool_signatures: list[str] = field(default_factory=list)
    seen_result_fingerprints: list[str] = field(default_factory=list)

    @classmethod
    def from_value(cls, agent_id: str, value: Mapping[str, Any]) -> "SubagentRecord":
        role = str(value.get("role") or "").strip().lower()
        mode = normalized_mode(str(value.get("mode") or ""), role)
        max_messages = value.get("discussion_max_messages")
        try:
            normalized_max = (
                max(1, min(50, int(max_messages)))
                if max_messages is not None else None
            )
        except (TypeError, ValueError, OverflowError):
            normalized_max = None
        return cls(
            agent_id=str(agent_id),
            tree_id=str(value.get("tree_id") or ""),
            task=str(value.get("task") or ""),
            parent_agent_id=str(value.get("parent_agent_id") or "main"),
            round_id=str(value.get("round_id") or ""),
            mode=mode,
            role=role,
            success_criteria=normalized_criteria(value.get("success_criteria") or []),
            discussion_id=str(
                value.get("discussion_id") or value.get("round_id") or ""
            ),
            discussion_max_messages=normalized_max,
            use_secondary=bool(value.get("use_secondary")),
            current_run_id=str(value.get("current_run_id") or value.get("round_id") or ""),
            status=str(value.get("status") or "running"),
            outcome=str(value.get("outcome") or ""),
            stop_reason=str(value.get("stop_reason") or ""),
            result=str(value.get("result") or ""),
            error=str(value.get("error") or ""),
            instruction_node_id=str(value.get("instruction_node_id") or ""),
            reported_node_id=str(value.get("reported_node_id") or ""),
            waiting_node_id=str(value.get("waiting_node_id") or ""),
            authorization_request=str(value.get("authorization_request") or ""),
            spawn_effect_key=str(value.get("spawn_effect_key") or ""),
            generation=max(1, int(value.get("generation") or 1)),
            created_at=str(value.get("created_at") or utc_now()),
            updated_at=str(value.get("updated_at") or utc_now()),
            started_at=str(value.get("started_at") or utc_now()),
            lease_started_at=str(value.get("lease_started_at") or utc_now()),
            finalization_reason=str(value.get("finalization_reason") or ""),
            metrics=SubagentMetrics.from_value(value.get("metrics")),
            finish=FinishRequest.from_value(value.get("finish")),
            seen_tool_signatures=[str(item) for item in value.get("seen_tool_signatures", ())],
            seen_result_fingerprints=[str(item) for item in value.get("seen_result_fingerprints", ())],
        )

    def touch(self) -> None:
        self.updated_at = utc_now()

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "tree_id": self.tree_id,
            "task": self.task,
            "parent_agent_id": self.parent_agent_id,
            "round_id": self.round_id,
            "mode": self.mode,
            "role": self.role,
            "success_criteria": list(self.success_criteria),
            "discussion_id": self.discussion_id,
            "discussion_max_messages": self.discussion_max_messages,
            "use_secondary": self.use_secondary,
            "current_run_id": self.current_run_id,
            "status": self.status,
            "outcome": self.outcome,
            "stop_reason": self.stop_reason,
            "result": self.result,
            "error": self.error,
            "instruction_node_id": self.instruction_node_id,
            "reported_node_id": self.reported_node_id,
            "waiting_node_id": self.waiting_node_id,
            "authorization_request": self.authorization_request,
            "spawn_effect_key": self.spawn_effect_key,
            "generation": self.generation,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "lease_started_at": self.lease_started_at,
            "finalization_reason": self.finalization_reason,
            "metrics": self.metrics.as_dict(),
            "finish": self.finish.as_dict(),
            "seen_tool_signatures": list(self.seen_tool_signatures[-256:]),
            "seen_result_fingerprints": list(self.seen_result_fingerprints[-256:]),
        }

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.agent_id,
            "agent_id": self.agent_id,
            "tree_id": self.tree_id,
            "task": self.task,
            "parent_agent_id": self.parent_agent_id,
            "round_id": self.round_id,
            "mode": self.mode,
            "role": self.role,
            "discussion_id": self.discussion_id,
            "status": self.status,
            "outcome": self.outcome,
            "stop_reason": self.stop_reason,
            "result": self.result,
            "error": self.error,
            "success_criteria": list(self.success_criteria),
            "metrics": self.metrics.as_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(slots=True)
class DiscussionState:
    discussion_id: str
    round_id: str
    rounds: int = 0
    messages_total: int = 0
    no_new_info_rounds: int = 0
    current_round_has_new_information: bool = False
    fingerprints: list[str] = field(default_factory=list)
    participants: list[str] = field(default_factory=list)
    moderator: str = ""
    per_agent_messages: dict[str, int] = field(default_factory=dict)
    transcript: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_value(cls, key: str, value: Mapping[str, Any]) -> "DiscussionState":
        return cls(
            discussion_id=str(value.get("discussion_id") or key),
            round_id=str(value.get("round_id") or ""),
            rounds=max(0, int(value.get("rounds") or 0)),
            messages_total=max(0, int(value.get("messages_total") or 0)),
            no_new_info_rounds=max(0, int(value.get("no_new_info_rounds") or 0)),
            current_round_has_new_information=bool(
                value.get("current_round_has_new_information")
            ),
            fingerprints=[str(item) for item in value.get("fingerprints", ())],
            participants=[str(item) for item in value.get("participants", ())],
            moderator=str(value.get("moderator") or ""),
            per_agent_messages={
                str(agent): max(0, int(count or 0))
                for agent, count in (
                    value.get("per_agent_messages", {}).items()
                    if isinstance(value.get("per_agent_messages"), Mapping)
                    else ()
                )
            },
            transcript=[
                dict(item) for item in value.get("transcript", ())
                if isinstance(item, Mapping)
            ],
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "discussion_id": self.discussion_id,
            "round_id": self.round_id,
            "rounds": self.rounds,
            "messages_total": self.messages_total,
            "no_new_info_rounds": self.no_new_info_rounds,
            "current_round_has_new_information": self.current_round_has_new_information,
            "fingerprints": list(self.fingerprints[-512:]),
            "participants": list(self.participants),
            "moderator": self.moderator,
            "per_agent_messages": dict(self.per_agent_messages),
            "transcript": [dict(item) for item in self.transcript[-500:]],
        }


__all__ = [
    "DISCUSSION_MODE",
    "DISCUSSION_ROLES",
    "EXECUTION_MODE",
    "SUMMARY_MODE",
    "TERMINAL_STATUSES",
    "DiscussionLimits",
    "DiscussionState",
    "ExecutionLimits",
    "FinishRequest",
    "SubagentMetrics",
    "SubagentRecord",
    "normalized_criteria",
    "normalized_mode",
    "utc_now",
]
