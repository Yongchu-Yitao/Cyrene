"""Agent-facing Hook proposals with immutable user approval."""

from __future__ import annotations

import json
from typing import Any

from cyrene.hooks.service import get_hook_service, public_hook_config, public_hook_proposal

TOOL_NAME = "ManageAgentHooks"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "List global Agent Hooks or submit an exact Hook configuration proposal. "
            "Proposals remain disabled until the user approves them in Extension Center."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "propose"]},
                "extension": {
                    "type": "object",
                    "description": "Optional extension metadata: key, id, kind, name, path, version.",
                },
                "hook": {
                    "type": "object",
                    "description": (
                        "Exact Hook: name, description, event, matcher, priority, failure_policy, "
                        "timeout_seconds, and runner. runner is either {type:'command', executable, args, env} "
                        "or {type:'script', path, args, env}."
                    ),
                },
                "rationale": {"type": "string"},
            },
            "required": ["action"],
        },
    },
}
TOOL_METADATA = {
    "read_only": False,
    "resource_keys": ("agent-hooks:global",),
    "requires_order": True,
}


async def _tool_manage_agent_hooks(args: dict[str, Any], *_unused: Any) -> str:
    service = get_hook_service()
    action = str(args.get("action") or "list").strip().lower()
    if action == "list":
        return json.dumps(
            {
                "ok": True,
                "hooks": [public_hook_config(item) for item in service.list()],
                "proposals": [public_hook_proposal(item) for item in service.proposals()],
            },
            ensure_ascii=False,
        )
    if action != "propose":
        return json.dumps({"ok": False, "error": "unsupported action"}, ensure_ascii=False)
    hook = args.get("hook")
    if not isinstance(hook, dict):
        return json.dumps({"ok": False, "error": "hook is required"}, ensure_ascii=False)
    extension = args.get("extension") if isinstance(args.get("extension"), dict) else {}
    proposal = service.add_proposal(
        extension=extension,
        hook=hook,
        rationale=str(args.get("rationale") or ""),
        actor="agent",
    )
    try:
        from cyrene.workbench.notifications import append_notification

        append_notification(
            title="Agent Hook 配置等待批准",
            body=f"Agent 已生成“{proposal['hook']['name']}”配置提案。批准后才会启用。",
            tab="system",
            source="agent_hook_configuration",
            source_label="扩展中心",
            link_label="查看 Hook 提案",
            meta={
                "category": "hook_approval",
                "proposalId": proposal["id"],
                "extensionKey": str(extension.get("key") or ""),
            },
        )
    except Exception:
        # The durable proposal remains visible in Hook management even if the
        # notification store is temporarily unavailable.
        pass
    return json.dumps(
        {"ok": True, "status": "pending_user_approval", "proposal": public_hook_proposal(proposal)},
        ensure_ascii=False,
    )


handler = _tool_manage_agent_hooks

__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler", "_tool_manage_agent_hooks"]
