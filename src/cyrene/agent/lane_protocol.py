"""Canonical records and pure transcript projections for the two-lane loop.

The session message store remains authoritative.  Lane histories are derived
views over those records; they are never persisted as a second copy.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Literal, Mapping, Sequence

Lane = Literal["decision", "execution"]

SCHEMA_VERSION = 1
LANE_CACHE_EPOCH_VERSION = 1
LANE_EPOCHS_KEY = "lane_epochs"
HANDOFF_KIND = "execution_handoff"
OUTCOME_KIND = "execution_outcome"
_VALID_LANES = frozenset({"decision", "execution"})
_VALID_OUTCOME_STATUSES = frozenset(
    {"completed", "awaiting_user", "failed", "cancelled"}
)
_CURRENT_AGENT_LANE: ContextVar[Lane] = ContextVar(
    "cyrene_current_agent_lane",
    default="decision",
)


def current_agent_lane() -> Lane:
    return _CURRENT_AGENT_LANE.get()


def _normalized_lane(lane: Lane | str) -> Lane:
    stable_lane = str(lane or "").strip().lower()
    if stable_lane not in _VALID_LANES:
        raise ValueError(f"unsupported lane: {lane}")
    return stable_lane  # type: ignore[return-value]


@contextmanager
def bind_agent_lane(lane: Lane) -> Iterator[None]:
    stable_lane = _normalized_lane(lane)
    token = _CURRENT_AGENT_LANE.set(stable_lane)
    try:
        yield
    finally:
        _CURRENT_AGENT_LANE.reset(token)


def lane_epochs_from_state(state: Mapping[str, Any]) -> dict[Lane, int]:
    """Return the fixed two-lane generation map, defaulting old state to zero."""
    raw = state.get(LANE_EPOCHS_KEY)
    source = raw if isinstance(raw, Mapping) else {}

    def generation(lane: Lane) -> int:
        try:
            return max(0, int(source.get(lane) or 0))
        except (TypeError, ValueError):
            return 0

    return {
        "decision": generation("decision"),
        "execution": generation("execution"),
    }


def advance_lane_epoch_in_state(state: dict[str, Any], lane: Lane | str) -> int:
    """Advance exactly one persisted lane generation and return the new value."""
    stable_lane = _normalized_lane(lane)
    epochs = lane_epochs_from_state(state)
    epochs[stable_lane] += 1
    state[LANE_EPOCHS_KEY] = epochs
    return epochs[stable_lane]


def lane_cache_epoch_id(
    state: Mapping[str, Any],
    lane: Lane | str,
) -> str:
    """Return a stable cache-epoch identifier derived only from canonical state."""
    stable_lane = _normalized_lane(lane)
    try:
        session_epoch = max(0, int(state.get("_session_epoch") or 0))
    except (TypeError, ValueError):
        session_epoch = 0
    generation = lane_epochs_from_state(state)[stable_lane]
    return (
        f"lane-v{LANE_CACHE_EPOCH_VERSION}:"
        f"{stable_lane}:s{session_epoch}:e{generation}"
    )


def _json_value(value: Any, *, sort_mappings: bool = True) -> Any:
    """Copy JSON-shaped input into a deterministic, provider-neutral value."""
    if isinstance(value, Mapping):
        keys = sorted(value, key=lambda item: str(item)) if sort_mappings else value
        return {str(key): _json_value(value[key]) for key in keys}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def stable_json(value: Mapping[str, Any] | Sequence[Any]) -> str:
    """Serialize already ordered schema objects without volatile whitespace."""
    normalized = _json_value(value, sort_mappings=False)
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def _event_id(kind: str, turn_id: str, attempt: int) -> str:
    seed = f"{kind}\x00{turn_id}\x00{attempt}".encode("utf-8")
    return f"evt_{kind}_{hashlib.sha256(seed).hexdigest()[:20]}"


def _tuple_of_json(values: Iterable[Any] | None) -> tuple[Any, ...]:
    return tuple(_json_value(value) for value in (values or ()))


@dataclass(frozen=True, slots=True)
class ExecutionHandoff:
    event_id: str
    turn_id: str
    attempt: int
    request: str
    execution_brief: str
    hard_constraints: tuple[Any, ...]
    attachment_refs: tuple[Any, ...]
    conversation_delta: tuple[Any, ...]

    @classmethod
    def create(
        cls,
        turn_id: str,
        request: str,
        execution_brief: str = "",
        hard_constraints: Iterable[Any] | None = None,
        attachment_refs: Iterable[Any] | None = None,
        conversation_delta: Iterable[Any] | None = None,
        *,
        attempt: int = 1,
    ) -> "ExecutionHandoff":
        stable_turn_id = str(turn_id or "").strip()
        if not stable_turn_id:
            raise ValueError("turn_id is required")
        stable_attempt = max(1, int(attempt))
        return cls(
            event_id=_event_id(HANDOFF_KIND, stable_turn_id, stable_attempt),
            turn_id=stable_turn_id,
            attempt=stable_attempt,
            request=str(request or ""),
            execution_brief=str(execution_brief or "").strip()[:300],
            hard_constraints=_tuple_of_json(hard_constraints),
            attachment_refs=_tuple_of_json(attachment_refs),
            conversation_delta=_tuple_of_json(conversation_delta),
        )

    def to_dict(self) -> dict[str, Any]:
        # Field order is part of the cache protocol.
        return {
            "type": HANDOFF_KIND,
            "version": SCHEMA_VERSION,
            "event_id": self.event_id,
            "turn_id": self.turn_id,
            "attempt": self.attempt,
            "request": self.request,
            "execution_brief": self.execution_brief,
            "hard_constraints": [_json_value(item) for item in self.hard_constraints],
            "attachment_refs": [_json_value(item) for item in self.attachment_refs],
            "conversation_delta": [_json_value(item) for item in self.conversation_delta],
        }

    def stable_json(self) -> str:
        return stable_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    event_id: str
    turn_id: str
    attempt: int
    status: str
    public_reply: str
    state_summary: str
    artifacts: tuple[Any, ...]
    unresolved: tuple[Any, ...]
    conversation_delta: tuple[Any, ...]

    @classmethod
    def create(
        cls,
        turn_id: str,
        status: str,
        public_reply: str = "",
        state_summary: str = "",
        artifacts: Iterable[Any] | None = None,
        unresolved: Iterable[Any] | None = None,
        conversation_delta: Iterable[Any] | None = None,
        *,
        attempt: int = 1,
    ) -> "ExecutionOutcome":
        stable_turn_id = str(turn_id or "").strip()
        if not stable_turn_id:
            raise ValueError("turn_id is required")
        stable_status = str(status or "").strip().lower()
        if stable_status not in _VALID_OUTCOME_STATUSES:
            raise ValueError(f"unsupported execution outcome status: {status}")
        stable_attempt = max(1, int(attempt))
        return cls(
            event_id=_event_id(OUTCOME_KIND, stable_turn_id, stable_attempt),
            turn_id=stable_turn_id,
            attempt=stable_attempt,
            status=stable_status,
            public_reply=str(public_reply or ""),
            state_summary=str(state_summary or "").strip(),
            artifacts=_tuple_of_json(artifacts),
            unresolved=_tuple_of_json(unresolved),
            conversation_delta=_tuple_of_json(conversation_delta),
        )

    def to_dict(self) -> dict[str, Any]:
        # Field order is part of the cache protocol.
        return {
            "type": OUTCOME_KIND,
            "version": SCHEMA_VERSION,
            "event_id": self.event_id,
            "turn_id": self.turn_id,
            "attempt": self.attempt,
            "status": self.status,
            "public_reply": self.public_reply,
            "state_summary": self.state_summary,
            "artifacts": [_json_value(item) for item in self.artifacts],
            "unresolved": [_json_value(item) for item in self.unresolved],
            "conversation_delta": [_json_value(item) for item in self.conversation_delta],
        }

    def stable_json(self) -> str:
        return stable_json(self.to_dict())


def _normalized_lane_refs(lane_refs: Iterable[str] | str) -> list[Lane]:
    raw_refs = [lane_refs] if isinstance(lane_refs, str) else list(lane_refs)
    refs: list[Lane] = []
    for raw in raw_refs:
        lane = str(raw or "").strip().lower()
        if lane not in _VALID_LANES:
            raise ValueError(f"unsupported lane: {raw}")
        if lane not in refs:
            refs.append(lane)  # type: ignore[arg-type]
    if not refs:
        raise ValueError("at least one lane_ref is required")
    return refs


def tag_lane_record(
    message: Mapping[str, Any],
    lane_refs: Iterable[str] | str,
    *,
    record_kind: str = "conversation",
    persist_model_record: bool = True,
    hidden_from_ui: bool = False,
) -> dict[str, Any]:
    """Return one canonical store record with the minimal lane metadata."""
    tagged = dict(message)
    tagged["lane_refs"] = _normalized_lane_refs(lane_refs)
    tagged["record_kind"] = str(record_kind or "conversation").strip()
    tagged["persist_model_record"] = bool(persist_model_record)
    if hidden_from_ui:
        tagged["hidden_from_ui"] = True
    return tagged


def build_execution_handoff_message(event: ExecutionHandoff) -> dict[str, Any]:
    return tag_lane_record(
        {
            "role": "user",
            "content": event.stable_json(),
            "message_id": f"msg_{event.event_id}",
            "event_id": event.event_id,
            "turn_id": event.turn_id,
        },
        "execution",
        record_kind=HANDOFF_KIND,
        persist_model_record=True,
        hidden_from_ui=True,
    )


def build_execution_outcome_message(
    event: ExecutionOutcome,
    *,
    tool_call_id: str,
) -> dict[str, Any]:
    stable_tool_call_id = str(tool_call_id or "").strip()
    if not stable_tool_call_id:
        raise ValueError("tool_call_id is required for an execution outcome")
    return tag_lane_record(
        {
            "role": "tool",
            "tool_call_id": stable_tool_call_id,
            "content": event.stable_json(),
            "message_id": f"msg_{event.event_id}",
            "event_id": event.event_id,
            "turn_id": event.turn_id,
        },
        "decision",
        record_kind=OUTCOME_KIND,
        persist_model_record=True,
        hidden_from_ui=True,
    )


def _legacy_shared_record(message: Mapping[str, Any]) -> bool:
    """Keep safe pre-migration conversation in a lane view."""
    if message.get("question_prompt"):
        # Historical normalized prompts are UI records.  Codex histories with
        # no lane metadata still take the unchanged legacy fast path above.
        return False
    role = str(message.get("role") or "").strip()
    if role == "tool":
        return False
    if role == "assistant" and message.get("tool_calls"):
        return False
    return role in {"system", "user", "assistant"}


def project_lane_history(
    messages: Sequence[Mapping[str, Any]],
    lane: Lane,
) -> list[dict[str, Any]]:
    """Project one independent model transcript from the canonical records.

    A history with no lane metadata is returned unchanged as the Codex/legacy
    path.  Once lane records exist, another lane's assistant/tool trace is
    excluded; only explicitly shared records and safe visible pre-migration
    conversation remain available to both lanes.
    """
    stable_lane = str(lane or "").strip().lower()
    if stable_lane not in _VALID_LANES:
        raise ValueError(f"unsupported lane: {lane}")
    source = [dict(message) for message in messages if isinstance(message, Mapping)]
    if not any(message.get("lane_refs") is not None for message in source):
        return source

    projected: list[dict[str, Any]] = []
    for message in source:
        if message.get("persist_model_record") is False:
            continue
        raw_refs = message.get("lane_refs")
        if raw_refs is None:
            if _legacy_shared_record(message):
                projected.append(message)
            continue
        refs = [raw_refs] if isinstance(raw_refs, str) else list(raw_refs or [])
        if stable_lane in {str(ref or "").strip().lower() for ref in refs}:
            projected.append(message)
    return projected


__all__ = [
    "ExecutionHandoff",
    "ExecutionOutcome",
    "HANDOFF_KIND",
    "LANE_CACHE_EPOCH_VERSION",
    "LANE_EPOCHS_KEY",
    "Lane",
    "OUTCOME_KIND",
    "SCHEMA_VERSION",
    "advance_lane_epoch_in_state",
    "build_execution_handoff_message",
    "build_execution_outcome_message",
    "bind_agent_lane",
    "current_agent_lane",
    "lane_cache_epoch_id",
    "lane_epochs_from_state",
    "project_lane_history",
    "stable_json",
    "tag_lane_record",
]
