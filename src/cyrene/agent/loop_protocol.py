"""Small, deterministic protocol helpers for the main agent loop.

The orchestration loop owns model calls, persistence, and tool execution.  This
module keeps the response-shape decisions pure so they can be reviewed and
tested without recreating an entire agent run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from cyrene.model_runtime.messages import parse_tool_arguments


def _tool_name(tool_call: dict[str, Any]) -> str:
    return str(tool_call.get("function", {}).get("name") or "").strip()


@dataclass(frozen=True, slots=True)
class Phase1Decision:
    """Normalized control selection returned by the decision phase."""

    response: dict[str, Any]
    tool_calls: tuple[dict[str, Any], ...]
    concrete_calls: tuple[dict[str, Any], ...]
    invalid_tool_names: tuple[str, ...]
    use_tools_call: dict[str, Any] | None
    ask_user_call: dict[str, Any] | None
    quit_call: dict[str, Any] | None

    @property
    def enters_execution(self) -> bool:
        return self.use_tools_call is not None or bool(self.concrete_calls)

    @property
    def terminal_in_phase1(self) -> bool:
        return not self.enters_execution


def normalize_phase1_decision(
    response: dict[str, Any],
    *,
    allowed_tool_names: set[str],
    wire_tool_names: set[str],
    can_promote_tools: bool,
    system_initiated: bool,
) -> Phase1Decision:
    """Normalize Phase-1 signals once and classify the resulting decision.

    ``use_tools`` carries only its bounded execution brief.  If a model emits a
    concrete action and ``ask_user`` together, clarification wins.  A concrete
    action otherwise wins over a contradictory sibling ``quit``.  These were
    previously spread over several mutable scans in ``agent.py``.
    """

    raw_calls = [
        tool_call
        for tool_call in (response.get("tool_calls") or [])
        if isinstance(tool_call, dict)
    ]
    promotable_names = {
        _tool_name(tool_call)
        for tool_call in raw_calls
        if can_promote_tools
        and _tool_name(tool_call) in wire_tool_names
        and _tool_name(tool_call) not in allowed_tool_names
    }
    invalid_names = tuple(
        _tool_name(tool_call)
        for tool_call in raw_calls
        if (
            system_initiated and _tool_name(tool_call) == "ask_user"
        ) or (
            _tool_name(tool_call) not in allowed_tool_names
            and _tool_name(tool_call) not in promotable_names
        )
    )

    normalized_calls: list[dict[str, Any]] = []
    for tool_call in raw_calls:
        if _tool_name(tool_call) != "use_tools":
            normalized_calls.append(tool_call)
            continue
        try:
            raw_arguments = parse_tool_arguments(
                tool_call.get("function", {}).get("arguments")
            )
        except Exception:
            raw_arguments = {}
        execution_brief = str(raw_arguments.get("execution_brief") or "").strip()[:300]
        normalized_calls.append({
            **tool_call,
            "function": {
                **tool_call.get("function", {}),
                "name": "use_tools",
                "arguments": json.dumps(
                    {"execution_brief": execution_brief},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        })

    concrete_calls = [
        tool_call
        for tool_call in normalized_calls
        if can_promote_tools
        and _tool_name(tool_call) in wire_tool_names
        and _tool_name(tool_call) not in {"use_tools", "ask_user", "quit"}
    ]
    ask_calls = [
        tool_call
        for tool_call in normalized_calls
        if not system_initiated and _tool_name(tool_call) == "ask_user"
    ]
    if concrete_calls and ask_calls:
        normalized_calls = ask_calls
        concrete_calls = []
    elif concrete_calls:
        normalized_calls = [
            tool_call
            for tool_call in normalized_calls
            if _tool_name(tool_call) != "quit"
        ]

    normalized_response = response
    if response.get("tool_calls") is not None:
        normalized_response = {**response, "tool_calls": normalized_calls}

    use_tools_call = None
    ask_user_call = None
    quit_call = None
    for tool_call in normalized_calls:
        name = _tool_name(tool_call)
        if name == "use_tools":
            use_tools_call = tool_call
        elif name == "ask_user" and not system_initiated:
            ask_user_call = tool_call
        elif name == "quit":
            quit_call = tool_call

    return Phase1Decision(
        response=normalized_response,
        tool_calls=tuple(normalized_calls),
        concrete_calls=tuple(concrete_calls),
        invalid_tool_names=invalid_names,
        use_tools_call=use_tools_call,
        ask_user_call=ask_user_call,
        quit_call=quit_call,
    )


def deferred_decision_protocol_entries(
    response: dict[str, Any],
    *,
    round_id: str,
    assistant_entry_factory: Callable[[dict[str, Any], str], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Close a superseded Phase-1 response before appending new guidance."""

    assistant_entry = assistant_entry_factory(response, round_id)
    assistant_entry["hidden_from_ui"] = True
    entries = [assistant_entry]
    for tool_call in response.get("tool_calls") or []:
        entry: dict[str, Any] = {
            "role": "tool",
            "tool_call_id": tool_call["id"],
            "content": "Decision deferred because new user guidance arrived.",
            "hidden_from_ui": True,
        }
        if round_id:
            entry["round_id"] = round_id
        entries.append(entry)
    return entries


def execution_outcome_tool_defs(
    tool_defs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add structured outcome fields to dual-lane Phase-2 ``quit`` only.

    The legacy/Codex bundle is never passed through this helper, so its stable
    tool schema and shared-prefix behavior remain unchanged.
    """

    outcome_defs: list[dict[str, Any]] = []
    for tool_def in tool_defs:
        function = tool_def.get("function") if isinstance(tool_def, dict) else None
        if not isinstance(function, dict) or _tool_name({"function": function}) != "quit":
            outcome_defs.append(tool_def)
            continue
        outcome_defs.append({
            **tool_def,
            "function": {
                **function,
                "description": (
                    "Terminal execution signal. Write the complete public answer "
                    "in assistant content, then provide a concise state summary, "
                    "artifacts, and unresolved items here for the decision lane."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "state_summary": {"type": "string", "maxLength": 1200},
                        "artifacts": {
                            "type": "array",
                            "items": {"type": "object"},
                            "maxItems": 32,
                        },
                        "unresolved": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 32,
                        },
                    },
                    "required": ["state_summary", "artifacts", "unresolved"],
                    "additionalProperties": False,
                },
            },
        })
    return outcome_defs


def execution_outcome_arguments(
    response: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return the structured metadata from an Execution-lane ``quit``.

    ``quit`` remains only the terminal control signal: its arguments contain
    durable completion metadata, not the public answer.  Keeping this parser in
    the protocol module gives Outcome persistence and empty-body finalization a
    single interpretation of that metadata.
    """

    for tool_call in (response or {}).get("tool_calls") or []:
        if not isinstance(tool_call, Mapping) or _tool_name(dict(tool_call)) != "quit":
            continue
        try:
            parsed = parse_tool_arguments(
                tool_call.get("function", {}).get("arguments")
            )
        except Exception:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def side_conversation_delta(
    coordinator_request: Any,
) -> list[dict[str, Any]]:
    """Extract the coordinator-frozen public Side context from its wrapper.

    Only the two explicit public tags are accepted.  Unrecognized enriched
    prompts are not forwarded, which prevents private run instructions from
    becoming a handoff payload merely because they differ from the public
    request.
    """

    candidates: list[str] = []
    if isinstance(coordinator_request, str):
        candidates.append(coordinator_request)
    elif isinstance(coordinator_request, Sequence) and not isinstance(
        coordinator_request, (bytes, bytearray)
    ):
        for item in coordinator_request:
            if isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    candidates.append(text)
                content = item.get("content")
                if isinstance(content, str):
                    candidates.append(content)

    def section(source: str, name: str) -> str | None:
        opening = f"<{name}>"
        closing = f"</{name}>"
        if opening not in source or closing not in source:
            return None
        return source.split(opening, 1)[1].split(closing, 1)[0].strip()

    for source in candidates:
        parent_transcript = section(source, "main_conversation")
        selected_quote = section(source, "selected_quote")
        if parent_transcript is None or selected_quote is None:
            continue
        return [{
            "type": "side_conversation_snapshot",
            "parent_public_transcript": parent_transcript,
            "selected_quote": selected_quote,
        }]
    return []


def _lane_refs(message: Mapping[str, Any]) -> set[str]:
    raw_refs = message.get("lane_refs")
    if raw_refs is None:
        return set()
    refs = [raw_refs] if isinstance(raw_refs, str) else list(raw_refs or [])
    return {str(ref or "").strip().lower() for ref in refs}


def _public_conversation_item(message: Mapping[str, Any]) -> dict[str, str] | None:
    """Project one public utterance without model- or storage-only metadata."""

    if bool(message.get("hidden_from_ui")):
        return None
    role = str(message.get("role") or "").strip().lower()
    if role not in {"user", "assistant"}:
        return None
    if role == "assistant" and message.get("tool_calls"):
        return None
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    return {
        "type": "conversation_message",
        "role": role,
        "content": content,
    }


def decision_conversation_delta(
    messages: Sequence[Mapping[str, Any]],
    *,
    current_user_message_id: str = "",
    runtime_guidance_message_ids: Sequence[str] = (),
) -> list[dict[str, str]]:
    """Return public Decision dialogue not observed by the Execution lane.

    The most recent execution outcome is the synchronization boundary. Legacy
    untagged dialogue is already visible in both projections, so only explicit
    Decision records (plus in-memory runtime guidance) are transferred. The
    current request is carried by ``ExecutionHandoff.request`` and is excluded
    by message id instead of being duplicated in the delta.
    """

    boundary = -1
    for index, message in enumerate(messages):
        if str(message.get("record_kind") or "") == "execution_outcome":
            boundary = index

    excluded_id = str(current_user_message_id or "").strip()
    guidance_ids = {
        str(message_id or "").strip()
        for message_id in runtime_guidance_message_ids
        if str(message_id or "").strip()
    }
    delta: list[dict[str, str]] = []
    for message in messages[boundary + 1 :]:
        if excluded_id and str(message.get("message_id") or "") == excluded_id:
            continue
        refs = _lane_refs(message)
        if (
            "decision" not in refs
            and str(message.get("message_id") or "").strip() not in guidance_ids
        ):
            continue
        item = _public_conversation_item(message)
        if item is not None:
            delta.append(item)
    return delta


def execution_conversation_delta(
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Return execution-owned clarification/guidance since the last handoff.

    Tool traces, progress narration, final answers, and hidden protocol records
    are deliberately omitted.  ``public_reply`` carries the final answer while
    this delta only closes the conversational gap created by an execution-owned
    wait or by in-flight user guidance.
    """

    boundary = -1
    for index, message in enumerate(messages):
        if str(message.get("record_kind") or "") == "execution_handoff":
            boundary = index

    delta: list[dict[str, str]] = []
    for message in messages[boundary + 1 :]:
        refs = _lane_refs(message)
        if refs and "execution" not in refs:
            continue
        role = str(message.get("role") or "").strip().lower()
        if role == "assistant" and str(message.get("record_kind") or "") != "pending_question":
            continue
        if role == "user" and not (
            bool(message.get("runtime_guidance"))
            or str(message.get("record_kind") or "") == "conversation"
        ):
            continue
        item = _public_conversation_item(message)
        if item is not None:
            delta.append(item)
    return delta


def execution_finalization_packet(
    messages: Sequence[Mapping[str, Any]],
    response: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build the append-only no-tools request for an empty-body ``quit``.

    The packet supplies the facts that a final reply needs without treating a
    progress narration as a previous public answer.  It is appended to the
    Execution transcript for the bounded no-tools call; it is never persisted
    as a third lane or copied into the Decision transcript.
    """

    request = ""
    for message in reversed(messages):
        if str(message.get("record_kind") or "") != "execution_handoff":
            continue
        try:
            handoff = json.loads(str(message.get("content") or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(handoff, Mapping):
            request = str(handoff.get("request") or "")
            if request:
                break

    outcome_args = execution_outcome_arguments(response)
    artifacts = outcome_args.get("artifacts")
    unresolved = outcome_args.get("unresolved")
    return {
        "type": "execution_finalization_request",
        "version": 1,
        "instruction": (
            "Write the complete final user-facing reply now from this packet. "
            "Do not call tools and do not refer to any earlier message."
        ),
        "request": request,
        "state_summary": str(outcome_args.get("state_summary") or ""),
        "artifacts": list(artifacts) if isinstance(artifacts, list) else [],
        "unresolved": list(unresolved) if isinstance(unresolved, list) else [],
        "conversation_delta": execution_conversation_delta(messages),
        "reply_contract": {
            "self_contained": True,
            "prior_public_reply_available": False,
            "include_unresolved_items": True,
            "language": "match_request",
        },
    }


def public_assistant_artifact_refs(
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Collect short public file refs emitted by Execution assistants.

    This intentionally reads assistant attachments only. Tool results can be
    large or private and must never be copied into an outcome merely to recover
    an artifact path.
    """

    boundary = -1
    for index, message in enumerate(messages):
        if str(message.get("record_kind") or "") == "execution_handoff":
            boundary = index

    allowed_keys = (
        "id",
        "name",
        "content_type",
        "size",
        "kind",
        "url",
        "path",
    )
    artifacts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for message in messages[boundary + 1 :]:
        if str(message.get("role") or "").strip().lower() != "assistant":
            continue
        for attachment in message.get("attachments") or []:
            if not isinstance(attachment, Mapping):
                continue
            public_ref = {
                key: attachment[key]
                for key in allowed_keys
                if attachment.get(key) not in (None, "")
            }
            if not public_ref:
                continue
            identity = str(
                public_ref.get("id")
                or public_ref.get("url")
                or public_ref.get("path")
                or public_ref.get("name")
                or ""
            )
            if identity in seen:
                continue
            seen.add(identity)
            artifacts.append(public_ref)
    return artifacts
