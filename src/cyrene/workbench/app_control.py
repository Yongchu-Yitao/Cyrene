"""Shared policy, result, idempotency and audit helpers for Cyrene tools."""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from agent.plugin.execution import current_plugin_execution
from agent.plugin.native_runtime import publish_runtime_event, run_context_value
from cyrene.config import DATA_DIR
from cyrene.workbench.app_operations import OPERATION_BY_ID

_AUDIT_PATH = DATA_DIR / "app_control_audit.jsonl"
_IDEMPOTENCY_PATH = DATA_DIR / "app_control_idempotency.json"
_STATE_LOCK = threading.RLock()


def _active_context():
    execution = current_plugin_execution()
    return execution.context if execution is not None else None


def _context_value(name: str, default: Any = "") -> Any:
    context = _active_context()
    if context is None:
        return default
    return run_context_value(context, name, default)


async def _publish(event: dict[str, Any]) -> None:
    context = _active_context()
    if context is not None:
        await publish_runtime_event(context, event)


def canonical_hash(operation_id: str, arguments: dict[str, Any]) -> str:
    payload = json.dumps(
        {"operation_id": operation_id, "arguments": arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def envelope(
    status: str,
    operation_id: str,
    summary: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "status": status,
        "operation_id": operation_id,
        "summary": str(summary or ""),
        "revision": extra.pop("revision", None),
        "apply_mode": extra.pop("apply_mode", "immediate"),
        "restart_required": bool(extra.pop("restart_required", False)),
        "action_id": str(extra.pop("action_id", "") or ""),
        "audit_id": str(extra.pop("audit_id", "") or ""),
        "effects": extra.pop("effects", []),
        "next_valid_actions": extra.pop("next_valid_actions", []),
        **extra,
    }


def audit(
    operation_id: str,
    arguments: dict[str, Any],
    *,
    status: str,
    risk: str,
    diff: dict[str, Any] | None = None,
    error_code: str = "",
) -> str:
    argument_hash = canonical_hash(operation_id, arguments)
    audit_id = f"audit_{uuid.uuid4().hex}"
    record = {
        "audit_id": audit_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "operation_id": operation_id,
        "schema_version": 1,
        "actor_type": str(_context_value("caller", "main_agent") or "main_agent"),
        "actor_id": str(_context_value("agent_id", "main") or "main"),
        "conversation_source": str(_context_value("conversation_source") or ""),
        "session_id": str(_context_value("session_id") or ""),
        "round_id": str(
            _context_value("round_id") or _context_value("run_id") or ""
        ),
        "argument_hash": argument_hash,
        "arguments": _redact(arguments),
        "diff": _redact(diff or {}),
        "risk": risk,
        "status": status,
        "error_code": str(error_code or ""),
        "decision_source": "plugin_pre_tool_review",
        "delegation_receipt": "",
    }
    with _STATE_LOCK:
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return audit_id


def _redact(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if any(token in lowered for token in ("secret", "token", "password", "api_key", "private_key", "auth")):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    if isinstance(value, str) and len(value) > 4000:
        return value[:4000] + "…"
    return value


def _load_idempotency() -> dict[str, Any]:
    try:
        value = json.loads(_IDEMPOTENCY_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def replay_idempotent(operation_id: str, key: str, argument_hash: str) -> dict[str, Any] | None:
    if not key:
        return None
    with _STATE_LOCK:
        entry = _load_idempotency().get(f"{operation_id}:{key}")
    if not isinstance(entry, dict):
        return None
    if entry.get("argument_hash") != argument_hash:
        return envelope("error", operation_id, "Idempotency key was reused with different arguments.", error_code="idempotency_conflict")
    result = entry.get("result")
    return dict(result) if isinstance(result, dict) else None


def remember_idempotent(operation_id: str, key: str, argument_hash: str, result: dict[str, Any]) -> None:
    if not key:
        return
    with _STATE_LOCK:
        state = _load_idempotency()
        state[f"{operation_id}:{key}"] = {
            "argument_hash": argument_hash,
            "result": _redact(result),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _IDEMPOTENCY_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _IDEMPOTENCY_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_IDEMPOTENCY_PATH)


async def authorize(
    operation_id: str,
    arguments: dict[str, Any],
    *,
    reason: str,
) -> str | None:
    """Apply deterministic application guards after Plugin PreToolUse review.

    The Plugin Runtime's PreToolUse Hook is the only semantic approval path.
    This function deliberately does not create pending questions, mint
    ContextVar receipts, or call a second reviewer. It only enforces invariant
    application boundaries that no model decision may widen.

    """

    spec = OPERATION_BY_ID.get(operation_id)
    if spec is None:
        return "Tool unavailable: unclassified Cyrene operation."
    if _active_context() is None:
        return "Tool unavailable: Cyrene operations require the Plugin Runtime."

    fingerprint = canonical_hash(operation_id, arguments)
    await _publish({
        "type": "cyrene_operation_requested",
        "operation_id": operation_id,
        "argument_hash": fingerprint,
        "risk": spec.risk,
    })
    agent_id = str(_context_value("agent_id", "main") or "main")
    caller = str(_context_value("caller", "main_agent") or "main_agent")
    source = str(_context_value("conversation_source") or "")
    if agent_id != "main" or caller not in {"main_agent", "main"}:
        return "Tool unavailable: Cyrene self-management is main-agent only."
    if "main" not in spec.actors:
        return "Tool unavailable: this operation is not Agent-callable."
    if spec.risk == "R4" or spec.exposure == "forbidden":
        return "Tool unavailable: this Cyrene self-management operation is permanently forbidden."
    if spec.risk != "R0" and not str(reason or "").strip():
        return "Tool unavailable: a user-grounded reason is required for this change."
    if spec.risk == "R1" and source not in {"desktop_local", "webui"}:
        return "Tool unavailable: current-app UI changes require a local Workbench turn."
    if spec.risk in {"R2", "R3"} and source != "desktop_local":
        return (
            "Tool unavailable: privileged Cyrene operations require a local "
            "desktop Workbench turn."
        )
    await _publish({
        "type": "cyrene_operation_approved",
        "operation_id": operation_id,
        "argument_hash": fingerprint,
        "risk": spec.risk,
        "decision_source": "plugin_pre_tool_review",
        "round_id": str(
            _context_value("round_id") or _context_value("run_id") or ""
        ),
    })
    return None


async def publish_result(result: dict[str, Any]) -> None:
    """Publish a secret-free status summary for local observability."""
    status = str(result.get("status") or "error")
    await _publish({
        "type": (
            "cyrene_operation_completed"
            if status in {"success", "scheduled"}
            else "cyrene_operation_failed"
        ),
        "operation_id": str(result.get("operation_id") or ""),
        "status": status,
        "audit_id": str(result.get("audit_id") or ""),
        "action_id": str(result.get("action_id") or ""),
        "revision": result.get("revision"),
        "error_code": str(result.get("error_code") or ""),
    })


def authorization_decision(operation_id: str, arguments: dict[str, Any]) -> dict[str, str]:
    """Return the deterministic receipt produced by Plugin PreToolUse review."""

    fingerprint = canonical_hash(operation_id, arguments)
    return {
        "source": "plugin_pre_tool_review",
        "receipt": fingerprint,
    }


__all__ = [
    "audit", "authorization_decision", "authorize", "canonical_hash", "envelope", "publish_result", "remember_idempotent",
    "replay_idempotent",
]
