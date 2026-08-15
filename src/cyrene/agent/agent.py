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
    _final_plain_reply_from_history,
    _final_reply_from_history,
    _final_user_reply_from_history,
    _is_placeholder_reply,
    _tool_result_fallback_text,
)
from cyrene.agent.deep_reflection import create_deep_reflection_record, project_history_for_llm
from cyrene.agent.message import (
    _apply_assistant_meta,
    _assistant_entry_from_response,
    _ensure_message_identity,
    _flush_intermediate_user_replies,
    _tool_result_requests_user_input,
)
from cyrene.agent.prompts import (
    _DEEP_RESEARCH_PHASE1_DECISION,
    _MAIN_AGENT_PROMPT_TEMPLATE,
    _PHASE1_DECISION_PROMPT,
    prompt_for_enabled_tool_packs,
)
from cyrene.agent.model_service import take_final_reply_usage
from cyrene.agent.session import _append_session_message, _save_session_messages
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
    _economy_mode,
    _emit_reply_stream_event,
    _ensure_session,
    _LIGHT_TOOL_DEFS,
    _llm_phase_override,
    _publish_runtime_event,
    _streaming_reply_requested,
    _ui_round_assistant_meta,
    _ui_round_hide_initial_detail,
    activate_run_model_lease,
    reset_run_model_lease,
)
from cyrene.model_runtime.messages import (
    assistant_text,
    parse_tool_arguments,
    truncate,
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
from cyrene.workbench.inbox import current_workbench_inbox

logger = logging.getLogger(__name__)


async def _call_phase1_llm(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    """Tag Phase-1 telemetry without changing its cache-stable tool array."""
    token = _llm_phase_override.set("phase1")
    try:
        return await _call_llm(messages, tools=tools)
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
) -> None:
    """Close the live lifecycle opened by ``_publish_tool_call_started``.

    The wire wrapper owns this event because progressive ``discover`` and
    ``describe`` calls return before reaching the concrete executor. Publishing
    here guarantees that every started wire call has a matching terminal event,
    regardless of which gateway branch handled it.
    """
    try:
        await _publish_runtime_event({
            "type": "tool_call_finished",
            "tool_call_id": str(tool_call_id),
            "tool": str(tool_name or ""),
            "args": redact_value(arguments),
            "status": str(status or "completed"),
            "failed": str(status or "").casefold() == "failed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        # Live activity is observability. A disconnected SSE subscriber must not
        # turn an otherwise successful tool result into an agent failure.
        logger.debug("Failed to publish tool completion for %s", tool_name, exc_info=True)


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
    except asyncio.CancelledError:
        await _publish_tool_call_finished(
            tool_call_id,
            tool_name,
            arguments,
            status="cancelled",
        )
        raise
    except Exception:
        await _publish_tool_call_finished(
            tool_call_id,
            tool_name,
            arguments,
            status="failed",
        )
        raise
    else:
        await _publish_tool_call_finished(
            tool_call_id,
            tool_name,
            arguments,
            status="completed" if _wire_result_succeeded(result) else "failed",
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


def _without_tool(tool_defs: list[dict[str, Any]], tool_name: str) -> list[dict[str, Any]]:
    return [tool_def for tool_def in tool_defs if _tool_def_name(tool_def) != tool_name]


def _attach_final_usage(entry: dict[str, Any]) -> dict[str, Any]:
    """Carry the final-reply call's token usage onto the persisted entry."""
    usage = take_final_reply_usage()
    if usage:
        entry["usage"] = dict(usage)
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


def _economy_compact_messages(messages: list[dict], current_round_id: str) -> list[dict]:
    """经济模式：清除已完成轮次的工具结果，只保留对话流。

    - 保留当前轮（tool loop 进行中）的全部消息（LLM 协议需要 role:tool 配对）
    - 清除前一轮及更早的 role:tool 消息，以及 asst 消息中的 tool_calls
    - 只保存对话主干：user ↔ asst(纯文本回复)
    """
    if not messages:
        return messages
    result: list[dict] = []
    for m in messages:
        role = m.get("role")
        msg_round = str(m.get("round_id") or "").strip()
        # 当前轮的消息：全部保留（包括 tool 结果，LLM 协议要求）
        if msg_round == current_round_id:
            result.append(m)
            continue
        # 旧的 tool 结果：丢弃
        if role == "tool":
            continue
        # 旧的 assistant 消息：去掉 tool_calls，只留文本回复
        if role == "assistant" and m.get("tool_calls"):
            m = {k: v for k, v in m.items() if k != "tool_calls"}
            if not str(m.get("content") or "").strip():
                continue  # 去掉 tool_calls 后无内容则跳过
        result.append(m)
    return result


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

    # Phase 1 and Phase 2 use the same deterministic bundle for the current
    # package settings. Disabling a package intentionally changes the cache key:
    # its gateway schema and package-specific prompt lines are both omitted.
    # Deep Research's length handshake keeps its dedicated tiny bundle.
    wire_tool_defs = get_main_wire_tool_defs()
    enabled_wire_names = {
        str((tool_def.get("function") or {}).get("name") or "")
        for tool_def in wire_tool_defs
        if str((tool_def.get("function") or {}).get("name") or "").endswith(
            "_tools"
        )
    }

    visible_user_message = user_message if public_user_message is None else str(public_user_message)
    user_message_id = f"user_{uuid4().hex}"
    user_entry = {"role": "user", "content": visible_user_message, "message_id": user_message_id}
    if public_attachments:
        user_entry["attachments"] = [dict(item) for item in public_attachments if isinstance(item, dict)]
    if round_id:
        user_entry["round_id"] = round_id
    if client_request_id:
        user_entry["client_request_id"] = client_request_id
    if persist_user_message:
        await _append_session_message(user_entry)
    effective_system = (
        str(system_prompt)
        if str(system_prompt or "").strip()
        else prompt_for_enabled_tool_packs(
            _MAIN_AGENT_PROMPT_TEMPLATE,
            enabled_wire_names,
        )
    )
    llm_user_entry = dict(user_entry)
    llm_user_entry["content"] = (
        llm_user_content if llm_user_content is not None else user_message
    )
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
    history = _annotate_history_context(history)
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
        phase1_decision = _PHASE1_DECISION_PROMPT
    if system_initiated:
        phase1_decision += (
            "\n- This is a proactive system-initiated round. Do not call `ask_user`; "
            "either complete the check-in autonomously or finish silently."
        )
        phase1_tools = _without_tool(phase1_tools, "ask_user")
    phase1_decision_entry = attach_context({"role": "user", "content": phase1_decision}, context_block(
        "phase1.decision_rules",
        "phase_rules",
        source="cyrene.agent.prompts",
        reason="decision-phase tool-gating rules",
        content=phase1_decision,
    ))
    phase1_decision_entry["hidden_from_ui"] = True
    phase1_messages = [*run_prefix, llm_user_entry, phase1_decision_entry]
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

    async def _ensure_text_reply(
        response_obj: dict[str, Any],
        base_messages: list[dict[str, Any]],
        fallback: str = "Done.",
    ) -> str:
        # A valid terminal answer has already paid for the main model call.
        # Deliver it directly instead of rebuilding the full history. Tool-markup
        # or placeholder replies deliberately fall through to no-tool recovery.
        text = _safe_terminal_reply_from_response(response_obj, base_messages)
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
        llm_base_messages = project_history_for_llm(base_messages)
        if _history_has_tool_results(base_messages):
            final_user_text = (await _final_user_reply_from_history(llm_base_messages, max_tokens=None)).strip()
            if final_user_text and not _is_placeholder_reply(final_user_text):
                return final_user_text
            fallback_from_tools = _tool_result_fallback_text(base_messages).strip()
            if fallback_from_tools:
                return fallback_from_tools
        else:
            final_plain_text = (await _final_plain_reply_from_history(llm_base_messages, max_tokens=None)).strip()
            if final_plain_text and not _is_placeholder_reply(final_plain_text):
                return final_plain_text
        final_text = (await _final_reply_from_history(llm_base_messages, max_tokens=None)).strip()
        if final_text and not _is_placeholder_reply(final_text):
            return final_text
        return fallback

    def _session_messages_to_save(current_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        _flush_intermediate_user_replies(current_messages)
        saved: list[dict[str, Any]] = []
        for message in current_messages[1:]:
            if message["role"] == "system":
                continue
            if bool(message.get("hidden_from_ui")):
                continue
            if not persist_user_message and message.get("message_id") == user_message_id:
                continue
            if message.get("role") == "user" and message.get("message_id") == user_message_id:
                saved.append(dict(user_entry))
                continue
            saved.append(message)
        if _economy_mode.get():
            saved = _economy_compact_messages(saved, round_id)
        return saved

    # Phase 1: lightweight decision. Wire the SAME full array as Phase 2 so the
    # two phases share DeepSeek's tool-sensitive prefix cache even on the first
    # ordinary round. Deep-research's first round keeps its tiny ask_user-only set
    # because it has a separate length-preference handshake.
    phase1_wire_tools = (
        phase1_tools if _deep_research_first_round.get() else wire_tool_defs
    )
    phase1_runtime_guidance_entries: list[dict[str, Any]] = []
    response = await _call_with_runtime_guidance(
        phase1_messages,
        lambda: _call_phase1_llm(
            project_history_for_llm(phase1_messages),
            tools=phase1_wire_tools,
        ),
    )
    if runtime_inbox is not None:
        phase1_guidance = runtime_inbox.collect_guidance_nowait()
        if phase1_guidance:
            phase1_messages.append(_assistant_entry_from_response(response, round_id))
            for tc in (response.get("tool_calls") or []):
                phase1_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": "Decision deferred because new user guidance arrived.",
                    **({"round_id": round_id} if round_id else {}),
                })
            await _inject_runtime_guidance(phase1_messages, phase1_guidance)
            phase1_runtime_guidance_entries = [
                message for message in phase1_messages if message.get("runtime_guidance")
            ]
            response = await _call_with_runtime_guidance(
                phase1_messages,
                lambda: _call_phase1_llm(
                    project_history_for_llm(phase1_messages),
                    tools=phase1_wire_tools,
                ),
            )
    tool_calls = response.get("tool_calls") or []
    phase1_allowed = {_tool_def_name(tool_def) for tool_def in phase1_tools}
    phase1_wire_names = {
        _tool_def_name(tool_def) for tool_def in phase1_wire_tools
    }
    phase1_can_promote_tools = (
        not _deep_research_first_round.get()
        and _current_command.get() != "quick-answer"
    )
    promotable_phase1_tool_names = {
        str(tc.get("function", {}).get("name") or "").strip()
        for tc in tool_calls
        if phase1_can_promote_tools
        and str(tc.get("function", {}).get("name") or "").strip()
        in phase1_wire_names
        and str(tc.get("function", {}).get("name") or "").strip()
        not in phase1_allowed
    }
    invalid_phase1_tools = [
        str(tc.get("function", {}).get("name") or "").strip()
        for tc in tool_calls
        if str(tc.get("function", {}).get("name") or "").strip() not in phase1_allowed
        and str(tc.get("function", {}).get("name") or "").strip()
        not in promotable_phase1_tool_names
    ]
    phase1_context_messages = phase1_messages
    if invalid_phase1_tools:
        retry_messages = [
            *phase1_messages,
            {
                **_assistant_entry_from_response(response, round_id="", include_tool_calls=False),
                "content": assistant_text(response) or (response.get("content") or ""),
            },
            {
                "role": "user",
                "content": (
                    f"[Decision-phase correction] You attempted unavailable tool(s): {', '.join(invalid_phase1_tools)}. "
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
                            "Otherwise say there is no suitable tool in this phase.")
                ),
            },
        ]
        response = await _call_with_runtime_guidance(
            retry_messages,
            lambda: _call_phase1_llm(
                project_history_for_llm(retry_messages),
                tools=phase1_wire_tools,
            ),
        )
        phase1_context_messages = retry_messages
    tool_calls = response.get("tool_calls") or []
    normalized_phase1_calls: list[dict[str, Any]] = []
    for tool_call in tool_calls:
        if str(tool_call.get("function", {}).get("name") or "") != "use_tools":
            normalized_phase1_calls.append(tool_call)
            continue
        try:
            raw_use_tools_args = parse_tool_arguments(
                tool_call.get("function", {}).get("arguments")
            )
        except Exception:
            raw_use_tools_args = {}
        execution_brief = str(
            raw_use_tools_args.get("execution_brief") or ""
        ).strip()[:300]
        normalized_call = {
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
        }
        normalized_phase1_calls.append(normalized_call)
    tool_calls = normalized_phase1_calls
    if response.get("tool_calls") is not None:
        response = {**response, "tool_calls": tool_calls}
    phase1_concrete_calls = [
        tool_call
        for tool_call in tool_calls
        if phase1_can_promote_tools
        and str(tool_call.get("function", {}).get("name") or "")
        in phase1_wire_names
        and str(tool_call.get("function", {}).get("name") or "")
        not in {"use_tools", "ask_user", "quit"}
    ]
    phase1_ask_calls = [
        tool_call
        for tool_call in tool_calls
        if not system_initiated
        and str(tool_call.get("function", {}).get("name") or "") == "ask_user"
    ]
    if phase1_concrete_calls and phase1_ask_calls:
        # Clarification wins over execution. Drop sibling actions so the saved
        # assistant/tool protocol cannot contain unresolved concrete calls.
        tool_calls = phase1_ask_calls
        phase1_concrete_calls = []
        response = {**response, "tool_calls": tool_calls}
    elif phase1_concrete_calls:
        # A concrete action is stronger evidence of execution intent than a
        # contradictory quit emitted in the same decision response. Keep
        # use_tools as a harmless gateway result, but let Phase 2 execute the
        # concrete calls before asking the model whether the run is complete.
        tool_calls = [
            tool_call
            for tool_call in tool_calls
            if str(tool_call.get("function", {}).get("name") or "") != "quit"
        ]
        response = {**response, "tool_calls": tool_calls}
    phase1_runtime_guidance_entries = [
        message
        for message in phase1_context_messages
        if message.get("runtime_guidance")
    ]
    messages = [*run_prefix, llm_user_entry, *phase1_runtime_guidance_entries]
    # The wait state's durable form is written by _upsert_pending_question
    # (clean user + question_prompt pair, no raw tool trace), so the finally
    # below must skip the save when exiting via the pause path.
    paused = False
    final_saved = False
    try:
        assistant_entry = _assistant_entry_from_response(response, round_id)
        messages.append(assistant_entry)

        use_tools_call = None
        ask_user_call = None
        quit_call = None
        for tc in tool_calls:
            name = tc.get("function", {}).get("name")
            if name == "use_tools":
                use_tools_call = tc
            elif name == "ask_user" and not system_initiated:
                ask_user_call = tc
            elif name == "quit":
                quit_call = tc

        # Phase 1 has several direct-return branches. Atomically close guidance
        # admission before taking one; if a durable command won the race, promote
        # this turn into Phase 2 so the command is applied instead of cancelled.
        if (
            use_tools_call is None
            and not phase1_concrete_calls
            and runtime_inbox is not None
        ):
            boundary_guidance = await runtime_inbox.collect_guidance_or_seal()
            if boundary_guidance:
                phase1_messages.append(_assistant_entry_from_response(response, round_id))
                for tc in tool_calls:
                    phase1_messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": "Decision deferred because new user guidance arrived.",
                        **({"round_id": round_id} if round_id else {}),
                    })
                await _inject_runtime_guidance(phase1_messages, boundary_guidance)
                phase1_runtime_guidance_entries = [
                    message
                    for message in phase1_messages
                    if message.get("runtime_guidance")
                ]
                use_tools_call = {
                    "id": f"guidance_use_tools_{uuid4().hex}",
                    "function": {
                        "name": "use_tools",
                        "arguments": json.dumps({
                            "execution_brief": "Apply the newly delivered runtime guidance.",
                        }),
                    },
                }
                phase1_context_messages = phase1_messages
                phase1_concrete_calls = []
                ask_user_call = None
                quit_call = None

        if quit_call is not None:
            if runtime_inbox is not None:
                await runtime_inbox.wait_for_active_tools()
            final_text = await _ensure_text_reply(response, messages)
            messages[-1]["content"] = final_text
            messages[-1].pop("tool_calls", None)
            if client_request_id:
                messages[-1]["client_request_id"] = client_request_id
            await _save(_session_messages_to_save(messages))
            final_saved = True
            return final_text

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
            truncated_result = truncate(result)
            tool_entry: dict[str, Any] = {"role": "tool", "tool_call_id": ask_user_call["id"], "content": truncated_result}
            tool_entry = attach_context(tool_entry, context_block(
                f"tool.result.ask_user.{ask_user_call['id']}",
                "tool_result",
                source="tool:ask_user",
                reason="ask_user tool output returned to LLM",
                transforms=["truncate"] if str(truncated_result) != str(result) else [],
                content=truncated_result,
                metadata={"tool_name": "ask_user", "tool_call_id": ask_user_call["id"]},
            ))
            if round_id:
                tool_entry["round_id"] = round_id
            messages.append(tool_entry)
            if _tool_result_requests_user_input(result):
                # Pause path: _upsert_pending_question already wrote the durable
                # wait state (clean user + question_prompt pair); saving the raw
                # in-memory trace here would overwrite it. Just exit.
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
            if phase1_concrete_calls:
                phase2_assistant = _assistant_entry_from_response(response, round_id)
                phase2_assistant["hidden_from_ui"] = True
                messages = [*phase1_context_messages, phase2_assistant]
                promoted_phase1_response = response
            else:
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

            while True:
                if promoted_phase1_response is not None:
                    response = promoted_phase1_response
                    promoted_phase1_response = None
                    entry = phase2_assistant
                else:
                    await _inject_runtime_guidance(messages)
                    response = await _call_with_runtime_guidance(
                        messages,
                        lambda: _call_llm(
                            project_history_for_llm(messages),
                            tools=wire_tool_defs,
                        ),
                    )
                    entry = {"role": "assistant", "content": response.get("content") or ""}
                    if response.get("reasoning_content"):
                        entry["reasoning_content"] = response["reasoning_content"]
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
                if done_via_quit or not tcs:
                    if done_via_quit and runtime_inbox is not None:
                        await runtime_inbox.wait_for_active_tools()
                    # Guidance may have arrived while this model call was in flight.
                    # Do not finalize an answer that the user has already superseded.
                    if runtime_inbox is not None:
                        pending_guidance = runtime_inbox.collect_guidance_nowait()
                        if pending_guidance:
                            if done_via_quit:
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
                    if done_via_quit:
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
                    # A missing/invalid terminal answer may be repaired, but only by
                    # the no-tool final-reply path used inside ``_ensure_text_reply``.
                    # Once quit is observed, this run can never reopen execution.
                    final_text = await _ensure_text_reply(response, messages)
                    final_entry = entry
                    if done_via_quit:
                        # Preserve a valid assistant(tool_calls) -> tool-results
                        # sequence, then store the user-visible answer as the
                        # terminal assistant message after that sequence.
                        entry["content"] = ""
                        final_entry = _apply_assistant_meta(
                            {
                                "role": "assistant",
                                "content": final_text,
                                **({"round_id": round_id} if round_id else {}),
                            }
                        )
                        if entry.get("usage"):
                            final_entry["usage"] = entry.pop("usage")
                        messages.append(final_entry)
                    else:
                        entry["content"] = final_text
                        entry.pop("tool_calls", None)
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
                        continue
                    await _save(_session_messages_to_save(messages))
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
                    truncated_result = truncate(result)
                    tool_entry: dict[str, Any] = {"role": "tool", "tool_call_id": t["id"], "content": truncated_result}
                    tool_entry = attach_context(tool_entry, context_block(
                        f"tool.result.{tool_name}.{t['id']}",
                        "tool_result",
                        source=f"tool:{tool_name}",
                        reason="tool output returned to LLM",
                        transforms=["truncate"] if str(truncated_result) != str(result) else [],
                        content=truncated_result,
                        metadata={"tool_name": tool_name, "tool_call_id": t["id"]},
                    ))
                    if round_id:
                        tool_entry["round_id"] = round_id
                    messages.append(tool_entry)
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
                    final_text = await _ensure_text_reply(response, messages)
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
                        continue
                    # 弹出 Phase 2 的 assistant entry（content="" + tool_calls），避免
                    # 流式输出时与 synthesis_entry 的 clientRequestId 重复导致前端去重异常
                    if _streaming_reply_requested():
                        messages.pop()
                    messages.append(_apply_assistant_meta(synthesis_entry))
                    await _sub_clear(round_id=round_id)
                    await _save(_session_messages_to_save(messages))
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
                truncated_result = truncate(result)
                tool_entry: dict[str, Any] = {"role": "tool", "tool_call_id": ask_user_call["id"], "content": truncated_result}
                tool_entry = attach_context(tool_entry, context_block(
                    f"tool.result.ask_user.{ask_user_call['id']}",
                    "tool_result",
                    source="tool:ask_user",
                    reason="ask_user tool output returned to LLM after correction",
                    transforms=["truncate"] if str(truncated_result) != str(result) else [],
                    content=truncated_result,
                    metadata={"tool_name": "ask_user", "tool_call_id": ask_user_call["id"]},
                ))
                if round_id:
                    tool_entry["round_id"] = round_id
                messages.append(tool_entry)
                if _tool_result_requests_user_input(result):
                    # Pause path: _upsert_pending_question already wrote the
                    # durable wait state; see the other pause site above.
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
        if _streaming_reply_requested():
            if client_request_id:
                messages[-1]["client_request_id"] = client_request_id
            await _save(_session_messages_to_save(messages))
            final_saved = True
            return await _ensure_text_reply(response, messages)
        if client_request_id:
            messages[-1]["client_request_id"] = client_request_id
        await _save(_session_messages_to_save(messages))
        final_saved = True
        return await _ensure_text_reply(response, messages)
    finally:
        # Persist unconditionally (except the pause exit, whose durable state
        # was already written by _upsert_pending_question). The interrupt path
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
) -> str:
    """Run one main-agent turn against an immutable capability snapshot."""
    from cyrene.tooling.gateway import (
        activate_catalog_snapshot,
        reset_catalog_snapshot,
    )

    snapshot_token = activate_catalog_snapshot("main")
    try:
        model_lease_token = activate_run_model_lease()
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
            )
        finally:
            reset_run_model_lease(model_lease_token)
    finally:
        reset_catalog_snapshot(snapshot_token)
