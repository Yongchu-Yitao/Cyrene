"""Agent coordinator: entry points, chat agent orchestration, execution agent.

Depends on all other ``agent.*`` modules.  ``_run_chat_agent`` is the
main orchestration function that sets up context, assembles the system
prompt, and delegates to ``agent._run_main_agent`` (the core two-phase
loop).
"""

import asyncio
import importlib
import logging
import sys
from datetime import datetime
from typing import Any, Awaitable, Callable

import cyrene.agent.state as _state

from cyrene.agent.commands import DEEP_REFLECT_COMMAND_ID, parse_deep_reflect_command
from cyrene.agent.deep_reflection import create_deep_reflection_record
from cyrene.agent.message import (
    _apply_assistant_meta,
    _ensure_message_identity,
)
from cyrene.agent.prompts import (
    _CLAUDE_CODE_PROMPT,
    _DAILY_REVIEW_PROMPT,
    _DEEP_COMPARE_PROMPT,
    _DEEP_RESEARCH_PROMPT,
    _EXECUTION_SYSTEM_PROMPT,
    _HELP_ME_DECIDE_PROMPT,
    _LEARNING_PLAN_PROMPT,
    _MAIN_AGENT_PROMPT_TEMPLATE,
    _QUICK_ANSWER_PROMPT,
    _WORKBENCH_TASK_REPLY_PROMPT,
    WORKBENCH_RENDERER_TRIGGER_PROMPT,
    _WORKSPACE_SCOPE_BLOCK,
    _spawn_policy_prompt_block,
    conversation_identity_block,
    prompt_for_enabled_tool_packs,
    workspace_scope_block,
)
from cyrene.agent.session import (
    _expand_report_reference_history,
    _load_session_messages,
    _save_session_messages,
    _schedule_session_label_refresh,  # noqa: F401 - compatibility no-op
    get_session_labels,
)
from cyrene.agent.state import (
    _active_workspace_dir,
    response_capabilities as response_capabilities_context,
    _AWAITING_USER_SENTINEL,
    _call_llm,
    _caller_type,
    _current_client_request_id,
    _current_command,
    _current_round_id,
    _current_session_id,
    _ui_instance_id,
    _conversation_source,
    _deep_research_first_round,
    _deep_research_mode,
    _economy_mode,
    _ensure_session,
    _pending_intermediate_user_replies,
    _persist_base_messages,
    _persist_history_prefix_len,
    _persist_insert_at,
    _persist_merge_live_state,
    _publish_runtime_event,
    _ui_round_assistant_meta,
    _ui_round_hide_initial_detail,
    active_workspace_dir,
)
from cyrene.observability.context_trace import context_block
from cyrene.observability.trace import (
    bind_trace_context,
    current_trace_context,
    new_trace_id,
    trace_span,
)
from cyrene.config import PATTERN_DETECTION_INTERVAL
from cyrene.model_runtime.messages import (
    assistant_text,
    parse_tool_arguments,
    truncate,
)
from cyrene.memory import get_memory_context
from cyrene.runtime.memory.short_term import get_context
from cyrene.learning.skills import build_skill_prompt_block
from cyrene.runtime.settings_store import (
    get as _get_setting,
    get_spawn_policy,
    is_tool_pack_enabled,
)
from cyrene.tooling import execute_wire_tool, get_main_wire_tool_defs
from cyrene.tooling.mcp_content import build_mcp_observation_message
from cyrene.runtime.task_lifecycle import cancel_and_wait, track_task

logger = logging.getLogger(__name__)
_BACKGROUND_BEHAVIOR_TASKS: set[asyncio.Task[Any]] = set()
_DEFERRED_BEHAVIOR_TASK: asyncio.Task[Any] | None = None


def _track_background_behavior_task(task: asyncio.Task[Any]) -> None:
    track_task(
        task,
        _BACKGROUND_BEHAVIOR_TASKS,
        logger=logger,
        label="coordinator background task",
    )


async def shutdown_background_tasks() -> None:
    """Stop coordinator-owned jobs before runtime teardown."""
    global _DEFERRED_BEHAVIOR_TASK
    await cancel_and_wait(_BACKGROUND_BEHAVIOR_TASKS)
    _BACKGROUND_BEHAVIOR_TASKS.clear()
    _DEFERRED_BEHAVIOR_TASK = None


async def _kick_behavior_learning_processing() -> None:
    """Coalesce completed turns instead of starting one LLM job per turn."""
    global _DEFERRED_BEHAVIOR_TASK
    from cyrene.learning import engine as _behavior_learning

    # Normal desktop/server runtimes already own the configured 10-minute
    # behavior-learning job. Do not add a second offset timer after every turn.
    try:
        _scheduler_module = importlib.import_module("cyrene.runtime.scheduler")

        runtime_scheduler = getattr(_scheduler_module, "_scheduler", None)
        if runtime_scheduler is not None and runtime_scheduler.running:
            return
    except Exception:
        logger.debug("Cannot inspect runtime scheduler; will defer behavior learning", exc_info=True)

    loop = asyncio.get_running_loop()
    existing = _DEFERRED_BEHAVIOR_TASK
    if existing is not None and not existing.done():
        try:
            if existing.get_loop() is loop:
                return
            if not existing.get_loop().is_closed():
                existing.cancel()
        except RuntimeError:
            pass

    async def _run_after_quiet_period() -> None:
        await asyncio.sleep(max(1, int(PATTERN_DETECTION_INTERVAL)))
        await _behavior_learning.process_unprocessed_turns()

    task = asyncio.create_task(_run_after_quiet_period())
    _DEFERRED_BEHAVIOR_TASK = task
    _track_background_behavior_task(task)

    def _clear(completed: asyncio.Task[Any]) -> None:
        global _DEFERRED_BEHAVIOR_TASK
        if _DEFERRED_BEHAVIOR_TASK is completed:
            _DEFERRED_BEHAVIOR_TASK = None

    task.add_done_callback(_clear)


# ---------------------------------------------------------------------------
# Execution agent (internal, all tools)
# ---------------------------------------------------------------------------

async def _run_execution_agent(task: str, bot: Any, chat_id: int, db_path: str, notify_state: dict[str, bool] | None = None) -> str:
    # 使用默认 session 的锁防止与用户聊天并发执行
    default_ctx = _ensure_session("")
    if default_ctx.lock.locked():
        return ""
    async with default_ctx.lock:
        default_ctx.interrupt_event.clear()
        from cyrene.tooling.gateway import (
            activate_catalog_snapshot,
            reset_catalog_snapshot,
        )

        snapshot_token = activate_catalog_snapshot("main")
        try:
            return await _run_execution_agent_locked(
                task,
                bot,
                chat_id,
                db_path,
                notify_state,
            )
        finally:
            reset_catalog_snapshot(snapshot_token)


async def _run_execution_agent_locked(task: str, bot: Any, chat_id: int, db_path: str, notify_state: dict[str, bool] | None = None) -> str:
    _caller_type.set("execution_agent")
    wire_tool_defs = get_main_wire_tool_defs()
    enabled_wire_names = {
        str((tool_def.get("function") or {}).get("name") or "")
        for tool_def in wire_tool_defs
        if str((tool_def.get("function") or {}).get("name") or "").endswith(
            "_tools"
        )
    }
    messages = [
        {
            "role": "system",
            "content": prompt_for_enabled_tool_packs(
                _EXECUTION_SYSTEM_PROMPT
                + "\n\n"
                + _WORKSPACE_SCOPE_BLOCK,
                enabled_wire_names,
            ),
        },
        {"role": "user", "content": task},
    ]

    final_text = "Done."
    while True:
        response = await _call_llm(messages, tools=wire_tool_defs)

        assistant_entry: dict[str, Any] = {"role": "assistant"}
        if response.get("content"):
            assistant_entry["content"] = response["content"]
        else:
            assistant_entry["content"] = ""
        if response.get("tool_calls"):
            assistant_entry["tool_calls"] = response["tool_calls"]
        if response.get("reasoning_content"):
            assistant_entry["reasoning_content"] = response["reasoning_content"]
        if response.get("usage"):
            assistant_entry["usage"] = response["usage"]
        messages.append(assistant_entry)

        tool_calls = response.get("tool_calls") or []
        if any(tc.get("function", {}).get("name") == "quit" for tc in tool_calls):
            final_text = assistant_text(response) or "Done."
            break
        if not tool_calls:
            return assistant_text(response) or "Done."

        mcp_observations: list[dict[str, Any]] = []
        for tc in tool_calls:
            call_id = tc["id"]
            fn = tc["function"]
            name = fn["name"]
            try:
                args = parse_tool_arguments(fn.get("arguments"))
                result = await execute_wire_tool(
                    name,
                    args,
                    bot,
                    chat_id,
                    db_path,
                    notify_state,
                    actor="main",
                )
            except Exception as e:
                result = f"Tool {name} failed: {e}"
            messages.append({"role": "tool", "tool_call_id": call_id, "content": truncate(result)})
            observation = build_mcp_observation_message(
                result,
                tool_name=str(name or ""),
            )
            if observation is not None:
                mcp_observations.append(observation)
        messages.extend(mcp_observations)

    return final_text


# ---------------------------------------------------------------------------
# Chat agent (entry point with lock)
# ---------------------------------------------------------------------------


async def run_session_operation(
    session_id: str,
    operation: Callable[[], Awaitable[Any]],
) -> Any:
    """Run an agent continuation under the session's interrupt ownership.

    Every user-visible slice of agent work, including a continuation resumed
    from a pending question, must hold the session lock and expose its task via
    ``active_task``.  ``interrupt_active_run`` deliberately relies on those two
    fields; calling ``_run_chat_agent`` directly without this boundary creates a
    run that looks active in the UI but cannot be stopped.
    """
    normalized_session_id = str(session_id or "")
    ctx = _ensure_session(normalized_session_id)
    if ctx.lock.locked():
        interrupt_active_run(session_id=normalized_session_id)
    async with ctx.lock:
        ctx.interrupt_event.clear()
        current_task = asyncio.current_task()
        ctx.active_task = current_task
        try:
            return await operation()
        finally:
            if ctx.active_task is current_task:
                ctx.active_task = None


async def run_agent(
    user_message: str,
    bot: Any,
    chat_id: int,
    db_path: str,
    client_request_id: str = "",
    lang: str = "",
    command: str = "",
    public_user_message: str | None = None,
    public_attachments: list[dict[str, Any]] | None = None,
    llm_user_content: Any | None = None,
    permission_mode: str = "default",
    session_id: str = "",
    workspace_dir: str = "",
    soul_enabled: bool | None = None,
    workspace_enabled: bool | None = None,
    ephemeral_system: str = "",
    fixed_ephemeral_system: str = "",
    volatile_ephemeral_system: str = "",
    static_system_extra: str = "",
    final_system_extra: str = "",
    response_capabilities: tuple[str, ...] | frozenset[str] = (),
    ui_instance_id: str = "",
    conversation_source: str = "",
) -> str:
    """Main entry point. Runs the main agent loop with stable tool gateways.

    ``workspace_dir`` scopes the agent's file tools + Bash cwd to a specific
    directory (a Workbench project's workspacePath). Empty → global WORKSPACE_DIR.

    ``ephemeral_system`` is kept as a compatibility alias for run-scoped context.
    It is fixed for the duration of the run and inserted before the current user
    turn, so tool rounds can reuse the full previous prompt prefix. Use
    ``volatile_ephemeral_system`` only for context that can differ between calls
    inside the same run; it is appended at the prompt tail.

    ``static_system_extra`` is a run-invariant block (e.g. Workbench task-mode
    framing) concatenated into the SYSTEM prefix right after the base prompt,
    ahead of every volatile block. Use it for instructions that never change
    between runs so they stay in the cache-stable prefix; use ``ephemeral_system``
    for anything that varies per run.

    ``final_system_extra`` is reserved for a conversation-frozen Workbench
    project-memory block and is appended after every other system fragment.

    ``response_capabilities`` declares stable client rendering features. It
    participates in the wire-bundle cache key; Workbench uses
    ``interactive_blocks`` to expose ``LoadRendererContract``.
    """
    session_token = _current_session_id.set(session_id)
    ui_instance_token = _ui_instance_id.set(str(ui_instance_id or ""))
    conversation_source_token = _conversation_source.set(str(conversation_source or ""))
    user_request_text_token = _state._user_request_text.set(str(
        public_user_message if public_user_message is not None else user_message or ""
    ))
    delegation_receipts_token = _state._explicit_delegation_receipts.set(set())
    delegation_batches_token = _state._explicit_delegation_batches.set({})
    workspace_token = _active_workspace_dir.set(workspace_dir or "")
    soul_enabled_token = _state._soul_context_enabled.set(soul_enabled)
    workspace_enabled_token = _state._workspace_context_enabled.set(workspace_enabled)
    response_capabilities_token = response_capabilities_context.set(frozenset(
        str(item or "").strip()
        for item in response_capabilities
        if str(item or "").strip()
    ))
    try:
        async def run_chat() -> str:
            return await _run_chat_agent(
                user_message, bot, chat_id, db_path,
                client_request_id=client_request_id, lang=lang, command=command,
                public_user_message=public_user_message, public_attachments=public_attachments,
                llm_user_content=llm_user_content,
                permission_mode=permission_mode, ephemeral_system=ephemeral_system,
                fixed_ephemeral_system=fixed_ephemeral_system,
                volatile_ephemeral_system=volatile_ephemeral_system,
                static_system_extra=static_system_extra,
                final_system_extra=final_system_extra,
            )

        return await run_session_operation(session_id, run_chat)
    finally:
        _state._explicit_delegation_batches.reset(delegation_batches_token)
        _state._explicit_delegation_receipts.reset(delegation_receipts_token)
        _state._user_request_text.reset(user_request_text_token)
        response_capabilities_context.reset(response_capabilities_token)
        _conversation_source.reset(conversation_source_token)
        _ui_instance_id.reset(ui_instance_token)
        _current_session_id.reset(session_token)
        _active_workspace_dir.reset(workspace_token)
        _state._workspace_context_enabled.reset(workspace_enabled_token)
        _state._soul_context_enabled.reset(soul_enabled_token)


def is_session_running(session_id: str = "") -> bool:
    """Return whether an agent run currently owns the requested session."""
    return _ensure_session(session_id).lock.locked()


async def _clear_interrupt_when_idle(session_id: str = "") -> None:
    ctx = _ensure_session(session_id)
    try:
        while ctx.lock.locked():
            await asyncio.sleep(0.05)
    finally:
        ctx.interrupt_event.clear()


def interrupt_active_run(session_id: str = "") -> bool:
    ctx = _ensure_session(session_id)
    if not ctx.lock.locked():
        ctx.interrupt_event.clear()
        return False
    ctx.interrupt_event.set()
    task = ctx.active_task
    if task is not None and not task.done() and task is not asyncio.current_task():
        task.cancel()
    round_id = str(ctx.active_main_round_id or "").strip()
    if round_id or session_id:
        async def _cancel_subagents() -> None:
            try:
                from cyrene.subagent import cancel_subagent_tasks

                await cancel_subagent_tasks(round_id=round_id, session_id=session_id)
            except Exception:
                logger.exception("Failed to cancel subagents for interrupted session %s", session_id)

        try:
            _track_background_behavior_task(asyncio.create_task(_cancel_subagents()))
        except RuntimeError:
            pass
    task = asyncio.create_task(_clear_interrupt_when_idle(session_id=session_id))
    ctx.pending_interrupt_clearers.add(task)
    task.add_done_callback(ctx.pending_interrupt_clearers.discard)
    return True


# ---------------------------------------------------------------------------
# Chat agent coordinator
# ---------------------------------------------------------------------------

async def _run_chat_agent(
    user_message: str,
    bot: Any,
    chat_id: int,
    db_path: str,
    ephemeral_system: str = "",
    fixed_ephemeral_system: str = "",
    volatile_ephemeral_system: str = "",
    static_system_extra: str = "",
    final_system_extra: str = "",
    forced_round_id: str = "",
    history_override: list[dict[str, Any]] | None = None,
    persist_base_messages: list[dict[str, Any]] | None = None,
    persist_insert_at: int | None = None,
    client_request_id: str = "",
    persist_user_message: bool = True,
    behavior_user_message: str | None = None,
    behavior_system_initiated: bool = False,
    public_user_message: str | None = None,
    public_attachments: list[dict[str, Any]] | None = None,
    llm_user_content: Any | None = None,
    public_prompt: str | None = None,
    refresh_labels: bool = True,
    hide_initial_detail: bool = False,
    assistant_message_meta: dict[str, Any] | None = None,
    lang: str = "",
    command: str = "",
    permission_mode: str = "default",
    plan_modification: str = "",
) -> str:
    """Run one main Agent slice with complete global Hook lifecycle events."""
    import time as _time

    from cyrene.hooks import run_lifecycle_hooks

    round_id = str(forced_round_id or "").strip() or f"round_{int(_time.time() * 1000)}"
    round_token = _current_round_id.set(round_id)
    caller_token = _caller_type.set("main_agent")
    try:
        injected = await run_lifecycle_hooks("SessionStart")
        effective_fixed = "\n\n".join(
            part for part in (
                fixed_ephemeral_system,
                ("## Agent Hook Context\n" + injected) if injected else "",
            ) if part
        )
        try:
            result = await _run_chat_agent_impl(
                user_message,
                bot,
                chat_id,
                db_path,
                ephemeral_system=ephemeral_system,
                fixed_ephemeral_system=effective_fixed,
                volatile_ephemeral_system=volatile_ephemeral_system,
                static_system_extra=static_system_extra,
                final_system_extra=final_system_extra,
                forced_round_id=round_id,
                history_override=history_override,
                persist_base_messages=persist_base_messages,
                persist_insert_at=persist_insert_at,
                client_request_id=client_request_id,
                persist_user_message=persist_user_message,
                behavior_user_message=behavior_user_message,
                behavior_system_initiated=behavior_system_initiated,
                public_user_message=public_user_message,
                public_attachments=public_attachments,
                llm_user_content=llm_user_content,
                public_prompt=public_prompt,
                refresh_labels=refresh_labels,
                hide_initial_detail=hide_initial_detail,
                assistant_message_meta=assistant_message_meta,
                lang=lang,
                command=command,
                permission_mode=permission_mode,
                plan_modification=plan_modification,
            )
        except asyncio.CancelledError:
            await asyncio.shield(run_lifecycle_hooks(
                "Stop", reason="cancelled", details={"status": "cancelled"},
            ))
            raise
        except Exception as exc:
            await run_lifecycle_hooks(
                "Stop",
                reason=f"abnormal:{type(exc).__name__}",
                details={"status": "error", "error": str(exc)[:2000]},
            )
            raise
        await run_lifecycle_hooks(
            "SessionEnd",
            reason="awaiting_user" if result == _AWAITING_USER_SENTINEL else "completed",
            details={
                "status": "awaiting_user" if result == _AWAITING_USER_SENTINEL else "completed",
                "summary": str(result or "")[:16000],
            },
        )
        return result
    finally:
        _caller_type.reset(caller_token)
        _current_round_id.reset(round_token)


async def _run_chat_agent_impl(
    user_message: str,
    bot: Any,
    chat_id: int,
    db_path: str,
    ephemeral_system: str = "",
    fixed_ephemeral_system: str = "",
    volatile_ephemeral_system: str = "",
    static_system_extra: str = "",
    final_system_extra: str = "",
    forced_round_id: str = "",
    history_override: list[dict[str, Any]] | None = None,
    persist_base_messages: list[dict[str, Any]] | None = None,
    persist_insert_at: int | None = None,
    client_request_id: str = "",
    persist_user_message: bool = True,
    behavior_user_message: str | None = None,
    behavior_system_initiated: bool = False,
    public_user_message: str | None = None,
    public_attachments: list[dict[str, Any]] | None = None,
    llm_user_content: Any | None = None,
    public_prompt: str | None = None,
    refresh_labels: bool = True,
    hide_initial_detail: bool = False,
    assistant_message_meta: dict[str, Any] | None = None,
    lang: str = "",
    command: str = "",
    permission_mode: str = "default",
    plan_modification: str = "",
) -> str:
    import time as _time

    original_user_message = str(user_message or "")
    deep_reflect_parse = parse_deep_reflect_command(original_user_message)
    if deep_reflect_parse.get("matched"):
        command = DEEP_REFLECT_COMMAND_ID
        user_message = str(deep_reflect_parse.get("focus") or "")
        if public_user_message is None:
            public_user_message = original_user_message
        if public_prompt is None:
            public_prompt = original_user_message

    round_id = str(forced_round_id or "").strip() or f"round_{int(_time.time() * 1000)}"
    round_token = _current_round_id.set(round_id)
    standalone_run_span = None
    if not current_trace_context().trace_id:
        standalone_run_id = new_trace_id("run")
        standalone_run_span = trace_span(
            "run",
            "agent_run",
            span_id=standalone_run_id,
            trace_id=standalone_run_id,
            run_id=standalone_run_id,
            db_path=db_path,
        ).start()
    trace_ids_binding = bind_trace_context(
        session_id=_current_session_id.get(),
        round_id=round_id,
    )
    round_span = trace_span(
        "round",
        "agent_round",
        span_id=round_id,
        db_path=db_path,
        attributes={"command": str(command or "chat")},
    ).start()
    prepare_span = trace_span("local", "round_prepare", db_path=db_path).start()
    full_session_messages = _load_session_messages()
    # Update per-session context so reads via cyrene.agent.state are visible
    _ctx = _ensure_session(_current_session_id.get())
    _ctx.active_main_round_id = round_id
    _ctx.active_main_round_prompt = user_message
    _ctx.active_main_round_public_prompt = user_message if public_prompt is None else str(public_prompt)
    _ctx.active_main_round_started_at = _time.time()
    # Keep module-level globals in sync for the default session (backward compat)
    _state._active_main_round_id = round_id
    _state._active_main_round_prompt = user_message
    _state._active_main_round_public_prompt = user_message if public_prompt is None else str(public_prompt)
    _state._active_main_round_started_at = _time.time()
    raw_history = list(history_override) if history_override is not None else _load_session_messages()
    history = _expand_report_reference_history(raw_history, user_message)
    merge_base = persist_base_messages
    merge_insert_at = persist_insert_at
    merge_live_state = history_override is None
    if history_override is not None and merge_base is None:
        merge_base = list(full_session_messages)
        merge_insert_at = len(merge_base)
        merge_live_state = False
    elif merge_live_state and merge_insert_at is None:
        merge_insert_at = len(history)

    base_token = _persist_base_messages.set(merge_base)
    merge_live_token = _persist_merge_live_state.set(merge_live_state and merge_base is None)
    prefix_token = _persist_history_prefix_len.set(len(history) if (merge_base is not None or merge_live_state) else 0)
    insert_token = _persist_insert_at.set(merge_insert_at if (merge_base is not None or merge_live_state) else None)
    client_request_token = _current_client_request_id.set(client_request_id)
    intermediate_reply_token = _pending_intermediate_user_replies.set([])
    hide_initial_detail_token = _ui_round_hide_initial_detail.set(bool(hide_initial_detail))
    assistant_meta_token = _ui_round_assistant_meta.set(dict(assistant_message_meta) if assistant_message_meta else None)
    _mode = permission_mode if permission_mode in _state.PERMISSION_MODES else "default"
    mode_token = _state._permission_mode.set(_mode)
    # Explicit full-access mode is inherited by every Workbench tool task.
    if _mode == "full_access":
        _state._temporary_full_access.set(True)
    behavior_turn_context: dict[str, Any] | None = None
    dr_token = None
    dr_first_token = None
    cmd_token = None
    economy_token = None
    final_output = ""
    try:
        # 全局 short_term 只属于默认会话（旧 UI 单线程对话的跨重启恢复）。
        # workbench 的任务/对话会话有独立 session_id，注入会把别的话题带进
        # 全新会话造成答非所问，因此一律跳过。
        is_default_session = not _current_session_id.get()
        restored_short_term = False
        if not history and is_default_session:
            st = get_context(max_chars=5000)
            if st:
                history = [{"role": "system", "content": "[Restored context]\n" + st}]
                restored_short_term = True
        # Run-scoped ephemeral context is not persisted into history. It is inserted
        # immediately before the current user turn inside ``_run_main_agent`` so a
        # tool loop evolves by pure append (system/history/fixed-context/user →
        # assistant/tool...), preserving the full prior request as a cache prefix.

        if command != DEEP_REFLECT_COMMAND_ID:
            try:
                from cyrene.learning import engine as _behavior_learning
                labels = get_session_labels(round_id)
                behavior_turn_context = await _behavior_learning.begin_turn(
                    session_id=labels.get("archive_session_id", ""),
                    round_id=round_id,
                    user_message=(behavior_user_message if behavior_user_message is not None else user_message),
                    history=history,
                    session_title=labels.get("session_title", ""),
                    system_initiated=behavior_system_initiated,
                )
            except Exception:
                logger.warning("Failed to initialize behavior-learning turn context", exc_info=True)
                behavior_turn_context = None

        try:
            # 同理：system prompt 里的 short_term 摘要也只给默认会话。
            memory_context = get_memory_context(
                include_short_term=is_default_session and not restored_short_term
            )
        except TypeError as exc:
            if "include_short_term" not in str(exc):
                raise
            memory_context = get_memory_context()
        main_system = prompt_for_enabled_tool_packs(_MAIN_AGENT_PROMPT_TEMPLATE)
        now = datetime.now().astimezone()
        temporal_context = (
            "## Current Date\n"
            f"- Current local date: {now:%Y-%m-%d} ({now:%A}).\n"
            "- Interpret relative phrases such as today, recently, this week, last week, 最近, 最近一周, 今天, 本周 relative to this date.\n"
            "- When dealing with time-related tasks, search for current forecast/current conditions. Do not invent or substitute old years unless the user explicitly asks for historical weather."
        )
        main_system_context = [
            context_block(
                "main.system.base",
                "system",
                source="cyrene.agent.prompts._MAIN_AGENT_PROMPT",
                reason="base main-agent instructions",
                content=main_system,
            ),
        ]
        plan_mode_active = _state._permission_mode.get() == "plan"
        if plan_mode_active:
            revision_note = (
                "\n- The user is revising a previous proposed plan. Their revision request is:\n"
                f"{plan_modification.strip()}\n"
                if str(plan_modification or "").strip() else ""
            )
            plan_mode_prompt = (
                "## Plan Mode Discovery\n"
                "- The user selected plan mode. Your goal is to prepare a proposed plan for approval, not to complete the work yet.\n"
                "- You may call tools before generating the plan when they help you inspect the workspace, search project memory, read files, gather public/current facts, or understand constraints.\n"
                "- Before approval, avoid mutating tools and side effects: do not write/edit/delete files, commit, schedule tasks, send files/messages, or change external state unless the user explicitly requested that as part of planning.\n"
                f"{revision_note}"
                "- After enough context is collected, call `enter_plan_mode` to submit the structured plan and pause for the user's decision. Do not finish with a normal answer instead of presenting the plan.\n"
                "- If no exploration is needed, still call `enter_plan_mode` directly."
            )
            main_system = main_system + "\n\n" + plan_mode_prompt
            main_system_context.append(context_block(
                "mode.plan.discovery",
                "mode_policy",
                source="cyrene.agent.coordinator",
                reason="Workbench chat plan mode allows pre-plan tool discovery",
                content=plan_mode_prompt,
            ))
        # Caller-provided static system extension (e.g. Workbench task-mode framing).
        # Concatenated right after the base prompt — ahead of every volatile block
        # (memory, temporal, workspace) — so it stays inside the byte-stable cached
        # prefix instead of being re-processed each tool round at the prompt tail.
        if static_system_extra:
            main_system = main_system + "\n\n" + static_system_extra
            main_system_context.append(context_block(
                "main.system.static_extra",
                "system",
                source="run_agent(static_system_extra)",
                reason="caller-provided static system extension; cache-stable prefix",
                transforms=["concat_into_system"],
                content=static_system_extra,
            ))
        if _state.has_response_capability("interactive_blocks"):
            main_system = (
                main_system
                + "\n\n"
                + WORKBENCH_RENDERER_TRIGGER_PROMPT
            )
            main_system_context.append(context_block(
                "client.renderer.workbench",
                "system",
                source="run_agent(response_capabilities)",
                reason=(
                    "Workbench renderer is available; detailed contracts are "
                    "loaded just in time through LoadRendererContract"
                ),
                transforms=["concat_into_system"],
                content=WORKBENCH_RENDERER_TRIGGER_PROMPT,
            ))
        if lang and lang != "en":
            lang_prompt = f"The user has set their preferred language to {lang}. Reply in this language."
            main_system += "\n\n" + lang_prompt
            main_system_context.append(context_block(
                "main.system.language",
                "system",
                source="run_agent(lang)",
                reason="user selected preferred language",
                content=lang_prompt,
                metadata={"lang": lang},
            ))
        if memory_context:
            main_system = main_system + "\n\n## Memory Context\n" + memory_context
            main_system_context.append(context_block(
                "memory.context",
                "memory",
                source="cyrene.memory.get_memory_context",
                reason="main agent memory injection",
                transforms=["concat_into_system"],
                content=memory_context,
            ))
        skill_prompt_block = (
            build_skill_prompt_block()
            if is_tool_pack_enabled("skill_tools")
            else ""
        )
        if skill_prompt_block:
            main_system = main_system + "\n\n" + skill_prompt_block
            main_system_context.append(context_block(
                "skills.installed",
                "skills",
                source="cyrene.learning.skills.build_skill_prompt_block",
                reason="enabled external skills are visible to the agent",
                transforms=["preview", "concat_into_system"],
                content=skill_prompt_block,
            ))
        try:
            learned_skill_block = (
                await _behavior_learning.build_learned_skill_block()
                if is_tool_pack_enabled("skill_tools")
                else ""
            )
            if learned_skill_block:
                main_system = main_system + "\n\n" + learned_skill_block
                main_system_context.append(context_block(
                    "skills.learned",
                    "skills",
                    source="cyrene.learning.engine.build_learned_skill_block",
                    reason="learned reusable workflows visible to the agent",
                    transforms=["concat_into_system"],
                    content=learned_skill_block,
                ))
        except Exception:
            logger.warning("Failed to build learned-skill names block", exc_info=True)
        # ``temporal_context`` is deliberately NOT concatenated into the system
        # prefix: the date rolls over daily, which would invalidate the entire
        # system+history prefix every midnight. It is run-fixed context instead.
        try:
            from cyrene.tooling.backends.shell_runtime import resolve_shell
            _shell_kind = resolve_shell()[0]
        except Exception:
            _shell_kind = "bash"
        current_workspace_scope = prompt_for_enabled_tool_packs(
            workspace_scope_block(
                active_workspace_dir(),
                shell_kind=_shell_kind,
            )
        )
        main_system += "\n\n" + current_workspace_scope
        main_system_context.append(context_block(
            "runtime.workspace_scope",
            "system",
            source="cyrene.agent.prompts.workspace_scope_block",
            reason="constrain agent to workspace; prevent unnecessary permission prompts",
            transforms=["concat_into_system"],
            content=current_workspace_scope,
        ))

        is_deep_research = command == "deep-research"
        dr_token = _deep_research_mode.set(is_deep_research)
        dr_first_token = _deep_research_first_round.set(is_deep_research and not bool(forced_round_id))
        cmd_token = _current_command.set(command)
        economy_token = _economy_mode.set(
            str(_get_setting("budget_mode", "normal") or "normal").strip().lower() == "economy"
        )

        if command == DEEP_REFLECT_COMMAND_ID:
            visible_command_text = str(public_user_message if public_user_message is not None else original_user_message or "/deep-reflect").strip() or "/deep-reflect"
            visible_history = [
                message for message in history
                if isinstance(message, dict)
                and str(message.get("role") or "") != "system"
                and not bool(message.get("hidden_from_ui"))
            ]
            user_entry: dict[str, Any] = {
                "role": "user",
                "content": visible_command_text,
                "round_id": round_id,
            }
            if client_request_id:
                user_entry["client_request_id"] = client_request_id
            _ensure_message_identity([user_entry])
            try:
                reflection_record = await create_deep_reflection_record(
                    list(visible_history),
                    scope="current_round",
                    goal_gap="The user manually requested deep reflection because the current work may not be satisfying the goal.",
                    focus=user_message,
                    lang_text=visible_command_text or user_message,
                )
                reflection_record["round_id"] = round_id
                if client_request_id:
                    reflection_record["client_request_id"] = client_request_id
                main_text = str(reflection_record.get("content") or "Deep reflection is complete.")
                history = [*visible_history, user_entry, reflection_record]
                await _save_session_messages(history)
            except Exception as exc:
                logger.warning("Manual deep reflection failed", exc_info=True)
                main_text = f"深度反思失败：{exc}" if any("\u4e00" <= ch <= "\u9fff" for ch in visible_command_text) else f"Deep reflection failed: {exc}"
                assistant_entry = _apply_assistant_meta({
                    "role": "assistant",
                    "content": main_text,
                    "round_id": round_id,
                })
                if client_request_id:
                    assistant_entry["client_request_id"] = client_request_id
                _ensure_message_identity([assistant_entry])
                await _save_session_messages([*visible_history, user_entry, assistant_entry])

                final_output = main_text
                await _publish_runtime_event({
                    "type": "chat_message",
                    "client_request_id": client_request_id,
                })
                if behavior_turn_context is not None:
                    try:
                        from cyrene.learning import engine as _behavior_learning
                        latest_labels = get_session_labels(round_id)
                        await _behavior_learning.complete_turn(
                            turn_id=behavior_turn_context["turn_id"],
                            assistant_response=final_output,
                            session_title=latest_labels.get("session_title", ""),
                            round_title=latest_labels.get("round_title", ""),
                        )
                        await _kick_behavior_learning_processing()
                    except Exception:
                        logger.warning("Failed to finalize behavior-learning turn", exc_info=True)
                return final_output

            focus_text = str(user_message or "").strip()
            if any("\u4e00" <= ch <= "\u9fff" for ch in visible_command_text):
                user_message = (
                    "深度反思已完成。请从上面的 Deep reflection packet 继续自动工作，"
                    "不要只告知用户反思已完成；直接采取下一步行动或给出实质性结果。"
                )
                if focus_text:
                    user_message += f"\n用户指定的反思重点：{focus_text}"
            else:
                user_message = (
                    "Deep reflection is complete. Continue working automatically from the Deep reflection packet above. "
                    "Do not merely tell the user reflection is complete; take the next useful step or provide a substantive result."
                )
                if focus_text:
                    user_message += f"\nUser-specified reflection focus: {focus_text}"
            public_user_message = None
            public_attachments = []
            persist_user_message = False

        # Command-specific prompt injection. Package-specific lines are filtered
        # before concatenation so disabled package metadata never enters the
        # model-facing prompt.
        if command == "deep-research":
            command_prompt = prompt_for_enabled_tool_packs(
                _DEEP_RESEARCH_PROMPT
            )
            main_system = main_system + "\n\n" + command_prompt
            main_system_context.append(context_block(
                "command.deep-research",
                "command_prompt",
                source="cyrene.agent.prompts._DEEP_RESEARCH_PROMPT",
                reason="deep-research command selected",
                transforms=["concat_into_system"],
                content=command_prompt,
            ))
            deep_research_spawn_policy = (
                "\n\n## Subagent Spawn Policy\n"
                "Current policy: deep-research (maximum parallelism).\n"
                "- You MUST invoke `subagent.spawn` through `subagent_tools` for EVERY research track. Never do research yourself.\n"
                "- Describe `subagent.spawn` once, then launch ALL invokes in one batch.\n"
                "- If a track is broad, split it and invoke additional subagents.\n"
                "- Err on the side of MORE subagents. 5–10 subagents is normal; 10+ is acceptable for complex questions.\n"
                "- Even small, focused questions within a track deserve their own subagent. Granularity beats breadth per agent.\n"
                "- If any result is thin, contradictory, or incomplete, immediately invoke follow-up subagents.\n"
                "- The ONLY reason not to invoke a subagent is if the task is already fully answered with high confidence."
            )
            deep_research_spawn_policy = prompt_for_enabled_tool_packs(
                deep_research_spawn_policy
            )
            main_system += deep_research_spawn_policy
            main_system_context.append(context_block(
                "spawn_policy.deep-research",
                "spawn_policy",
                source="cyrene.agent.coordinator",
                reason="deep-research command forces maximum parallelism",
                transforms=["concat_into_system"],
                content=deep_research_spawn_policy,
            ))
        elif command == "quick-answer":
            command_prompt = prompt_for_enabled_tool_packs(
                _QUICK_ANSWER_PROMPT
            )
            main_system = main_system + "\n\n" + command_prompt
            main_system_context.append(context_block(
                "command.quick-answer",
                "command_prompt",
                source="cyrene.agent.prompts._QUICK_ANSWER_PROMPT",
                reason="quick-answer command selected",
                transforms=["concat_into_system"],
                content=command_prompt,
            ))
        elif command == "workbench-task-reply":
            command_prompt = prompt_for_enabled_tool_packs(
                _WORKBENCH_TASK_REPLY_PROMPT
            )
            main_system = main_system + "\n\n" + command_prompt
            main_system_context.append(context_block(
                "command.workbench-task-reply",
                "command_prompt",
                source="cyrene.agent.prompts._WORKBENCH_TASK_REPLY_PROMPT",
                reason="Workbench task reply mode selected",
                transforms=["concat_into_system"],
                content=command_prompt,
            ))
        elif command == "help-me-decide":
            command_prompt = prompt_for_enabled_tool_packs(
                _HELP_ME_DECIDE_PROMPT
            )
            main_system = main_system + "\n\n" + command_prompt
            main_system_context.append(context_block(
                "command.help-me-decide",
                "command_prompt",
                source="cyrene.agent.prompts._HELP_ME_DECIDE_PROMPT",
                reason="help-me-decide command selected",
                transforms=["concat_into_system"],
                content=command_prompt,
            ))
            help_me_decide_spawn_policy = (
                "\n\n## Subagent Spawn Policy\n"
                "Current policy: help-me-decide.\n"
                "- Spawn exactly ONE subagent per option. Launch all simultaneously.\n"
                "- Do NOT do any option research yourself — delegate every option to its own subagent.\n"
                "- After all subagents return, synthesize into a decision report."
            )
            help_me_decide_spawn_policy = prompt_for_enabled_tool_packs(
                help_me_decide_spawn_policy
            )
            main_system += help_me_decide_spawn_policy
            main_system_context.append(context_block(
                "spawn_policy.help-me-decide",
                "spawn_policy",
                source="cyrene.agent.coordinator",
                reason="help-me-decide command sets delegation policy",
                transforms=["concat_into_system"],
                content=help_me_decide_spawn_policy,
            ))
        elif command == "learning-plan":
            command_prompt = prompt_for_enabled_tool_packs(
                _LEARNING_PLAN_PROMPT
            )
            main_system = main_system + "\n\n" + command_prompt
            main_system_context.append(context_block(
                "command.learning-plan",
                "command_prompt",
                source="cyrene.agent.prompts._LEARNING_PLAN_PROMPT",
                reason="learning-plan command selected",
                transforms=["concat_into_system"],
                content=command_prompt,
            ))
            learning_plan_spawn_policy = (
                "\n\n## Subagent Spawn Policy\n"
                "Current policy: learning-plan.\n"
                "- Spawn exactly ONE subagent per knowledge module. Launch all simultaneously.\n"
                "- Do NOT research learning resources yourself — delegate every module to its own subagent.\n"
                "- After all subagents return, synthesize into a structured learning plan."
            )
            learning_plan_spawn_policy = prompt_for_enabled_tool_packs(
                learning_plan_spawn_policy
            )
            main_system += learning_plan_spawn_policy
            main_system_context.append(context_block(
                "spawn_policy.learning-plan",
                "spawn_policy",
                source="cyrene.agent.coordinator",
                reason="learning-plan command sets delegation policy",
                transforms=["concat_into_system"],
                content=learning_plan_spawn_policy,
            ))
        elif command == "daily-review":
            command_prompt = prompt_for_enabled_tool_packs(
                _DAILY_REVIEW_PROMPT
            )
            main_system = main_system + "\n\n" + command_prompt
            main_system_context.append(context_block(
                "command.daily-review",
                "command_prompt",
                source="cyrene.agent.prompts._DAILY_REVIEW_PROMPT",
                reason="daily-review command selected",
                transforms=["concat_into_system"],
                content=command_prompt,
            ))
            spawn_policy_block = prompt_for_enabled_tool_packs(
                _spawn_policy_prompt_block("off")
            )
            main_system = main_system + "\n\n" + spawn_policy_block
            main_system_context.append(context_block(
                "spawn_policy.off",
                "spawn_policy",
                source="cyrene.agent.prompts._spawn_policy_prompt_block",
                reason="daily-review disables subagents",
                transforms=["concat_into_system"],
                content=spawn_policy_block,
                metadata={"policy": "off"},
            ))
        elif command == "deep-compare":
            command_prompt = prompt_for_enabled_tool_packs(
                _DEEP_COMPARE_PROMPT
            )
            main_system = main_system + "\n\n" + command_prompt
            main_system_context.append(context_block(
                "command.deep-compare",
                "command_prompt",
                source="cyrene.agent.prompts._DEEP_COMPARE_PROMPT",
                reason="deep-compare command selected",
                transforms=["concat_into_system"],
                content=command_prompt,
            ))
            deep_compare_spawn_policy = (
                "\n\n## Subagent Spawn Policy\n"
                "Current policy: deep-compare.\n"
                "- Spawn exactly ONE subagent per comparison dimension. Launch all simultaneously.\n"
                "- Do NOT do any comparison research yourself — delegate every dimension to its own subagent.\n"
                "- After all subagents return, synthesize into a comparison matrix and recommendation."
            )
            deep_compare_spawn_policy = prompt_for_enabled_tool_packs(
                deep_compare_spawn_policy
            )
            main_system += deep_compare_spawn_policy
            main_system_context.append(context_block(
                "spawn_policy.deep-compare",
                "spawn_policy",
                source="cyrene.agent.coordinator",
                reason="deep-compare command sets delegation policy",
                transforms=["concat_into_system"],
                content=deep_compare_spawn_policy,
            ))
        elif command == "claude-code":
            command_prompt = prompt_for_enabled_tool_packs(
                _CLAUDE_CODE_PROMPT
            )
            main_system = main_system + "\n\n" + command_prompt
            main_system_context.append(context_block(
                "command.claude-code",
                "command_prompt",
                source="cyrene.agent.prompts._CLAUDE_CODE_PROMPT",
                reason="claude-code command selected",
                transforms=["concat_into_system"],
                content=command_prompt,
            ))
        else:
            spawn_policy = get_spawn_policy()
            spawn_policy_block = prompt_for_enabled_tool_packs(
                _spawn_policy_prompt_block(spawn_policy)
            )
            main_system = main_system + "\n\n" + spawn_policy_block
            main_system_context.append(context_block(
                f"spawn_policy.{spawn_policy}",
                "spawn_policy",
                source="cyrene.agent.prompts._spawn_policy_prompt_block",
                reason="configured spawn policy",
                transforms=["concat_into_system"],
                content=spawn_policy_block,
                metadata={"policy": spawn_policy},
            ))

        # Keep per-run / per-session dynamic blocks out of the base system prefix.
        # They are fixed for this run and inserted before the current user turn, so
        # each tool round can still reuse the full previous prompt as a prefix.
        conversation_identity = conversation_identity_block(_current_session_id.get())
        try:
            from cyrene.workbench.pinned_resources import global_agent_context
            pinned_resource_context = global_agent_context(_current_session_id.get())
        except Exception:
            logger.warning(
                "Failed to load pinned resource context (session=%s); running without it",
                _current_session_id.get() or "-",
                exc_info=True,
            )
            pinned_resource_context = ""
        effective_fixed_ephemeral = "\n\n".join(
            part
            for part in (
                fixed_ephemeral_system,
                ephemeral_system,
                temporal_context,
                conversation_identity,
                pinned_resource_context,
                # Conversation-frozen project memory is deliberately last in
                # the last system message before the current user turn.
                final_system_extra,
            )
            if part
        )
        effective_volatile_ephemeral = "\n\n".join(
            part for part in (volatile_ephemeral_system,) if part
        )

        from cyrene.agent.agent import _run_main_agent

        prepare_span.set_attribute("history_messages", len(history))
        await prepare_span.finish()
        main_text = await _run_main_agent(
            user_message, history, bot, chat_id, db_path, main_system,
            client_request_id=client_request_id, persist_user_message=persist_user_message,
            public_user_message=public_user_message, public_attachments=public_attachments, lang=lang,
            llm_user_content=llm_user_content,
            system_context=main_system_context,
            fixed_ephemeral_system=effective_fixed_ephemeral,
            ephemeral_system=effective_volatile_ephemeral,
        )

        if main_text == _AWAITING_USER_SENTINEL:
            return main_text
        if main_text:
            final_output = main_text
        elif assistant_message_meta and assistant_message_meta.get("system_initiated"):
            # System-initiated rounds (e.g. the proactive heartbeat) must stay
            # silent when the agent chose not to speak — never substitute a
            # filler "Done." that would be delivered to the user.
            final_output = ""
        else:
            final_output = "Done."
        await _publish_runtime_event({
            "type": "chat_message",
            "client_request_id": client_request_id,
        })
        if behavior_turn_context is not None:
            try:
                from cyrene.learning import engine as _behavior_learning
                latest_labels = get_session_labels(round_id)
                await _behavior_learning.complete_turn(
                    turn_id=behavior_turn_context["turn_id"],
                    assistant_response=final_output,
                    session_title=latest_labels.get("session_title", ""),
                    round_title=latest_labels.get("round_title", ""),
                )
                await _kick_behavior_learning_processing()
            except Exception:
                logger.warning("Failed to finalize behavior-learning turn", exc_info=True)
        return final_output
    finally:
        try:
            from cyrene.browser import finish_electron_browser_round

            await asyncio.shield(
                finish_electron_browser_round(_current_session_id.get(), round_id)
            )
        except asyncio.CancelledError:
            logger.warning("Browser tab finalization was cancelled for round %s", round_id)
        except Exception:
            logger.warning("Failed to finalize browser tabs for round %s", round_id, exc_info=True)
        if behavior_turn_context is not None:
            try:
                from cyrene.learning import engine as _behavior_learning
                _behavior_learning.clear_turn_context(behavior_turn_context)
            except Exception:
                logger.debug("Failed to clear behavior-learning context", exc_info=True)
        if cmd_token is not None:
            _current_command.reset(cmd_token)
        if dr_token is not None:
            _deep_research_mode.reset(dr_token)
        if dr_first_token is not None:
            _deep_research_first_round.reset(dr_first_token)
        if economy_token is not None:
            _economy_mode.reset(economy_token)
        _ui_round_assistant_meta.reset(assistant_meta_token)
        _ui_round_hide_initial_detail.reset(hide_initial_detail_token)
        _pending_intermediate_user_replies.reset(intermediate_reply_token)
        _current_client_request_id.reset(client_request_token)
        _persist_insert_at.reset(insert_token)
        _persist_history_prefix_len.reset(prefix_token)
        _persist_merge_live_state.reset(merge_live_token)
        _persist_base_messages.reset(base_token)
        _ctx = _ensure_session(_current_session_id.get())
        _ctx.active_main_round_id = ""
        _ctx.active_main_round_prompt = ""
        _ctx.active_main_round_public_prompt = ""
        _ctx.active_main_round_started_at = 0.0
        # Keep module-level globals in sync (backward compat)
        _state._active_main_round_id = ""
        _state._active_main_round_prompt = ""
        _state._active_main_round_public_prompt = ""
        _state._active_main_round_started_at = 0.0
        _state._temporary_full_access.set(False)
        permission_grants = _state._permission_elevation_grants.get()
        if permission_grants is not None:
            permission_grants.clear()
        path_grants = _state._scoped_path_access_grants.get()
        if path_grants is not None:
            path_grants.clear()
        _state._permission_elevation_grants.set(None)
        _state._scoped_path_access_grants.set(None)
        _state._permission_mode.reset(mode_token)
        exc_type = sys.exc_info()[0]
        if exc_type is not None and issubclass(exc_type, asyncio.CancelledError):
            round_status = "cancelled"
        else:
            round_status = "error" if exc_type is not None else "ok"
        if prepare_span.is_active:
            await prepare_span.finish(status=round_status)
        await round_span.finish(status=round_status)
        trace_ids_binding.reset()
        if standalone_run_span is not None:
            await standalone_run_span.finish(status=round_status)
        _current_round_id.reset(round_token)


# ---------------------------------------------------------------------------
# Backward-compatible public API
# ---------------------------------------------------------------------------

async def run_task_agent(prompt: str, bot: Any, chat_id: int, db_path: str, notify_state: dict[str, bool] | None = None) -> str:
    return await _run_execution_agent(prompt, bot, chat_id, db_path, notify_state=notify_state)


async def run_heartbeat_agent(
    prompt: str,
    bot: Any,
    chat_id: int,
    db_path: str,
    session_id: str = "",
    on_reply: Callable[[str], Awaitable[Any]] | None = None,
    lang: str = "",
    workspace_dir: str = "",
) -> str:
    # The scheduler runs server-side with no HTTP request, so it cannot read the
    # per-request UI ``lang`` that normal chats carry. When a language preference
    # has been persisted (``app_language``), pin the reply to it explicitly;
    # otherwise fall back to inferring from the user's past messages.
    lang = (lang or "").strip().lower()
    if lang == "en":
        lang_line = "Always write your reply in English (the user's configured language).\n"
    elif lang == "zh":
        lang_line = "Always write your reply in Chinese / 简体中文 (the user's configured language).\n"
    elif lang:
        lang_line = f"Always write your reply in the user's configured language ({lang}).\n"
    else:
        lang_line = "Match the user's preferred language based on their past messages.\n"
    proactive_system = (
        "This round was initiated by the scheduler, not by a user chat message.\n"
        "The hidden task you receive is internal guidance, not text to answer literally.\n"
        "Your final assistant reply will be shown directly to the user in the Web UI.\n"
        "Write to the user in a natural, user-facing voice.\n"
        + lang_line
        + "Do not mention the scheduler, heartbeat, lottery, hidden prompt, or internal instructions.\n"
        "\n"
        "DECISION RULE — autonomous work, not conversation:\n"
        "- Inspect the supplied context for a concrete open task, unresolved decision, due/stale item, research gap, verification need, or small maintenance action.\n"
        "- If a useful safe action exists, use tools and complete it now. Do not merely offer help, propose future work, or paraphrase the context.\n"
        "- A visible reply is justified only by a concrete completed result, a newly verified material fact, or a specific blocker/risk requiring the user's attention. Keep that report concise and factual.\n"
        "- If there is no useful safe action or no material result, call `quit` silently.\n"
        "- Never greet the user, make small talk, ask how they are, send lifestyle reminders, or use a casual past topic as an excuse to message.\n"
        "- This scheduler event is not user activity. Never imply the user just woke up, came online, returned, became available, finished work, is busy, or is doing something now.\n"
        "- Incremental-work boundary: you may read/search/inspect and may create new additive files or records, but you must not modify, overwrite, move, rename, or delete existing files. Use `Write` only for a path that does not already exist; do not use `Edit` for proactive work. Avoid shell write commands, redirects, `rm`, `mv`, or other file-changing shell operations."
    )
    session_token = _current_session_id.set(session_id)
    workspace_token = _active_workspace_dir.set(workspace_dir or "")
    try:
        ctx = _ensure_session(session_id)
        # Proactive work is strictly non-preemptive. A user-owned run always
        # wins; unlike run_agent(), this path must never interrupt or queue
        # behind an active conversation.
        if ctx.lock.locked():
            return ""
        async with ctx.lock:
            ctx.interrupt_event.clear()
            reply = await _run_chat_agent(
                prompt, bot, chat_id, db_path,
                ephemeral_system=proactive_system,
                persist_user_message=False,
                behavior_user_message="Scheduled proactive check-in",
                behavior_system_initiated=True,
                public_prompt="", refresh_labels=False, hide_initial_detail=True,
                assistant_message_meta={"proactive": True, "system_initiated": True},
            )
            # ``awaiting_user`` is an internal control outcome, never public
            # assistant content.  Proactive rounds are forbidden from pausing,
            # but keep this delivery-boundary guard so a future tool regression
            # cannot leak the sentinel into a Workbench transcript or alert.
            public_reply = _state.sanitize_public_agent_text(reply)
            if reply and not public_reply:
                logger.warning(
                    "Suppressing unexpected awaiting-user outcome from proactive round"
                )
                return ""
            if public_reply and on_reply is not None:
                delivered = await on_reply(public_reply)
                if delivered is None or delivered is False:
                    return ""
            return public_reply
    finally:
        _active_workspace_dir.reset(workspace_token)
        _current_session_id.reset(session_token)


async def run_steward_agent(conversation_text: str, soulmd_content: str, bot: Any, chat_id: int, db_path: str) -> str:
    # Query existing entity titles for LLM-level deduplication
    _existing_entity_hint = ""
    try:
        from cyrene.tool_impl.entity.store import list_entities
        _existing = await list_entities(db_path, limit=200)
        if _existing:
            _lines = [
                f"- [project={e.get('project_id') or 'default'}] "
                f"[{e['type']}] {e['title']}"
                for e in _existing
            ]
            _existing_entity_hint = "\n".join(_lines[:50])  # cap at 50 to keep prompt reasonable
    except Exception:
        logger.warning("Failed to query existing entities for steward dedup hint", exc_info=True)

    steward_prompt = f"""You are a memory steward and entity extractor. Your job is twofold:

1. Update Cyrene's SOUL.md based on recent conversations (existing).
2. Extract entities (事务) from the conversation for background tracking.

Supported entity types: task, project, decision, knowledge, relationship, event, resource, idea, problem, habit.

### Part 1: SOUL.md updates
Read the recent conversation and current SOUL.md. Every SOUL.md modification
MUST use exactly one of these line formats (the literal `` :: `` separator is
required):
- APPEND SECTION_NAME :: content to append
- ERASE SECTION_NAME :: exact substring to remove
- MERGE SECTION_NAME :: old_text|||new_text
- SKIP

SECTION_NAME must be an existing SOUL.md heading name such as
MEMORY:HIGH_IMPACT, PATTERN:USER, RELATIONSHIP:USER, or SELF:BELIEFS. Never use
``APPEND: ...``, ``APPEND SECTION: content``, parentheses, or an em dash as a
separator.

Content policy: only record what each of the user's projects is doing — never
the details of how it is done (no implementation details, code changes, file
names, or step-by-step procedures). Focus on the user's important habits,
explicit requirements, and personal information. No examples; keep entries
concise.

### Part 2: Entity extraction
From the conversation, extract entities the user mentioned. Only extract when you are confident the user is talking about something real — not hypotheticals, jokes, or casual remarks.

CRITICAL: Check the existing entities list below. If the conversation mentions something semantically equivalent to an existing entity (same topic, same intent, different wording), SKIP it — do NOT output a duplicate. Use meaning, not just exact string match.

For each entity, output ENTITY with these fields:
ENTITY project_id="project_abc" type="task" title="Buy groceries" confidence="0.85" content="User mentioned needing to buy groceries this weekend"

Use the exact project_id shown in the Workbench conversation header. For
legacy/global conversations without a project id, use project_id="default".

Confidence guidelines:
- ≥ 0.8: Clear actionable mention with specifics (dates, names, concrete actions)
- 0.5-0.7: Possible mention but lacks detail
- 0.2-0.5: Vague mention, store as low-confidence candidate
- < 0.2: Do not output (ignore)

Do NOT extract:
- Pure emotional expressions ("I'm so tired")
- Casual chit-chat ("I ate noodles")
- Hypothetical scenarios ("if I went to Mars")
- Anything semantically equivalent to an already-existing entity in the list below

### Existing entities (do NOT extract duplicates):
{_existing_entity_hint if _existing_entity_hint else "(none yet)"}

Output BOTH parts inline. Start with SOUL.md updates (APPEND/ERASE/MERGE/SKIP), then entity lines (ENTITY ...).

SOUL.md:
{soulmd_content}

Recent conversation:
{conversation_text}

Output only the modifications needed, one per line. SOUL.md lines must follow
the exact ``COMMAND SECTION_NAME :: content`` grammar above; entity lines must
follow the exact ENTITY grammar."""
    return await _run_execution_agent(steward_prompt, bot, chat_id, db_path)
