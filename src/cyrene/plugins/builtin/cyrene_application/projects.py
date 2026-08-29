from __future__ import annotations

from typing import Any
from cyrene.core.plugin import PluginContext

from cyrene.plugins.native_runtime import json_result
from cyrene.workbench.application import app_services
from cyrene.workbench.application.app_control import DELEGATION_OPERATIONS_SCHEMA, audit, authorize, canonical_hash, envelope, publish_result, remember_idempotent, replay_idempotent

TOOL_NAME = "CyreneProjectControl"
TOOL_DEF = {"type": "function", "function": {
    "name": TOOL_NAME,
    "description": "List, read, create, update or delete local Cyrene projects through the Workbench repository. Switching the visible project is available only through the current UI surface.",
    "parameters": {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["list", "read", "create", "update", "delete"]},
            "project_id": {"type": "string", "maxLength": 160},
            "name": {"type": "string", "maxLength": 120},
            "description": {"type": "string", "maxLength": 4000},
            "workspace_path": {"type": "string", "maxLength": 2000},
            "changes": {"type": "object", "maxProperties": 8},
            "delegation_quote": {"type": "string", "maxLength": 500},
            "delegation_operations": DELEGATION_OPERATIONS_SCHEMA,
            "reason": {"type": "string", "maxLength": 500},
            "idempotency_key": {"type": "string", "maxLength": 160},
        },
        "required": ["operation"],
        "additionalProperties": False,
    },
}}
TOOL_METADATA = {"read_only": False, "resource_keys": ("cyrene:projects",), "requires_order": True}


async def handler(args: dict[str, Any], _context: PluginContext) -> str:
    operation = str(args.get("operation") or "")
    if operation == "list":
        return json_result(envelope("success", "cyrene.project.manage", "Projects listed.", projects=app_services.list_projects()))
    if operation == "read":
        try:
            return json_result(envelope("success", "cyrene.project.manage", "Project read.", project=app_services.read_project(str(args.get("project_id") or ""))))
        except (LookupError, ValueError) as exc:
            return json_result(envelope("error", "cyrene.project.manage", str(exc), error_code="project_error"))

    op_id = "cyrene.project.delete" if operation == "delete" else "cyrene.project.manage"
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
        project_id = str(args.get("project_id") or "")
        if operation == "create":
            value = app_services.create_project(str(args.get("name") or ""), description=str(args.get("description") or ""), workspace_path=str(args.get("workspace_path") or ""))
        elif operation == "update":
            value = {"project": app_services.update_project(project_id, dict(args.get("changes") or {}))}
        elif operation == "delete":
            value = await app_services.delete_project(project_id)
        else:
            raise ValueError("unsupported project operation")
        result = envelope("success", op_id, f"Project operation {operation} completed.", effects=[value])
        result["audit_id"] = audit(op_id, op_args, status="success", risk="R3" if operation == "delete" else "R2")
    except (LookupError, ValueError) as exc:
        result = envelope("error", op_id, str(exc), error_code="project_error")
    remember_idempotent(op_id, key, fingerprint, result)
    await publish_result(result)
    return json_result(result)


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
