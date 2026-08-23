"""Application service for Agent Hook management and approvals."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from cyrene.hooks.service import HookService
from cyrene.runtime.secret_redaction import redact_value


class HookApplicationError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class HookApplicationService:
    def __init__(
        self,
        hooks: HookService,
        *,
        reviewer: Callable[..., Awaitable[tuple[bool, str]]],
        public_hook: Callable[[dict], dict],
        public_proposal: Callable[[dict], dict],
        configuration_results: Callable[[], list],
        audit_records: Callable[[int], list],
        extension_cards: Callable[[], dict],
        schedule_configuration: Callable[..., Any],
    ) -> None:
        self.hooks = hooks
        self.reviewer = reviewer
        self.public_hook = public_hook
        self.public_proposal = public_proposal
        self.configuration_results = configuration_results
        self.audit_records = audit_records
        self.extension_cards = extension_cards
        self.schedule_configuration = schedule_configuration

    def list(self) -> dict:
        return {
            "hooks": [self.public_hook(item) for item in self.hooks.list()],
            "proposals": [self.public_proposal(item) for item in self.hooks.proposals()],
            "configuration_results": self.configuration_results(),
        }

    async def create(self, payload: dict) -> dict:
        await self._review("create", payload)
        return self._save(payload)

    async def update(self, hook_id: str, payload: dict) -> dict:
        mutation = dict(payload)
        mutation["id"] = hook_id
        await self._review("update", mutation)
        return self._save(mutation)

    async def delete(self, hook_id: str) -> dict:
        await self._review("delete", {"id": hook_id})
        try:
            deleted = self.hooks.delete(hook_id, actor="user")
        except Exception as exc:
            raise HookApplicationError(str(exc)) from exc
        if not deleted:
            raise HookApplicationError("hook not found", 404)
        return {"ok": True}

    async def set_enabled(self, hook_id: str, enabled: Any) -> dict:
        if type(enabled) is not bool:
            raise HookApplicationError("enabled must be a boolean")
        await self._review(
            "enable" if enabled else "disable", {"id": hook_id, "enabled": enabled}
        )
        try:
            hook = self.hooks.set_enabled(hook_id, enabled, actor="user")
        except Exception as exc:
            raise HookApplicationError(str(exc)) from exc
        return {"ok": True, "hook": self.public_hook(hook)}

    async def test(self, hook_id: str, payload: dict) -> dict:
        try:
            return await self.hooks.test(hook_id, payload)
        except Exception as exc:
            raise HookApplicationError(str(exc)) from exc

    def audit(self, limit: int) -> dict:
        return {"records": self.audit_records(limit)}

    def decide_proposal(self, proposal_id: str, approve: Any) -> dict:
        if type(approve) is not bool:
            raise HookApplicationError("approve must be a boolean")
        try:
            result = self.hooks.decide_proposal(proposal_id, approve, actor="user")
        except Exception as exc:
            raise HookApplicationError(str(exc)) from exc
        projected = dict(result)
        if isinstance(projected.get("proposal"), dict):
            projected["proposal"] = self.public_proposal(projected["proposal"])
        if isinstance(projected.get("hook"), dict):
            projected["hook"] = self.public_hook(projected["hook"])
        return projected

    def configure_cli(self, extension_id: str) -> dict:
        cards = self.extension_cards().get("cli", [])
        card = next(
            (item for item in cards if str(item.get("id") or "") == extension_id),
            None,
        )
        if not card or card.get("observed_state") != "installed":
            raise HookApplicationError("installed CLI extension not found")
        try:
            self.schedule_configuration(card, trigger="manual")
        except Exception as exc:
            raise HookApplicationError(str(exc)) from exc
        return {"ok": True, "status": "started"}

    def _save(self, payload: dict) -> dict:
        try:
            hook = self.hooks.save(payload, actor="user")
        except Exception as exc:
            raise HookApplicationError(str(exc)) from exc
        return {"ok": True, "hook": self.public_hook(hook)}

    async def _review(self, action: str, payload: dict[str, Any]) -> None:
        review_payload = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
        runner = review_payload.get("runner") if isinstance(review_payload, dict) else None
        if isinstance(runner, dict) and isinstance(runner.get("env"), dict):
            runner["environment_keys"] = sorted(str(key) for key in runner["env"])
            runner.pop("env", None)
        safe = json.dumps(
            redact_value(review_payload), ensure_ascii=False, sort_keys=True, default=str
        )
        fingerprint = hashlib.sha256(safe.encode("utf-8")).hexdigest()
        try:
            approved, rationale = await self.reviewer(
                tool_name="ManageAgentHooks",
                operation=f"Agent Hook 全局配置：{action}",
                path_hint=f"agent-hook:{fingerprint[:20]}",
                reason=safe[:1600],
            )
        except PermissionError as exc:
            raise HookApplicationError(str(exc), 403) from exc
        except Exception as exc:
            raise HookApplicationError(str(exc)) from exc
        if not approved:
            raise HookApplicationError(
                rationale or "Hook configuration was rejected by the reviewer", 403
            )
