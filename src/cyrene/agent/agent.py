"""Core two-phase agent loop.

This module contains ONLY ``_run_main_agent``, the heart of the agent:
Phase 1 (policy-gated decision on the enabled-package wire bundle) → Phase 2
(progressive tool loop with subagent monitoring and Deep Research Phase 3).
"""

import asyncio
import importlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import uuid4

from cyrene.agent.replies import (
    _contains_visible_dsml_tool_markup,
    _is_placeholder_reply,
    _recover_final_reply,
)
from cyrene.agent.deep_reflection import create_deep_reflection_record, project_history_for_llm
from cyrene.agent.loop_protocol import (
    Phase1Decision,
    decision_conversation_delta,
    deferred_decision_protocol_entries,
    execution_conversation_delta,
    execution_finalization_packet,
    execution_outcome_arguments,
    execution_outcome_tool_defs,
    normalize_phase1_decision,
    public_assistant_artifact_refs,
    side_conversation_delta,
)
from cyrene.agent.lane_protocol import (
    ExecutionHandoff,
    ExecutionOutcome,
    bind_agent_lane,
    build_execution_handoff_message,
    build_execution_outcome_message,
    project_lane_history,
    tag_lane_record,
)
from cyrene.agent.message import (
    _apply_assistant_meta,
    _assistant_entry_from_response,
    _ensure_message_identity,
    _flush_intermediate_user_replies,
    _tool_result_requests_user_input,
)
from cyrene.agent.prompts import (
    _DEEP_RESEARCH_PHASE1_DECISION,
    _DUAL_LANE_DECISION_SYSTEM_PROMPT,
    _DUAL_LANE_EXECUTION_SYSTEM_PROMPT,
    _MAIN_AGENT_PROMPT_TEMPLATE,
    PHASE1_DECISION_PROMPT,
    prompt_for_enabled_tool_packs,
)
from cyrene.agent.model_service import take_final_reply_usage
from cyrene.agent.session import (
    _append_session_message,
    _pending_question_resume_context,
    _save_session_messages,
    append_or_upsert_lane_record,
)
from cyrene.agent.state import (
    _AWAITING_USER_SENTINEL,
    _call_llm,
    _caller_type,
    _current_command,
    _current_round_id,
    _current_session_id,
    _DEEP_RESEARCH_LIGHT_TOOL_DEFS,
    _deep_research_first_round,
    _deep_research_mode,
    _emit_reply_stream_event,
    _ensure_session,
    _LIGHT_TOOL_DEFS,
    _llm_phase_override,
    _publish_runtime_event,
    _streaming_reply_requested,
    _ui_round_assistant_meta,
    _ui_round_hide_initial_detail,
    activate_run_model_lease,
    current_run_transcript_policy,
    has_active_run_model_lease,
    reset_run_model_lease,
)
from cyrene.agent.transcript_policy import TranscriptPolicy
from cyrene.model_runtime.messages import (
    assistant_text,
    parse_tool_arguments,
)
from cyrene.observability.context_trace import attach_context, context_block
from cyrene.runtime.secret_redaction import redact_value
from cyrene.tooling import (
    execute_wire_tool,
    get_main_wire_tool_defs,
    get_wire_tool_execution_metadata,
    resolve_wire_call,
)
from cyrene.tooling.mcp_content import build_mcp_observation_message
from cyrene.tooling.result_store import (
    project_tool_result_batch_for_model,
    project_tool_result_for_model,
)
from cyrene.workbench.inbox import current_workbench_inbox

_PHASE1_DECISION_PROMPT = PHASE1_DECISION_PROMPT

logger = logging.getLogger(__name__)

_MAX_MISSING_CONTROL_REPAIRS = 1
_PHASE1_TOOL_CHOICE = "required"


class AgentControlProtocolError(RuntimeError):
    """Raised when the model cannot produce a control signal or usable reply."""


async def _call_phase1_llm(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    """Require one cache-stable Decision control signal from every provider."""
    token = _llm_phase_override.set("phase1")
    try:
        return await _call_llm(
            messages,
            tools=tools,
            tool_choice=_PHASE1_TOOL_CHOICE,
        )
    finally:
        _llm_phase_override.reset(token)

# Backward-compatible six-argument monkeypatch point used by integrations and
# tests. The implementation routes the stable wire protocol as the main actor.
async def _execute_tool(
    name: str,
    arguments: dict[str, Any],
    bot: Any,
    chat_id: int,
    db_path: str,
    notify_state: dict[str, bool] | None,
) -> str:
    return await execute_wire_tool(
        name,
        arguments,
        bot,
        chat_id,
        db_path,
        notify_state,
        actor="main",
    )


def _redact_sensitive_tool_calls_for_storage(
    message: dict[str, Any],
) -> dict[str, Any]:
    """Persist terminal credential delivery without persisting the credential."""
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        return message
    safe_calls: list[Any] = []
    changed = False
    for raw_call in tool_calls:
        if not isinstance(raw_call, dict):
            safe_calls.append(raw_call)
            continue
        function = raw_call.get("function")
        if not isinstance(function, dict):
            safe_calls.append(raw_call)
            continue
        raw_arguments = function.get("arguments")
        try:
            arguments = parse_tool_arguments(raw_arguments)
        except ValueError:
            safe_calls.append(raw_call)
            continue
        if not bool(arguments.get("sensitive")):
            safe_calls.append(raw_call)
            continue
        safe_arguments = dict(arguments)
        for field in ("text", "command"):
            if field in safe_arguments:
                safe_arguments[field] = "[REDACTED_SENSITIVE_INPUT]"
        safe_function = dict(function)
        safe_function["arguments"] = (
            safe_arguments
            if isinstance(raw_arguments, dict)
            else json.dumps(safe_arguments, ensure_ascii=False, separators=(",", ":"))
        )
        safe_call = dict(raw_call)
        safe_call["function"] = safe_function
        safe_calls.append(safe_call)
        changed = True
    if not changed:
        return message
    safe_message = dict(message)
    safe_message["tool_calls"] = safe_calls
    return safe_message


async def _publish_tool_call_started(
    tool_call_id: str, tool_name: str, arguments: dict[str, Any]
) -> None:
    """Tell the live transcript when a tool is actually about to execute."""
    await _publish_runtime_event({
        "type": "tool_call_started",
        "tool_call_id": str(tool_call_id),
        "tool": str(tool_name or ""),
        "args": redact_value(arguments),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


async def _publish_tool_call_finished(
    tool_call_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    status: str,
    result: Any = None,
    error: BaseException | None = None,
) -> None:
    """Close the live lifecycle opened by ``_publish_tool_call_started``.

    The wire wrapper owns this event because progressive ``discover`` and
    ``describe`` calls return before reaching the concrete executor. Publishing
    here guarantees that every started wire call has a matching terminal event,
    regardless of which gateway branch handled it.
    """
    try:
        error_payload = _tool_error_payload(result=result, error=error)
        event = {
            "type": "tool_call_finished",
            "tool_call_id": str(tool_call_id),
            "tool": str(tool_name or ""),
            "args": redact_value(arguments),
            "status": str(status or "completed"),
            "failed": str(status or "").casefold() == "failed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if error_payload:
            event["error"] = error_payload
            event["error_code"] = error_payload.get("error_code")
            event["message"] = error_payload.get("message")
            if error_payload.get("details") is not None:
                event["details"] = error_payload["details"]
        await _publish_runtime_event(event)
    except Exception:
        # Live activity is observability. A disconnected SSE subscriber must not
        # turn an otherwise successful tool result into an agent failure.
        logger.debug("Failed to publish tool completion for %s", tool_name, exc_info=True)


def _tool_error_payload(*, result: Any = None, error: BaseException | None = None) -> dict[str, Any] | None:
    if error is not None:
        return {
            "error_code": str(getattr(error, "code", "tool_exception") or "tool_exception"),
            "message": str(error)[:1000],
        }
    payload = result
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            if payload.casefold().startswith(("tool failed:", "tool unavailable:")):
                return {"error_code": "tool_failed", "message": payload[:1000]}
            return None
    if not isinstance(payload, dict) or payload.get("status") != "error":
        return None
    if isinstance(payload.get("result"), dict) and payload["result"].get("status") == "error":
        payload = payload["result"]
    nested = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    details = payload.get("details", nested.get("details"))
    safe_details = redact_value(details) if details is not None else None
    if safe_details is not None:
        try:
            encoded = json.dumps(safe_details, ensure_ascii=False)
            if len(encoded) > 4000:
                safe_details = {"summary": encoded[:4000] + "…"}
        except (TypeError, ValueError):
            safe_details = {"summary": str(safe_details)[:4000]}
    result_payload: dict[str, Any] = {
        "error_code": str(payload.get("error_code") or nested.get("type") or nested.get("code") or "tool_error"),
        "message": str(payload.get("message") or nested.get("message") or "Tool call failed.")[:1000],
    }
    if safe_details is not None:
        result_payload["details"] = safe_details
    return result_payload


async def _execute_tool_for_call(
    tool_call_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    bot: Any,
    chat_id: int,
    db_path: str,
) -> str:
    """Execute a tool while tagging its eventual completion with its call id."""
    from cyrene.tooling.executor import bind_active_tool_call

    # Publish from the executor boundary, not while the LLM's whole batch is
    # merely being submitted. Ordered predecessors (notably ``send_message``)
    # have completed by the time this coroutine is entered, so the live event
    # order now reflects the same causal order persisted in session history.
    await _publish_tool_call_started(tool_call_id, tool_name, arguments)
    binding = bind_active_tool_call(str(tool_call_id))
    try:
        result = await _execute_tool(
            tool_name, arguments, bot, chat_id, db_path, None
        )
    except asyncio.CancelledError as exc:
        await _publish_tool_call_finished(
            tool_call_id,
            tool_name,
            arguments,
            status="cancelled",
            error=exc,
        )
        raise
    except Exception as exc:
        await _publish_tool_call_finished(
            tool_call_id,
            tool_name,
            arguments,
            status="failed",
            error=exc,
        )
        raise
    else:
        await _publish_tool_call_finished(
            tool_call_id,
            tool_name,
            arguments,
            status="completed" if _wire_result_succeeded(result) else "failed",
            result=result,
        )
        return result
    finally:
        binding.reset()


def _inbox_tool_metadata(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Combine scheduler metadata with redacted arguments for the live UI."""
    return {
        **get_wire_tool_execution_metadata(tool_name, arguments, actor="main"),
        "arguments": redact_value(arguments),
    }


def _resolved_capability_id(tool_name: str, arguments: dict[str, Any]) -> str:
    try:
        return resolve_wire_call(
            tool_name,
            arguments,
            actor="main",
        ).capability_id
    except Exception:
        return str(tool_name or "")


def _wire_result_succeeded(result: Any) -> bool:
    text = str(result or "")
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return not text.casefold().startswith(("tool failed:", "tool unavailable:"))
    return not isinstance(payload, dict) or payload.get("status") != "error"


def _tool_def_name(tool_def: dict[str, Any]) -> str:
    return str(tool_def.get("function", {}).get("name") or "").strip()


def _enabled_wire_names(tool_defs: list[dict[str, Any]]) -> set[str]:
    names_in_bundle = {_tool_def_name(tool_def) for tool_def in tool_defs}
    if "toolbox" in names_in_bundle:
        # The provider-visible universal gateway deliberately hides package
        # names. Prompt filtering still needs the run's allowed package set for
        # compatibility blocks outside the stable main tool protocol.
        from cyrene.tooling.wire import enabled_module_tool_names

        return set(enabled_module_tool_names("main"))
    names = {
        name for name in names_in_bundle if name.endswith("_tools")
    }
    if any(_tool_def_name(tool_def) == "PowerPointToolSearch" for tool_def in tool_defs):
        names.add("office_tools")
    return names


def _missing_completion_signal_entry(
    round_id: str,
    *,
    structured_execution_finalize: bool = False,
) -> dict[str, Any]:
    """Return the protocol correction for a response without a tool signal."""
    next_action = (
        "If work remains, call the next required real tool now. If the task is "
        "complete, call `quit` with a complete self-contained `public_reply`, "
        "`state_summary`, `artifacts`, and `unresolved` record; the coordinator "
        "will publish `public_reply` directly. Do not refer to the rejected text."
        if structured_execution_finalize
        else
        "If work remains, call the next required real tool now. If the task is "
        "complete, restate the entire self-contained user-facing answer in this "
        "response and call `quit` as the terminal signal."
    )
    return {
        "role": "user",
        "content": (
            "[Control protocol error] Your previous response did not call a "
            "control or execution tool. That response was rejected as a "
            "terminal result and was not published to the user, so it cannot be "
            "referenced as an earlier answer. The run is still active. "
            + next_action
            + " Do not say that the result appeared in a previous message. Plain assistant "
            "text without an explicit control signal never ends the run."
        ),
        "hidden_from_ui": True,
        **({"round_id": round_id} if round_id else {}),
    }


def _merge_usage_records(*records: Any) -> dict[str, Any]:
    """Merge token-usage records without dropping hidden protocol calls."""
    merged: dict[str, Any] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        for key, value in record.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                previous = merged.get(key)
                merged[key] = (
                    previous + value
                    if isinstance(previous, (int, float))
                    and not isinstance(previous, bool)
                    else value
                )
            else:
                merged.setdefault(key, value)
    return merged


def _attach_final_usage(entry: dict[str, Any]) -> dict[str, Any]:
    """Carry the final-reply call's token usage onto the persisted entry."""
    usage = take_final_reply_usage()
    if usage:
        entry["usage"] = _merge_usage_records(entry.get("usage"), usage)
    return entry


def _history_has_tool_results(messages: list[dict[str, Any]]) -> bool:
    return any(
        (
            str(message.get("role") or "") == "tool"
            or (
                str(message.get("role") or "") == "assistant"
                and bool(message.get("tool_calls"))
            )
        )
        for message in messages
        if isinstance(message, dict)
    )


def _safe_terminal_reply_from_response(
    response_obj: dict[str, Any],
    base_messages: list[dict[str, Any]],
) -> str:
    """Return an existing terminal reply only when it is safe and complete.

    A terminal answer must live in normal assistant content. ``quit`` is only a
    control signal, so its arguments are intentionally ignored. Tool-markup
    content is never delivered directly, and placeholder-only replies after real
    tool work require a no-tool recovery call.
    """
    has_tool_results = _history_has_tool_results(base_messages)
    text = assistant_text(response_obj).strip()
    if not text or _contains_visible_dsml_tool_markup(text):
        return ""
    if has_tool_results and _is_placeholder_reply(text):
        return ""
    return text


def _annotate_history_context(history: list) -> list[dict[str, Any]]:
    annotated: list[dict[str, Any]] = []
    for index, raw in enumerate(history or []):
        if not isinstance(raw, dict):
            continue
        message = dict(raw)
        role = str(message.get("role") or "")
        content = message.get("content") or ""
        if message.get("deep_reflection_record"):
            block = context_block(
                f"history.deep_reflection.{message.get('reflection_id') or message.get('message_id') or index}",
                "history_deep_reflection",
                source="data/state.json",
                reason="visible deep reflection record; projected before LLM calls",
                transforms=["visible_record"],
                content=content,
                metadata={"reflection_id": str(message.get("reflection_id") or "")},
            )
        elif message.get("compacted_block"):
            block = context_block(
                f"history.compacted.{message.get('message_id') or index}",
                "history_compacted",
                source="data/state.json",
                reason="older session history compacted for token budget",
                transforms=["mechanical_compaction", "llm_distillation"] if message.get("llm_compacted") else ["mechanical_compaction"],
                content=content,
                metadata={"message_id": message.get("message_id", ""), "round_id": message.get("round_id", "")},
            )
        elif message.get("report_expanded_for_turn"):
            block = context_block(
                f"history.report_expanded.{message.get('message_id') or index}",
                "history",
                source="conversation archive",
                reason="user explicitly referenced an archived report",
                transforms=["restore_archived_report_for_turn"],
                content=content,
                metadata={"message_id": message.get("message_id", ""), "round_id": message.get("round_id", "")},
            )
        elif message.get("chat_group_context_event"):
            event = message.get("chat_group_event") if isinstance(message.get("chat_group_event"), dict) else {}
            block = context_block(
                f"history.chat_group.{message.get('message_id') or index}",
                "chat_group",
                source="data/sessions/<session_id>/state.json",
                reason="append-only authoritative chat-group membership event",
                transforms=["append_only_event"],
                content=content,
                metadata={
                    "project_id": str(event.get("projectId") or ""),
                    "group_id": str(event.get("groupId") or ""),
                    "access": str(event.get("access") or ""),
                    "membership_revision": int(event.get("projectMembershipRevision") or 0),
                },
            )
        elif role == "tool":
            block = context_block(
                f"history.tool_result.{message.get('tool_call_id') or index}",
                "tool_result",
                source="data/state.json",
                reason="tool result from session history",
                content=content,
                metadata={"tool_call_id": message.get("tool_call_id", ""), "round_id": message.get("round_id", "")},
            )
        elif role == "system":
            block_id = f"history.system.{index}"
            block_type = "system"
            reason = "system message from prepared history"
            if str(content).startswith("[Restored context]"):
                block_id = "short_term.restored"
                block_type = "short_term"
                reason = "short-term memory restored because session history was empty"
            block = context_block(
                block_id,
                block_type,
                source="prepared history",
                reason=reason,
                content=content,
                metadata={"message_id": message.get("message_id", ""), "round_id": message.get("round_id", "")},
            )
        else:
            block = context_block(
                f"session.history.{message.get('message_id') or index}",
                "history",
                source="data/state.json",
                reason="session history included in current LLM call",
                content=content,
                metadata={"role": role, "message_id": message.get("message_id", ""), "round_id": message.get("round_id", "")},
            )
        annotated.append(attach_context(message, block))
    return annotated


def _project_main_tool_result_batch(
    records: list[tuple[dict[str, Any], object, str, str]],
) -> None:
    projected_batch = project_tool_result_batch_for_model([
        (result, tool_name, tool_call_id)
        for _entry, result, tool_name, tool_call_id in records
    ])
    for (entry, _result, tool_name, tool_call_id), projected in zip(
        records, projected_batch
    ):
        entry["content"] = projected.content
        annotated = attach_context(entry, context_block(
            f"tool.result.{tool_name}.{tool_call_id}",
            "tool_result",
            source=f"tool:{tool_name}",
            reason="tool output returned to LLM",
            transforms=["tool_result_projection"] if projected.truncated else [],
            content=projected.content,
            metadata={
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "original_tokens": projected.original_tokens,
                "original_bytes": projected.original_bytes,
                "content_ref": projected.content_ref,
            },
        ))
        entry.clear()
        entry.update(annotated)


async def _run_main_agent_impl(
    user_message: str,
    history: list,
    bot: Any,
    chat_id: int,
    db_path: str,
    system_prompt: str = "",
    client_request_id: str = "",
    persist_user_message: bool = True,
    public_user_message: str | None = None,
    public_attachments: list[dict[str, Any]] | None = None,
    llm_user_content: Any | None = None,
    lang: str = "",
    system_context: list[dict[str, Any]] | None = None,
    ephemeral_system: str = "",
    fixed_ephemeral_system: str = "",
    resume_lane: str = "",
) -> str:
    _caller_type.set("main_agent")
    suppress_initial_detail = _ui_round_hide_initial_detail.get()
    round_id = _current_round_id.get()
    logger.info(
        "Agent run started (round=%s, chat=%s, user_msg=%.120r)",
        round_id or "-",
        chat_id,
        user_message,
    )
    assistant_meta = _ui_round_assistant_meta.get()
    system_initiated = bool(
        isinstance(assistant_meta, dict) and assistant_meta.get("system_initiated")
    )
    runtime_inbox = current_workbench_inbox()
    if runtime_inbox is not None:
        runtime_inbox.round_id = round_id
    transcript_policy = current_run_transcript_policy()
    dual_lane = transcript_policy is TranscriptPolicy.DUAL_LANE
    normalized_resume_lane = str(resume_lane or "").strip().lower()
    resume_execution = dual_lane and normalized_resume_lane == "execution"
    if dual_lane and normalized_resume_lane and not resume_execution:
        raise ValueError(
            f"unsupported resume lane for {transcript_policy.value}: {resume_lane}"
        )
    execution_lane_binding = None
    storage_lane = "execution" if resume_execution else "decision"

    async def _inject_runtime_guidance(
        msgs: list[dict[str, Any]],
        events: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Inject queued Workbench steering at a safe model/tool boundary."""
        if runtime_inbox is None:
            return False
        events = runtime_inbox.collect_guidance_nowait() if events is None else events
        if not events:
            return False
        texts = [
            str((event.get("payload") or {}).get("text") or "").strip()
            for event in events
        ]
        texts = [text for text in texts if text]
        if not texts:
            return False
        content = (
            "[Workbench runtime guidance]\n"
            "The user sent this while the current task was running. Treat the "
            "latest guidance as authoritative for all work not already completed.\n\n"
            + "\n\n".join(texts)
        )
        entry: dict[str, Any] = {
            "role": "user",
            "content": content,
            "message_id": f"guidance_{uuid4().hex}",
            "runtime_guidance": True,
        }
        if round_id:
            entry["round_id"] = round_id
        msgs.append(attach_context(entry, context_block(
            f"runtime.guidance.{entry['message_id']}",
            "user",
            source="cyrene.workbench.inbox",
            reason="user guidance delivered while the Workbench chat run was active",
            content=content,
        )))
        await _publish_runtime_event({
            "type": "guidance_applied",
            "round_id": round_id,
            "count": len(texts),
            "detail": "The running agent applied new user guidance.",
        })
        runtime_inbox.acknowledge(events)
        return True

    async def _call_with_runtime_guidance(
        msgs: list[dict[str, Any]],
        call: Callable[[], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Let durable guidance preempt an unfinished model request.

        Previously the inbox was checked only after the upstream response, so a
        user could wait for the full model timeout before steering took effect.
        Tool execution already has an inbox wake path; this gives model waits
        the same behavior.
        """
        if runtime_inbox is None:
            return await call()
        while True:
            await _inject_runtime_guidance(msgs)
            model_task = asyncio.create_task(call())
            guidance_task = asyncio.create_task(runtime_inbox.wait_for_guidance())
            try:
                done, _pending = await asyncio.wait(
                    {model_task, guidance_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                model_task.cancel()
                guidance_task.cancel()
                await asyncio.gather(
                    model_task, guidance_task, return_exceptions=True
                )
                raise
            if model_task in done:
                guidance_task.cancel()
                await asyncio.gather(guidance_task, return_exceptions=True)
                return await model_task
            guidance_available = await guidance_task
            if not guidance_available:
                return await model_task
            model_task.cancel()
            await asyncio.gather(model_task, return_exceptions=True)
            await _inject_runtime_guidance(msgs)

    async def _save(msgs):
        saved_ephemeral = "\n\n".join(
            part for part in (fixed_ephemeral_system, ephemeral_system) if part
        )
        await _save_session_messages(
            msgs,
            system_context_blocks=system_context,
            ephemeral_context=saved_ephemeral,
        )

    # Throttle the per-tool-batch save (the hottest write path: a full state
    # rewrite per batch). Every completion path persists before returning, so
    # skipping intermediate batches changes nothing observable — the only cost
    # is a wider crash-loss window, matching the run-failure retry semantics.
    _last_batch_save_ts = time.monotonic()
    _pending_saved_batches = 0

    # Codex keeps the historical shared tool-sensitive prefix.  The
    # OpenAI-compatible policy uses a light Decision bundle and a separate full
    # Execution bundle without changing the execution machinery below.
    wire_tool_defs = get_main_wire_tool_defs()
    enabled_wire_names = _enabled_wire_names(wire_tool_defs)
    execution_wire_tool_defs = (
        execution_outcome_tool_defs(wire_tool_defs)
        if dual_lane
        else wire_tool_defs
    )

    visible_user_message = user_message if public_user_message is None else str(public_user_message)
    user_message_id = f"user_{uuid4().hex}"
    user_entry = {"role": "user", "content": visible_user_message, "message_id": user_message_id}
    if public_attachments:
        user_entry["attachments"] = [dict(item) for item in public_attachments if isinstance(item, dict)]
    if round_id:
        user_entry["round_id"] = round_id
    if client_request_id:
        user_entry["client_request_id"] = client_request_id
    if dual_lane:
        user_entry = tag_lane_record(
            user_entry,
            "execution" if resume_execution else "decision",
        )
    if persist_user_message:
        await _append_session_message(user_entry)
    legacy_system = (
        str(system_prompt)
        if str(system_prompt or "").strip()
        else prompt_for_enabled_tool_packs(
            _MAIN_AGENT_PROMPT_TEMPLATE,
            enabled_wire_names,
        )
    )
    dual_execution_system = (
        legacy_system + "\n\n" + _DUAL_LANE_EXECUTION_SYSTEM_PROMPT
    )
    effective_system = (
        dual_execution_system
        if resume_execution
        else _DUAL_LANE_DECISION_SYSTEM_PROMPT
        if dual_lane
        else legacy_system
    )
    llm_user_entry = dict(user_entry)
    llm_user_entry["content"] = (
        llm_user_content if llm_user_content is not None else user_message
    )
    if dual_lane and resume_execution:
        system_blocks = list(system_context or [])
        system_blocks.append(context_block(
            "execution.system.lane_boundary",
            "system",
            source="cyrene.agent.prompts._DUAL_LANE_EXECUTION_SYSTEM_PROMPT",
            reason="independent OpenAI-compatible execution lane boundary",
            content=_DUAL_LANE_EXECUTION_SYSTEM_PROMPT,
        ))
    elif dual_lane:
        system_blocks = [context_block(
            "decision.system.effective",
            "system",
            source="cyrene.agent.prompts._DUAL_LANE_DECISION_SYSTEM_PROMPT",
            reason="independent OpenAI-compatible decision lane",
            content=effective_system,
        )]
    else:
        system_blocks = list(system_context or [
            context_block(
                "main.system.effective",
                "system",
                source="cyrene.agent.agent._run_main_agent",
                reason="effective system prompt",
                content=effective_system,
            )
        ])
    system_entry = attach_context({"role": "system", "content": effective_system}, system_blocks)
    fixed_ephemeral_entry = None
    if fixed_ephemeral_system:
        fixed_ephemeral_entry = attach_context(
            {"role": "system", "content": fixed_ephemeral_system},
            context_block(
                "run.fixed_ephemeral",
                "system",
                source="run_agent(fixed_ephemeral_system)",
                reason="run-scoped context fixed before the current user turn for prompt-cache stability",
                content=fixed_ephemeral_system,
            ),
        )
    llm_user_entry = attach_context(llm_user_entry, context_block(
        "user.current.raw",
        "user",
        source="run_agent(user_message)",
        reason="current user request passed to LLM",
        content=user_message,
        metadata={"visible_differs": public_user_message is not None and public_user_message != user_message},
    ))
    canonical_history = [
        dict(message) for message in history if isinstance(message, dict)
    ]
    legacy_shared_history_message_ids = {
        str(message.get("message_id") or "").strip()
        for message in canonical_history
        if dual_lane
        and message.get("lane_refs") is None
        and str(message.get("message_id") or "").strip()
    }
    if dual_lane:
        history = project_lane_history(
            canonical_history,
            "execution" if resume_execution else "decision",
        )
    history = _annotate_history_context(history)
    legacy_shared_history_object_ids = {
        id(history_message)
        for history_message in history
        if dual_lane and history_message.get("lane_refs") is None
    }
    # Keep the append-only lane transcript immediately after its system prompt.
    # Run-scoped context is a stable tail for this run; putting it before the
    # growing history would invalidate the whole historical cache prefix.
    run_prefix = [system_entry, *history]
    if fixed_ephemeral_entry is not None:
        run_prefix.append(fixed_ephemeral_entry)
    phase1_tools = _LIGHT_TOOL_DEFS
    if _deep_research_first_round.get():
        phase1_decision = _DEEP_RESEARCH_PHASE1_DECISION
        phase1_tools = _DEEP_RESEARCH_LIGHT_TOOL_DEFS
    elif _current_command.get() == "quick-answer":
        phase1_decision = (
            "Decision phase rules:\n"
            "- You are in Quick Answer mode. The user wants a fast, text-only answer.\n"
            "- Write the complete answer as normal assistant content, then call `quit` only as the terminal signal. Keep quit's arguments empty. Do NOT call `use_tools`.\n"
            "- Call `ask_user` ONLY if the question is genuinely unclear.\n"
            "- This mode is for pure conversation only — no tools, no research."
        )
    elif _current_command.get() == "workbench-task-reply":
        phase1_decision = (
            "Decision phase rules:\n"
            "- You are replying inside a Workbench task, and this turn is a question/follow-up rather than a work request.\n"
            "- If the current task/session context is enough, write the complete answer as normal assistant content and call `quit` only as the terminal signal with empty arguments.\n"
            "- Do NOT call `use_tools` merely because the conversation is attached to a task, project, plan, or workspace.\n"
            "- Call `use_tools` only if the user explicitly asks you to inspect, execute, modify, or if an accurate answer truly needs facts not present in the current context.\n"
            "- If clarification is needed before answering, call `ask_user`."
        )
    else:
        phase1_decision = PHASE1_DECISION_PROMPT
    if system_initiated:
        phase1_decision += (
            "\n- This is a proactive system-initiated round. Do not call `ask_user`; "
            "either complete the check-in autonomously or finish silently."
        )
    phase1_decision_entry = attach_context({"role": "user", "content": phase1_decision}, context_block(
        "phase1.decision_rules",
        "phase_rules",
        source="cyrene.agent.prompts",
        reason="decision-phase tool-gating rules",
        content=phase1_decision,
    ))
    phase1_decision_entry["hidden_from_ui"] = True
    phase1_messages = [*run_prefix, llm_user_entry]
    if not resume_execution:
        phase1_messages.append(phase1_decision_entry)
    if ephemeral_system:
        # Once observed, volatile context is immutable. Later in-run changes
        # append a new version instead of moving or rewriting this prompt tail.
        phase1_messages.append(attach_context(
            {
                "role": "system",
                "content": ephemeral_system,
                "hidden_from_ui": True,
                "volatile_context_version": 1,
            },
            context_block(
                "run.volatile_ephemeral.v1",
                "system",
                source="run_agent(volatile_ephemeral_system)",
                reason="append-only volatile context version observed by this run",
                content=ephemeral_system,
                metadata={"version": 1},
            ),
        ))

    hidden_phase1_usage: dict[str, Any] = {}

    def _rollup_replaced_phase1_usage(response_obj: dict[str, Any]) -> None:
        """Account for a Decision response replaced by a bounded re-decision."""
        nonlocal hidden_phase1_usage
        hidden_phase1_usage = _merge_usage_records(
            hidden_phase1_usage,
            response_obj.get("usage"),
        )

    async def _require_phase1_control_signal(
        response_obj: dict[str, Any],
        context_messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        nonlocal hidden_phase1_usage
        # Deep Research's first turn has a stricter, user-visible length
        # handshake below.  Let that dedicated correction run before the
        # generic bounded missing-control repair.
        if _deep_research_first_round.get() and not (
            response_obj.get("tool_calls") or []
        ):
            return response_obj
        repairs = 0
        while not (response_obj.get("tool_calls") or []):
            if repairs >= _MAX_MISSING_CONTROL_REPAIRS:
                raise AgentControlProtocolError(
                    "Model repeatedly returned no explicit control signal."
                )
            incomplete_entry = _assistant_entry_from_response(
                response_obj,
                round_id,
            )
            hidden_phase1_usage = _merge_usage_records(
                hidden_phase1_usage,
                incomplete_entry.pop("usage", None),
            )
            incomplete_entry["hidden_from_ui"] = True
            context_messages.extend([
                incomplete_entry,
                _missing_completion_signal_entry(round_id),
            ])
            response_obj = await _call_with_runtime_guidance(
                context_messages,
                lambda: _call_phase1_llm(
                    project_history_for_llm(context_messages),
                    tools=tools,
                ),
            )
            repairs += 1
        return response_obj

    async def _ensure_text_reply(
        response_obj: dict[str, Any],
        base_messages: list[dict[str, Any]],
        fallback: str = "Done.",
        execution_completion: bool = False,
        force_completion_packet: bool = False,
        recovery_tools: list[dict[str, Any]] | None = None,
    ) -> str:
        # A valid terminal answer has already paid for the main model call.
        # Deliver it directly instead of rebuilding the full history. Tool-markup
        # or placeholder replies deliberately fall through to no-tool recovery.
        direct_text = _safe_terminal_reply_from_response(response_obj, base_messages)
        structured_text = ""
        if execution_completion:
            public_reply = str(
                execution_outcome_arguments(response_obj).get("public_reply") or ""
            ).strip()
            if public_reply:
                structured_text = _safe_terminal_reply_from_response(
                    {**response_obj, "content": public_reply},
                    base_messages,
                )
        # After a rejected plain-text turn, only the accepted structured reply
        # can become public. On ordinary terminal turns, retain compatibility
        # with providers that return the answer in assistant content while also
        # accepting the new structured form when content is empty.
        text = (
            structured_text
            if execution_completion and force_completion_packet
            else structured_text or direct_text
        )
        if text:
            if _streaming_reply_requested():
                await _emit_reply_stream_event({"type": "reply_start"})
                await _emit_reply_stream_event({"type": "reply_delta", "delta": text})
                await _emit_reply_stream_event({"type": "reply_done", "response": text})
            return text
        # System-initiated rounds (e.g. the proactive heartbeat) must honor the
        # agent's choice to stay silent. When the terminal turn carried no genuine
        # user-facing text, never reconstruct a reply it didn't write: the reconstruction
        # below re-prompts the model to "answer directly" and would manufacture an
        # unsolicited check-in, overriding the quit. Deliver nothing instead.
        meta = _ui_round_assistant_meta.get()
        if isinstance(meta, dict) and meta.get("system_initiated"):
            return ""
        completion_packet = (
            execution_finalization_packet(base_messages, response_obj)
            if execution_completion
            else None
        )
        recovered = (
            await _recover_final_reply(
                project_history_for_llm(base_messages),
                max_tokens=None,
                completion_packet=completion_packet,
                tools=recovery_tools,
                call_llm=_call_llm,
                streaming_reply_requested=_streaming_reply_requested,
            )
        ).strip()
        return recovered or fallback

    def _session_messages_to_save(current_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        _flush_intermediate_user_replies(current_messages)
        # Storage merges run repeatedly while a tool loop is live.  Assign ids
        # to the in-memory transcript once so later saves update the same
        # canonical records instead of manufacturing a fresh set of lane keys
        # and re-inserting earlier tool episodes ahead of the new suffix.
        _ensure_message_identity(current_messages)
        saved: list[dict[str, Any]] = []
        for message in current_messages[1:]:
            if message["role"] == "system":
                continue
            if bool(message.get("hidden_from_ui")) and not bool(
                message.get("persist_model_record")
            ):
                continue
            if not persist_user_message and message.get("message_id") == user_message_id:
                continue
            if message.get("role") == "user" and message.get("message_id") == user_message_id:
                saved.append(dict(user_entry))
                continue
            preserve_legacy_shared = (
                id(message) in legacy_shared_history_object_ids
                or str(message.get("message_id") or "").strip()
                in legacy_shared_history_message_ids
            )
            stored_message = _redact_sensitive_tool_calls_for_storage(message)
            if (
                dual_lane
                and not stored_message.get("lane_refs")
                and not preserve_legacy_shared
            ):
                stored_message = tag_lane_record(stored_message, storage_lane)
            saved.append(stored_message)
        return saved

    # Codex keeps the historical full shared tool array in both phases.  The
    # dual-lane policy gives Decision only its light controls; Deep Research's
    # first turn also keeps its dedicated length-handshake bundle.
    phase1_wire_tools = (
        phase1_tools
        if dual_lane or _deep_research_first_round.get()
        else wire_tool_defs
    )
    if resume_execution:
        resumed_handoff = next(
            (
                message
                for message in reversed(history)
                if str(message.get("record_kind") or "") == "execution_handoff"
                and str(message.get("turn_id") or "") == str(round_id or "")
            ),
            None,
        )
        resumed_use_tools_id = str(
            (resumed_handoff or {}).get("decision_tool_call_id") or ""
        ).strip() or f"use_tools_{round_id}"
        response = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": resumed_use_tools_id,
                "function": {
                    "name": "use_tools",
                    "arguments": json.dumps(
                        {"execution_brief": "Resume execution with the user's answer."},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            }],
        }
    else:
        response = await _call_with_runtime_guidance(
            phase1_messages,
            lambda: _call_phase1_llm(
                project_history_for_llm(phase1_messages),
                tools=phase1_wire_tools,
            ),
        )
    if runtime_inbox is not None and not resume_execution:
        phase1_guidance = runtime_inbox.collect_guidance_nowait()
        if phase1_guidance:
            _rollup_replaced_phase1_usage(response)
            phase1_messages.extend(deferred_decision_protocol_entries(
                response,
                round_id=round_id,
                assistant_entry_factory=_assistant_entry_from_response,
            ))
            await _inject_runtime_guidance(phase1_messages, phase1_guidance)
            response = await _call_with_runtime_guidance(
                phase1_messages,
                lambda: _call_phase1_llm(
                    project_history_for_llm(phase1_messages),
                    tools=phase1_wire_tools,
                ),
            )
    phase1_allowed = {_tool_def_name(tool_def) for tool_def in phase1_tools}
    phase1_wire_names = {
        _tool_def_name(tool_def) for tool_def in phase1_wire_tools
    }
    phase1_can_promote_tools = (
        not dual_lane
        and not _deep_research_first_round.get()
        and _current_command.get() != "quick-answer"
    )

    async def _prepare_phase1_decision(
        response_obj: dict[str, Any],
        context_messages: list[dict[str, Any]],
    ) -> tuple[Phase1Decision, list[dict[str, Any]]]:
        """Validate and normalize one Decision-Phase response.

        Invalid tool selection receives one model correction.  Missing or
        malformed terminal output is handled separately by the bounded control
        repair helper, so this function never grows into another agent loop.
        """
        response_obj = await _require_phase1_control_signal(
            response_obj,
            context_messages,
            phase1_wire_tools,
        )
        decision = normalize_phase1_decision(
            response_obj,
            allowed_tool_names=phase1_allowed,
            wire_tool_names=phase1_wire_names,
            can_promote_tools=phase1_can_promote_tools,
            system_initiated=system_initiated,
        )
        if not decision.invalid_tool_names:
            return decision, context_messages

        _rollup_replaced_phase1_usage(response_obj)
        invalid_names = ", ".join(decision.invalid_tool_names)
        retry_messages = [
            *context_messages,
            {
                **_assistant_entry_from_response(
                    response_obj, round_id="", include_tool_calls=False
                ),
                "content": assistant_text(response_obj)
                or (response_obj.get("content") or ""),
            },
            {
                "role": "user",
                "content": (
                    f"[Decision-phase correction] You attempted unavailable tool(s): {invalid_names}. "
                    + ("This is a proactive system-initiated round. `ask_user` is forbidden; use an available tool or finish without pausing for user input."
                       if system_initiated
                       else "Quick Answer mode does not allow execution tools. Answer directly with `quit`, or use `ask_user` only when the request is genuinely unclear."
                       if _current_command.get() == "quick-answer"
                       else "Only `ask_user` and `quit` are available in this phase. You MUST ask the user about the report length before starting research."
                       if _deep_research_first_round.get()
                       else "Only `use_tools`, `ask_user`, and `quit` are available in this phase. "
                            "If real tool work is needed, make the shortest reliable decision and call `use_tools` "
                            "with an `execution_brief` under 300 characters "
                            "containing only the intent, first useful action, and hard user constraints. "
                            "If clarification is needed before acting, call `ask_user`. "
                            "Otherwise answer directly and call `quit`.")
                ),
            },
        ]
        corrected_response = await _call_with_runtime_guidance(
            retry_messages,
            lambda: _call_phase1_llm(
                project_history_for_llm(retry_messages),
                tools=phase1_wire_tools,
            ),
        )
        corrected_response = await _require_phase1_control_signal(
            corrected_response,
            retry_messages,
            phase1_wire_tools,
        )
        return normalize_phase1_decision(
            corrected_response,
            allowed_tool_names=phase1_allowed,
            wire_tool_names=phase1_wire_names,
            can_promote_tools=phase1_can_promote_tools,
            system_initiated=system_initiated,
        ), retry_messages

    phase1_context_messages = phase1_messages
    while True:
        decision, phase1_context_messages = await _prepare_phase1_decision(
            response,
            phase1_context_messages,
        )
        response = decision.response
        if not decision.terminal_in_phase1 or runtime_inbox is None:
            break
        boundary_guidance = await runtime_inbox.collect_guidance_or_seal()
        if not boundary_guidance:
            break
        _rollup_replaced_phase1_usage(response)
        phase1_context_messages.extend(deferred_decision_protocol_entries(
            response,
            round_id=round_id,
            assistant_entry_factory=_assistant_entry_from_response,
        ))
        await _inject_runtime_guidance(
            phase1_context_messages,
            boundary_guidance,
        )
        # Guidance at the terminal boundary belongs to the Decision Phase.  Let
        # the model decide again instead of fabricating ``use_tools`` and
        # forcing a harmless chat refinement through the execution loop.
        response = await _call_with_runtime_guidance(
            phase1_context_messages,
            lambda: _call_phase1_llm(
                project_history_for_llm(phase1_context_messages),
                tools=phase1_wire_tools,
            ),
        )

    tool_calls = list(decision.tool_calls)
    phase1_concrete_calls = list(decision.concrete_calls)
    use_tools_call = decision.use_tools_call
    ask_user_call = decision.ask_user_call
    quit_call = decision.quit_call
    phase1_runtime_guidance_entries = [
        message
        for message in phase1_context_messages
        if message.get("runtime_guidance")
    ]
    messages = [*run_prefix, llm_user_entry, *phase1_runtime_guidance_entries]
    # Awaiting-user exits persist both the UI-only question projection and the
    # hidden assistant/tool protocol pair needed for an exact model resume.
    paused = False
    final_saved = False
    execution_turn_id = str(round_id or user_message_id)
    execution_decision_tool_call_id = str(
        (use_tools_call or {}).get("id") or ""
    ).strip()
    execution_outcome_synced = False
    execution_started = False

    async def _sync_execution_outcome(
        final_text: str,
        response_obj: dict[str, Any] | None = None,
        *,
        status: str = "completed",
        state_summary_override: str | None = None,
        unresolved_override: list[str] | None = None,
    ) -> None:
        """Close the Decision-lane ``use_tools`` call without copying evidence."""
        nonlocal execution_outcome_synced
        if not dual_lane or execution_outcome_synced:
            return
        outcome_args = execution_outcome_arguments(response_obj)
        raw_artifacts = outcome_args.get("artifacts")
        artifacts = list(raw_artifacts) if isinstance(raw_artifacts, list) else []
        artifact_identities = {
            str(
                artifact.get("id")
                or artifact.get("url")
                or artifact.get("path")
                or artifact.get("name")
                or ""
            )
            for artifact in artifacts
            if isinstance(artifact, dict)
        }
        for artifact in public_assistant_artifact_refs(messages):
            identity = str(
                artifact.get("id")
                or artifact.get("url")
                or artifact.get("path")
                or artifact.get("name")
                or ""
            )
            if identity and identity in artifact_identities:
                continue
            artifacts.append(artifact)
            if identity:
                artifact_identities.add(identity)
        raw_unresolved = outcome_args.get("unresolved")
        unresolved = (
            list(unresolved_override)
            if unresolved_override is not None
            else raw_unresolved
            if isinstance(raw_unresolved, list)
            else []
        )
        outcome = ExecutionOutcome.create(
            execution_turn_id,
            status,
            public_reply=final_text,
            state_summary=(
                str(state_summary_override)
                if state_summary_override is not None
                else str(outcome_args.get("state_summary") or "")
            ),
            artifacts=artifacts,
            unresolved=unresolved,
            conversation_delta=execution_conversation_delta(messages),
        )
        outcome_message = build_execution_outcome_message(
            outcome,
            tool_call_id=(
                execution_decision_tool_call_id
                or f"use_tools_{execution_turn_id}"
            ),
        )
        if round_id:
            outcome_message["round_id"] = round_id
        await append_or_upsert_lane_record(outcome_message)
        execution_outcome_synced = True

    async def _persist_awaiting_user_protocol(
        assistant_protocol_entry: dict[str, Any],
        tool_protocol_entry: dict[str, Any],
        *,
        tool_call_id: str,
    ) -> None:
        """Persist the real ask_user protocol plus one UI-only question card."""
        nonlocal final_saved
        question_usage = assistant_protocol_entry.pop("usage", None)
        for suffix, protocol_entry in (
            ("assistant", assistant_protocol_entry),
            ("result", tool_protocol_entry),
        ):
            protocol_entry.setdefault(
                "message_id",
                f"msg_ask_user_{tool_call_id}_{suffix}",
            )
            protocol_entry["hidden_from_ui"] = True
            protocol_entry["persist_model_record"] = True
            if dual_lane:
                tagged = tag_lane_record(
                    protocol_entry,
                    storage_lane,
                    record_kind="control_protocol",
                    persist_model_record=True,
                    hidden_from_ui=True,
                )
                protocol_entry.clear()
                protocol_entry.update(tagged)

        pending_context = _pending_question_resume_context("")
        pending = pending_context.get("pending_question") or {}
        pending_message_id = str(pending.get("message_id") or "").strip()
        visible_question = next(
            (
                dict(message)
                for message in pending_context.get("full_messages") or []
                if pending_message_id
                and str(message.get("message_id") or "").strip()
                == pending_message_id
            ),
            None,
        )
        if visible_question is not None:
            # The normalized card is a UI projection, never a second model
            # transcript record. Usage belongs here while the raw control pair
            # remains hidden and usage-free, avoiding double accounting.
            visible_question["persist_model_record"] = False
            visible_question["hidden_from_llm"] = True
            if question_usage:
                visible_question["usage"] = dict(question_usage)
            messages.append(visible_question)

        await _save(_session_messages_to_save(messages))
        final_saved = True

    async def _rollup_legacy_pending_question_usage(
        usage: dict[str, Any] | None,
    ) -> None:
        """Update the legacy UI/model question without persisting raw controls."""
        if not usage:
            return
        pending_context = _pending_question_resume_context("")
        pending = pending_context.get("pending_question") or {}
        pending_message_id = str(pending.get("message_id") or "").strip()
        visible_question = next(
            (
                dict(message)
                for message in pending_context.get("full_messages") or []
                if pending_message_id
                and str(message.get("message_id") or "").strip()
                == pending_message_id
            ),
            None,
        )
        if visible_question is None:
            return
        visible_question["persist_model_record"] = True
        visible_question.pop("hidden_from_llm", None)
        visible_question["usage"] = dict(usage)
        await append_or_upsert_lane_record(visible_question)

    async def _commit_phase1_terminal_reply(
        response_obj: dict[str, Any],
        current_messages: list[dict[str, Any]],
    ) -> str:
        """Resolve and persist the single public reply for a Phase-1 terminal."""
        nonlocal final_saved
        final_text = await _ensure_text_reply(response_obj, current_messages)
        terminal_entry = current_messages[-1]
        terminal_entry["content"] = final_text
        terminal_entry.pop("tool_calls", None)
        if client_request_id:
            terminal_entry["client_request_id"] = client_request_id
        _attach_final_usage(terminal_entry)
        await _save(_session_messages_to_save(current_messages))
        final_saved = True
        return final_text

    try:
        assistant_entry = _assistant_entry_from_response(response, round_id)
        phase1_usage = _merge_usage_records(
            hidden_phase1_usage,
            assistant_entry.get("usage"),
        )
        if phase1_usage:
            assistant_entry["usage"] = phase1_usage
        messages.append(assistant_entry)

        if quit_call is not None:
            if runtime_inbox is not None:
                await runtime_inbox.wait_for_active_tools()
            await _publish_runtime_event({
                "type": "phase_transition",
                "from": "phase1_decision",
                "to": "done",
                "detail": "Agent called quit",
                "detail_key": "phase.agentQuit",
            })
            return await _commit_phase1_terminal_reply(response, messages)

        if ask_user_call:
            try:
                args = parse_tool_arguments(
                    ask_user_call["function"].get("arguments")
                )
                result = await execute_wire_tool(
                    "ask_user", args, bot, chat_id, db_path, None, actor="main"
                )
            except Exception as exc:
                logger.warning("Tool ask_user failed: %s", exc, exc_info=True)
                result = f"Tool failed: {exc}"
            projected_result = project_tool_result_for_model(
                result,
                tool_name="ask_user",
                tool_call_id=ask_user_call["id"],
            )
            tool_entry: dict[str, Any] = {"role": "tool", "tool_call_id": ask_user_call["id"], "content": projected_result.content}
            tool_entry = attach_context(tool_entry, context_block(
                f"tool.result.ask_user.{ask_user_call['id']}",
                "tool_result",
                source="tool:ask_user",
                reason="ask_user tool output returned to LLM",
                transforms=["tool_result_projection"] if projected_result.truncated else [],
                content=projected_result.content,
                metadata={
                    "tool_name": "ask_user",
                    "tool_call_id": ask_user_call["id"],
                    "original_tokens": projected_result.original_tokens,
                    "original_bytes": projected_result.original_bytes,
                    "content_ref": projected_result.content_ref,
                },
            ))
            if round_id:
                tool_entry["round_id"] = round_id
            messages.append(tool_entry)
            if _tool_result_requests_user_input(result):
                if dual_lane:
                    await _persist_awaiting_user_protocol(
                        assistant_entry,
                        tool_entry,
                        tool_call_id=str(ask_user_call["id"]),
                    )
                else:
                    await _rollup_legacy_pending_question_usage(
                        assistant_entry.get("usage")
                    )
                    paused = True
                return _AWAITING_USER_SENTINEL
            await _save(_session_messages_to_save(messages))
            final_saved = True
            return (await _ensure_text_reply(response, messages, fallback=str(result)))

        if use_tools_call or phase1_concrete_calls:
            logger.info("Agent run phase1 -> phase2_execution (round=%s)", round_id or "-")
            event = {"type": "phase_transition", "from": "phase1_decision", "to": "phase2_execution"}
            if not suppress_initial_detail:
                phase_task = visible_user_message.strip()[:120]
                if phase_task:
                    event["detail"] = f"Phase 1 decided to use tools. Task: {phase_task}"
                    event["detail_key"] = "phase.useTools"
                    event["detail_params"] = {"task": phase_task}
                else:
                    event["detail"] = "Phase 1 decided to use tools. Task: Analyze uploaded attachments"
                    event["detail_key"] = "phase.useToolsAttachments"
            if phase1_concrete_calls:
                event["promoted_tool_calls"] = [
                    str(call.get("function", {}).get("name") or "")
                    for call in phase1_concrete_calls
                ]
            await _publish_runtime_event(event)
            promoted_phase1_response: dict[str, Any] | None = None
            if dual_lane:
                execution_started = True
                storage_lane = "execution"
                execution_lane_binding = bind_agent_lane("execution")
                execution_lane_binding.__enter__()
                phase2_assistant: dict[str, Any] = {}
                if resume_execution:
                    # ``phase1_messages`` is already the execution projection
                    # plus the user's clarification answer.  The synthetic
                    # use_tools above only selects this existing loop; it is not
                    # part of either model transcript.
                    messages = list(phase1_messages)
                else:
                    for guidance_entry in phase1_runtime_guidance_entries:
                        await append_or_upsert_lane_record(tag_lane_record(
                            guidance_entry,
                            "decision",
                            persist_model_record=True,
                        ))
                    decision_assistant = tag_lane_record(
                        {
                            **_assistant_entry_from_response(response, round_id),
                            "message_id": f"msg_decision_{execution_turn_id}",
                        },
                        "decision",
                        persist_model_record=True,
                        hidden_from_ui=True,
                    )
                    # Usage rolls forward to the eventual public Execution
                    # reply; the hidden routing record must not count it twice.
                    decision_assistant.pop("usage", None)
                    await append_or_upsert_lane_record(decision_assistant)
                    try:
                        handoff_args = parse_tool_arguments(
                            use_tools_call.get("function", {}).get("arguments")
                        )
                    except Exception:
                        handoff_args = {}
                    attachment_refs = [
                        {
                            key: item[key]
                            for key in (
                                "id",
                                "name",
                                "content_type",
                                "size",
                                "kind",
                                "url",
                            )
                            if item.get(key) not in (None, "")
                        }
                        for item in (public_attachments or [])
                        if isinstance(item, dict)
                    ]
                    missed_decision_conversation = decision_conversation_delta(
                        phase1_context_messages,
                        current_user_message_id=user_message_id,
                        runtime_guidance_message_ids=[
                            str(message.get("message_id") or "")
                            for message in phase1_runtime_guidance_entries
                        ],
                    )
                    side_delta = side_conversation_delta(user_message)
                    if not side_delta:
                        side_delta = side_conversation_delta(llm_user_content)
                    missed_decision_conversation.extend(side_delta)
                    handoff = ExecutionHandoff.create(
                        execution_turn_id,
                        visible_user_message,
                        execution_brief=str(
                            handoff_args.get("execution_brief") or ""
                        ),
                        # Hard constraints require direct user-message evidence.
                        # The Decision model's interpretation is intentionally
                        # not promoted to authoritative constraints.
                        hard_constraints=(),
                        attachment_refs=attachment_refs,
                        conversation_delta=missed_decision_conversation,
                    )
                    handoff_message = build_execution_handoff_message(handoff)
                    handoff_message["decision_tool_call_id"] = (
                        execution_decision_tool_call_id
                    )
                    if round_id:
                        handoff_message["round_id"] = round_id
                    await append_or_upsert_lane_record(handoff_message)

                    # Preserve the complete coordinator-built execution policy
                    # (command modes, tool packs, plan/deep-research rules,
                    # static extensions) and append only the lane boundary.
                    execution_system = dual_execution_system
                    execution_system_blocks = list(system_context or [])
                    execution_system_blocks.append(context_block(
                            "execution.system.effective",
                            "system",
                            source="cyrene.agent.prompts._DUAL_LANE_EXECUTION_SYSTEM_PROMPT",
                            reason="independent OpenAI-compatible execution lane",
                            content=_DUAL_LANE_EXECUTION_SYSTEM_PROMPT,
                    ))
                    execution_system_entry = attach_context(
                        {"role": "system", "content": execution_system},
                        execution_system_blocks,
                    )
                    execution_history = _annotate_history_context(
                        project_lane_history(canonical_history, "execution")
                    )
                    legacy_shared_history_object_ids.update(
                        id(history_message)
                        for history_message in execution_history
                        if history_message.get("lane_refs") is None
                    )
                    messages = [execution_system_entry, *execution_history]
                    if fixed_ephemeral_entry is not None:
                        messages.append(fixed_ephemeral_entry)
                    messages.append(handoff_message)
                    if ephemeral_system:
                        messages.append(attach_context(
                            {
                                "role": "system",
                                "content": ephemeral_system,
                                "hidden_from_ui": True,
                                "volatile_context_version": 1,
                            },
                            context_block(
                                "execution.volatile_ephemeral.v1",
                                "system",
                                source="run_agent(volatile_ephemeral_system)",
                                reason="execution-lane volatile context observed by this run",
                                content=ephemeral_system,
                                metadata={"version": 1},
                            ),
                        ))
            elif phase1_concrete_calls:
                execution_started = True
                phase2_assistant = _assistant_entry_from_response(response, round_id)
                phase2_assistant["hidden_from_ui"] = True
                messages = [*phase1_context_messages, phase2_assistant]
                promoted_phase1_response = response
            else:
                execution_started = True
                normal_use_tools = any(
                    str(tc.get("id") or "") == str(use_tools_call.get("id") or "")
                    for tc in tool_calls
                )
                phase2_assistant = (
                    _assistant_entry_from_response(response, round_id)
                    if normal_use_tools
                    else _apply_assistant_meta({
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [use_tools_call],
                        **({"round_id": round_id} if round_id else {}),
                    })
                )
                phase2_assistant["hidden_from_ui"] = True
                messages = [*phase1_context_messages, phase2_assistant]
                phase2_calls = tool_calls if normal_use_tools else [use_tools_call]
                for phase2_call in phase2_calls:
                    phase2_name = str(
                        phase2_call.get("function", {}).get("name") or ""
                    )
                    phase2_result = (
                        "Execution phase entered. Follow the concise execution "
                        "brief in the original use_tools arguments and adapt from "
                        "tool evidence."
                        if phase2_name == "use_tools"
                        else "Skipped because the same decision selected use_tools."
                    )
                    phase2_tool_entry = attach_context(
                        {
                            "role": "tool",
                            "tool_call_id": phase2_call["id"],
                            "content": phase2_result,
                            "hidden_from_ui": True,
                        },
                        context_block(
                            f"tool.result.{phase2_name}.{phase2_call['id']}",
                            "tool_result",
                            source=f"tool:{phase2_name}",
                            reason="preserve the complete Phase-1 assistant/tool protocol while entering Phase 2",
                            content=phase2_result,
                            metadata={
                                "tool_name": phase2_name,
                                "tool_call_id": phase2_call["id"],
                            },
                        ),
                    )
                    if round_id:
                        phase2_tool_entry["round_id"] = round_id
                    messages.append(phase2_tool_entry)

            phase1_execution_usage = dict(assistant_entry.get("usage") or {})
            phase2_assistant.pop("usage", None)
            missing_control_repairs = 0
            execution_finalization_required = False
            hidden_phase2_usage: dict[str, Any] = phase1_execution_usage
            while True:
                if promoted_phase1_response is not None:
                    response = promoted_phase1_response
                    promoted_phase1_response = None
                    entry = phase2_assistant
                else:
                    if await _inject_runtime_guidance(messages):
                        missing_control_repairs = 0
                    guidance_boundary = len(messages)
                    response = await _call_with_runtime_guidance(
                        messages,
                        lambda: _call_llm(
                            project_history_for_llm(messages),
                            tools=execution_wire_tool_defs,
                        ),
                    )
                    if any(
                        bool(message.get("runtime_guidance"))
                        for message in messages[guidance_boundary:]
                    ):
                        missing_control_repairs = 0
                    entry = {"role": "assistant", "content": response.get("content") or ""}
                    if (
                        "reasoning_content" in response
                        and response["reasoning_content"] is not None
                    ):
                        entry["reasoning_content"] = str(response["reasoning_content"])
                    if response.get("tool_calls"):
                        entry["tool_calls"] = response["tool_calls"]
                    if response.get("usage"):
                        entry["usage"] = response["usage"]
                    if round_id:
                        entry["round_id"] = round_id
                    messages.append(_apply_assistant_meta(entry))

                tcs = response.get("tool_calls") or []
                tool_names = [str(t.get("function", {}).get("name") or "") for t in tcs]
                # ``quit`` is a hard terminal signal. If the model mistakenly mixes
                # it with sibling calls, none of those siblings may execute.
                done_via_quit = "quit" in tool_names
                if not tcs:
                    # Unlike Decision Phase, plain text during execution can be
                    # an in-progress narration (for example, "opening the
                    # browser now"). Keep one bounded control repair. The text
                    # is rejected, not staged as a public answer; only a later
                    # self-contained response paired with ``quit`` can finish.
                    if missing_control_repairs < _MAX_MISSING_CONTROL_REPAIRS:
                        missing_control_repairs += 1
                        execution_finalization_required = (
                            dual_lane and not system_initiated
                        )
                        hidden_phase2_usage = _merge_usage_records(
                            hidden_phase2_usage,
                            entry.pop("usage", None),
                        )
                        entry["hidden_from_ui"] = True
                        messages.append(_missing_completion_signal_entry(
                            round_id,
                            structured_execution_finalize=(
                                dual_lane and not system_initiated
                            ),
                        ))
                        continue

                    hidden_phase2_usage = _merge_usage_records(
                        hidden_phase2_usage,
                        entry.pop("usage", None),
                    )
                    entry["hidden_from_ui"] = True
                    raise AgentControlProtocolError(
                        "Model repeatedly returned no explicit control signal."
                    )
                missing_control_repairs = 0
                if done_via_quit:
                    if runtime_inbox is not None:
                        await runtime_inbox.wait_for_active_tools()
                    # Guidance may have arrived while this model call was in flight.
                    # Do not finalize an answer that the user has already superseded.
                    if runtime_inbox is not None:
                        pending_guidance = runtime_inbox.collect_guidance_nowait()
                        if pending_guidance:
                            for tc in tcs:
                                is_quit = str(tc.get("function", {}).get("name") or "") == "quit"
                                tool_entry = {
                                    "role": "tool",
                                    "tool_call_id": tc["id"],
                                    "content": (
                                        "Completion deferred because new user guidance arrived."
                                        if is_quit else
                                        "Skipped because the same batch contained terminal quit."
                                    ),
                                }
                                if round_id:
                                    tool_entry["round_id"] = round_id
                                messages.append(tool_entry)
                            await _inject_runtime_guidance(messages, pending_guidance)
                            await _save(_session_messages_to_save(messages))
                            continue
                    for tc in tcs:
                        is_quit = (
                            str(tc.get("function", {}).get("name") or "") == "quit"
                        )
                        tool_entry = {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": (
                                "Agent requested to finish."
                                if is_quit
                                else "Skipped because the same batch contained terminal quit."
                            ),
                        }
                        if round_id:
                            tool_entry["round_id"] = round_id
                        messages.append(tool_entry)
                    await _publish_runtime_event({"type": "phase_transition", "from": "execution", "to": "done", "detail": "Agent called quit", "detail_key": "phase.agentQuit"})
                    # An empty-body terminal response is finalized from the
                    # structured Execution packet. Rejected progress text is
                    # never reused as an implicit public answer.
                    # Normalize the protocol assistant before any continuation
                    # request sees it; the same empty-content record is then
                    # persisted, so finalization does not create a changed
                    # historical prefix on the next Execution epoch.
                    entry["content"] = ""
                    final_text = await _ensure_text_reply(
                        response,
                        messages,
                        execution_completion=dual_lane,
                        force_completion_packet=execution_finalization_required,
                        recovery_tools=execution_wire_tool_defs,
                    )
                    # Preserve a valid assistant(tool_calls) -> tool-results
                    # sequence, then store the user-visible answer after it.
                    final_entry = _apply_assistant_meta(
                        {
                            "role": "assistant",
                            "content": final_text,
                            **({"round_id": round_id} if round_id else {}),
                        }
                    )
                    merged_usage = _merge_usage_records(
                        hidden_phase2_usage,
                        entry.pop("usage", None),
                    )
                    if merged_usage:
                        final_entry["usage"] = merged_usage
                    messages.append(final_entry)
                    _attach_final_usage(final_entry)
                    if client_request_id:
                        final_entry["client_request_id"] = client_request_id

                    # Guidance that arrived while a no-tool repair was in flight
                    # starts a continuation; it does not revive the terminated batch.
                    late_guidance = (
                        await runtime_inbox.collect_guidance_or_seal()
                        if runtime_inbox is not None
                        else []
                    )
                    if late_guidance:
                        final_entry["intermediate_reply"] = True
                        await _inject_runtime_guidance(messages, late_guidance)
                        await _save(_session_messages_to_save(messages))
                        hidden_phase2_usage = {}
                        continue
                    await _save(_session_messages_to_save(messages))
                    await _sync_execution_outcome(final_text, response)
                    final_saved = True
                    return final_text

                awaiting_user = False
                spawned = False
                quit_requested = False
                reflection_requested = False
                guidance_supersedes_batch = bool(
                    runtime_inbox is not None and runtime_inbox.has_guidance_nowait()
                )
                pending_reflection_tool_calls: list[tuple[dict[str, Any], dict[str, Any]]] = []
                inbox_batch_args: dict[str, dict[str, Any]] = {}
                mcp_observations: list[dict[str, Any]] = []
                batch_projection_records: list[
                    tuple[dict[str, Any], object, str, str]
                ] = []
                tool_batch_id = f"batch_{uuid4().hex}"
                if runtime_inbox is not None and not guidance_supersedes_batch:
                    inbox_calls: list[tuple[Any, ...]] = []
                    for pending_call in tcs:
                        pending_name = str(pending_call.get("function", {}).get("name") or "")
                        if pending_name in {"use_tools", "quit", "DeepReflect"}:
                            continue
                        if system_initiated and pending_name == "ask_user":
                            continue
                        try:
                            pending_args = parse_tool_arguments(
                                pending_call["function"].get("arguments")
                            )
                        except (KeyError, TypeError, ValueError):
                            continue
                        inbox_batch_args[pending_call["id"]] = pending_args
                        inbox_calls.append((
                            pending_call["id"],
                            pending_name,
                            lambda pending_call_id=pending_call["id"], pending_name=pending_name, pending_args=dict(pending_args): _execute_tool_for_call(
                                pending_call_id, pending_name, pending_args, bot, chat_id, db_path
                            ),
                            _inbox_tool_metadata(pending_name, pending_args),
                        ))
                    if inbox_calls:
                        runtime_inbox.submit_tool_batch(
                            inbox_calls, batch_id=tool_batch_id
                        )
                for index, t in enumerate(tcs):
                    tool_name = t.get("function", {}).get("name")
                    capability_id = str(tool_name or "")
                    if guidance_supersedes_batch and t["id"] not in inbox_batch_args:
                        skipped_tool_entry: dict[str, Any] = {
                            "role": "tool",
                            "tool_call_id": t["id"],
                            "content": "Skipped before execution because new user guidance superseded this tool-call batch.",
                        }
                        if round_id:
                            skipped_tool_entry["round_id"] = round_id
                        messages.append(skipped_tool_entry)
                        continue
                    if awaiting_user:
                        skipped_tool_entry: dict[str, Any] = {
                            "role": "tool", "tool_call_id": t["id"],
                            "content": "Skipped because a previous tool already paused the round until the user answers.",
                        }
                        skipped_tool_entry = attach_context(skipped_tool_entry, context_block(
                            f"tool.result.skipped.{t['id']}",
                            "tool_result",
                            source="cyrene.agent.agent",
                            reason="tool skipped after ask_user paused the round",
                            transforms=["synthetic_tool_result"],
                            content=skipped_tool_entry["content"],
                        ))
                        if round_id:
                            skipped_tool_entry["round_id"] = round_id
                        messages.append(skipped_tool_entry)
                        continue
                    if reflection_requested:
                        skipped_tool_entry = {
                            "role": "tool",
                            "tool_call_id": t["id"],
                            "content": "Skipped because DeepReflect already reframed this turn; continue from the reflection packet instead of executing stale follow-up tools.",
                        }
                        skipped_tool_entry = attach_context(skipped_tool_entry, context_block(
                            f"tool.result.skipped_after_reflection.{t['id']}",
                            "tool_result",
                            source="cyrene.agent.agent",
                            reason="tool skipped after DeepReflect reframed the round",
                            transforms=["synthetic_tool_result"],
                            content=skipped_tool_entry["content"],
                            metadata={"tool_name": tool_name, "tool_call_id": t["id"]},
                        ))
                        if round_id:
                            skipped_tool_entry["round_id"] = round_id
                        messages.append(skipped_tool_entry)
                        continue
                    try:
                        args = inbox_batch_args.get(t["id"])
                        if args is None:
                            args = parse_tool_arguments(
                                t["function"].get("arguments")
                            )
                        capability_id = _resolved_capability_id(str(tool_name or ""), args)
                        if system_initiated and tool_name == "ask_user":
                            result = (
                                "Tool unavailable: proactive system-initiated rounds "
                                "cannot ask the user to clarify or pause for an answer."
                            )
                        elif tool_name == "use_tools":
                            # ``use_tools`` is the Phase-1 gateway, wired into the
                            # execution toolset only for prefix-cache parity with Phase 1.
                            # There is no gate to open here, so treat it as a no-op nudge.
                            result = "Already in the execution phase — call the concrete tools you need directly, or quit when done."
                        elif tool_name == "quit":
                            quit_requested = True
                            result = "Agent requested to finish after this tool-call batch."
                        elif tool_name == "DeepReflect":
                            pending_reflection_tool_calls.append((t, args))
                            reflection_requested = True
                            result = "Deep reflection complete. A reflection record will be added to the visible transcript."
                        else:
                            if runtime_inbox is None:
                                result = await _execute_tool_for_call(
                                    t["id"], str(tool_name or ""), args, bot, chat_id, db_path
                                )
                            else:
                                if t["id"] not in inbox_batch_args:
                                    runtime_inbox.submit_tool(
                                        t["id"],
                                        str(tool_name or ""),
                                        lambda tool_call_id=t["id"], tool_name=tool_name, args=dict(args): _execute_tool_for_call(
                                            tool_call_id, str(tool_name or ""), args, bot, chat_id, db_path
                                        ),
                                        batch_id=tool_batch_id,
                                        metadata=_inbox_tool_metadata(
                                            str(tool_name or ""), args
                                        ),
                                    )
                                result = await runtime_inbox.wait_for_tool_result(t["id"])
                                guidance_supersedes_batch = runtime_inbox.has_guidance_nowait()
                    except Exception as e:
                        logger.warning(
                            "Tool %r failed: %s", tool_name, e, exc_info=True
                        )
                        result = f"Tool failed: {e}"
                    tool_entry: dict[str, Any] = {
                        "role": "tool",
                        "tool_call_id": t["id"],
                        "content": str(result),
                    }
                    if round_id:
                        tool_entry["round_id"] = round_id
                    messages.append(tool_entry)
                    batch_projection_records.append((
                        tool_entry,
                        result,
                        str(tool_name or ""),
                        t["id"],
                    ))
                    observation = build_mcp_observation_message(
                        result,
                        tool_name=str(tool_name or ""),
                    )
                    if observation is not None:
                        if round_id:
                            observation["round_id"] = round_id
                        mcp_observations.append(observation)
                    if _tool_result_requests_user_input(str(result)):
                        awaiting_user = True
                    if capability_id == "subagent.spawn" and _wire_result_succeeded(result):
                        spawned = True
                _project_main_tool_result_batch(batch_projection_records)
                # Keep the assistant -> N tool-results protocol sequence contiguous.
                # Multimodal observations follow the complete tool batch as a
                # model-only user message and are omitted from persisted history.
                messages.extend(mcp_observations)
                await _inject_runtime_guidance(messages)
                if pending_reflection_tool_calls:
                    _ensure_message_identity(messages)
                    pending_reflection_records: list[dict[str, Any]] = []
                    for _tool_call, args in pending_reflection_tool_calls:
                        reflection_record = await create_deep_reflection_record(
                            messages,
                            scope=str(args.get("scope") or "current_round"),
                            goal_gap=str(args.get("goal_gap") or ""),
                            user_requirement=str(args.get("user_requirement") or ""),
                            focus=str(args.get("focus") or ""),
                            lang_text=user_message,
                        )
                        if round_id:
                            reflection_record["round_id"] = round_id
                        if client_request_id:
                            reflection_record["client_request_id"] = client_request_id
                        pending_reflection_records.append(_apply_assistant_meta(reflection_record))
                    messages.extend(pending_reflection_records)
                if awaiting_user:
                    boundary_guidance = (
                        await runtime_inbox.collect_guidance_or_seal()
                        if runtime_inbox is not None
                        else []
                    )
                    if boundary_guidance:
                        await _inject_runtime_guidance(messages, boundary_guidance)
                        await _save(_session_messages_to_save(messages))
                        continue
                    # Pause path: persist before handing back to the user so a
                    # resumed run rebuilds its context with the throttled batches
                    # that were never written to the state file.
                    await _save(_session_messages_to_save(messages))
                    final_saved = True
                    return _AWAITING_USER_SENTINEL
                if quit_requested and not pending_reflection_tool_calls:
                    await _publish_runtime_event({"type": "phase_transition", "from": "execution", "to": "done", "detail": "Agent called quit", "detail_key": "phase.agentQuit"})
                    final_text = await _ensure_text_reply(
                        response,
                        messages,
                        execution_completion=dual_lane,
                        force_completion_packet=execution_finalization_required,
                        recovery_tools=execution_wire_tool_defs,
                    )
                    boundary_guidance = (
                        await runtime_inbox.collect_guidance_or_seal()
                        if runtime_inbox is not None
                        else []
                    )
                    if boundary_guidance:
                        intermediate = _apply_assistant_meta({
                            "role": "assistant",
                            "content": final_text,
                            "intermediate_reply": True,
                            **({"round_id": round_id} if round_id else {}),
                        })
                        messages.append(intermediate)
                        await _inject_runtime_guidance(messages, boundary_guidance)
                        await _save(_session_messages_to_save(messages))
                        continue
                    await _save(_session_messages_to_save(messages))
                    await _sync_execution_outcome(final_text, response)
                    final_saved = True
                    return final_text
                _pending_saved_batches += 1
                if (
                    _pending_saved_batches >= 5
                    or time.monotonic() - _last_batch_save_ts >= 5.0
                    or spawned
                    # An interrupt may never reach the subagent monitoring loop
                    # (e.g. no subagents), so persist promptly once one is pending
                    # — matching the pre-throttle behaviour of saving every batch.
                    or _ensure_session(_current_session_id.get()).interrupt_event.is_set()
                ):
                    await _save(_session_messages_to_save(messages))
                    _last_batch_save_ts = time.monotonic()
                    _pending_saved_batches = 0

                # Subagent monitoring loop
                if spawned:
                    await _publish_runtime_event({
                        "type": "phase_transition", "from": "phase2_execution", "to": "subagent_monitoring",
                        "detail": "Subagents spawned, entering monitoring loop",
                        "detail_key": "phase.subagentMonitoring",
                    })
                    from cyrene.subagent import (
                        run_subagent, spawn_subagent_task,
                        build_deep_research_source as _build_deep_research_source,
                        build_flow_snapshot as _build_subagent_flow_snapshot,
                        cancel_subagent_tasks as _cancel_subagent_tasks,
                        clear as _sub_clear, get_snapshot as _sub_snapshot,
                        get_raw_messages as _sub_raw_msgs, reactivate as _sub_reactivate,
                        run_summary_subagent as _run_summary_subagent,
                        timeout_subagents as _timeout_subagents,
                    )
                    from cyrene.runtime.inbox import get_unread_count as _inbox_unread_base
                    fan_out_guidance_to_subagents = importlib.import_module(
                        "cyrene.agent.guidance"
                    ).fan_out_guidance_to_subagents
                    _agent_session_id = _current_session_id.get()

                    def _inbox_unread(agent_id: str) -> int:
                        return _inbox_unread_base(
                            agent_id,
                            session_id=_agent_session_id,
                        )

                    from cyrene.agent.research import (
                        deduplicate_references as _deduplicate_references,
                        deep_research_pdf_attachment as _deep_research_pdf_attachment,
                        expansion_pass as _expansion_pass,
                        extract_new_references as _extract_new_references,
                        generate_deep_research_outline as _generate_deep_research_outline,
                        load_research_template as _load_research_template,
                        parse_length_preference as _parse_length_preference,
                        assemble_report as _assemble_report,
                        write_section as _write_section,
                    )

                    _interrupt_event_sess = _ensure_session(_current_session_id.get()).interrupt_event
                    _interrupt_event_sess.clear()
                    interrupted = False
                    monitoring_expired = False
                    quiet_ticks = 0
                    from cyrene.runtime.settings_store import get as _get_runtime_setting
                    monitor_timeout_seconds = max(
                        int(_get_runtime_setting("subagent_execution_max_wall_seconds", 1800) or 1800),
                        int(_get_runtime_setting("subagent_discussion_max_wall_seconds", 600) or 600),
                    ) + 30
                    monitor_deadline = asyncio.get_running_loop().time() + monitor_timeout_seconds
                    while asyncio.get_running_loop().time() < monitor_deadline:
                        if runtime_inbox is not None and runtime_inbox.has_guidance_nowait():
                            live_guidance = runtime_inbox.collect_guidance_nowait()
                            guidance_text = "\n\n".join(
                                str((item.get("payload") or {}).get("text") or "").strip()
                                for item in live_guidance
                                if str((item.get("payload") or {}).get("text") or "").strip()
                            )
                            await _inject_runtime_guidance(messages, live_guidance)
                            if guidance_text:
                                await fan_out_guidance_to_subagents(
                                    round_id, guidance_text, bot, chat_id, db_path
                                )
                        try:
                            await asyncio.wait_for(_interrupt_event_sess.wait(), timeout=0.5)
                            _interrupt_event_sess.clear()
                            interrupted = True
                            break
                        except asyncio.TimeoutError:
                            pass
                        snap = await _sub_snapshot(round_id=round_id)
                        if not snap:
                            break
                        resurrected = False
                        for aid, info in snap.items():
                            if info["status"] in ("done", "timeout", "incomplete") and _inbox_unread(aid) > 0:
                                if await _sub_reactivate(aid):
                                    raw = await _sub_raw_msgs(aid)
                                    spawn_subagent_task(
                                        run_subagent(aid, info["task"], bot, chat_id, db_path, resume_messages=raw),
                                        aid,
                                    )
                                    resurrected = True
                        snap2 = await _sub_snapshot(round_id=round_id)
                        all_truly_done = all(
                            info["status"] in ("done", "timeout", "incomplete") and _inbox_unread(aid) == 0
                            for aid, info in snap2.items()
                        )
                        if all_truly_done and not resurrected:
                            quiet_ticks += 1
                            if quiet_ticks >= 2:
                                break
                        else:
                            quiet_ticks = 0
                    else:
                        monitoring_expired = True
                    if interrupted:
                        await _save(_session_messages_to_save(messages))
                        # Cancel running subagents immediately and mark them done so
                        # the summary phase can start right away.
                        await _cancel_subagent_tasks(round_id=round_id)
                    elif monitoring_expired:
                        expired_snapshot = await _sub_snapshot(round_id=round_id)
                        active_ids = [
                            aid
                            for aid, info in expired_snapshot.items()
                            if info.get("status") in ("running", "resumed")
                        ]
                        if active_ids:
                            await _timeout_subagents(
                                active_ids,
                                reason="Subagent parent-monitor safety deadline reached.",
                            )
                    await _publish_runtime_event({
                        "type": "phase_transition", "from": "subagent_monitoring", "to": "synthesis",
                        "detail": "All subagents done, starting summary subagent",
                        "detail_key": "phase.synthesis",
                    })
                    summary_task = asyncio.create_task(_run_summary_subagent(
                        round_id=round_id, parent_task=user_message, round_history=messages,
                    ))
                    if runtime_inbox is not None:
                        guidance_task = asyncio.create_task(runtime_inbox.wait_for_guidance())
                        try:
                            done, _pending = await asyncio.wait(
                                {summary_task, guidance_task},
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                        except asyncio.CancelledError:
                            summary_task.cancel()
                            guidance_task.cancel()
                            await asyncio.gather(
                                summary_task, guidance_task, return_exceptions=True
                            )
                            raise
                        if guidance_task in done and summary_task not in done and await guidance_task:
                            summary_task.cancel()
                            await asyncio.gather(summary_task, return_exceptions=True)
                            live_guidance = runtime_inbox.collect_guidance_nowait()
                            guidance_text = "\n\n".join(
                                str((item.get("payload") or {}).get("text") or "").strip()
                                for item in live_guidance
                                if str((item.get("payload") or {}).get("text") or "").strip()
                            )
                            await _inject_runtime_guidance(messages, live_guidance)
                            if guidance_text:
                                await fan_out_guidance_to_subagents(
                                    round_id, guidance_text, bot, chat_id, db_path
                                )
                            await _save(_session_messages_to_save(messages))
                            continue
                        guidance_task.cancel()
                        await asyncio.gather(guidance_task, return_exceptions=True)
                    summary_result = await summary_task

                    # Deep research Phase 3
                    if _deep_research_mode.get():
                        source_material = await _build_deep_research_source(round_id)
                        template = _load_research_template()
                        length_pref = _parse_length_preference(messages)
                        outline = await _generate_deep_research_outline(source_material, template, user_message, lang, length_pref)
                        units: list[dict] = outline.get("units", [])
                        if not units:
                            logger.warning("Deep research outline has no units, falling back to research materials")
                            final_text = source_material
                            synthesis_entry = {"role": "assistant", "content": final_text}
                        else:
                            sections_raw = await asyncio.gather(*[
                                _write_section(
                                    source_material=source_material, outline=outline,
                                    unit_def=unit_def, unit_no=unit_no,
                                    total_units=len(units), all_units=units,
                                    lang=lang, length_pref=length_pref,
                                )
                                for unit_no, unit_def in enumerate(units, 1)
                            ])
                            sections_written: list[str] = []
                            references_accumulated: list[str] = []
                            for section_text in sections_raw:
                                body, new_refs = _extract_new_references(section_text)
                                sections_written.append(body)
                                references_accumulated.extend(new_refs)
                            total_len = sum(len(s) for s in sections_written)
                            expand_threshold = {"short": 4000, "medium": 8000, "long": 15000}.get(length_pref, 8000)
                            if total_len < expand_threshold:
                                sections_written = await _expansion_pass(
                                    outline, sections_written, references_accumulated, lang,
                                )
                            references_accumulated, dedup_mapping = _deduplicate_references(references_accumulated)
                            final_text = _assemble_report(sections_written, references_accumulated, outline, dedup_mapping=dedup_mapping)
                        # Add a brief concluding message after the report
                        if lang and lang != "en":
                            closing_note = "\n\n---\n\n✅ **深度研究报告已生成完成。**"
                        else:
                            closing_note = "\n\n---\n\n✅ **Deep research report has been generated.**"
                        pdf_attachment = _deep_research_pdf_attachment(round_id, user_message, final_text)
                        if pdf_attachment:
                            pdf_name = pdf_attachment.get("name", "deep-research-report.pdf")
                            pdf_url = pdf_attachment.get("url", "")
                            if pdf_url:
                                closing_note += f"\n\n📎 [{pdf_name}]({pdf_url})"
                        final_text = final_text.rstrip() + closing_note
                        synthesis_entry = {"role": "assistant", "content": final_text, "deep_research_report": True}
                        if pdf_attachment:
                            synthesis_entry["attachments"] = [pdf_attachment]
                    else:
                        final_text = summary_result
                        synthesis_entry = {"role": "assistant", "content": final_text}

                    flow_snapshot = await _build_subagent_flow_snapshot(round_id)
                    if client_request_id:
                        synthesis_entry["client_request_id"] = client_request_id
                    if round_id:
                        synthesis_entry["round_id"] = round_id
                    if flow_snapshot:
                        synthesis_entry["subagent_flow_snapshot"] = flow_snapshot
                    synthesis_usage = _merge_usage_records(
                        hidden_phase2_usage,
                        synthesis_entry.get("usage"),
                    )
                    if synthesis_usage:
                        synthesis_entry["usage"] = synthesis_usage
                    boundary_guidance = (
                        await runtime_inbox.collect_guidance_or_seal()
                        if runtime_inbox is not None
                        else []
                    )
                    if boundary_guidance:
                        synthesis_entry["intermediate_reply"] = True
                        if _streaming_reply_requested():
                            messages.pop()
                        messages.append(_apply_assistant_meta(synthesis_entry))
                        guidance_text = "\n\n".join(
                            str((item.get("payload") or {}).get("text") or "").strip()
                            for item in boundary_guidance
                            if str((item.get("payload") or {}).get("text") or "").strip()
                        )
                        await _inject_runtime_guidance(messages, boundary_guidance)
                        if guidance_text:
                            await fan_out_guidance_to_subagents(
                                round_id, guidance_text, bot, chat_id, db_path
                            )
                        await _save(_session_messages_to_save(messages))
                        hidden_phase2_usage = {}
                        continue
                    # 弹出 Phase 2 的 assistant entry（content="" + tool_calls），避免
                    # 流式输出时与 synthesis_entry 的 clientRequestId 重复导致前端去重异常
                    if _streaming_reply_requested():
                        messages.pop()
                    messages.append(_apply_assistant_meta(synthesis_entry))
                    await _sub_clear(round_id=round_id)
                    await _save(_session_messages_to_save(messages))
                    await _sync_execution_outcome(final_text)
                    final_saved = True
                    return final_text

        # Deep research first round: if LLM output text instead of calling ask_user, retry
        if _deep_research_first_round.get() and not ask_user_call and not use_tools_call:
            retry_messages = [
                *phase1_messages,
                {
                    **_assistant_entry_from_response(response, round_id="", include_tool_calls=False),
                    "content": assistant_text(response) or (response.get("content") or ""),
                },
                {
                    "role": "user",
                    "content": (
                        "You replied with text. You MUST call the `ask_user` function. "
                        "Call `ask_user` with text=\"请选择报告篇幅\" and "
                        "options=[\"长（30+页）\", \"中（20+页）\", \"短（10+页）\"]."
                    ),
                },
            ]
            response = await _call_with_runtime_guidance(
                retry_messages,
                lambda: _call_phase1_llm(
                    project_history_for_llm(retry_messages),
                    tools=phase1_tools,
                ),
            )
            for tc in (response.get("tool_calls") or []):
                if tc.get("function", {}).get("name") == "ask_user":
                    ask_user_call = tc
                    break
            if ask_user_call:
                # Replace the rejected plain-text handshake attempt with the
                # actual assistant control record. Its usage still rolls into
                # the visible pending question.
                if messages and messages[-1] is assistant_entry:
                    messages.pop()
                retry_assistant_entry = _assistant_entry_from_response(
                    response,
                    round_id,
                )
                retry_usage = _merge_usage_records(
                    assistant_entry.get("usage"),
                    retry_assistant_entry.get("usage"),
                )
                if retry_usage:
                    retry_assistant_entry["usage"] = retry_usage
                messages.append(retry_assistant_entry)
                try:
                    args = parse_tool_arguments(
                        ask_user_call["function"].get("arguments")
                    )
                    result = await execute_wire_tool(
                        "ask_user", args, bot, chat_id, db_path, None, actor="main"
                    )
                except Exception as exc:
                    logger.warning("Tool ask_user failed (retry): %s", exc, exc_info=True)
                    result = f"Tool failed: {exc}"
                projected_result = project_tool_result_for_model(
                    result,
                    tool_name="ask_user",
                    tool_call_id=ask_user_call["id"],
                )
                tool_entry: dict[str, Any] = {"role": "tool", "tool_call_id": ask_user_call["id"], "content": projected_result.content}
                tool_entry = attach_context(tool_entry, context_block(
                    f"tool.result.ask_user.{ask_user_call['id']}",
                    "tool_result",
                    source="tool:ask_user",
                    reason="ask_user tool output returned to LLM after correction",
                    transforms=["tool_result_projection"] if projected_result.truncated else [],
                    content=projected_result.content,
                    metadata={
                        "tool_name": "ask_user",
                        "tool_call_id": ask_user_call["id"],
                        "original_tokens": projected_result.original_tokens,
                        "original_bytes": projected_result.original_bytes,
                        "content_ref": projected_result.content_ref,
                    },
                ))
                if round_id:
                    tool_entry["round_id"] = round_id
                messages.append(tool_entry)
                if _tool_result_requests_user_input(result):
                    if dual_lane:
                        await _persist_awaiting_user_protocol(
                            retry_assistant_entry,
                            tool_entry,
                            tool_call_id=str(ask_user_call["id"]),
                        )
                    else:
                        await _rollup_legacy_pending_question_usage(
                            retry_assistant_entry.get("usage")
                        )
                        paused = True
                    return _AWAITING_USER_SENTINEL
                await _save(_session_messages_to_save(messages))
                final_saved = True
                return (await _ensure_text_reply(response, messages, fallback=str(result)))

        # Chat-only path (no tools)
        logger.info("Agent run phase1 -> chat_only (round=%s)", round_id or "-")
        event = {"type": "phase_transition", "from": "phase1_decision", "to": "chat_only"}
        if not suppress_initial_detail:
            event["detail"] = "Phase 1 decided chat-only, no tools needed"
            event["detail_key"] = "phase.chatOnly"
        await _publish_runtime_event(event)
        return await _commit_phase1_terminal_reply(response, messages)
    except asyncio.CancelledError:
        if dual_lane and execution_started and not execution_outcome_synced:
            try:
                await asyncio.shield(_sync_execution_outcome(
                    "",
                    status="cancelled",
                    state_summary_override="Execution was cancelled before completion.",
                    unresolved_override=["Execution was cancelled before completion."],
                ))
            except Exception:
                logger.warning(
                    "Failed to persist cancelled ExecutionOutcome",
                    exc_info=True,
                )
        raise
    except Exception:
        if dual_lane and execution_started and not execution_outcome_synced:
            logger.warning(
                "Execution lane failed before completion",
                exc_info=True,
            )
            try:
                await _sync_execution_outcome(
                    "",
                    status="failed",
                    state_summary_override="Execution failed before completion.",
                    unresolved_override=[
                        "Execution failed before completion."
                    ],
                )
            except Exception:
                logger.warning(
                    "Failed to persist failed ExecutionOutcome",
                    exc_info=True,
                )
        raise
    finally:
        # Persist unconditionally unless a completion/pause path already wrote
        # its exact transcript. The interrupt path
        # cancels the run task at its next await point — which may sit inside
        # the throttle's own save — so a plain save here could be torn down
        # too. Shield keeps the write running on its own task even if this
        # finally's await is cancelled again, so every executed batch reaches
        # the state file. The completion paths save the exact final payload
        # themselves (final_saved), so a clean return only writes once.
        if not paused and not final_saved:
            try:
                await asyncio.shield(_save(_session_messages_to_save(messages)))
            except Exception:
                # Best-effort: the run's own completion paths already surface
                # save failures; this must not mask a clean return or a
                # cancellation.
                logger.warning("Failed to persist final agent state", exc_info=True)
        if execution_lane_binding is not None:
            execution_lane_binding.__exit__(None, None, None)


async def _run_main_agent(
    user_message: str,
    history: list,
    bot: Any,
    chat_id: int,
    db_path: str,
    system_prompt: str = "",
    client_request_id: str = "",
    persist_user_message: bool = True,
    public_user_message: str | None = None,
    public_attachments: list[dict[str, Any]] | None = None,
    llm_user_content: Any | None = None,
    lang: str = "",
    system_context: list[dict[str, Any]] | None = None,
    ephemeral_system: str = "",
    fixed_ephemeral_system: str = "",
    resume_lane: str = "",
) -> str:
    """Run one main-agent turn against an immutable capability snapshot."""
    from cyrene.tooling.gateway import (
        activate_catalog_snapshot,
        reset_catalog_snapshot,
    )

    snapshot_token = activate_catalog_snapshot("main")
    try:
        lease_was_active = has_active_run_model_lease()
        # The coordinator freezes the provider policy before entering this
        # compatibility wrapper. Historical direct callers have no such lease:
        # pin their candidates, but keep the original shared transcript/cache
        # contract instead of silently switching their internal call shape.
        model_lease_token = (
            None
            if lease_was_active
            else activate_run_model_lease(
                transcript_policy_override=TranscriptPolicy.LEGACY_SHARED
            )
        )
        try:
            return await _run_main_agent_impl(
                user_message,
                history,
                bot,
                chat_id,
                db_path,
                system_prompt=system_prompt,
                client_request_id=client_request_id,
                persist_user_message=persist_user_message,
                public_user_message=public_user_message,
                public_attachments=public_attachments,
                llm_user_content=llm_user_content,
                lang=lang,
                system_context=system_context,
                ephemeral_system=ephemeral_system,
                fixed_ephemeral_system=fixed_ephemeral_system,
                resume_lane=resume_lane,
            )
        finally:
            if model_lease_token is not None:
                reset_run_model_lease(model_lease_token)
    finally:
        reset_catalog_snapshot(snapshot_token)
