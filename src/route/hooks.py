"""Agent Hook management, testing, and proposal approval routes."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene.agent.auto_review import review_elevation
from cyrene.hooks.config_agent import configuration_results, schedule_cli_configuration
from cyrene.hooks.service import (
    get_hook_service,
    hook_audit_records,
    public_hook_config,
    public_hook_proposal,
)
from cyrene.runtime.secret_redaction import redact_value


def _error(exc: Exception, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=status)


async def _review(action: str, payload: dict[str, Any]) -> None:
    review_payload = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
    runner = review_payload.get("runner") if isinstance(review_payload, dict) else None
    if isinstance(runner, dict) and isinstance(runner.get("env"), dict):
        runner["environment_keys"] = sorted(str(key) for key in runner["env"])
        runner.pop("env", None)
    safe = json.dumps(redact_value(review_payload), ensure_ascii=False, sort_keys=True, default=str)
    fingerprint = hashlib.sha256(safe.encode("utf-8")).hexdigest()
    approved, rationale = await review_elevation(
        tool_name="ManageAgentHooks",
        operation=f"Agent Hook 全局配置：{action}",
        path_hint=f"agent-hook:{fingerprint[:20]}",
        reason=safe[:1600],
    )
    if not approved:
        raise PermissionError(rationale or "Hook configuration was rejected by the reviewer")


def register_hook_routes(router: APIRouter, _bot: Any, _db_path: str) -> None:
    service = get_hook_service()

    @router.get("/api/hooks")
    async def api_hooks():
        return {
            "hooks": [public_hook_config(item) for item in service.list()],
            "proposals": [public_hook_proposal(item) for item in service.proposals()],
            "configuration_results": configuration_results(),
        }

    @router.post("/api/hooks")
    async def api_create_hook(request: Request):
        try:
            body = await request.json()
            await _review("create", body)
            return {"ok": True, "hook": public_hook_config(service.save(body, actor="user"))}
        except Exception as exc:
            return _error(exc, 403 if isinstance(exc, PermissionError) else 400)

    @router.put("/api/hooks/{hook_id}")
    async def api_update_hook(hook_id: str, request: Request):
        try:
            body = await request.json()
            body["id"] = hook_id
            await _review("update", body)
            return {"ok": True, "hook": public_hook_config(service.save(body, actor="user"))}
        except Exception as exc:
            return _error(exc, 403 if isinstance(exc, PermissionError) else 400)

    @router.delete("/api/hooks/{hook_id}")
    async def api_delete_hook(hook_id: str):
        try:
            await _review("delete", {"id": hook_id})
            if not service.delete(hook_id, actor="user"):
                return _error(ValueError("hook not found"), 404)
            return {"ok": True}
        except Exception as exc:
            return _error(exc, 403 if isinstance(exc, PermissionError) else 400)

    @router.post("/api/hooks/{hook_id}/enabled")
    async def api_enable_hook(hook_id: str, request: Request):
        try:
            body = await request.json()
            enabled = body.get("enabled")
            if type(enabled) is not bool:
                raise ValueError("enabled must be a boolean")
            await _review("enable" if enabled else "disable", {"id": hook_id, "enabled": enabled})
            return {"ok": True, "hook": public_hook_config(service.set_enabled(hook_id, enabled, actor="user"))}
        except Exception as exc:
            return _error(exc, 403 if isinstance(exc, PermissionError) else 400)

    @router.post("/api/hooks/{hook_id}/test")
    async def api_test_hook(hook_id: str, request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            return await service.test(hook_id, body if isinstance(body, dict) else {})
        except Exception as exc:
            return _error(exc)

    @router.get("/api/hooks/audit/records")
    async def api_hook_audit(limit: int = 200):
        return {"records": hook_audit_records(limit)}

    @router.post("/api/hooks/proposals/{proposal_id}/decision")
    async def api_hook_proposal_decision(proposal_id: str, request: Request):
        try:
            body = await request.json()
            approve = body.get("approve")
            if type(approve) is not bool:
                raise ValueError("approve must be a boolean")
            # This is the explicit human approval requested by the background
            # configuration Agent; do not put another model reviewer between
            # the user and that exact immutable proposal.
            result = service.decide_proposal(proposal_id, approve, actor="user")
            if isinstance(result.get("proposal"), dict):
                result["proposal"] = public_hook_proposal(result["proposal"])
            if isinstance(result.get("hook"), dict):
                result["hook"] = public_hook_config(result["hook"])
            return result
        except Exception as exc:
            return _error(exc)

    @router.post("/api/hooks/extensions/cli/{extension_id}/configure")
    async def api_configure_cli_hook(extension_id: str):
        try:
            from cyrene.extensions.service import get_extension_service

            card = next(
                (item for item in get_extension_service().list_extensions().get("cli", []) if str(item.get("id") or "") == extension_id),
                None,
            )
            if not card or card.get("observed_state") != "installed":
                raise ValueError("installed CLI extension not found")
            schedule_cli_configuration(card, trigger="manual")
            return {"ok": True, "status": "started"}
        except Exception as exc:
            return _error(exc)
