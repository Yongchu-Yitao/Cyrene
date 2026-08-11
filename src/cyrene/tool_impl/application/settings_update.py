from __future__ import annotations

from typing import Any

from cyrene.runtime import config_store
from cyrene.runtime.host_bridge import HostBridgeError, call_host
from cyrene.runtime.settings_service import (
    SPEC_BY_KEY,
    SettingsServiceError,
    update,
    validate_changes,
)
from cyrene.tooling.runtime_api import json_result
from cyrene.workbench.app_control import DELEGATION_OPERATIONS_SCHEMA, audit, authorize, canonical_hash, envelope, publish_result, remember_idempotent, replay_idempotent

TOOL_NAME = "CyreneSettingsUpdate"
TOOL_DEF = {"type": "function", "function": {
    "name": TOOL_NAME,
    "description": "Atomically patch one typed Cyrene settings namespace with revision/CAS protection. Shortcut maps preserve unspecified actions; use null only to reset an explicitly named action.",
    "parameters": {
        "type": "object",
        "properties": {
            "namespace": {"type": "string", "enum": ["runtime", "desktop", "appearance", "profile", "shortcuts"]},
            "changes": {"type": "object", "minProperties": 1, "maxProperties": 30},
            "expected_revision": {"type": "integer", "minimum": 0},
            "reason": {"type": "string", "minLength": 1, "maxLength": 500},
            "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 160},
            "delegation_quote": {"type": "string", "maxLength": 500},
            "delegation_operations": DELEGATION_OPERATIONS_SCHEMA,
        },
        "required": ["namespace", "changes", "expected_revision", "reason", "idempotency_key"],
        "additionalProperties": False,
    },
}}
TOOL_METADATA = {"read_only": False, "resource_keys": ("cyrene:settings",), "requires_order": True}


def _operation_for_changes(changes: dict[str, Any]) -> tuple[str, frozenset[str]]:
    risks = {SPEC_BY_KEY[key].risk for key in changes if key in SPEC_BY_KEY}
    if "shortcut_bindings" in changes:
        return "cyrene.settings.shortcuts", frozenset({"R2"})
    if "enabled_tool_packs" in changes or "enabled_tools" in changes:
        return "cyrene.settings.capabilities", frozenset({"R2"})
    if any(key.startswith("subagent_") or key in {"agent_proactive", "spawn_policy"} for key in changes):
        return "cyrene.settings.agent", frozenset({"R2"})
    if "R2" in risks:
        return "cyrene.settings.global", frozenset({"R2"})
    return "cyrene.settings.update", frozenset()


async def handler(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify: Any) -> str:
    namespace = str(args.get("namespace") or "")
    changes = dict(args.get("changes") or {})
    operation_id, approved_risks = _operation_for_changes(changes)
    op_args = {"namespace": namespace, "changes": changes, "expected_revision": args.get("expected_revision")}
    key = str(args.get("idempotency_key") or "")
    fingerprint = canonical_hash(operation_id, op_args)
    replay = replay_idempotent(operation_id, key, fingerprint)
    if replay is not None:
        return json_result(replay)
    approval = await authorize(
        operation_id, op_args,
        reason=str(args.get("reason") or ""),
        delegation_quote=str(args.get("delegation_quote") or ""),
        delegation_operations=args.get("delegation_operations"),
    )
    if approval:
        return approval
    try:
        if namespace == "desktop":
            normalized, _specs = validate_changes(
                "desktop", changes, actor="agent", approved_risks=approved_risks,
            )
            host_result = await call_host(
                "desktop.settings.update",
                {
                    "changes": normalized,
                    "expectedRevision": int(args["expected_revision"]),
                },
            )
            if host_result.get("ok") is False:
                code = str(host_result.get("error") or "desktop_settings_error")
                result = envelope(
                    "error", operation_id,
                    "Desktop settings changed concurrently; read them again before retrying."
                    if code == "revision_conflict" else "Desktop settings update failed.",
                    revision=host_result.get("revision"), error_code=code,
                )
                result["audit_id"] = audit(
                    operation_id, op_args, status="error",
                    risk="R2" if approved_risks else "R1", error_code=code,
                )
                remember_idempotent(operation_id, key, fingerprint, result)
                await publish_result(result)
                return json_result(result)
            settings = dict(host_result.get("settings") or {})
            outcome = {
                "revision": settings.get("settingsRevision"),
                "apply_mode": "immediate",
                "changed": list(normalized),
                "diff": dict(host_result.get("diff") or {}),
            }
        else:
            outcome = update(
                namespace,
                changes,
                actor="agent",
                expected_revision=int(args["expected_revision"]),
                approved_risks=approved_risks,
            )
        result = envelope(
            "success", operation_id, "Cyrene settings updated atomically.",
            revision=outcome["revision"], apply_mode=outcome["apply_mode"],
            effects=[{"setting": key, **value} for key, value in outcome["diff"].items()],
        )
        audit_id = audit(operation_id, op_args, status="success", risk="R2" if approved_risks else "R1", diff=outcome["diff"])
        result["audit_id"] = audit_id
        from cyrene.observability import debug
        await debug.publish_event({
            "type": "settings_changed",
            "namespace": namespace,
            "revision": outcome["revision"],
            "changed": outcome["changed"],
        })
    except config_store.SettingsRevisionConflict as exc:
        result = envelope("error", operation_id, "Settings changed concurrently; read them again before retrying.", revision=exc.actual, error_code="revision_conflict")
    except SettingsServiceError as exc:
        result = envelope("error", operation_id, str(exc), error_code=exc.code)
    except HostBridgeError as exc:
        result = envelope("unsupported", operation_id, str(exc), error_code=exc.code)
    remember_idempotent(operation_id, key, fingerprint, result)
    await publish_result(result)
    return json_result(result)


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
