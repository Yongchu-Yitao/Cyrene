from __future__ import annotations

from pathlib import Path
from typing import Any
from cyrene.core.plugin import PluginContext

from cyrene.platform.backup import delete_backup, export_backup, list_backups, restore_backup
from cyrene.plugins.native_runtime import json_result
from cyrene.platform.attachments import register_generated_attachment
from cyrene.workbench.application.app_control import DELEGATION_OPERATIONS_SCHEMA, audit, authorize, canonical_hash, envelope, publish_result, remember_idempotent, replay_idempotent

TOOL_NAME = "CyreneDataControl"
TOOL_DEF = {"type": "function", "function": {
    "name": TOOL_NAME,
    "description": "List, create, validate, restore or delete Cyrene-owned backup archives by stable backup name.",
    "parameters": {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["list", "create", "validate_restore", "restore", "delete"]},
            "backup_name": {"type": "string", "maxLength": 255},
            "include_database": {"type": "boolean"},
            "delegation_quote": {"type": "string", "maxLength": 500},
            "delegation_operations": DELEGATION_OPERATIONS_SCHEMA,
            "reason": {"type": "string", "maxLength": 500},
            "idempotency_key": {"type": "string", "maxLength": 160},
        },
        "required": ["operation"],
        "additionalProperties": False,
    },
}}
TOOL_METADATA = {"read_only": False, "resource_keys": ("cyrene:backups",), "requires_order": True}


def _backup_path(name: str) -> Path:
    entry = next((item for item in list_backups() if item.get("name") == name), None)
    if not entry:
        raise LookupError("backup not found")
    return Path(str(entry["path"]))


async def handler(args: dict[str, Any], _context: PluginContext) -> str:
    operation = str(args.get("operation") or "")
    if operation == "list":
        public = [{key: value for key, value in item.items() if key != "path"} for item in list_backups()]
        return json_result(envelope("success", "cyrene.data.manage", "Backups listed.", backups=public))
    if operation == "validate_restore":
        try:
            result = await restore_backup(str(_backup_path(str(args.get("backup_name") or ""))), dry_run=True)
            return json_result(envelope("success" if result.get("ok") else "error", "cyrene.data.manage", "Backup validation completed.", validation=result))
        except LookupError as exc:
            return json_result(envelope("error", "cyrene.data.manage", str(exc), error_code="backup_not_found"))

    op_id = "cyrene.data.delete" if operation == "delete" else "cyrene.data.restore" if operation == "restore" else "cyrene.data.backup"
    op_args = {
        key: value
        for key, value in args.items()
        if key not in {"reason", "idempotency_key", "delegation_quote", "delegation_operations"}
    }
    key = str(args.get("idempotency_key") or "")
    if not key:
        return json_result(envelope("error", op_id, "idempotency_key is required.", error_code="idempotency_required"))
    fingerprint = canonical_hash(op_id, op_args)
    replay = replay_idempotent(op_id, key, fingerprint)
    if replay is not None:
        return json_result(replay)
    approval = await authorize(
        op_id, op_args,
        reason=str(args.get("reason") or ""),
        delegation_quote=str(args.get("delegation_quote") or ""),
        delegation_operations=args.get("delegation_operations"),
    )
    if approval:
        return approval
    try:
        if operation == "create":
            value = await export_backup(include_db=bool(args.get("include_database", True)))
            attachment = register_generated_attachment(value["path"], display_name=Path(value["path"]).name)
            safe_value = {key: item for key, item in value.items() if key != "path"}
            safe_value["attachment"] = attachment
            result = envelope("success", op_id, "Cyrene backup created.", effects=[safe_value])
        elif operation == "restore":
            value = await restore_backup(str(_backup_path(str(args.get("backup_name") or ""))), dry_run=False)
            result = envelope("success" if value.get("ok") else "error", op_id, "Cyrene backup restore completed." if value.get("ok") else "Cyrene backup restore failed.", restart_required=bool(value.get("restart_required")), effects=[value])
        elif operation == "delete":
            name = str(args.get("backup_name") or "")
            if not await delete_backup(name):
                raise LookupError("backup not found")
            result = envelope("success", op_id, "Cyrene backup deleted.", effects=[{"backup_name": name}])
        else:
            raise ValueError("unsupported data operation")
        result["audit_id"] = audit(op_id, op_args, status=result["status"], risk="R3" if operation in {"restore", "delete"} else "R2")
    except (LookupError, ValueError) as exc:
        result = envelope("error", op_id, str(exc), error_code="backup_error")
    remember_idempotent(op_id, key, fingerprint, result)
    await publish_result(result)
    return json_result(result)


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
