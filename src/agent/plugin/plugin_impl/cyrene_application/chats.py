from __future__ import annotations

from typing import Any
from agent.plugin import PluginContext

from agent.plugin.native_runtime import json_result
from cyrene.workbench import app_services
from cyrene.workbench.app_control import audit, authorize, canonical_hash, envelope, publish_result, remember_idempotent, replay_idempotent

TOOL_NAME = "CyreneChatControl"
TOOL_DEF = {"type": "function", "function": {
    "name": TOOL_NAME,
    "description": "List, read, create, rename, compact, fork, group or delete Workbench conversations without sending a message as the user.",
    "parameters": {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["list", "read", "create", "rename", "compact", "fork", "groups", "group", "ungroup", "rename_group", "delete"]},
            "project_id": {"type": "string", "maxLength": 160},
            "chat_id": {"type": "string", "maxLength": 160},
            "title": {"type": "string", "maxLength": 80},
            "message_id": {"type": "string", "maxLength": 160},
            "group_id": {"type": "string", "maxLength": 160},
            "chat_ids": {"type": "array", "items": {"type": "string", "maxLength": 160}, "minItems": 2, "maxItems": 50, "uniqueItems": True},
            "content": {"type": "string", "maxLength": 20000},
            "reason": {"type": "string", "maxLength": 500},
            "idempotency_key": {"type": "string", "maxLength": 160},
        },
        "required": ["operation"],
        "additionalProperties": False,
    },
}}
TOOL_METADATA = {"read_only": False, "resource_keys": ("cyrene:chats",), "requires_order": True}


async def handler(args: dict[str, Any], _context: PluginContext) -> str:
    operation = str(args.get("operation") or "")
    if operation == "list":
        return json_result(envelope("success", "cyrene.chat.manage", "Chats listed.", chats=app_services.list_chats(str(args.get("project_id") or ""))))
    if operation == "read":
        try:
            return json_result(envelope("success", "cyrene.chat.manage", "Chat read.", chat=app_services.read_chat(str(args.get("chat_id") or ""))))
        except (LookupError, ValueError) as exc:
            return json_result(envelope("error", "cyrene.chat.manage", str(exc), error_code="chat_error"))
    if operation == "groups":
        try:
            groups = app_services.list_chat_groups(str(args.get("project_id") or ""))
            return json_result(envelope("success", "cyrene.chat.manage", "Chat groups listed.", groups=groups))
        except (LookupError, ValueError) as exc:
            return json_result(envelope("error", "cyrene.chat.manage", str(exc), error_code="chat_error"))

    op_id = "cyrene.chat.delete" if operation == "delete" else "cyrene.chat.manage"
    op_args = {
        key: value
        for key, value in args.items()
        if key not in {"reason", "idempotency_key"}
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
    )
    if approval:
        return approval
    try:
        chat_id = str(args.get("chat_id") or "")
        if operation == "create":
            value = {"chat": app_services.create_chat(str(args.get("project_id") or ""), str(args.get("title") or ""))}
        elif operation == "rename":
            value = {"chat": app_services.rename_chat(chat_id, str(args.get("title") or ""))}
        elif operation == "compact":
            value = await app_services.compact_chat(chat_id)
        elif operation == "fork":
            value = {"chat": await app_services.fork_chat(chat_id, str(args.get("message_id") or ""), str(args.get("content") or ""))}
        elif operation in {"group", "ungroup", "rename_group"}:
            value = await app_services.manage_chat_group(
                str(args.get("project_id") or ""), operation,
                group_id=str(args.get("group_id") or ""),
                chat_ids=list(args.get("chat_ids") or []),
                title=str(args.get("title") or ""),
            )
        elif operation == "delete":
            value = await app_services.delete_chat(chat_id)
        else:
            raise ValueError("unsupported chat operation")
        result = envelope("success", op_id, f"Chat operation {operation} completed.", effects=[value])
        result["audit_id"] = audit(op_id, op_args, status="success", risk="R3" if operation == "delete" else "R2")
    except (LookupError, ValueError) as exc:
        result = envelope("error", op_id, str(exc), error_code="chat_error")
    remember_idempotent(op_id, key, fingerprint, result)
    await publish_result(result)
    return json_result(result)


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
