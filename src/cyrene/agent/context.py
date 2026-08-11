"""Public query and binding API for agent run context.

Consumers outside ``cyrene.agent`` must not reach into the ContextVars in
``agent.state`` directly.  This module exposes immutable snapshots, focused
queries, and resettable bindings while the implementation remains compatible
with the existing agent loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any

from cyrene.agent import state as _state

_UNSET = object()
AWAITING_USER_SENTINEL = _state._AWAITING_USER_SENTINEL
MAIN_AGENT_ID = _state._MAIN_INBOX_AGENT_ID


@dataclass(frozen=True, slots=True)
class AgentRunContext:
    agent_id: str
    caller: str
    client_request_id: str
    command: str
    conversation_source: str
    round_id: str
    session_id: str
    permission_mode: str
    response_capabilities: frozenset[str]
    deep_research: bool
    temporary_full_access: bool
    bounded_remote_authorization: bool


class ContextBinding:
    """A set of ContextVar assignments that can be reset exactly once."""

    def __init__(self, tokens: list[tuple[Any, Any]]):
        self._tokens = tokens

    def reset(self) -> None:
        tokens, self._tokens = self._tokens, []
        for variable, token in reversed(tokens):
            variable.reset(token)

    def __enter__(self) -> ContextBinding:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.reset()


def current_run_context() -> AgentRunContext:
    """Return an immutable view of the active run-local values."""
    return AgentRunContext(
        agent_id=str(_state._current_agent_id.get() or "main"),
        caller=str(_state._caller_type.get() or "main_agent"),
        client_request_id=str(_state._current_client_request_id.get() or ""),
        command=str(_state._current_command.get() or ""),
        conversation_source=str(_state._conversation_source.get() or ""),
        round_id=str(_state._current_round_id.get() or ""),
        session_id=str(_state._current_session_id.get() or ""),
        permission_mode=str(_state._permission_mode.get() or "default"),
        response_capabilities=frozenset(_state.response_capabilities.get()),
        deep_research=bool(_state._deep_research_mode.get()),
        temporary_full_access=bool(_state._temporary_full_access.get()),
        bounded_remote_authorization=bool(
            _state._bounded_remote_authorization.get()
        ),
    )


def bind_run_context(
    *,
    agent_id: object = _UNSET,
    caller: object = _UNSET,
    client_request_id: object = _UNSET,
    command: object = _UNSET,
    conversation_source: object = _UNSET,
    round_id: object = _UNSET,
    session_id: object = _UNSET,
    workspace_dir: object = _UNSET,
    permission_mode: object = _UNSET,
    response_capabilities: object = _UNSET,
    deep_research: object = _UNSET,
    temporary_full_access: object = _UNSET,
    bounded_remote_authorization: object = _UNSET,
    destructive_confirmation_allow_all: object = _UNSET,
    assistant_meta: object = _UNSET,
    attachment_paths: object = _UNSET,
    reply_stream_writer: object = _UNSET,
    runtime_event_writer: object = _UNSET,
) -> ContextBinding:
    """Bind selected run values and return an idempotent reset handle."""
    assignments = (
        (_state._current_agent_id, agent_id),
        (_state._caller_type, caller),
        (_state._current_client_request_id, client_request_id),
        (_state._current_command, command),
        (_state._conversation_source, conversation_source),
        (_state._current_round_id, round_id),
        (_state._current_session_id, session_id),
        (_state._active_workspace_dir, workspace_dir),
        (_state._permission_mode, permission_mode),
        (_state.response_capabilities, response_capabilities),
        (_state._deep_research_mode, deep_research),
        (_state._temporary_full_access, temporary_full_access),
        (_state._bounded_remote_authorization, bounded_remote_authorization),
        (_state._destructive_confirmation_allow_all, destructive_confirmation_allow_all),
        (_state._ui_round_assistant_meta, assistant_meta),
        (_state._attachment_paths_by_name, attachment_paths),
        (_state._reply_stream_writer, reply_stream_writer),
        (_state._runtime_event_writer, runtime_event_writer),
    )
    tokens: list[tuple[Any, Any]] = []
    try:
        for variable, value in assignments:
            if value is not _UNSET:
                tokens.append((variable, variable.set(value)))
    except BaseException:
        ContextBinding(tokens).reset()
        raise
    return ContextBinding(tokens)


def with_run_context(**values: object):
    """Decorate an async entry point with a resettable run-context binding."""
    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            with bind_run_context(**values):
                return await function(*args, **kwargs)

        return wrapped

    return decorate


def current_agent_id() -> str:
    return current_run_context().agent_id


def current_caller() -> str:
    return current_run_context().caller


def current_client_request_id() -> str:
    return current_run_context().client_request_id


def current_command() -> str:
    return current_run_context().command


def current_conversation_source() -> str:
    return current_run_context().conversation_source


def current_round_id() -> str:
    return current_run_context().round_id


def current_session_id() -> str:
    return current_run_context().session_id


# Verbose getter aliases avoid shadowing common local variables such as
# ``current_session_id`` in adapters while keeping the concise query API.
get_current_agent_id = current_agent_id
get_current_caller = current_caller
get_current_client_request_id = current_client_request_id
get_current_command = current_command
get_current_conversation_source = current_conversation_source
get_current_round_id = current_round_id
get_current_session_id = current_session_id


def current_permission_mode() -> str:
    return current_run_context().permission_mode


def is_permission_mode(value: str) -> bool:
    return str(value or "").strip().lower() in _state.PERMISSION_MODES


def deep_research_enabled() -> bool:
    return current_run_context().deep_research


def has_temporary_full_access() -> bool:
    return current_run_context().temporary_full_access


def grant_temporary_full_access() -> None:
    _state._temporary_full_access.set(True)


def _add_one_shot_grant(variable: Any, value: str) -> None:
    normalized = str(value or "").strip()
    if not normalized:
        return
    existing = variable.get()
    if existing is None:
        existing = set()
        variable.set(existing)
    existing.add(normalized)


def _consume_one_shot_grant(variable: Any, value: str) -> bool:
    normalized = str(value or "").strip()
    existing = variable.get()
    if existing is None:
        return False
    if not normalized or normalized not in existing:
        return False
    existing.remove(normalized)
    return True


def grant_permission_elevation(fingerprint: str) -> None:
    """Grant one exact permission request for the current run."""
    _add_one_shot_grant(_state._permission_elevation_grants, fingerprint)


def permission_elevation_fingerprint(
    *,
    tool_name: str,
    permission_kind: str,
    path_hint: str,
    operation: str,
    reason: str = "",
) -> str:
    """Return the stable identity of one exact permission request."""
    payload = json.dumps(
        {
            "tool": str(tool_name or "").strip(),
            "kind": str(permission_kind or "").strip(),
            "path": str(path_hint or "").strip(),
            "operation": str(operation or "").strip(),
            "reason": str(reason or "").strip(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def consume_permission_elevation(fingerprint: str) -> bool:
    """Consume a previously approved exact permission request."""
    return _consume_one_shot_grant(
        _state._permission_elevation_grants,
        fingerprint,
    )


def _scoped_path_key(access_kind: str, path: str | Path) -> str:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = active_workspace_dir() / candidate
    return f"{str(access_kind or '').strip().lower()}:{candidate.resolve()}"


def grant_scoped_path_access(access_kind: str, path: str | Path) -> None:
    """Grant one exact canonical path lookup for the current tool execution."""
    _add_one_shot_grant(
        _state._scoped_path_access_grants,
        _scoped_path_key(access_kind, path),
    )


def consume_scoped_path_access(access_kind: str, path: str | Path) -> bool:
    """Consume an exact canonical path grant."""
    return _consume_one_shot_grant(
        _state._scoped_path_access_grants,
        _scoped_path_key(access_kind, path),
    )


def allow_all_destructive_operations_for_run() -> None:
    _state._destructive_confirmation_allow_all.set(True)


def grant_destructive_operation(fingerprint: str) -> None:
    normalized = str(fingerprint or "").strip()
    if not normalized:
        return
    existing = set(_state._destructive_confirmation_fingerprints.get())
    existing.add(normalized)
    _state._destructive_confirmation_fingerprints.set(frozenset(existing))


def grant_external_upload(fingerprint: str) -> None:
    normalized = str(fingerprint or "").strip()
    if not normalized:
        return
    existing = set(_state._external_upload_confirmation_fingerprints.get())
    existing.add(normalized)
    _state._external_upload_confirmation_fingerprints.set(frozenset(existing))


def current_assistant_meta() -> dict[str, Any] | None:
    value = _state._ui_round_assistant_meta.get()
    return dict(value) if isinstance(value, dict) else None


def current_attachment_paths() -> dict[str, str] | None:
    value = _state._attachment_paths_by_name.get()
    return dict(value) if isinstance(value, dict) else None


def take_pending_intermediate_replies() -> list[dict[str, Any]]:
    pending = _state._pending_intermediate_user_replies.get()
    if not pending:
        return []
    replies = [dict(item) for item in pending if isinstance(item, dict)]
    pending.clear()
    return replies


def append_pending_intermediate_reply(entry: dict[str, Any]) -> bool:
    pending = _state._pending_intermediate_user_replies.get()
    if pending is None:
        return False
    pending.append(dict(entry))
    return True


def active_round_prompt(*, public: bool = True) -> str:
    if public and _state._active_main_round_public_prompt:
        return str(_state._active_main_round_public_prompt)
    return str(_state._active_main_round_prompt or "")


def set_attachment_paths(paths: dict[str, str] | None) -> None:
    _state._attachment_paths_by_name.set(dict(paths) if paths is not None else None)


def has_external_upload_grant(fingerprint: str) -> bool:
    return str(fingerprint or "").strip() in _state._external_upload_confirmation_fingerprints.get()


def consume_external_upload_grant(fingerprint: str) -> bool:
    normalized = str(fingerprint or "").strip()
    existing = set(_state._external_upload_confirmation_fingerprints.get())
    if normalized not in existing:
        return False
    existing.remove(normalized)
    _state._external_upload_confirmation_fingerprints.set(frozenset(existing))
    return True


def has_destructive_confirmation(fingerprint: str) -> bool:
    normalized = str(fingerprint or "").strip()
    return bool(
        _state._destructive_confirmation_allow_all.get()
        or normalized in _state._destructive_confirmation_fingerprints.get()
    )


def active_workspace_dir() -> Path:
    return _state.active_workspace_dir()


def workspace_override() -> str:
    return str(_state._active_workspace_dir.get() or "")


def session_state_file(session_id: str = "") -> Path:
    return _state._session_state_file(session_id)


def session_interrupt_event(session_id: str = "") -> asyncio.Event:
    # Keep the default-session compatibility point observable to existing
    # integrations and tests that replace the module-level event.
    if not session_id:
        return _state._interrupt_event
    return _state._ensure_session(session_id).interrupt_event


def default_agent_lock() -> asyncio.Lock:
    """Return the compatibility lock for operations that pause the main agent."""
    return _state._agent_lock


def default_session_state_lock() -> asyncio.Lock:
    """Return the compatibility lock protecting default-session persistence."""
    return _state._session_state_lock


def current_session_state_lock() -> asyncio.Lock:
    """Return the persistence lock owned by the active session."""
    return _state._get_session().session_state_lock


async def publish_runtime_event(event: dict[str, Any]) -> None:
    await _state._publish_runtime_event(event)


async def emit_reply_stream_event(event: dict[str, Any]) -> None:
    await _state._emit_reply_stream_event(event)


__all__ = [
    "AgentRunContext",
    "AWAITING_USER_SENTINEL",
    "ContextBinding",
    "MAIN_AGENT_ID",
    "active_round_prompt",
    "active_workspace_dir",
    "allow_all_destructive_operations_for_run",
    "append_pending_intermediate_reply",
    "bind_run_context",
    "current_agent_id",
    "current_assistant_meta",
    "current_attachment_paths",
    "current_caller",
    "current_client_request_id",
    "current_command",
    "current_conversation_source",
    "current_permission_mode",
    "current_round_id",
    "current_run_context",
    "current_session_id",
    "current_session_state_lock",
    "deep_research_enabled",
    "default_agent_lock",
    "default_session_state_lock",
    "emit_reply_stream_event",
    "grant_destructive_operation",
    "grant_external_upload",
    "grant_permission_elevation",
    "grant_scoped_path_access",
    "grant_temporary_full_access",
    "get_current_agent_id",
    "get_current_caller",
    "get_current_client_request_id",
    "get_current_command",
    "get_current_conversation_source",
    "get_current_round_id",
    "get_current_session_id",
    "has_external_upload_grant",
    "has_destructive_confirmation",
    "has_temporary_full_access",
    "consume_permission_elevation",
    "consume_scoped_path_access",
    "is_permission_mode",
    "permission_elevation_fingerprint",
    "consume_external_upload_grant",
    "publish_runtime_event",
    "session_interrupt_event",
    "session_state_file",
    "set_attachment_paths",
    "take_pending_intermediate_replies",
    "workspace_override",
    "with_run_context",
]
