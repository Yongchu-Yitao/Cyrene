"""Application service owned by the native CLI Plugin pack."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from agent.plugin.plugin_impl.cyrene_extensions.extension_service import ExtensionService, get_extension_service

from .hooks import (
    CliHookService,
    hook_audit_records,
    public_hook,
    public_proposal,
)


class CLIPluginService:
    """Own the CLI slice while reusing the reviewed extension installer."""

    def __init__(
        self,
        extensions: ExtensionService | None = None,
        hooks: CliHookService | None = None,
    ) -> None:
        self.extensions = extensions or get_extension_service()
        self.hooks = hooks or CliHookService()

    @property
    def tasks(self):
        return self.extensions.tasks

    @staticmethod
    def _require_cli(kind: str) -> None:
        if str(kind or "").strip().lower() != "cli":
            raise ValueError("cyrene_cli only manages CLI Plugins")

    def list_extensions(self) -> dict[str, Any]:
        state = self.extensions.list_extensions()
        return {
            "cli": list(state.get("cli") or []),
            "tasks": [
                item for item in state.get("tasks", [])
                if str(item.get("kind") or "") == "cli"
            ],
        }

    async def search(
        self,
        kind: str,
        query: str,
        *,
        advanced: bool = False,
        cursor: str = "",
    ) -> dict[str, Any]:
        self._require_cli(kind)
        return await self.extensions.search(
            "cli", query, advanced=advanced, cursor=cursor
        )

    def start_install(
        self,
        kind: str,
        extension_id: str,
        request: dict[str, Any],
        *,
        actor: str = "user",
    ) -> dict[str, Any]:
        self._require_cli(kind)
        return self.extensions.start_install(
            "cli", extension_id, request, actor=actor
        )

    async def uninstall(
        self,
        kind: str,
        extension_id: str,
        *,
        version: str = "",
        actor: str = "user",
    ) -> dict[str, Any]:
        self._require_cli(kind)
        return await self.extensions.uninstall(
            "cli", extension_id, version=version, actor=actor
        )

    async def set_extension_enabled(
        self,
        kind: str,
        extension_id: str,
        enabled: bool,
        *,
        actor: str = "user",
    ) -> dict[str, Any]:
        self._require_cli(kind)
        return await self.extensions.set_extension_enabled(
            "cli", extension_id, enabled, actor=actor
        )

    def bind_system_executable(self, extension_id: str, path: str) -> dict[str, Any]:
        return self.extensions.bind_system_executable(extension_id, path)

    def unbind_system_executable(self, extension_id: str) -> dict[str, Any]:
        return self.extensions.unbind_system_executable(extension_id)

    def hook_listing(self) -> dict[str, Any]:
        self.resume_pending_hook_generations()
        return {
            "hooks": [public_hook(item) for item in self.hooks.list()],
            "proposals": [public_proposal(item) for item in self.hooks.proposals()],
            "configuration_results": self.hooks.configuration_results(),
        }

    def resume_pending_hook_generations(self) -> int:
        """Resume persisted Agent jobs after an application restart."""

        from .config_agent import schedule_user_hook_configuration

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return 0
        resumed = 0
        for hook in self.hooks.list():
            if (
                hook.get("configured_by_agent") is True
                and hook.get("configuration_status") == "configuring"
            ):
                resumed += int(
                    schedule_user_hook_configuration(hook, hooks=self.hooks)
                )
        return resumed

    def save_hook(
        self,
        payload: Mapping[str, Any],
        *,
        hook_id: str = "",
    ) -> dict[str, Any]:
        mutation = dict(payload)
        if hook_id:
            existing = self.hooks.get(hook_id)
            if existing is None:
                raise ValueError("CLI Hook not found")
            if existing.get("configured_by_agent") is True:
                if existing.get("configuration_status") != "ready":
                    raise ValueError("Agent configuration is not complete")
                unexpected = set(mutation) - {"timeout_seconds", "priority"}
                if unexpected:
                    raise ValueError(
                        "Agent-configured Hooks only allow timeout and priority tuning"
                    )
            mutation["id"] = hook_id
        return {"ok": True, "hook": public_hook(self.hooks.save(mutation))}

    def request_hook_generation(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        from .config_agent import schedule_user_hook_configuration

        hook = self.hooks.create_generation_request(payload)
        schedule_user_hook_configuration(hook, hooks=self.hooks)
        return {"ok": True, "status": "configuring", "hook": public_hook(hook)}

    def retry_hook_generation(
        self,
        hook_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        from .config_agent import schedule_user_hook_configuration

        hook = self.hooks.get(hook_id)
        if hook is None or hook.get("configured_by_agent") is not True:
            raise ValueError("Agent-configured Hook not found")
        mutation = dict(payload or {})
        if mutation:
            hook = self.hooks.update_generation_request(hook_id, mutation)
        else:
            if hook.get("configuration_status") == "ready":
                raise ValueError("Agent configuration is already complete")
            hook = self.hooks.set_generation_state(hook_id, status="configuring")
        schedule_user_hook_configuration(hook, hooks=self.hooks)
        return {"ok": True, "status": "configuring", "hook": public_hook(hook)}

    def delete_hook(self, hook_id: str) -> dict[str, Any]:
        if not self.hooks.delete(hook_id):
            raise ValueError("CLI Hook not found")
        return {"ok": True}

    def set_hook_enabled(self, hook_id: str, enabled: Any) -> dict[str, Any]:
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        return {
            "ok": True,
            "hook": public_hook(self.hooks.set_enabled(hook_id, enabled)),
        }

    async def test_hook(
        self,
        hook_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self.hooks.test(hook_id, payload)

    def decide_hook_proposal(
        self,
        proposal_id: str,
        approve: Any,
    ) -> dict[str, Any]:
        if type(approve) is not bool:
            raise ValueError("approve must be a boolean")
        return self.hooks.decide_proposal(proposal_id, approve)

    def hook_audit(self, limit: int = 200) -> dict[str, Any]:
        return {"records": hook_audit_records(limit)}

    def schedule_hook_configuration(
        self,
        extension: Mapping[str, Any],
        *,
        trigger: str = "install",
    ) -> bool:
        from .config_agent import schedule_cli_configuration

        return schedule_cli_configuration(
            dict(extension),
            hooks=self.hooks,
            trigger=trigger,
        )

    def configure_installed_cli(self, extension_id: str) -> dict[str, Any]:
        card = next(
            (
                item for item in self.list_extensions().get("cli", [])
                if str(item.get("id") or "") == str(extension_id)
            ),
            None,
        )
        if not card or str(card.get("observed_state") or "") != "installed":
            raise ValueError("installed CLI Plugin not found")
        self.schedule_hook_configuration(
            {**card, "key": f"cli:{extension_id}"},
            trigger="manual",
        )
        return {"ok": True, "status": "started"}


__all__ = ["CLIPluginService"]
