"""Agent module state: ContextVars, locks, and LLM call wrappers.

This is the leaf module of the ``agent/`` subpackage — it must not import
from any other ``agent.*`` module, so that every other module can safely
import from it without circular-dependency risk.
"""

import asyncio
import copy
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from contextvars import ContextVar

from cyrene.observability import debug
from cyrene.config import (
    ASSISTANT_NAME as ASSISTANT_NAME,
    DATA_DIR as _DATA_DIR,
    STATE_FILE as _STATE_FILE,
    WORKSPACE_DIR as _WORKSPACE_DIR,
)

# Mutable references so tests that swap STATE_FILE/DATA_DIR are visible to all
# ``agent.*`` sub-modules (which import ``state.STATE_FILE`` / ``state.DATA_DIR``).
STATE_FILE = _STATE_FILE
DATA_DIR = _DATA_DIR

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SessionContext — per-session state container
# ---------------------------------------------------------------------------

@dataclass
class SessionContext:
    """Holds all mutable state for a single agent session.

    Each active session (including the default "" session) gets its own
    ``SessionContext`` instance.  Fields previously stored as module-level
    globals are migrated here incrementally across the multi‑session phases.
    """
    session_id: str = ""
    state_file: Path | None = None  # None → DATA_DIR / "state.json" (default session)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    session_state_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    session_epoch: int = 0
    interrupt_event: asyncio.Event = field(default_factory=asyncio.Event)
    active_main_round_id: str = ""
    active_main_round_prompt: str = ""
    active_main_round_public_prompt: str = ""
    active_main_round_started_at: float = 0.0
    pending_compressors: set[asyncio.Task] = field(default_factory=set)
    pending_label_refreshes: set[asyncio.Task] = field(default_factory=set)
    pending_interrupt_clearers: set[asyncio.Task] = field(default_factory=set)
    pending_housekeeping: set[asyncio.Task] = field(default_factory=set)
    pending_distill_task: asyncio.Task | None = None
    main_inbox_worker: asyncio.Task | None = None
    active_task: asyncio.Task | None = None
    last_main_model_messages: list[dict[str, Any]] = field(default_factory=list)
    last_main_model_identity: dict[str, str] = field(default_factory=dict)
    last_main_model_round_id: str = ""

# Per‑session identifier carried by ContextVar — set at entry to run_agent()
_current_session_id: ContextVar[str] = ContextVar("_current_session_id", default="")

# Opaque renderer instance that submitted the current desktop-local turn.  It
# is run-local state, never model prompt text and never a tool argument.
_ui_instance_id: ContextVar[str] = ContextVar("_ui_instance_id", default="")

# Per‑run workspace root for the agent's FILE operations (Read/Write/Edit/Glob)
# and Bash cwd. Empty → fall back to the global WORKSPACE_DIR. Set at run_agent()
# entry from the active Workbench project's workspacePath so each project's agent
# stays confined to its own workspace.
#
# NOTE: SOUL.md / memory / behaviour‑learning files keep using the global
# WORKSPACE_DIR directly — they are cross‑project runtime state, not project
# files, so they must NOT be redirected here.
_active_workspace_dir: ContextVar[str] = ContextVar("_active_workspace_dir", default="")

# Client response features available for the current run.  Keep these separate
# from the workspace/session identity: they determine the stable model-facing
# tool bundle and must therefore be set before the catalog snapshot is built.
response_capabilities: ContextVar[frozenset[str]] = ContextVar(
    "response_capabilities",
    default=frozenset(),
)


def has_response_capability(name: str) -> bool:
    """Return whether the current client advertises one response feature."""
    return str(name or "").strip() in response_capabilities.get()


def active_workspace_dir() -> Path:
    """Return the workspace root for the current agent run.

    Falls back to the global ``WORKSPACE_DIR`` when no per‑run override is set
    (legacy chat, scheduler runs, or any agent outside a Workbench project)."""
    raw = _active_workspace_dir.get()
    if raw:
        return Path(raw).expanduser().resolve()
    return _WORKSPACE_DIR.resolve()

# Lazily populated cache of SessionContext instances, keyed by session_id.
# The default session (id="") is created on first access and lives forever.
_sessions: dict[str, SessionContext] = {}


def _ensure_session(session_id: str = "") -> SessionContext:
    """Return the ``SessionContext`` for *session_id*, creating it if needed."""
    global _session_epoch
    if session_id not in _sessions:
        if not session_id:
            state_file: Path | None = None  # signal "use DATA_DIR / state.json"
        else:
            state_file = _DATA_DIR / "sessions" / session_id / "state.json"
            state_file.parent.mkdir(parents=True, exist_ok=True)
        ctx = SessionContext(session_id=session_id, state_file=state_file)
        # The default session MUST share the existing module-level globals
        # so that code holding a reference to ``_agent_lock`` or importing
        # ``_interrupt_event`` from this module still works correctly.
        if not session_id:
            ctx.lock = _agent_lock
            ctx.session_state_lock = _session_state_lock
            ctx.session_epoch = _session_epoch
            ctx.interrupt_event = _interrupt_event
            ctx.pending_compressors = _pending_compressors
            ctx.pending_label_refreshes = _pending_label_refreshes
            ctx.pending_interrupt_clearers = _pending_interrupt_clearers
            ctx.pending_housekeeping = _pending_housekeeping
            ctx.main_inbox_worker = _main_inbox_worker
            ctx.active_main_round_id = _active_main_round_id
            ctx.active_main_round_prompt = _active_main_round_prompt
            ctx.active_main_round_public_prompt = _active_main_round_public_prompt
            ctx.active_main_round_started_at = _active_main_round_started_at
        _sessions[session_id] = ctx
    return _sessions[session_id]


def _get_session() -> SessionContext:
    """Return the ``SessionContext`` for the currently active session."""
    return _ensure_session(_current_session_id.get())


def _session_state_file(session_id: str = "") -> Path:
    """Return the state‑file path for *session_id*.

    The default session still reads from ``DATA_DIR / "state.json"`` for full
    backward compatibility.
    """
    ctx = _ensure_session(session_id)
    if ctx.state_file is not None:
        return ctx.state_file
    return STATE_FILE

# ---------------------------------------------------------------------------
# ContextVars — per-request state
# ---------------------------------------------------------------------------

_current_agent_id: ContextVar[str] = ContextVar("_current_agent_id", default="main")
_current_round_id: ContextVar[str] = ContextVar("_current_round_id", default="")
_current_client_request_id: ContextVar[str] = ContextVar("_current_client_request_id", default="")
_caller_type: ContextVar[str] = ContextVar("_caller_type", default="main_agent")
_persist_base_messages: ContextVar[list[dict[str, Any]] | None] = ContextVar("_persist_base_messages", default=None)
_persist_merge_live_state: ContextVar[bool] = ContextVar("_persist_merge_live_state", default=False)
_persist_history_prefix_len: ContextVar[int] = ContextVar("_persist_history_prefix_len", default=0)
_persist_insert_at: ContextVar[int | None] = ContextVar("_persist_insert_at", default=None)
_pending_intermediate_user_replies: ContextVar[list[dict[str, Any]] | None] = ContextVar("_pending_intermediate_user_replies", default=None)
_reply_stream_writer: ContextVar[Callable[[dict[str, Any]], Awaitable[None]] | None] = ContextVar("_reply_stream_writer", default=None)
_runtime_event_writer: ContextVar[Callable[[dict[str, Any]], Awaitable[None]] | None] = ContextVar("_runtime_event_writer", default=None)
# Usage dict of the most recent final-reply LLM call (streaming finals return
# plain text, so token usage would otherwise be lost before persisting).
_last_final_reply_usage: ContextVar[dict[str, Any] | None] = ContextVar("_last_final_reply_usage", default=None)

_ui_round_hide_initial_detail: ContextVar[bool] = ContextVar("_ui_round_hide_initial_detail", default=False)
_ui_round_assistant_meta: ContextVar[dict[str, Any] | None] = ContextVar("_ui_round_assistant_meta", default=None)
_deep_research_mode: ContextVar[bool] = ContextVar("_deep_research_mode", default=False)
_deep_research_first_round: ContextVar[bool] = ContextVar("_deep_research_first_round", default=False)
_economy_mode: ContextVar[bool] = ContextVar("_economy_mode", default=False)
_current_command: ContextVar[str] = ContextVar("_current_command", default="")
_conversation_source: ContextVar[str] = ContextVar("_conversation_source", default="")
# Exact public text supplied by the real caller for this run.  This is kept
# separate from ``_current_command`` because Workbench commands and internal
# run prompts are not evidence that a user delegated an approval or answer.
_user_request_text: ContextVar[str] = ContextVar("_user_request_text", default="")
# Single-use delegation quotes consumed by Cyrene self-management policy.  The
# set is initialized at ``run_agent`` entry so copied/background contexts cannot
# mint or recycle receipts across runs.
_explicit_delegation_receipts: ContextVar[set[str] | None] = ContextVar(
    "_explicit_delegation_receipts",
    default=None,
)
# Ordered, argument-bound operation batches approved from one explicit local
# user quote.  Each entry stores the immutable operation-key plan and the next
# index to consume; it is initialized and discarded with the surrounding run.
_explicit_delegation_batches: ContextVar[dict[str, dict[str, Any]] | None] = ContextVar(
    "_explicit_delegation_batches",
    default=None,
)
# Map from filename (and original name without uuid prefix) → full absolute path
# Populated by the chat route adapter when the user sends attachments.
# Allows tools to auto-resolve agent-guessed paths (e.g. /tmp/file.txt) to the
# correct webui_uploads path without requiring a permission prompt.
_attachment_paths_by_name: ContextVar[dict[str, str] | None] = ContextVar("_attachment_paths_by_name", default=None)

# ---------------------------------------------------------------------------
# Module-level shared state
# ---------------------------------------------------------------------------

_agent_lock = asyncio.Lock()
_session_state_lock = asyncio.Lock()
_session_epoch: int = 0
_interrupt_event = asyncio.Event()

_pending_compressors: set[asyncio.Task] = set()
_pending_label_refreshes: set[asyncio.Task] = set()
_pending_interrupt_clearers: set[asyncio.Task] = set()
_pending_housekeeping: set[asyncio.Task] = set()
_main_inbox_worker: asyncio.Task | None = None

_active_main_round_id = ""
_active_main_round_prompt = ""
_active_main_round_public_prompt = ""
_active_main_round_started_at = 0.0
# Explicit run-wide full_access marker.  Exact one-shot approvals use the
# fingerprint/path grant stores below and must never set this broad flag.
_temporary_full_access: ContextVar[bool] = ContextVar("_temporary_full_access", default=False)

# Exact, one-shot grants used when a human approves a single permission
# request.  Unlike ``_temporary_full_access`` these are bound to the request
# fingerprint and cannot authorize an unrelated tool/path.
_permission_elevation_grants: ContextVar[set[str] | None] = ContextVar(
    "_permission_elevation_grants",
    default=None,
)

# Exact path grants bridge a successful read/write elevation check to the
# resolver retry performed by the same tool call.  Entries are consumed on use.
_scoped_path_access_grants: ContextVar[set[str] | None] = ContextVar(
    "_scoped_path_access_grants",
    default=None,
)

# 破坏性/不可逆操作的二次确认与 full_access 解耦。单次确认使用
# fingerprint 避免同一工具重试时反复弹窗；"本次会话内总是允许" 使用
# allow_all，均随当前 async round 上下文结束而清理。
_destructive_confirmation_fingerprints: ContextVar[frozenset[str]] = ContextVar(
    "_destructive_confirmation_fingerprints",
    default=frozenset(),
)
_destructive_confirmation_allow_all: ContextVar[bool] = ContextVar(
    "_destructive_confirmation_allow_all",
    default=False,
)

# 外部网页文件上传始终需要真人逐次确认。授权以绑定页面目标与文件哈希的
# fingerprint 保存，并由上传工具在第一次执行时消费；auto/full_access
# 不能设置或绕过这组授权。
_external_upload_confirmation_fingerprints: ContextVar[frozenset[str]] = ContextVar(
    "_external_upload_confirmation_fingerprints",
    default=frozenset(),
)

# 本轮权限模式 —— 由 /api/chat 的 mode 字段决定，round 起始设置、结束重置。
#   "default"     —— 碰到权限边界时提问让用户授权（现状）
#   "full_access" —— 默认放行所有操作（round 起始时同时置 _temporary_full_access）
#   "auto"        —— 由审核 agent 自主裁决提权请求，从不打扰用户
#   "plan"        —— 先规划再执行（同意后回退默认模式）
PERMISSION_MODES = ("default", "full_access", "auto", "plan")
_permission_mode: ContextVar[str] = ContextVar("_permission_mode", default="default")
_bounded_remote_authorization: ContextVar[bool] = ContextVar(
    "_bounded_remote_authorization",
    default=False,
)
_llm_phase_override: ContextVar[str] = ContextVar("_llm_phase_override", default="")

_MAIN_INBOX_AGENT_ID = "main"
_AWAITING_USER_SENTINEL = "[[cyrene.awaiting_user]]"


def sanitize_public_agent_text(value: object) -> str:
    """Remove internal control sentinels before text crosses a public boundary.

    Callers must not rely on exact equality here: provider adapters or a model
    can surround a control value with whitespace, Markdown, or other text.
    """
    raw = str(value or "")
    cleaned = raw.replace(_AWAITING_USER_SENTINEL, "").strip()
    if _AWAITING_USER_SENTINEL in raw and not cleaned.strip("*_`~[](){}<> "):
        return ""
    return cleaned


_sanitize_public_agent_text = sanitize_public_agent_text

_REPORT_REF_PREFIX = "[Deep research report]"
_REPORT_REF_MAX_PREVIEW = 280

# ---------------------------------------------------------------------------
# Light tool defs — Phase 1 decision toolset
# ---------------------------------------------------------------------------

_LIGHT_TOOL_DEFS = [
    {"type": "function", "function": {"name": "use_tools", "description": "Gateway to execution. Use it for actions or when retrieval or verification materially improves the answer; stable low-risk facts and explanations may be answered directly. First make a bounded plan, then call this without an assistant preamble. Keep task equal to the user's exact original message and put the concise provisional plan in execution_brief.", "parameters": {"type": "object", "properties": {"task": {"type": "string", "description": "The user's exact original message, unchanged."}, "execution_brief": {"type": "string", "description": "Concise handoff with objective, acceptance evidence, constraints/assumptions, approach, initial steps/tools, validation, and material risks/fallbacks; no private chain-of-thought."}}, "required": ["task", "execution_brief"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "ask_user", "description": "Ask the user a clarification question. Use this proactively whenever: the request is ambiguous, a critical detail is missing, multiple approaches exist and the choice matters, or you need confirmation before a destructive/irreversible action. Guessing is worse than asking. If you need to ask the user anything, use this tool instead of writing a question in assistant text. Use freeform text, or add a short options array when structured choices help. Do not combine with other tools in the same turn.", "parameters": {"type": "object", "properties": {"text": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "quit", "description": "Terminal control signal. Call this only after writing the complete user-facing answer in normal assistant content. Do not put answer text or tool syntax in the arguments. A quit call ends the current run and never reopens tools.", "parameters": {"type": "object", "properties": {}}}},
]

_DEEP_RESEARCH_LIGHT_TOOL_DEFS = [
    {"type": "function", "function": {"name": "ask_user", "description": "Ask the user a clarification question. Use this to ask about the desired report length before starting research. Use freeform text, or add a short options array when structured choices help. Do not combine with other tools in the same turn.", "parameters": {"type": "object", "properties": {"text": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "quit", "description": "Terminal control signal. If the user cancels Deep Research, write any acknowledgement in normal assistant content and call quit with no answer text in its arguments.", "parameters": {"type": "object", "properties": {}}}},
]


# ---------------------------------------------------------------------------
# Session epoch (survives server restarts)
# ---------------------------------------------------------------------------

def _init_session_epoch() -> None:
    global _session_epoch
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                epoch = data.get("_session_epoch", 0)
                _session_epoch = epoch
                _ensure_session("").session_epoch = epoch
    except Exception:
        pass


_init_session_epoch()


# ---------------------------------------------------------------------------
# Runtime event helpers
# ---------------------------------------------------------------------------

_PUBLIC_RUN_EVENT_TYPES = frozenset({
    "auto_review",
    "phase_transition",
    "plan",
    "plan_progress",
    "permission_decision",
    "subagent_update",
    "tool_call_started",
    "tool_call_progress",
    "tool_call_finished",
    "user_question",
    "user_question_answered",
})


async def _publish_runtime_event(event: dict[str, Any]) -> None:
    round_id = _current_round_id.get()
    if round_id and not str(event.get("round_id", "")).strip():
        event = {**event, "round_id": round_id}
    session_id = _current_session_id.get()
    if session_id:
        event = {**event, "session_id": session_id}
    await debug.publish_event(event)
    writer = _runtime_event_writer.get()
    if writer is None or str(event.get("type") or "") not in _PUBLIC_RUN_EVENT_TYPES:
        return
    try:
        await writer(dict(event))
    except Exception:
        # Per-run live activity is presentation state. A disconnected client or
        # renderer must never turn an otherwise successful agent action into a
        # failed run.
        logger.debug("Failed to publish public run event", exc_info=True)


async def _emit_reply_stream_event(event: dict[str, Any]) -> None:
    writer = _reply_stream_writer.get()
    if writer is None:
        return
    await writer(dict(event))


def _streaming_reply_requested() -> bool:
    return _reply_stream_writer.get() is not None


# ---------------------------------------------------------------------------
# LLM call wrappers
# ---------------------------------------------------------------------------

def _llm_phase_name(tools: list | None) -> str:
    override = _llm_phase_override.get()
    if override:
        return override
    if tools is _LIGHT_TOOL_DEFS or tools is _DEEP_RESEARCH_LIGHT_TOOL_DEFS:
        return "phase1"
    return "phase2" if tools else "no_tools"


def _record_last_main_model_context(
    messages: list[dict[str, Any]],
    response: Any,
    *,
    secondary: bool,
) -> None:
    """Keep the exact provider-normalized main-Agent exchange in memory.

    Memory learning reads this snapshot directly while the run is active and a
    completed copy is persisted by the Workbench chat finalizer. Main-Agent tool
    schemas are deliberately excluded; the learner receives only its dedicated
    project-memory submission tool.
    """
    if secondary or _current_agent_id.get() != "main" or not isinstance(response, dict):
        return
    from cyrene.model_runtime.client import (
        sanitize_messages_for_llm,
        model_candidate_identity_for_response,
    )

    normalized = sanitize_messages_for_llm(
        copy.deepcopy(messages),
        materialize_internal_media=False,
    )
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": response.get("content") or "",
    }
    for key in ("reasoning_content", "tool_calls"):
        if response.get(key):
            assistant[key] = copy.deepcopy(response[key])
    normalized.extend(sanitize_messages_for_llm([assistant]))
    session_id = _current_session_id.get()
    ctx = _ensure_session(session_id)
    ctx.last_main_model_messages = normalized
    actual_identity = response.get("_candidate_identity")
    ctx.last_main_model_identity = (
        dict(actual_identity)
        if isinstance(actual_identity, dict)
        else model_candidate_identity_for_response(
            session_id, str(response.get("model") or "")
        )
    )
    ctx.last_main_model_round_id = _current_round_id.get()


def get_last_main_model_context(session_id: str = "") -> dict[str, Any] | None:
    """Return a defensive copy of the latest exact main-model exchange."""
    ctx = _ensure_session(str(session_id or ""))
    if not ctx.last_main_model_messages:
        return None
    return {
        "messages": copy.deepcopy(ctx.last_main_model_messages),
        "model": dict(ctx.last_main_model_identity),
        "roundId": str(ctx.last_main_model_round_id or ""),
    }


async def _call_llm(
    messages: list[dict],
    tools: list | None = None,
    max_tokens: int | None = None,
    *,
    candidates: list[dict] | None = None,
    secondary: bool = False,
    thinking: str = "auto",
    response_format: dict | None = None,
) -> dict:
    from cyrene.call_llm import call_llm as _unified_call_llm

    # Workbench owns a live stream writer for the duration of an agent run. Use
    # the upstream streaming transport for intermediate LLM calls as well, but
    # forward only reasoning events here: their content/tool deltas are internal
    # turns and must not be mistaken for the final user-facing reply.
    stream_writer = _reply_stream_writer.get()

    async def _forward_reasoning(event: dict[str, Any]) -> None:
        if stream_writer is not None and str(event.get("type") or "").startswith("reasoning_"):
            await stream_writer(event)

    response = await _unified_call_llm(
        messages,
        tools=tools,
        max_tokens=max_tokens,
        candidates=candidates,
        model_type="secondary" if secondary else "primary",
        thinking=thinking,
        response_format=response_format,
        stream=stream_writer is not None,
        stream_callback=_forward_reasoning if stream_writer is not None else None,
        caller=_caller_type.get(),
        phase=_llm_phase_name(tools),
        round_id=_current_round_id.get(),
        session_id=_current_session_id.get(),
    )
    _record_last_main_model_context(messages, response, secondary=secondary)
    return response


async def _call_llm_stream(
    messages: list[dict],
    max_tokens: int | None = None,
    *,
    secondary: bool = False,
    tools: list | None = None,
) -> dict[str, Any]:
    from cyrene.call_llm import call_llm as _unified_call_llm

    response = await _unified_call_llm(
        messages,
        max_tokens=max_tokens,
        model_type="secondary" if secondary else "primary",
        stream=True,
        stream_callback=_reply_stream_writer.get(),
        tools=tools,
        caller=_caller_type.get(),
        phase=_llm_phase_name(tools),
        round_id=_current_round_id.get(),
        session_id=_current_session_id.get(),
    )
    _record_last_main_model_context(messages, response, secondary=secondary)
    return response


# ---------------------------------------------------------------------------
# Quit tool handler
# ---------------------------------------------------------------------------

async def _tool_quit(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    return "Interaction ended."
