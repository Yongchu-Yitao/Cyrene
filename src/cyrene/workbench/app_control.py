"""Shared policy, result, idempotency and audit helpers for Cyrene tools."""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from cyrene.agent.context import current_run_context
from cyrene.config import DATA_DIR
from cyrene.tooling.policy.approvals import (
    request_destructive_confirmation,
    request_host_lifecycle_confirmation,
    request_self_configuration_confirmation,
)
from cyrene.workbench.app_operations import OPERATION_BY_ID

_AUDIT_PATH = DATA_DIR / "app_control_audit.jsonl"
_IDEMPOTENCY_PATH = DATA_DIR / "app_control_idempotency.json"
_STATE_LOCK = threading.RLock()
_AUTHORIZATION_DECISIONS: ContextVar[dict[str, dict[str, str]] | None] = ContextVar(
    "_cyrene_authorization_decisions",
    default=None,
)

DELEGATION_OPERATIONS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "minItems": 2,
    "maxItems": 12,
    "description": (
        "Optional ordered batch of exact R2/R3 Cyrene operations authorized by "
        "the same delegation_quote. Every later call must repeat this identical list."
    ),
    "items": {
        "type": "object",
        "properties": {
            "operation_id": {"type": "string", "maxLength": 160},
            "arguments": {"type": "object"},
        },
        "required": ["operation_id", "arguments"],
        "additionalProperties": False,
    },
}


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
    context = current_run_context()
    argument_hash = canonical_hash(operation_id, arguments)
    decision = (_AUTHORIZATION_DECISIONS.get() or {}).get(argument_hash, {})
    audit_id = f"audit_{uuid.uuid4().hex}"
    record = {
        "audit_id": audit_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "operation_id": operation_id,
        "schema_version": 1,
        "actor_type": context.caller,
        "actor_id": context.agent_id,
        "conversation_source": context.conversation_source,
        "session_id": context.session_id,
        "round_id": context.round_id,
        "argument_hash": argument_hash,
        "arguments": _redact(arguments),
        "diff": _redact(diff or {}),
        "risk": risk,
        "status": status,
        "error_code": str(error_code or ""),
        "decision_source": str(decision.get("source") or "policy"),
        "delegation_receipt": str(decision.get("receipt") or ""),
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
    delegation_quote: str = "",
    delegation_operations: list[dict[str, Any]] | None = None,
) -> str | None:
    from cyrene.agent.context import publish_runtime_event

    spec = OPERATION_BY_ID.get(operation_id)
    if spec is None:
        return "Tool unavailable: unclassified Cyrene operation."
    context = current_run_context()
    await publish_runtime_event({
        "type": "cyrene_operation_requested",
        "operation_id": operation_id,
        "argument_hash": canonical_hash(operation_id, arguments),
        "risk": spec.risk,
    })
    # `use_tools` deliberately changes the caller marker while the main
    # agent's execution phase is running.  It is still the main actor (the
    # gateway passes actor="main" and subagents never receive this pack), so
    # accept that exact internal phase while rejecting every other helper,
    # compactor, remote harness and subagent caller.
    if context.agent_id != "main" or context.caller not in {
        "main_agent", "execution_agent", "main",
    }:
        return "Tool unavailable: Cyrene self-management is main-agent only."
    if spec.risk == "R4" or spec.exposure == "forbidden":
        return "Tool unavailable: this Cyrene self-management operation is permanently forbidden."
    if spec.risk != "R0" and not str(reason or "").strip():
        return "Tool unavailable: a user-grounded reason is required for this change."
    fingerprint = canonical_hash(operation_id, arguments)
    # The desktop client request ID is useful correlation metadata, but older
    # and renderer-originated local turns may legitimately omit it.  The bound
    # session + round still provides a trusted, run-local identity.  Also make
    # the full current user request the reviewer candidate when the model did
    # not repeat an exact quote; semantic authorization remains owned by the
    # permission-review agent and still fails closed.
    effective_delegation_quote = str(delegation_quote or "").strip()
    if not effective_delegation_quote:
        effective_delegation_quote = str(context.user_request_text or "").strip()
    delegated_receipt = await _consume_explicit_user_delegation(
        operation_id,
        fingerprint,
        arguments,
        reason,
        effective_delegation_quote,
        delegation_operations,
    ) if spec.risk in {"R2", "R3"} else ""
    if spec.risk == "R2" and not delegated_receipt:
        approval = await request_self_configuration_confirmation(
            tool_name=spec.capability_id or operation_id,
            operation=operation_id,
            fingerprint=fingerprint,
            reason=reason,
        )
        if approval:
            return approval
    if spec.risk == "R3" and not delegated_receipt:
        if context.conversation_source != "desktop_local":
            return "Tool unavailable: destructive Cyrene operations require local desktop approval."
        if operation_id in {"cyrene.app.lifecycle", "cyrene.update.install"}:
            approval = await request_host_lifecycle_confirmation(
                tool_name=spec.capability_id or operation_id,
                operation=operation_id,
                fingerprint=fingerprint,
                reason=reason,
            )
        else:
            approval = await request_destructive_confirmation(
                tool_name=spec.capability_id or operation_id,
                operation=operation_id,
                detail=reason,
                destructive_kind=operation_id,
                meta_extra={"canonical_parameter_hash": fingerprint},
                single_use=True,
            )
        if approval:
            return approval
    if spec.risk == "R1" and context.conversation_source not in {"desktop_local", "webui"}:
        return "Tool unavailable: current-app UI changes require a local Workbench turn."
    decision_source = "permission_reviewer_delegation" if delegated_receipt else "policy"
    decisions = dict(_AUTHORIZATION_DECISIONS.get() or {})
    decisions[fingerprint] = {
        "source": decision_source,
        "receipt": delegated_receipt,
    }
    _AUTHORIZATION_DECISIONS.set(decisions)
    await publish_runtime_event({
        "type": "cyrene_operation_approved",
        "operation_id": operation_id,
        "argument_hash": fingerprint,
        "risk": spec.risk,
        "decision_source": decision_source,
        "delegation_receipt": delegated_receipt,
    })
    return None


async def _consume_explicit_user_delegation(
    operation_id: str,
    argument_hash: str,
    arguments: dict[str, Any],
    reason: str,
    quote: str,
    delegation_operations: list[dict[str, Any]] | None,
) -> str:
    """Review and consume a one-shot receipt from the real local user turn.

    The candidate is either the model's exact quote or, when omitted, the whole
    current local-user request. Forwarded agent text, remote/system turns and
    semantically unrelated statements cannot authorize anything. A reviewed
    candidate mints either one single-operation receipt or one exact, ordered
    operation-list batch; it cannot mint a second plan. Semantic intent is
    decided by the existing permission-review agent rather than a fixed word
    list.
    """
    from cyrene.agent.auto_review import review_user_delegation
    from cyrene.agent.context import (
        consume_explicit_delegation_batch,
        consume_explicit_delegation_receipt,
        explicit_delegation_batch_status,
        grant_explicit_delegation_batch,
        publish_runtime_event,
    )

    context = current_run_context()
    raw_quote = str(quote or "").strip()
    user_request = str(context.user_request_text or "")
    if (
        not raw_quote
        or raw_quote not in user_request
        or context.conversation_source != "desktop_local"
        or context.bounded_remote_authorization
        or not context.round_id
    ):
        return ""

    if delegation_operations is None:
        operations = ({"operation_id": operation_id, "arguments": dict(arguments)},)
    else:
        if not isinstance(delegation_operations, list) or not 2 <= len(delegation_operations) <= 12:
            return ""
        normalized: list[dict[str, Any]] = []
        for item in delegation_operations:
            if not isinstance(item, dict) or set(item) != {"operation_id", "arguments"}:
                return ""
            item_operation_id = str(item.get("operation_id") or "").strip()
            item_arguments = item.get("arguments")
            item_spec = OPERATION_BY_ID.get(item_operation_id)
            if (
                item_spec is None
                or item_spec.risk not in {"R2", "R3"}
                or item_spec.exposure == "forbidden"
                or not isinstance(item_arguments, dict)
            ):
                return ""
            normalized.append({
                "operation_id": item_operation_id,
                "arguments": dict(item_arguments),
            })
        operations = tuple(normalized)

    operation_keys = tuple(
        canonical_hash(str(item["operation_id"]), dict(item["arguments"]))
        for item in operations
    )
    current_operation_key = canonical_hash(operation_id, arguments)
    batch_plan_hash = hashlib.sha256(json.dumps(
        [{"operation_id": item["operation_id"], "argument_hash": key}
         for item, key in zip(operations, operation_keys)],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    batch_id = "delegation_batch_" + hashlib.sha256(json.dumps({
        "client_request_id": context.client_request_id,
        "round_id": context.round_id,
        "session_id": context.session_id,
        "quote": raw_quote,
        "plan_hash": batch_plan_hash,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:32]
    quote_identity = hashlib.sha256(json.dumps({
        "client_request_id": context.client_request_id,
        "round_id": context.round_id,
        "quote": raw_quote,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    batch_status = explicit_delegation_batch_status(batch_id, operation_keys)
    approved = False
    rationale = ""
    if batch_status == "ready":
        approved = True
        rationale = "该精确操作已包含在本轮已审核的批量票据中。"
    elif batch_status == "missing":
        if not operation_keys or current_operation_key != operation_keys[0]:
            return ""
        operations_json = json.dumps(
            [{
                "operation_id": item["operation_id"],
                "arguments": _redact(item["arguments"]),
            } for item in operations],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        approved, rationale = await review_user_delegation(
            user_request=user_request,
            delegation_quote=raw_quote,
            operations_json=operations_json,
            reason=reason,
        )
        if approved:
            approved = bool(
                consume_explicit_delegation_receipt(quote_identity)
                and grant_explicit_delegation_batch(batch_id, operation_keys)
            )
            if not approved:
                rationale = "该用户授权引用已经用于本轮的另一项操作批次。"
    else:
        rationale = "批量票据已耗尽或与原始操作列表不一致。"
    consumed_position = (
        consume_explicit_delegation_batch(
            batch_id,
            operation_keys,
            current_operation_key,
        )
        if approved else 0
    )
    await publish_runtime_event({
        "type": "auto_review",
        "approved": bool(consumed_position),
        "operation": operation_id,
        "tool_name": operation_id,
        "permission_kind": "explicit_user_delegation",
        "path_hint": "",
        "fingerprint": argument_hash,
        "source": "permission_reviewer",
        "rationale": rationale if consumed_position else (
            rationale or "当前操作不是批量票据中的下一个精确操作。"
        ),
        "delegation_batch_id": batch_id,
        "delegation_batch_position": consumed_position,
        "delegation_batch_size": len(operation_keys),
        "round_id": context.round_id,
    })
    if not consumed_position:
        return ""
    receipt_seed = json.dumps({
        "client_request_id": context.client_request_id,
        "round_id": context.round_id,
        "session_id": context.session_id,
        "quote": raw_quote,
        "operation_id": operation_id,
        "argument_hash": argument_hash,
        "batch_id": batch_id,
        "batch_position": consumed_position,
        "batch_size": len(operation_keys),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "delegation_" + hashlib.sha256(receipt_seed.encode("utf-8")).hexdigest()[:32]


async def publish_result(result: dict[str, Any]) -> None:
    """Publish a secret-free status summary for local observability."""
    from cyrene.agent.context import publish_runtime_event

    status = str(result.get("status") or "error")
    await publish_runtime_event({
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
    """Return the receipt metadata recorded by the matching authorization."""
    return dict((_AUTHORIZATION_DECISIONS.get() or {}).get(
        canonical_hash(operation_id, arguments),
        {},
    ))


__all__ = [
    "DELEGATION_OPERATIONS_SCHEMA", "audit", "authorization_decision", "authorize", "canonical_hash", "envelope", "publish_result", "remember_idempotent",
    "replay_idempotent",
]
