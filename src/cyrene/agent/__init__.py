"""Lazy compatibility facade for the agent runtime.

Application code should import focused modules such as
``cyrene.agent.coordinator`` or ``cyrene.agent.session``.  The historical
``from cyrene.agent import run_agent`` API remains available without eagerly
loading the entire agent graph whenever any ``agent.*`` submodule is imported.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORT_GROUPS: dict[str, tuple[str, ...]] = {
    "cyrene.agent.state": (
        "_active_main_round_id",
        "_active_main_round_prompt",
        "_active_main_round_public_prompt",
        "_active_main_round_started_at",
        "_agent_lock",
        "_AWAITING_USER_SENTINEL",
        "_call_llm",
        "_call_llm_stream",
        "_caller_type",
        "_current_agent_id",
        "_current_client_request_id",
        "_current_command",
        "_current_round_id",
        "_deep_research_mode",
        "_economy_mode",
        "_emit_reply_stream_event",
        "_init_session_epoch",
        "_interrupt_event",
        "_LIGHT_TOOL_DEFS",
        "_llm_phase_name",
        "_MAIN_INBOX_AGENT_ID",
        "_main_inbox_worker",
        "_pending_compressors",
        "_pending_interrupt_clearers",
        "_pending_label_refreshes",
        "_pending_intermediate_user_replies",
        "_persist_base_messages",
        "_persist_history_prefix_len",
        "_persist_insert_at",
        "_persist_merge_live_state",
        "_publish_runtime_event",
        "_REPORT_REF_MAX_PREVIEW",
        "_REPORT_REF_PREFIX",
        "_reply_stream_writer",
        "_session_epoch",
        "_session_state_lock",
        "_streaming_reply_requested",
        "_tool_quit",
        "_ui_round_assistant_meta",
        "_ui_round_hide_initial_detail",
    ),
    "cyrene.agent.prompts": (
        "_TERMINAL_PROMPT",
        "_COMPARE_SUBAGENT_PROMPT",
        "_DAILY_REVIEW_PROMPT",
        "_DECISION_SUBAGENT_PROMPT",
        "_DEEP_COMPARE_PROMPT",
        "_DEEP_RESEARCH_PROMPT",
        "_DEEP_RESEARCH_SUBAGENT_PROMPT",
        "_DEFAULT_TEMPLATE",
        "_EXECUTION_SYSTEM_PROMPT",
        "_EXPANSION_PROMPT",
        "_HELP_ME_DECIDE_PROMPT",
        "_LEARNING_PLAN_PROMPT",
        "_LEARNING_SUBAGENT_PROMPT",
        "_MAIN_AGENT_PROMPT",
        "_OUTLINE_GENERATION_PROMPT",
        "_PHASE1_DECISION_PROMPT",
        "_QUICK_ANSWER_PROMPT",
        "_SECTION_WRITE_PROMPT",
        "_spawn_policy_prompt_block",
    ),
    "cyrene.agent.message": (
        "_apply_assistant_meta",
        "_assistant_entry_from_response",
        "_dedupe_messages_by_id",
        "_ensure_message_identity",
        "_extract_json_object",
        "_fallback_label",
        "_flush_intermediate_user_replies",
        "_insert_intermediate_user_reply",
        "_is_placeholder_reply",
        "_is_replaceable_live_message",
        "_merge_message_sequence",
        "_message_suffix_after_persisted_prefix",
        "_round_epoch_ms",
        "_round_started_iso",
        "_round_title_from_entry",
        "_tool_result_requests_user_input",
    ),
    "cyrene.agent.session": (
        "_append_session_message",
        "_clear_pending_question",
        "_compress_old_messages",
        "_compress_report_messages_for_storage",
        "_expand_report_reference_history",
        "_guidance_persist_context_after_ack",
        "_guidance_round_context",
        "_iter_report_refs",
        "_load_pending_question",
        "_load_round_messages",
        "_load_session_messages",
        "_load_session_state",
        "_looks_like_report_followup",
        "_normalize_pending_question",
        "_pending_question_resume_context",
        "_refresh_session_labels",
        "_remove_messages_by_request_id",
        "_report_reference_stub",
        "_report_title_from_text",
        "_restore_pending_question",
        "_save_session_messages",
        "_schedule_memory_compression",
        "_select_report_ref",
        "_upsert_pending_question",
        "_write_session_messages_locked",
        "_write_session_state",
        "append_system_message",
        "clear_session_id",
        "compact_session_if_needed",
        "get_pending_question",
        "get_session_labels",
    ),
    "cyrene.agent.round": (
        "get_live_rounds",
        "query_live_rounds",
    ),
    "cyrene.agent.guidance": (
        "_fan_out_guidance_to_subagents",
        "_generate_guidance_ack",
        "_guidance_ack_text",
        "_guidance_error_text",
        "_is_affirmative_answer",
        "_is_negative_answer",
        "_process_main_inbox_message",
        "_publish_round_guidance_update",
        "_synthesize_subagent_results",
        "_wait_for_subagent_round",
        "answer_pending_question",
        "format_httpx_error",
        "queue_round_guidance",
    ),
    "cyrene.agent.replies": (
        "_final_plain_reply_from_history",
        "_final_reply_from_history",
        "_final_user_reply_from_history",
        "_tool_result_fallback_text",
    ),
    "cyrene.agent.coordinator": (
        "_run_chat_agent",
        "_run_execution_agent",
        "SessionRunConflictError",
        "is_session_running",
        "interrupt_active_run",
        "run_agent",
        "run_session_operation",
        "run_heartbeat_agent",
        "run_steward_agent",
        "run_task_agent",
    ),
    "cyrene.agent.agent": ("_run_main_agent",),
    "cyrene.memory": ("get_memory_context",),
    "cyrene.runtime.memory.short_term": ("get_context",),
}

_EXPORTS = {
    name: module_name
    for module_name, names in _EXPORT_GROUPS.items()
    for name in names
}

__all__ = sorted(
    (*_EXPORTS, "DATA_DIR", "STATE_FILE", "_register_quit_handler")
)


def _register_quit_handler() -> None:
    """Preserve the historical explicit quit-handler registration hook."""
    catalog = import_module("cyrene.tooling.catalog")

    catalog.TOOL_HANDLERS["quit"] = __getattr__("_tool_quit")


def __getattr__(name: str) -> Any:
    if name in {"DATA_DIR", "STATE_FILE"}:
        return getattr(import_module("cyrene.agent.state"), name)

    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted((*globals(), *__all__))
