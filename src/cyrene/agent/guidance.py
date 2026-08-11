"""Guidance processing: inbox management, subagent coordination, result synthesis.

Depends on ``state``, ``session``, ``round`` (or merged session.py) and
``message``.  Inline-imports ``coordinator._run_chat_agent`` to break the
module-level cycle.
"""

import asyncio
import json
import logging
from typing import Any

import httpx

import cyrene.agent.replies as _reply_helpers
from cyrene.agent.context import (
    AWAITING_USER_SENTINEL as _AWAITING_USER_SENTINEL,
    MAIN_AGENT_ID as _MAIN_INBOX_AGENT_ID,
    allow_all_destructive_operations_for_run,
    bind_run_context,
    current_session_id,
    default_agent_lock,
    default_session_state_lock,
    grant_destructive_operation,
    grant_external_upload,
    grant_permission_elevation,
    grant_scoped_path_access,
    grant_temporary_full_access,
    permission_elevation_fingerprint,
    publish_runtime_event as _publish_runtime_event,
    session_interrupt_event,
)
from cyrene.observability import debug
from cyrene.agent.message import (
    _ensure_message_identity,
    _insert_intermediate_user_reply,
)
from cyrene.agent.model_service import (
    call_agent_model as _call_llm,
    stream_agent_model as _call_llm_stream,
    streaming_reply_requested as _streaming_reply_requested,
)
from cyrene.agent.round import get_live_rounds, _main_inbox_pending_by_round
from cyrene.agent.session import (
    _append_session_message,
    _clear_pending_question,
    _guidance_persist_context_after_ack,
    _guidance_round_context,
    _load_session_state,
    _pending_question_resume_context,
    _pending_question_is_permission_elevation,
    _restore_pending_question,
    _write_session_messages_locked,
    get_session_labels,
)
from cyrene.model_runtime.errors import format_httpx_error  # noqa: F401
from cyrene.model_runtime.messages import assistant_text

logger = logging.getLogger(__name__)


def _original_round_user_prompt(context: dict[str, Any]) -> str:
    """Return the original public user request for a resumed round."""
    for message in context.get("round_history") or []:
        if str(message.get("role") or "") != "user":
            continue
        content = str(message.get("public_content") or message.get("content") or "").strip()
        if content:
            return content
    return ""


def _clarification_authorization_request(
    context: dict[str, Any],
    answer_text: str,
) -> str:
    """Build the trusted user-authored request for a clarification resume.

    A clarification answer refines the original request; it does not replace
    it.  Keep both user-authored strings available to exact Cyrene-operation
    review without incorporating the assistant's question (which is context,
    not authorization evidence).
    """
    user_parts: list[str] = []
    for message in context.get("round_history") or []:
        if str(message.get("role") or "") != "user":
            continue
        content = str(
            message.get("public_content") or message.get("content") or ""
        ).strip()
        if content and content not in user_parts:
            user_parts.append(content)
    answer = str(answer_text or "").strip()
    if answer and answer not in user_parts:
        user_parts.append(answer)
    if not user_parts:
        return answer
    return "\n\n".join(
        part if index == 0 else f"用户随后澄清：{part}"
        for index, part in enumerate(user_parts)
    )

# Historical private helpers remained importable from this module before the
# reply synthesizer was extracted.  Keep the exact function objects so direct
# calls and monkeypatch identity continue to work.
_looks_chinese = _reply_helpers._looks_chinese
_record_final_reply_usage = _reply_helpers._record_final_reply_usage
_VISIBLE_DSML_TOOL_BLOCK_RE = _reply_helpers._VISIBLE_DSML_TOOL_BLOCK_RE
_VISIBLE_DSML_TOOL_MARKUP_RE = _reply_helpers._VISIBLE_DSML_TOOL_MARKUP_RE

# ---------------------------------------------------------------------------
# Guidance ack / error text
# ---------------------------------------------------------------------------

async def _publish_round_guidance_update(target_round_id: str) -> None:
    live = next((item for item in get_live_rounds() if item.get("id") == target_round_id), None)
    await debug.publish_event({
        "type": "round_guidance_update",
        "target_round_id": target_round_id,
        "pending_guidance": int(live.get("pendingGuidance", 0) if live else 0),
        "status": live.get("status", "") if live else "",
        "title": live.get("title", "") if live else "",
    })


def _guidance_error_text(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        reason = "the upstream model timed out"
    elif isinstance(exc, httpx.HTTPError):
        reason = "the upstream model request failed"
    else:
        reason = "an internal error occurred while applying the guidance"
    return f"Guidance could not be applied because {reason}."


def _guidance_ack_text() -> str:
    return "已接受引导。我会按这条新要求调整当前这一轮的工作，并在完成后给你更新。"


async def _generate_guidance_ack(
    guidance: str,
    *,
    round_title: str = "",
    round_history: list[dict[str, Any]] | None = None,
) -> str:
    latest_assistant = next(
        (
            str(msg.get("content") or "").strip()
            for msg in reversed(round_history or [])
            if str(msg.get("role") or "").strip() == "assistant" and str(msg.get("content") or "").strip()
        ),
        "",
    )
    prompt_messages = [
        {
            "role": "system",
            "content": (
                "You are acknowledging new user guidance for an ongoing task.\n"
                "Reply with exactly one short sentence.\n"
                "Do not answer the task itself.\n"
                "Do not mention queues, rounds, internal state, or implementation details.\n"
                "Say that you understood the guidance and will adjust the current work accordingly.\n"
                "Match the user's language."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Round title: {round_title or '—'}\n"
                f"Latest assistant reply: {latest_assistant or '—'}\n"
                f"New user guidance: {guidance}"
            ),
        },
    ]
    try:
        response = await _call_llm(prompt_messages, tools=None, max_tokens=240, secondary=True)
        ack_text = assistant_text(response).strip()
        return ack_text or _guidance_ack_text()
    except Exception:
        logger.warning("Failed to generate guidance acknowledgement via LLM", exc_info=True)
        return _guidance_ack_text()


# ---------------------------------------------------------------------------
# Fan-out / wait helpers
# ---------------------------------------------------------------------------

async def _insert_guidance_reply(
    target_round_id: str,
    guidance_id: str,
    content: str,
    round_title: str = "",
    client_request_id: str = "",
    subagent_flow_snapshot: dict[str, Any] | None = None,
) -> None:
    from datetime import datetime, timezone

    assistant_entry: dict[str, Any] = {
        "role": "assistant",
        "content": content,
        "round_id": target_round_id,
        "in_reply_to_guidance_id": guidance_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "intermediate_reply": True,
    }
    if round_title:
        assistant_entry["round_title"] = round_title
    if client_request_id:
        assistant_entry["client_request_id"] = client_request_id
    if subagent_flow_snapshot:
        assistant_entry["subagent_flow_snapshot"] = subagent_flow_snapshot

    async with default_session_state_lock():
        state = _load_session_state()
        existing = state.get("messages", [])
        full_messages = list(existing) if isinstance(existing, list) else []
        _ensure_message_identity([assistant_entry])
        replacement_index = next(
            (
                idx
                for idx, msg in enumerate(full_messages)
                if str(msg.get("in_reply_to_guidance_id", "")).strip() == guidance_id
            ),
            -1,
        )
        if replacement_index >= 0:
            full_messages[replacement_index] = assistant_entry
        else:
            ack_index = next(
                (
                    idx
                    for idx, msg in enumerate(full_messages)
                    if str(msg.get("guidance_ack_for_guidance_id", "")).strip() == guidance_id
                ),
                -1,
            )
            insert_at = ack_index if ack_index >= 0 else next(
                (
                    idx
                    for idx, msg in enumerate(full_messages)
                    if str(msg.get("queued_guidance_id", "")).strip() == guidance_id
                ),
                len(full_messages) - 1,
            )
            full_messages.insert(max(0, insert_at + 1), assistant_entry)
        await _write_session_messages_locked(state, full_messages)
    await _publish_runtime_event({
        "type": "chat_message",
        "round_id": target_round_id,
        "client_request_id": client_request_id,
        "guidance_id": guidance_id,
        "message": {
            "id": assistant_entry.get("message_id", ""),
            "role": "assistant",
            "content": assistant_entry["content"],
            "createdAt": assistant_entry["created_at"],
            "intermediate": True,
        },
    })


async def _insert_guidance_ack(
    target_round_id: str,
    guidance_id: str,
    content: str,
    round_title: str = "",
    client_request_id: str = "",
) -> None:
    from datetime import datetime, timezone

    assistant_entry: dict[str, Any] = {
        "role": "assistant",
        "content": content,
        "round_id": target_round_id,
        "guidance_ack_for_guidance_id": guidance_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "intermediate_reply": True,
    }
    if round_title:
        assistant_entry["round_title"] = round_title
    async with default_session_state_lock():
        state = _load_session_state()
        existing = state.get("messages", [])
        full_messages = list(existing) if isinstance(existing, list) else []
        _ensure_message_identity([assistant_entry])
        replacement_index = next(
            (
                idx
                for idx, msg in enumerate(full_messages)
                if str(msg.get("guidance_ack_for_guidance_id", "")).strip() == guidance_id
            ),
            -1,
        )
        if replacement_index >= 0:
            full_messages[replacement_index] = assistant_entry
        else:
            insert_at = next(
                (
                    idx
                    for idx, msg in enumerate(full_messages)
                    if str(msg.get("queued_guidance_id", "")).strip() == guidance_id
                ),
                len(full_messages) - 1,
            )
            full_messages.insert(max(0, insert_at + 1), assistant_entry)
        await _write_session_messages_locked(state, full_messages)
    await _publish_runtime_event({
        "type": "guidance_acknowledged",
        "round_id": target_round_id,
        "client_request_id": client_request_id,
        "guidance_id": guidance_id,
        "ack_text": assistant_entry["content"],
        "message": {
            "id": assistant_entry.get("message_id", ""),
            "role": "assistant",
            "content": assistant_entry["content"],
            "createdAt": assistant_entry["created_at"],
            "intermediate": True,
        },
    })


async def _fan_out_guidance_to_subagents(target_round_id: str, content: str, bot: Any, chat_id: int, db_path: str) -> list[str]:
    from cyrene.runtime.inbox import send_message as _send_inbox
    from cyrene.subagent import (
        get_raw_messages as _sub_raw_msgs,
        get_snapshot as _sub_snapshot,
        reactivate as _sub_reactivate,
        run_subagent,
        spawn_subagent_task,
    )

    guidance_text = (
        "Main agent received new user guidance for this round.\n"
        "Adjust your work accordingly and revise your result if needed.\n\n"
        f"User guidance:\n{content}"
    )
    snapshot = await _sub_snapshot(round_id=target_round_id)
    if not snapshot:
        return []

    sent: list[str] = []
    for agent_id in snapshot:
        await _send_inbox(_MAIN_INBOX_AGENT_ID, agent_id, "guidance", guidance_text, round_id=target_round_id)
        sent.append(agent_id)

    for agent_id, info in snapshot.items():
        if info.get("status") not in ("done", "timeout", "incomplete"):
            continue
        if await _sub_reactivate(agent_id):
            raw_messages = await _sub_raw_msgs(agent_id)
            spawn_subagent_task(
                run_subagent(agent_id, str(info.get("task") or ""), bot, chat_id, db_path, resume_messages=raw_messages),
                agent_id,
            )
    return sent


async def _wait_for_subagent_round(round_id: str, bot: Any, chat_id: int, db_path: str) -> tuple[bool, str]:
    from cyrene.runtime.inbox import get_unread_count as _inbox_unread
    from cyrene.subagent import (
        collect_results as _sub_collect,
        get_raw_messages as _sub_raw_msgs,
        get_snapshot as _sub_snapshot,
        reactivate as _sub_reactivate,
        run_subagent,
        spawn_subagent_task,
    )

    interrupt_event = session_interrupt_event()
    interrupt_event.clear()
    interrupted = False
    quiet_ticks = 0
    for _ in range(120):
        try:
            await asyncio.wait_for(interrupt_event.wait(), timeout=5)
            interrupt_event.clear()
            interrupted = True
            break
        except asyncio.TimeoutError:
            pass

        snapshot = await _sub_snapshot(round_id=round_id)
        if not snapshot:
            break

        resurrected = False
        for agent_id, info in snapshot.items():
            if info.get("status") not in ("done", "timeout", "incomplete") or _inbox_unread(agent_id) == 0:
                continue
            if await _sub_reactivate(agent_id):
                raw_messages = await _sub_raw_msgs(agent_id)
                spawn_subagent_task(
                    run_subagent(agent_id, str(info.get("task") or ""), bot, chat_id, db_path, resume_messages=raw_messages),
                    agent_id,
                )
                resurrected = True

        snapshot = await _sub_snapshot(round_id=round_id)
        all_truly_done = all(
            info.get("status") in ("done", "timeout", "incomplete") and _inbox_unread(agent_id) == 0
            for agent_id, info in snapshot.items()
        )
        if all_truly_done and not resurrected:
            quiet_ticks += 1
            if quiet_ticks >= 2:
                break
        else:
            quiet_ticks = 0

    if interrupted:
        return True, ""

    return False, await _sub_collect(round_id=round_id)


# ---------------------------------------------------------------------------
# Synthesis and final replies
# ---------------------------------------------------------------------------

async def _synthesize_subagent_results(
    task: str,
    summary: str,
    round_title: str = "",
    guidance: str = "",
    round_history: list[dict[str, Any]] | None = None,
) -> str:
    context_lines: list[str] = []
    if round_history:
        for msg in round_history[-16:]:
            role = str(msg.get("role", "")).strip()
            if role == "system":
                continue
            content = str(msg.get("content", "")).strip()
            tool_calls = msg.get("tool_calls") or []
            if role == "user" and content:
                label = "User query" if not context_lines else "User"
                context_lines.append(f"[{label}]\n{content[:800]}")
            elif role == "assistant":
                if content:
                    context_lines.append(f"[Assistant reasoning]\n{content[:600]}")
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    args = fn.get("arguments", "{}")
                    try:
                        a = json.loads(args)
                        from cyrene.tooling import resolve_wire_call

                        resolution = resolve_wire_call(
                            str(name or ""),
                            a,
                            actor="main",
                        )
                        capability_id = resolution.capability_id
                        concrete_args = resolution.concrete_arguments
                    except Exception:
                        capability_id = str(name or "")
                        concrete_args = {}
                    if capability_id == "subagent.spawn":
                        context_lines.append(
                            f"[Spawned subagent: {concrete_args.get('agent_id', '?')}]\n"
                            f"Task: {str(concrete_args.get('task', ''))[:300]}"
                        )
                    elif capability_id == "subagent.send_message":
                        context_lines.append(
                            f"[Subagent msg: {concrete_args.get('from', '?')} -> "
                            f"{concrete_args.get('to', '?')}]"
                        )
    context_block = "\n\n".join(context_lines) if context_lines else "—"

    experts_block = summary.strip() or "(No subagent results.)"

    if len(experts_block) < 50:
        return experts_block

    prompt_messages = [
        {
            "role": "system",
            "content": (
                "You are presenting the final answer after subagents completed their tasks.\n\n"
                "Rules:\n"
                "1. First, present EACH subagent's original output in full — verbatim, under their own heading.\n"
                "   This is mandatory. Do not rewrite, truncate, or summarize their work.\n"
                "2. After all subagent outputs, you MAY add a brief synthesis section that connects"
                " or contrasts their perspectives.\n"
                "3. For creative work (poems, code, art descriptions): quote the original completely.\n"
                "4. For research or analysis: present each expert's findings in full, then synthesize.\n\n"
                "Output format:\n"
                "--- <subagent name> ---\n"
                "<their complete original output>\n"
                "...\n"
                "--- Synthesis ---\n"
                "<your synthesis, if needed>"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Task: {task}\n\n"
                f"Round context:\n{context_block}\n\n"
                f"Expert findings from subagents:\n{experts_block}\n\n"
                "Present the final answer following the rules above."
            ),
        },
    ]
    response = await (_call_llm_stream(prompt_messages, max_tokens=None) if _streaming_reply_requested() else _call_llm(prompt_messages, tools=None, max_tokens=None))
    llm_text = assistant_text(response).strip()
    return llm_text or experts_block


# Preserve the historical guidance-module exports while keeping one live
# implementation of final-reply behavior.
from cyrene.agent import replies as _reply_helpers  # noqa: E402

_default_final_reply_call = _call_llm
_default_final_reply_stream_call = _call_llm_stream
_default_streaming_reply_requested = _streaming_reply_requested
_contains_visible_dsml_tool_markup = (
    _reply_helpers._contains_visible_dsml_tool_markup
)
_delivery_fallback_text = _reply_helpers._delivery_fallback_text
_is_placeholder_reply = _reply_helpers._is_placeholder_reply
_strip_visible_dsml_tool_blocks = (
    _reply_helpers._strip_visible_dsml_tool_blocks
)
_tool_result_fallback_text = _reply_helpers._tool_result_fallback_text


def _final_reply_dependency(
    local_value: Any,
    original_value: Any,
    current_reply_value: Any,
) -> Any:
    """Prefer a historical guidance override, then the current replies seam."""
    return (
        local_value
        if local_value is not original_value
        else current_reply_value
    )


async def _validated_final_no_tool_reply(
    messages: list[dict],
    max_tokens: int | None = None,
) -> str:
    return await _reply_helpers._validated_final_no_tool_reply(
        messages,
        max_tokens=max_tokens,
        call_llm=_final_reply_dependency(
            _call_llm,
            _default_final_reply_call,
            _reply_helpers._call_llm,
        ),
        call_llm_stream=_final_reply_dependency(
            _call_llm_stream,
            _default_final_reply_stream_call,
            _reply_helpers._call_llm_stream,
        ),
        streaming_reply_requested=_final_reply_dependency(
            _streaming_reply_requested,
            _default_streaming_reply_requested,
            _reply_helpers._streaming_reply_requested,
        ),
    )


async def _final_user_reply_from_history(
    messages: list[dict],
    max_tokens: int | None = None,
) -> str:
    last_user_text = next(
        (
            str(message.get("content") or "").strip()
            for message in reversed(messages)
            if isinstance(message, dict)
            and str(message.get("role") or "") == "user"
            and str(message.get("content") or "").strip()
        ),
        "",
    )
    prompt_messages = [
        *messages,
        {
            "role": "user",
            "content": (
                (
                    "Now answer the user's request directly using the gathered "
                    "tool results.\n"
                    if last_user_text
                    else
                    "The user uploaded one or more attachments without extra "
                    "text. Summarize the attachment contents directly using "
                    "the gathered tool results.\n"
                )
                + "Do not call tools.\n"
                + "Do not reply with only 'Done'.\n"
                + "If the tools extracted file or attachment contents, quote "
                "or summarize those contents in your answer."
            ),
        },
    ]
    return await _validated_final_no_tool_reply(
        prompt_messages,
        max_tokens=max_tokens,
    )


async def _final_plain_reply_from_history(
    messages: list[dict],
    max_tokens: int | None = None,
) -> str:
    prompt_messages = [
        *messages,
        {
            "role": "user",
            "content": (
                "Answer the latest user message directly.\n"
                "Do not call tools.\n"
                "Do not reply with only 'Done'."
            ),
        },
    ]
    return await _validated_final_no_tool_reply(
        prompt_messages,
        max_tokens=max_tokens,
    )


async def _final_reply_from_history(
    messages: list[dict],
    max_tokens: int | None = None,
) -> str:
    text = await _validated_final_no_tool_reply(
        messages,
        max_tokens=max_tokens,
    )
    return text or "Done."


async def _final_reply_with_tools(
    messages: list[dict],
    tools: list,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Keep the historical guidance monkeypatch seam for streaming replies."""
    prompt_messages = [
        *messages,
        {
            "role": "user",
            "content": (
                "Write the final user-facing reply now, in the user's language. "
                "Do not reply with only 'Done', 'OK', or another placeholder. "
                "If files were delivered with send_file or send_wechat_file, "
                "briefly confirm the delivery and mention the file name or what "
                "was sent. Call another tool only if it is genuinely needed to "
                "satisfy the user's request."
            ),
        },
    ]
    # New callers patch ``agent.replies``; historical callers patched this
    # guidance module.  Honor both seams without mutating shared globals.
    stream_call = _final_reply_dependency(
        _call_llm_stream,
        _default_final_reply_stream_call,
        _reply_helpers._call_llm_stream,
    )
    response = await stream_call(
        prompt_messages,
        max_tokens=max_tokens,
        tools=tools,
    )
    _reply_helpers._record_final_reply_usage(response)
    return response


# ---------------------------------------------------------------------------
# Main inbox processing
# ---------------------------------------------------------------------------

async def _process_main_inbox_message(message: dict[str, Any], bot: Any, chat_id: int, db_path: str) -> str:
    from cyrene.agent.coordinator import _run_chat_agent
    from cyrene.subagent import clear as _sub_clear, get_snapshot as _sub_snapshot

    target_round_id = str(message.get("round_id", "")).strip()
    guidance_id = str(message.get("message_id", "")).strip()
    content = str(message.get("content") or "").strip()
    if not target_round_id or not guidance_id or not content:
        return ""

    context = _guidance_round_context(target_round_id, guidance_id)
    live_round = next((live for live in get_live_rounds() if live.get("id") == target_round_id), None)
    round_title = context["round_title"] or str((live_round or {}).get("title") or "").strip() or target_round_id
    ack_text = await _generate_guidance_ack(
        content,
        round_title=round_title,
        round_history=context["round_history"],
    )
    snapshot = await _sub_snapshot(round_id=target_round_id)
    await _insert_guidance_ack(
        target_round_id,
        guidance_id,
        ack_text,
        round_title=round_title,
        client_request_id=context["client_request_id"],
    )
    has_live_subagents = bool(
        live_round
        and (
            int(live_round.get("subagentCount", 0) or 0) > 0
            or int(live_round.get("runningSubagents", 0) or 0) > 0
        )
    )
    if has_live_subagents or (live_round is None and snapshot):
        await _publish_runtime_event({
            "type": "phase_transition",
            "round_id": target_round_id,
            "from": "guidance_queue",
            "to": "subagent_guidance",
            "detail": f"Main agent is applying guidance to {len(snapshot)} subagent(s).",
            "detail_key": "phase.applyingGuidanceToSubagents",
            "detail_params": {"count": len(snapshot)},
        })
        await _fan_out_guidance_to_subagents(target_round_id, content, bot, chat_id, db_path)
        interrupted, _summary = await _wait_for_subagent_round(target_round_id, bot, chat_id, db_path)
        if interrupted:
            reply = "[Sub-agents are still working in the background. The guidance was delivered and the round is continuing.]"
        else:
            from cyrene.subagent import run_summary_subagent as _run_summary_subagent
            from cyrene.subagent import build_flow_snapshot as _build_subagent_flow_snapshot

            parent_task = next(
                (
                    str(msg.get("content") or "").strip()
                    for msg in context["round_history"]
                    if str(msg.get("role") or "").strip() == "user" and str(msg.get("content") or "").strip()
                ),
                content,
            )
            reply = await _run_summary_subagent(
                round_id=target_round_id,
                parent_task=parent_task,
                guidance=content,
                round_history=context["round_history"],
            )
            flow_snapshot = await _build_subagent_flow_snapshot(target_round_id)
            await _sub_clear(round_id=target_round_id)
        await _insert_guidance_reply(
            target_round_id,
            guidance_id,
            reply,
            round_title=round_title,
            client_request_id=context["client_request_id"],
            subagent_flow_snapshot=flow_snapshot if not interrupted else None,
        )
        return reply

    guidance_system = (
        "This user message came from the main-agent inbox for an earlier round.\n"
        f"Target round id: {target_round_id}\n"
        f"Target round title: {round_title}\n"
        "Treat it as steering or a follow-up for that round. Continue the round instead of starting a fresh topic."
    )
    await _publish_runtime_event({
        "type": "phase_transition",
        "round_id": target_round_id,
        "from": "guidance_queue",
        "to": "guided_round_continuation",
        "detail": "Main agent is continuing the same round with the new guidance.",
        "detail_key": "phase.guidedRoundContinuation",
    })
    persist_context = _guidance_persist_context_after_ack(guidance_id)
    return await _run_chat_agent(
        content,
        bot,
        chat_id,
        db_path,
        ephemeral_system=guidance_system,
        forced_round_id=target_round_id,
        history_override=context["round_history"],
        persist_base_messages=persist_context["persist_base_messages"],
        persist_insert_at=persist_context["persist_insert_at"],
        client_request_id=context["client_request_id"],
        persist_user_message=False,
        assistant_message_meta={"in_reply_to_guidance_id": guidance_id},
    )


def _ensure_main_inbox_worker(bot: Any, chat_id: int, db_path: str) -> None:
    import cyrene.agent.state as _state
    _def_ctx = _state._ensure_session("")
    if _def_ctx.main_inbox_worker is None or _def_ctx.main_inbox_worker.done():
        _def_ctx.main_inbox_worker = asyncio.create_task(_drain_main_inbox(bot, chat_id, db_path))


async def queue_round_guidance(
    target_round_id: str,
    content: str,
    bot: Any,
    chat_id: int,
    db_path: str,
    client_request_id: str = "",
) -> dict[str, Any]:
    from cyrene.runtime.inbox import send_message as _send_inbox
    from datetime import datetime, timezone

    live = {item["id"]: item for item in get_live_rounds()}
    target = live.get(target_round_id)
    if target is None:
        raise ValueError(f"Round {target_round_id} is not live.")

    created_at = datetime.now(timezone.utc).isoformat()
    guidance_id = await _send_inbox("user", _MAIN_INBOX_AGENT_ID, "guidance", content, round_id=target_round_id)
    if not guidance_id:
        raise ValueError("Failed to send guidance to the main-agent inbox.")
    item = {
        "id": guidance_id,
        "target_round_id": target_round_id,
        "content": content,
        "created_at": created_at,
    }
    labels = get_session_labels(target_round_id)
    queued_user_entry: dict[str, Any] = {
        "role": "user",
        "content": content,
        "round_id": target_round_id,
        "queued_guidance_id": guidance_id,
    }
    if labels.get("round_title"):
        queued_user_entry["round_title"] = labels["round_title"]
    if client_request_id:
        queued_user_entry["client_request_id"] = client_request_id
    await _append_session_message(queued_user_entry)
    await _publish_round_guidance_update(target_round_id)
    _ensure_main_inbox_worker(bot, chat_id, db_path)
    return item


async def _drain_main_inbox(bot: Any, chat_id: int, db_path: str) -> None:
    from cyrene.runtime.memory.conversations import archive_exchange
    from cyrene.runtime.inbox import get_unread_messages, mark_read_count

    import cyrene.agent.state as _state
    try:
        while True:
            unread = [
                message
                for message in get_unread_messages(_MAIN_INBOX_AGENT_ID)
                if str(message.get("type", "")).strip() == "guidance"
            ]
            if not unread:
                break

            item = unread[0]
            target_round_id = str(item.get("round_id", "")).strip()
            guidance_id = str(item.get("message_id", "")).strip()
            response = ""
            try:
                await _publish_runtime_event({
                    "type": "phase_transition",
                    "round_id": target_round_id,
                    "from": "queued_guidance",
                    "to": "guidance_execution",
                    "detail": "Main agent is now applying the queued guidance.",
                    "detail_key": "phase.guidanceExecution",
                })
                async with default_agent_lock():
                    session_interrupt_event().clear()
                    response = await _process_main_inbox_message(item, bot, chat_id, db_path)
            except Exception as exc:
                logger.exception("Failed to process main inbox guidance for %s", target_round_id or "<unknown>")
                if target_round_id and guidance_id:
                    context = _guidance_round_context(target_round_id, guidance_id)
                    round_title = context.get("round_title") or next(
                        (live["title"] for live in get_live_rounds() if live.get("id") == target_round_id),
                        target_round_id,
                    )
                    response = _guidance_error_text(exc)
                    await _insert_guidance_reply(
                        target_round_id,
                        guidance_id,
                        response,
                        round_title=round_title,
                        client_request_id=str(context.get("client_request_id") or ""),
                    )
            finally:
                await mark_read_count(_MAIN_INBOX_AGENT_ID, 1)
                if target_round_id:
                    await _publish_round_guidance_update(target_round_id)
            if response and response != _AWAITING_USER_SENTINEL:
                labels = get_session_labels(target_round_id)
                await archive_exchange(
                    str(item.get("content") or ""),
                    response,
                    chat_id,
                    session_title=labels.get("session_title", ""),
                    round_title=labels.get("round_title", ""),
                    round_id=labels.get("round_id", ""),
                    archive_session_id=labels.get("archive_session_id", ""),
                )
    except Exception:
        logger.exception("Failed to drain main inbox")
    finally:
        _state._ensure_session("").main_inbox_worker = None
        if get_live_rounds() and _main_inbox_pending_by_round():
            _ensure_main_inbox_worker(bot, chat_id, db_path)


# ---------------------------------------------------------------------------
# answer_pending_question (moved here from coordinator to keep it close to guidance)
# ---------------------------------------------------------------------------

async def answer_pending_question(
    question_id: str,
    answer_text: str,
    bot: Any,
    chat_id: int,
    db_path: str,
    client_request_id: str = "",
    permission_mode: str = "default",
) -> str:
    # ``permission_mode`` lets a caller keep a non-default permission mode across
    # the resume (e.g. a Workbench goal loop running in "auto" / "full_access").
    # It only applies to the normal clarification-resume path below; the
    # permission-elevation / plan-confirmation handlers keep their own modes.
    from cyrene.agent.coordinator import _run_chat_agent

    context = _pending_question_resume_context(question_id)
    pending = context.get("pending_question", {})
    if not pending:
        raise ValueError("Pending question not found.")
    client_request_id = str(
        client_request_id or context.get("client_request_id") or ""
    ).strip()

    content = str(answer_text or "").strip()
    if not content:
        raise ValueError("Answer cannot be empty.")

    round_id = str(context.get("round_id", "")).strip()
    if not round_id:
        raise ValueError("Pending question has no round context.")

    cleared = await _clear_pending_question(str(pending.get("id", "")).strip())
    if not cleared:
        raise ValueError("Pending question not found.")

    pending_meta = cleared.get("meta")
    if isinstance(pending_meta, dict) and str(pending_meta.get("kind", "")).strip() == "claude_code_prompt_confirmation":
        try:
            return await _handle_claude_code_prompt_answer(
                round_id=round_id,
                pending=cleared,
                answer_text=content,
                client_request_id=client_request_id,
            )
        except Exception:
            await _restore_pending_question(pending)
            raise
    if isinstance(pending_meta, dict) and _pending_question_is_permission_elevation(cleared):
        try:
            return await _handle_permission_elevation_answer(
                round_id=round_id,
                pending=cleared,
                answer_text=content,
                client_request_id=client_request_id,
                context=context,
                permission_mode=permission_mode,
            )
        except Exception:
            await _restore_pending_question(pending)
            raise

    if isinstance(pending_meta, dict) and str(pending_meta.get("kind", "")).strip() == "plan_confirmation":
        try:
            return await _handle_plan_confirmation_answer(
                round_id=round_id,
                pending=cleared,
                answer_text=content,
                client_request_id=client_request_id,
                context=context,
                permission_mode=permission_mode,
            )
        except Exception:
            await _restore_pending_question(pending)
            raise

    if isinstance(pending_meta, dict) and str(pending_meta.get("kind", "")).strip() == "browser_takeover":
        # The user finished logging in via the native window. Return the browser
        # session to headless (same profile → now authenticated), then fall through
        # to resume the round normally with the user's confirmation.
        try:
            from cyrene.browser import end_browser_takeover
            await end_browser_takeover(str(pending_meta.get("url", "") or ""))
        except Exception:
            logger.warning("browser end_takeover failed during resume", exc_info=True)

    answer_system = (
        "This user message answers your earlier clarification question for the same round.\n"
        f"Target round id: {round_id}\n"
        f"Original clarification question: {str(pending.get('text', '')).strip()}\n"
        "Treat the new user message as the answer and continue the same round."
    )
    import cyrene.agent.state as _state

    delegation_receipts_token = _state._explicit_delegation_receipts.set(set())
    delegation_batches_token = _state._explicit_delegation_batches.set({})
    authorization_binding = bind_run_context(
        user_request_text=_clarification_authorization_request(context, content),
    )
    try:
        return await _run_chat_agent(
            content,
            bot,
            chat_id,
            db_path,
            ephemeral_system=answer_system,
            forced_round_id=round_id,
            history_override=context.get("round_history") or [],
            persist_base_messages=context.get("persist_base_messages") or [],
            persist_insert_at=context.get("persist_insert_at"),
            client_request_id=client_request_id,
            persist_user_message=True,
            command=str(context.get("command", "") or "").strip(),
            permission_mode=permission_mode,
            public_prompt=_original_round_user_prompt(context),
        )
    except Exception:
        await _restore_pending_question(pending)
        raise
    finally:
        authorization_binding.reset()
        _state._explicit_delegation_batches.reset(delegation_batches_token)
        _state._explicit_delegation_receipts.reset(delegation_receipts_token)


def _is_affirmative_answer(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "同意并发送", "同意", "发送", "确认", "确认发送", "好", "好的", "可以", "行", "yes", "y", "ok", "okay", "send", "confirm",
    }


def _is_negative_answer(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "取消", "不用", "不发", "停止", "算了", "cancel", "no", "n", "stop",
    }


async def _handle_claude_code_prompt_answer(
    round_id: str,
    pending: dict[str, Any],
    answer_text: str,
    client_request_id: str = "",
) -> str:
    from cyrene.tooling.backends.claude_code_bridge import send_prompt_to_cc
    from cyrene.agent.prompts import _contains_cjk

    meta = pending.get("meta", {})
    optimized_prompt = str(meta.get("optimized_prompt") or "").strip()
    task = str(meta.get("task") or "").strip()
    user_answer = str(answer_text or "").strip()
    chinese = _contains_cjk(task or optimized_prompt or user_answer)

    user_entry: dict[str, Any] = {
        "role": "user",
        "content": user_answer,
        "round_id": round_id,
    }
    if client_request_id:
        user_entry["client_request_id"] = client_request_id
    await _append_session_message(user_entry)

    if _is_negative_answer(user_answer):
        reply = "已取消，Claude Code 没有收到这条提示词。" if chinese else "Cancelled. The prompt was not sent to Claude Code."
        await _insert_intermediate_user_reply(reply, round_id=round_id, client_request_id=client_request_id)
        return reply

    prompt_to_send = optimized_prompt if _is_affirmative_answer(user_answer) else user_answer
    if not prompt_to_send:
        reply = "没有可发送的提示词。" if chinese else "There is no prompt to send."
        await _insert_intermediate_user_reply(reply, round_id=round_id, client_request_id=client_request_id)
        return reply

    result = send_prompt_to_cc(prompt_to_send)
    if not result.get("ok"):
        reason = str(result.get("reason") or "unknown error").strip()
        reply = (
            f"没有成功发送到 Claude Code：{reason}"
            if chinese else
            f"Failed to send the prompt to Claude Code: {reason}"
        )
        await _insert_intermediate_user_reply(reply, round_id=round_id, client_request_id=client_request_id)
        return reply

    reply = (
        "已把提示词输入到 Claude Code，任务已经开始运行。"
        if chinese else
        "I sent the prompt to Claude Code and it is now running."
    )
    await _insert_intermediate_user_reply(reply, round_id=round_id, client_request_id=client_request_id)
    await _publish_runtime_event({
        "type": "chat_message",
        "client_request_id": client_request_id,
        "round_id": round_id,
    })
    return reply


async def _handle_write_permission_answer(
    *,
    round_id: str,
    pending: dict[str, Any],
    answer_text: str,
    client_request_id: str,
    context: dict[str, Any],
) -> str:
    return await _handle_permission_elevation_answer(
        round_id=round_id,
        pending=pending,
        answer_text=answer_text,
        client_request_id=client_request_id,
        context=context,
    )


def _permission_answer_granted(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    if normalized in {
        "拒绝", "保持仅限 workspace", "拒绝，保持 workspace_only", "workspace_only",
        "cancel", "no", "n", "stop",
    }:
        return False
    return normalized in {
        "同意一次", "仅这次允许", "allow once", "仅此次", "这次", "once",
        "始终允许", "always allow", "always", "永久允许", "allow",
        "在本次会话同意", "本次会话内总是允许", "本轮总是允许", "always allow this session",
        "允许这次", "允许这次读取", "允许这次上传", "允许执行", "允许执行这一次",
        "允许调用这一次", "允许删除", "仅此任务允许 full_access",
        "同意", "确认", "好", "好的", "可以", "行", "yes", "y", "ok", "okay",
        "allow_once",
    }


async def _handle_permission_elevation_answer(
    *,
    round_id: str,
    pending: dict[str, Any],
    answer_text: str,
    client_request_id: str,
    context: dict[str, Any],
    permission_mode: str = "default",
) -> str:
    from cyrene.agent.coordinator import _run_chat_agent
    from cyrene.runtime.settings_store import set_write_permission_mode

    normalized = str(answer_text or "").strip().lower()
    meta = pending.get("meta") if isinstance(pending.get("meta"), dict) else {}
    permission_kind = str(meta.get("kind", "")).strip()
    tool_name = str(meta.get("tool_name", "") or "").strip()
    operation = str(meta.get("operation", "") or "").strip()
    path_hint = str(meta.get("path_hint", "") or "").strip()
    reason = str(meta.get("reason", "") or "").strip()
    permission_fingerprint = str(meta.get("fingerprint", "") or "").strip()
    if not permission_fingerprint:
        permission_fingerprint = permission_elevation_fingerprint(
            tool_name=tool_name,
            permission_kind=permission_kind,
            path_hint=path_hint,
            operation=operation,
            reason=reason,
        )

    granted = _permission_answer_granted(answer_text)
    allow_for_session = normalized in {
        "在本次会话同意",
        "本次会话内总是允许",
        "本轮总是允许",
        "always allow this session",
    }
    if permission_kind == "write_permission_request":
        # New prompts offer session-scoped, one-shot, or denial. Keep
        # recognizing legacy permanent-grant answers so an already-open prompt
        # from an older client can still resume.
        if allow_for_session:
            grant_temporary_full_access()
            system = (
                "The user granted elevated write/delete permission for the rest of this session. "
                "Retry the blocked action if it is still required."
            )
        elif granted and normalized not in {"始终允许", "always allow", "always", "永久允许", "allow"}:
            grant_permission_elevation(permission_fingerprint)
            system = (
                "The user granted this exact write/delete permission request once. "
                "Retry the blocked action if it is still required."
            )
        elif normalized in {"始终允许", "always allow", "always", "永久允许", "allow"}:
            set_write_permission_mode("full_access")
            system = (
                "The user granted permanent elevated write/delete permission. "
                "Retry the blocked action if it is still required."
            )
        else:
            set_write_permission_mode("workspace_only")
            system = (
                "The user denied elevated write/delete permission. "
                "Stay within the workspace and choose a safer alternative."
            )
    elif permission_kind == "read_elevation":
        if allow_for_session:
            grant_temporary_full_access()
            system = (
                "The user granted elevated read permission for the rest of this session. "
                "Retry the blocked read action if it is still required."
            )
        elif granted:
            grant_permission_elevation(permission_fingerprint)
            system = (
                "The user granted this exact outside-workspace read once. "
                "Retry the blocked read action if it is still required."
            )
        else:
            system = (
                "The user denied read access outside the workspace. "
                "Do not retry; stay within the workspace and choose a safe alternative."
            )
    elif permission_kind == "destructive_confirmation":
        fingerprint = str(meta.get("fingerprint", "") or "").strip()
        await _publish_runtime_event({
            "type": "destructive_confirmation",
            "decision": "approved" if granted else "denied",
            "tool_name": tool_name,
            "operation": operation,
            "destructive_kind": str(meta.get("destructive_kind", "") or "").strip(),
            "risk_level": str(meta.get("risk_level", "") or "").strip(),
            "path_hint": path_hint,
            "fingerprint": fingerprint,
        })
        if granted:
            if bool(meta.get("grant_read_path")) and path_hint:
                grant_scoped_path_access("read", path_hint)
            if allow_for_session:
                allow_all_destructive_operations_for_run()
            else:
                grant_destructive_operation(fingerprint)
            system = (
                "The user confirmed the destructive/irreversible operation. "
                "Retry the blocked action if it is still required."
            )
        else:
            system = (
                "The user denied the destructive/irreversible operation. "
                "Treat the operation as refused, do not retry it, and choose a safer alternative."
            )
    elif permission_kind == "external_upload_confirmation":
        fingerprint = str(meta.get("fingerprint", "") or "").strip()
        safe_target = meta.get("target") if isinstance(meta.get("target"), dict) else {}
        safe_files = meta.get("files") if isinstance(meta.get("files"), list) else []
        await _publish_runtime_event({
            "type": "external_upload_confirmation",
            "decision": "approved" if granted else "denied",
            "tool_name": "browser_upload_files",
            "fingerprint": fingerprint,
            "target": safe_target,
            "files": safe_files,
        })
        if granted:
            grant_external_upload(fingerprint)
            system = (
                "The user approved exactly one external browser file upload bound to the displayed "
                "site, input target, and file hashes. Retry browser_upload_files with the same arguments."
            )
        else:
            system = (
                "The user denied the external browser file upload. Do not retry it or choose another "
                "file or destination unless the user explicitly asks."
            )
    elif permission_kind in {"self_configuration_confirmation", "host_lifecycle_confirmation"}:
        await _publish_runtime_event({
            "type": permission_kind,
            "decision": "approved" if granted else "denied",
            "tool_name": tool_name,
            "operation": operation,
            "fingerprint": permission_fingerprint,
        })
        if granted:
            grant_permission_elevation(permission_fingerprint)
            system = (
                "The user approved exactly this Cyrene self-management operation once. "
                "Retry it with identical arguments if it is still required."
            )
        else:
            system = (
                "The user denied this Cyrene self-management operation. "
                "Do not retry it or change the arguments to evade the decision."
            )
    elif allow_for_session:
        grant_temporary_full_access()
        system = (
            "The user granted elevated permission for the rest of this session. "
            "Retry the blocked action if it is still required."
        )
    elif granted:
        grant_permission_elevation(permission_fingerprint)
        system = (
            "The user granted this exact internal permission request once. "
            "Retry the blocked action if it is still required."
        )
    else:
        system = (
            "The user denied the internal permission/confirmation request for this round. "
            "Do not retry the blocked action; stay within the current safety constraints and choose a safer alternative."
        )
    details = []
    if permission_kind:
        details.append(f"Permission kind: {permission_kind}")
    if tool_name:
        details.append(f"Tool: {tool_name}")
    if operation:
        details.append(f"Operation: {operation}")
    if path_hint:
        details.append(f"Target/path hint: {path_hint}")
    if reason:
        details.append(f"Reason/request detail: {reason}")
    if details:
        system += "\n" + "\n".join(details)

    await _publish_runtime_event({
        "type": "permission_decision",
        "source": "user",
        "approved": granted,
        "tool_name": tool_name,
        "operation": operation,
        "permission_kind": permission_kind,
        "path_hint": path_hint,
        "fingerprint": permission_fingerprint,
        "rationale": "User approved the displayed request." if granted else "User denied the displayed request.",
        "round_id": round_id,
    })

    return await _run_chat_agent(
        "[Internal permission decision received. Continue the same round using the system instruction above.]",
        None,
        0,
        "",
        ephemeral_system=system,
        forced_round_id=round_id,
        history_override=context.get("round_history") or [],
        persist_base_messages=context.get("persist_base_messages") or [],
        persist_insert_at=context.get("persist_insert_at"),
        client_request_id=client_request_id,
        persist_user_message=False,
        command=str(context.get("command", "") or "").strip(),
        permission_mode=permission_mode,
        public_prompt=_original_round_user_prompt(context),
    )


async def _handle_plan_confirmation_answer(
    *,
    round_id: str,
    pending: dict[str, Any],
    answer_text: str,
    client_request_id: str,
    context: dict[str, Any],
    permission_mode: str = "default",
) -> str:
    """处理「计划模式」确认回答：同意并开始 / 拒绝 / 修改。"""
    from cyrene.agent.coordinator import _run_chat_agent
    from cyrene.agent.planning import _plan_to_text

    meta = pending.get("meta") if isinstance(pending.get("meta"), dict) else {}
    plan = meta.get("plan") if isinstance(meta.get("plan"), dict) else {}
    user_message = str(meta.get("user_message") or "").strip()
    raw = str(answer_text or "").strip()
    normalized = raw.lower()

    approve = raw in {"同意并开始", "同意并开始执行", "同意并执行", "同意", "开始"} or normalized in {"approve", "start", "yes", "ok", "okay", "go"}
    reject = raw in {"拒绝", "取消", "算了", "不用了"} or normalized in {"reject", "cancel", "no", "stop"}
    resume_mode = normalized if normalized in {"default", "auto", "full_access"} else str(permission_mode or "default").strip().lower()
    if resume_mode not in {"default", "auto", "full_access"}:
        resume_mode = "default"

    if approve:
        try:
            from cyrene.workbench.chat import activate_chat_plan

            plan = activate_chat_plan(current_session_id(), plan)
        except Exception:
            logger.warning("Failed to activate Workbench chat plan", exc_info=True)
        await _publish_runtime_event({"type": "plan", "status": "accepted", "plan": plan, "round_id": round_id})
        exec_system = (
            "用户已同意以下计划，请严格按计划执行。当前为默认权限模式：碰到 workspace 之外或写/删操作时，"
            "再按需向用户申请提权。执行每个步骤前必须调用 update_plan_progress 将该步骤设为 in_progress；"
            "完成后必须再次调用它设为 completed（失败则设为 failed），然后才能进入下一步。"
            "完成后用一段话总结结果。\n\n" + _plan_to_text(plan)
        )
        return await _run_chat_agent(
            user_message or "[按已同意的计划执行]",
            None, 0, "",
            ephemeral_system=exec_system,
            forced_round_id=round_id,
            history_override=context.get("round_history") or [],
            persist_base_messages=context.get("persist_base_messages") or [],
            persist_insert_at=context.get("persist_insert_at"),
            client_request_id=client_request_id,
            persist_user_message=False,
            command=str(context.get("command", "") or "").strip(),
            permission_mode=resume_mode,
        )

    if reject:
        try:
            from cyrene.workbench.chat import reject_chat_plan

            plan = reject_chat_plan(current_session_id(), plan)
        except Exception:
            logger.warning("Failed to reject Workbench chat plan", exc_info=True)
        await _publish_runtime_event({"type": "plan", "status": "rejected", "plan": plan, "round_id": round_id})
        reject_system = (
            "用户拒绝了刚才的计划，不要执行任何操作。用一句话礼貌确认已取消，"
            "并邀请用户提出新的方向或调整后的需求。"
        )
        return await _run_chat_agent(
            "[用户拒绝了计划]",
            None, 0, "",
            ephemeral_system=reject_system,
            forced_round_id=round_id,
            history_override=context.get("round_history") or [],
            persist_base_messages=context.get("persist_base_messages") or [],
            persist_insert_at=context.get("persist_insert_at"),
            client_request_id=client_request_id,
            persist_user_message=False,
            command=str(context.get("command", "") or "").strip(),
            permission_mode="default",
        )

    # 其他（含「修改」或任意自定义意见）→ 带着修改意见重新规划
    return await _run_chat_agent(
        user_message or raw,
        None, 0, "",
        forced_round_id=round_id,
        history_override=context.get("round_history") or [],
        persist_base_messages=context.get("persist_base_messages") or [],
        persist_insert_at=context.get("persist_insert_at"),
        client_request_id=client_request_id,
        persist_user_message=True,
        command=str(context.get("command", "") or "").strip(),
        permission_mode="plan",
        plan_modification=raw,
    )
