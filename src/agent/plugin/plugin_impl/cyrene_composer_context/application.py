"""Application-owned composer context catalog, validation, and projection."""

from __future__ import annotations

import asyncio
import json
import platform
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from agent.plugin import (
    PluginApplicationContext,
    PluginRegistry,
    active_plugin_application_host,
    active_plugin_service,
)
from cyrene.localization import localized

ACTIVATION_KEYS = ("mcpServers", "skills", "pluginPacks")
_MAX_SELECTIONS_PER_KIND = 50
_MAX_SELECTION_ID_LENGTH = 300
_COMPOSER_PACK_ID = "cyrene_composer_context"


def _l(en: str, zh: str, **values: Any) -> str:
    return localized(en, zh, **values)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _identities(value: Any, *, limit: int = _MAX_SELECTIONS_PER_KIND) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    result: list[str] = []
    for item in value:
        identity = str(item or "").strip()
        if not identity or len(identity) > _MAX_SELECTION_ID_LENGTH:
            continue
        if identity not in result:
            result.append(identity)
        if len(result) >= limit:
            break
    return result


class ProjectResolver:
    """Resolve the active Workbench project without making core own context."""

    def __init__(
        self,
        read_state: Callable[[], dict[str, Any]],
        default_workspace: Path,
    ) -> None:
        self.read_state = read_state
        self.default_workspace = default_workspace

    def active_workspace(self) -> str:
        state = self.read_state()
        active_id = str(state.get("activeProjectId") or "").strip()
        project = next(
            (
                item
                for item in state.get("projects") or []
                if isinstance(item, Mapping)
                and str(item.get("id") or "") == active_id
            ),
            None,
        )
        workspace = str(project.get("workspacePath") or "").strip() if project else ""
        return workspace or str(self.default_workspace)


class ComposerContextService:
    """Authoritative input-context state and SessionStart prompt builder."""

    def __init__(
        self,
        registry: PluginRegistry,
        *,
        projects: ProjectResolver | None = None,
        service_resolver: Callable[[str], Any | None] = active_plugin_service,
        system_name: Callable[[], str] = platform.system,
        run_process: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        self._registry = registry
        self._projects = projects
        self._service_resolver = service_resolver
        self._system_name = system_name
        self._run_process = run_process

    @staticmethod
    def normalize(value: Any) -> dict[str, list[str]]:
        """Return a bounded, deterministic activation payload."""

        source = _mapping(value)
        return {key: _identities(source.get(key)) for key in ACTIVATION_KEYS}

    def _service(
        self,
        name: str,
        services: Mapping[str, Any] | None = None,
    ) -> Any | None:
        # A supplied session mapping is authoritative. Falling back to the
        # process host here would let a quarantined session use stale code.
        if services is not None:
            return services.get(name)
        return self._service_resolver(name)

    def _pack(self, pack_id: str):
        return next(
            (pack for pack in self._registry.list_packs() if pack.id == pack_id),
            None,
        )

    def _pack_switch_state(self, pack_id: str) -> tuple[bool, bool, bool]:
        pack = self._pack(pack_id)
        if pack is None:
            return False, False, False
        try:
            configured = self._registry.pack_configured_enabled(pack_id)
            effective = self._registry.pack_enabled(pack_id)
        except (KeyError, RuntimeError):
            return False, False, False
        host = active_plugin_application_host()
        operational = (
            host.pack_operational(pack_id)
            if host is not None and pack.application_setup is not None
            else effective
        )
        return configured, effective, operational

    @staticmethod
    def _state_record(
        identity: str,
        *,
        pack_id: str,
        configured: bool,
        effective: bool,
        operational: bool,
        reason: str = "",
    ) -> dict[str, Any]:
        available = effective and operational
        return {
            "id": identity,
            "packId": pack_id,
            "configured": configured,
            "effective": effective,
            "operational": operational,
            # Preserve the existing composer convention while exposing why a
            # configured option cannot currently be used.
            "enabled": configured,
            "available": available,
            "reason": "" if available else reason or _l(
                "Plugin service is unavailable",
                "插件服务不可用",
            ),
        }

    def _mcp_catalog(
        self, services: Mapping[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        service = self._service("mcp", services)
        if service is None:
            return []
        statuses = {str(item.get("name") or ""): item for item in service.status()}
        packs = {pack.id: pack for pack in self._registry.list_packs()}
        result = []
        for config in service.configs(redacted=True):
            name = str(config.get("name") or "").strip()
            if not name:
                continue
            status = statuses.get(name, {})
            pack_id = str(status.get("pack_id") or service.pack_id_for_server(name))
            pack = packs.get(pack_id)
            pack_enabled = False
            if pack is not None:
                try:
                    pack_enabled = self._registry.pack_enabled(pack_id)
                except (KeyError, RuntimeError):
                    pack_enabled = False
            configured = bool(config.get("enabled", True))
            enabled = configured and pack_enabled
            available = (
                enabled
                and str(status.get("status") or "") == "connected"
                and pack is not None
                and any(self._registry.plugin_enabled(plugin.name) for plugin in pack.plugins)
            )
            result.append({
                "id": name,
                "name": name,
                "description": str(config.get("description") or _l(
                    "MCP server",
                    "MCP 服务",
                )),
                "i18n": dict(
                    pack.metadata.get("i18n") or config.get("i18n") or {}
                ) if pack is not None else dict(config.get("i18n") or {}),
                "configured": configured,
                "effective": enabled,
                "operational": available,
                "enabled": enabled,
                "available": available,
                "status": str(status.get("status") or "disconnected"),
                "toolCount": int(status.get("tool_count") or 0),
                "packId": pack_id,
                "error": (
                    _l("MCP server is unavailable", "MCP 服务不可用")
                    if status.get("error")
                    else ""
                ),
            })
        return result

    def _skill_catalog(
        self, services: Mapping[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        service = self._service("skills", services)
        catalog = getattr(service, "catalog", None)
        if not callable(catalog):
            return []
        return [
            {
                "id": str(skill.get("id") or ""),
                "name": str(skill.get("name") or skill.get("id") or ""),
                "description": str(skill.get("desc") or ""),
                "configured": bool(skill.get("enabled", True)),
                "effective": bool(skill.get("enabled", True)),
                "operational": bool(skill.get("enabled", True)),
                "enabled": bool(skill.get("enabled", True)),
                "available": bool(skill.get("enabled", True)),
            }
            for skill in catalog()
            if str(skill.get("id") or "").strip()
        ]

    def _plugin_pack_catalog(self) -> list[dict[str, Any]]:
        result = []
        for pack in self._registry.list_packs():
            if self._registry.pack_source(pack.id).startswith("mcp:"):
                continue
            tools = [
                plugin
                for plugin in pack.plugins
                if plugin.kind == "tool" and plugin.model_visible
            ]
            if not tools:
                continue
            configured, effective, operational = self._pack_switch_state(pack.id)
            available_tools = [
                plugin for plugin in tools if self._registry.plugin_enabled(plugin.name)
            ]
            available = effective and operational and bool(available_tools)
            result.append({
                "id": pack.id,
                "name": pack.id,
                "description": pack.description,
                "i18n": dict(pack.metadata.get("i18n") or {}),
                "configured": configured,
                "effective": effective,
                "operational": operational,
                "enabled": configured,
                "available": available,
                "toolCount": len(tools),
            })
        return result

    def _remote_device_catalog(
        self, services: Mapping[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        remote = self._service("remote", services)
        store = getattr(remote, "store", None)
        list_peers = getattr(store, "list_peers", None)
        if not callable(list_peers):
            return []
        result: list[dict[str, Any]] = []
        for peer in list_peers():
            if not isinstance(peer, Mapping) or str(peer.get("revoked_at") or ""):
                continue
            device_id = str(peer.get("device_id") or "").strip()
            if not device_id:
                continue
            available = bool(peer.get("received_capabilities")) and bool(
                peer.get("received_project_scopes")
            )
            result.append({
                "id": device_id,
                "name": str(peer.get("display_name") or device_id),
                "description": _l("Paired Cyrene device", "已配对的 Cyrene 设备"),
                "configured": True,
                "effective": available,
                "operational": available,
                "enabled": True,
                "available": available,
                "capabilities": list(peer.get("received_capabilities") or ()),
                "projectScopes": list(peer.get("received_project_scopes") or ()),
                "packId": "cyrene_remote",
            })
        return result

    def _option_catalog(
        self, services: Mapping[str, Any] | None = None
    ) -> dict[str, dict[str, Any]]:
        soul_configured, soul_effective, soul_pack_operational = (
            self._pack_switch_state("cyrene_soul")
        )
        soul_operational = soul_pack_operational and self._service("soul", services) is not None
        remote_configured, remote_effective, remote_pack_operational = (
            self._pack_switch_state("cyrene_remote")
        )
        remote_operational = (
            remote_pack_operational and self._service("remote", services) is not None
        )
        workspace = self._projects.active_workspace() if self._projects is not None else ""
        return {
            "soul": self._state_record(
                "soul",
                pack_id="cyrene_soul",
                configured=soul_configured,
                effective=soul_effective,
                operational=soul_operational,
                reason=_l(
                    "Soul Plugin is not operational",
                    "Soul 插件未运行",
                ),
            ),
            "workspace": {
                **self._state_record(
                    "workspace",
                    pack_id=_COMPOSER_PACK_ID,
                    configured=True,
                    effective=True,
                    operational=bool(workspace),
                    reason=_l("No workspace is available", "没有可用的工作区"),
                ),
                "workspaceDir": workspace,
            },
            "remoteDevices": self._state_record(
                "remoteDevices",
                pack_id="cyrene_remote",
                configured=remote_configured,
                effective=remote_effective,
                operational=remote_operational,
                reason=_l(
                    "Remote Plugin is not operational",
                    "Remote 插件未运行",
                ),
            ),
        }

    def catalog(self) -> dict[str, Any]:
        """Return every composer-selectable capability and toggle state."""

        return {
            "mcpServers": self._mcp_catalog(),
            "skills": self._skill_catalog(),
            "pluginPacks": self._plugin_pack_catalog(),
            "remoteDevices": self._remote_device_catalog(),
            "options": self._option_catalog(),
        }

    @staticmethod
    def _available_ids(items: Sequence[Mapping[str, Any]]) -> set[str]:
        return {
            str(item.get("id") or "")
            for item in items
            if bool(item.get("available", item.get("enabled")))
        }

    def validate(
        self,
        value: Any,
        *,
        services: Mapping[str, Any] | None = None,
    ) -> dict[str, list[str]]:
        """Reject selections whose owning runtime is not operational."""

        normalized = self.normalize(value)
        catalog = {
            "mcpServers": self._mcp_catalog(services),
            "skills": self._skill_catalog(services),
            "pluginPacks": self._plugin_pack_catalog(),
        }
        for key in ACTIVATION_KEYS:
            allowed = self._available_ids(catalog[key])
            unknown = [identity for identity in normalized[key] if identity not in allowed]
            if unknown:
                raise ValueError(
                    _l(
                        "Unavailable composer context selection(s) for {key}: {items}",
                        "{key} 中包含不可用的编写器上下文选项：{items}",
                        key=key,
                        items=", ".join(unknown),
                    )
                )
        return normalized

    def resolve(
        self,
        value: Any,
        *,
        services: Mapping[str, Any] | None = None,
    ) -> dict[str, list[str]]:
        """Prune stale selections and never resolve a non-operational source."""

        normalized = self.normalize(value)
        catalog = {
            "mcpServers": self._mcp_catalog(services),
            "skills": self._skill_catalog(services),
            "pluginPacks": self._plugin_pack_catalog(),
        }
        return {
            key: [
                identity
                for identity in normalized[key]
                if identity in self._available_ids(catalog[key])
            ]
            for key in ACTIVATION_KEYS
        }

    def _workspace_path(self, workspace_dir: Any, workspace_override: Any) -> str:
        override = str(workspace_override or "").strip()
        if override:
            candidate = Path(override).expanduser()
            if not candidate.is_absolute():
                raise ValueError(_l(
                    "Workspace override must be an absolute path.",
                    "工作区覆盖路径必须是绝对路径。",
                ))
            return str(candidate.resolve())
        selected = str(workspace_dir or "").strip()
        if selected:
            return selected
        return self._projects.active_workspace() if self._projects is not None else ""

    def resolve_input_context(
        self,
        *,
        soul_active: Any,
        workspace_active: Any,
        workspace_dir: Any = "",
        workspace_override: Any = "",
        remote_device_ids: Any = (),
        context_activations: Any = None,
        services: Mapping[str, Any] | None = None,
        strict: bool = True,
    ) -> dict[str, Any]:
        """Normalize all independent composer fields behind one boundary."""

        soul_enabled = bool(soul_active)
        workspace_enabled = bool(workspace_active)
        workspace = self._workspace_path(workspace_dir, workspace_override)
        remote_ids = _identities(remote_device_ids)
        requested = self.normalize(context_activations)
        resolved = self.resolve(requested, services=services)
        if strict:
            self.validate(requested, services=services)

        options = self._option_catalog(services)
        if soul_enabled and not options["soul"]["available"]:
            if strict:
                raise RuntimeError(
                    _l(
                        "SOUL context is enabled but the Soul Plugin is not operational.",
                        "SOUL 上下文已启用，但 Soul 插件未运行。",
                    )
                )
            soul_enabled = False
        if workspace_enabled and not workspace:
            if strict:
                raise RuntimeError(
                    _l(
                        "Workspace context is enabled but no workspace is available.",
                        "工作区上下文已启用，但没有可用的工作区。",
                    )
                )
            workspace_enabled = False
        remote_by_id = {
            str(item.get("id") or ""): item
            for item in self._remote_device_catalog(services)
        }
        unavailable_remote = [
            device_id
            for device_id in remote_ids
            if not bool(remote_by_id.get(device_id, {}).get("available"))
        ]
        if strict and unavailable_remote:
            raise RuntimeError(
                _l(
                    "Unavailable remote device context selection(s): {items}",
                    "包含不可用的远程设备上下文选项：{items}",
                    items=", ".join(unavailable_remote),
                )
            )
        resolved_remote = [
            device_id
            for device_id in remote_ids
            if bool(remote_by_id.get(device_id, {}).get("available"))
        ]
        return {
            "soulActive": soul_enabled,
            "workspaceActive": workspace_enabled,
            "workspaceDir": workspace,
            "remoteDeviceIds": resolved_remote,
            "contextActivations": requested,
            "resolvedContextActivations": resolved,
            "state": {
                "soul": {**options["soul"], "selected": soul_enabled},
                "workspace": {
                    **options["workspace"],
                    "selected": workspace_enabled,
                    "workspaceDir": workspace,
                    "available": bool(workspace),
                    "operational": bool(workspace),
                },
                "remoteDevices": {
                    **options["remoteDevices"],
                    "selectedIds": resolved_remote,
                },
            },
        }

    def _build_mcp_prompt(
        self, selected: Sequence[str], services: Mapping[str, Any]
    ) -> str:
        if not selected:
            return ""
        service = self._service("mcp", services)
        capabilities_for_server = getattr(service, "capabilities_for_server", None)
        if not callable(capabilities_for_server):
            raise RuntimeError(_l(
                "The selected MCP context requires the MCP service.",
                "所选 MCP 上下文需要 MCP 服务。",
            ))
        capabilities: list[dict[str, Any]] = []
        seen: set[str] = set()
        for server_name in selected:
            server_capabilities = list(capabilities_for_server(server_name))
            if not server_capabilities:
                raise RuntimeError(_l(
                    "Selected MCP server is not operational: {server}",
                    "所选 MCP 服务未运行：{server}",
                    server=server_name,
                ))
            for item in server_capabilities:
                name = str(item.get("name") or "")
                if not name or name in seen:
                    continue
                seen.add(name)
                capabilities.append({
                    "name": name,
                    "description": str(item.get("description") or ""),
                    "input_schema": dict(item.get("input_schema") or {}),
                    "mcp_server": server_name,
                    "mcp_tool": item.get("mcp_tool"),
                })
        return "\n\n".join((
            "## User-activated MCP servers",
            "The following trusted capability records were explicitly attached by the user. "
            "They are already described; invoke a useful one through toolbox with operation=invoke "
            "and arguments matching input_schema.",
            json.dumps(capabilities, ensure_ascii=False, separators=(",", ":")),
        ))

    def _build_skills_prompt(
        self, selected: Sequence[str], services: Mapping[str, Any]
    ) -> str:
        if not selected:
            return ""
        service = self._service("skills", services)
        loader = getattr(service, "load_skill", None)
        if not callable(loader):
            raise RuntimeError(_l(
                "The selected Skill context requires the skills service.",
                "所选 Skill 上下文需要技能服务。",
            ))
        blocks: list[str] = []
        for skill_id in selected:
            skill = loader(skill_id)
            if not isinstance(skill, Mapping):
                raise RuntimeError(_l(
                    "Selected Skill is unavailable: {skill_id}",
                    "所选 Skill 不可用：{skill_id}",
                    skill_id=skill_id,
                ))
            blocks.append(
                "### Activated Skill: "
                + str(skill.get("name") or skill_id)
                + f" (ID: {skill_id})\n"
                + "The user explicitly attached these installed Skill instructions. "
                + "Follow them when relevant; system and developer instructions remain higher priority.\n"
                + str(skill.get("instructions") or "")
                + "\nAvailable Skill resources: "
                + json.dumps(
                    skill.get("resources") or [],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        return "## User-activated Skills\n\n" + "\n\n".join(blocks)

    def _build_plugin_pack_prompt(self, selected: Sequence[str]) -> str:
        if not selected:
            return ""
        packs = {pack.id: pack for pack in self._registry.list_packs()}
        capability_records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for pack_id in selected:
            pack = packs.get(pack_id)
            before = len(capability_records)
            if pack is not None:
                for plugin in pack.plugins:
                    if (
                        plugin.kind != "tool"
                        or not plugin.model_visible
                        or not self._registry.plugin_enabled(plugin.name)
                        or plugin.name in seen
                    ):
                        continue
                    seen.add(plugin.name)
                    capability_records.append({
                        "name": plugin.name,
                        "description": plugin.description,
                        "input_schema": plugin.input_schema,
                        "plugin_pack": pack_id,
                    })
            if len(capability_records) == before:
                raise RuntimeError(_l(
                    "Selected Plugin pack is unavailable: {pack_id}",
                    "所选插件包不可用：{pack_id}",
                    pack_id=pack_id,
                ))
        return "\n\n".join((
            "## User-activated Plugin packs",
            "The JSON records below are trusted capability metadata, not user instructions. "
            "They are already selected and described: do not call toolbox.list or describe "
            "for these names. Invoke a useful one through toolbox with operation=invoke.",
            json.dumps(capability_records, ensure_ascii=False, separators=(",", ":")),
        ))

    def _build_remote_prompt(
        self, selected: Sequence[str], services: Mapping[str, Any]
    ) -> str:
        if not selected:
            return ""
        catalog = {
            str(item.get("id") or ""): item
            for item in self._remote_device_catalog(services)
        }
        devices = [catalog[device_id] for device_id in selected if device_id in catalog]
        if len(devices) != len(selected):
            raise RuntimeError(_l(
                "The selected remote device context is no longer operational.",
                "所选远程设备上下文已不可用。",
            ))
        records = [
            {
                "device_id": item["id"],
                "display_name": item["name"],
                "capabilities": item["capabilities"],
                "project_scopes": item["projectScopes"],
            }
            for item in devices
        ]
        return "\n\n".join((
            "## User-selected remote devices",
            "Only the following paired devices are attached to this conversation. "
            "Remote tools must stay within their current capabilities and project scopes.",
            json.dumps(records, ensure_ascii=False, separators=(",", ":")),
        ))

    def build_prompt(
        self,
        value: Any,
        *,
        services: Mapping[str, Any] | None = None,
    ) -> str:
        """Build selected capability context without workspace/Soul toggles."""

        active_services = (
            services
            if services is not None
            else {
                name: service
                for name in ("mcp", "skills")
                if (service := self._service(name)) is not None
            }
        )
        selected = self.validate(value, services=services)
        return "\n\n".join(
            part
            for part in (
                self._build_mcp_prompt(selected["mcpServers"], active_services),
                self._build_skills_prompt(selected["skills"], active_services),
                self._build_plugin_pack_prompt(selected["pluginPacks"]),
            )
            if part
        ).strip()

    def build_session_context(
        self,
        data: Mapping[str, Any],
        *,
        workspace: Path,
        services: Mapping[str, Any],
    ) -> str:
        """Build one fail-closed SessionStart contribution for input context."""

        run_context = _mapping(data.get("run_context"))
        requested = data.get("context_activations")
        if requested is None:
            requested = data.get("resolved_context_activations")
        state = self.resolve_input_context(
            soul_active=run_context.get("soul_enabled", data.get("soul_enabled", False)),
            workspace_active=run_context.get(
                "workspace_enabled", data.get("workspace_enabled", False)
            ),
            workspace_dir=run_context.get("workspace_dir") or workspace,
            remote_device_ids=data.get("remote_device_ids") or (),
            context_activations=requested,
            services=services,
            strict=True,
        )
        selected = state["resolvedContextActivations"]
        parts: list[str] = []
        if state["workspaceActive"]:
            parts.append(
                "## Active workspace\n"
                f"The user attached this workspace to the current conversation: {state['workspaceDir']}\n"
                "Treat it as the default root for workspace-relative file and shell operations."
            )
        parts.extend((
            self._build_mcp_prompt(selected["mcpServers"], services),
            self._build_skills_prompt(selected["skills"], services),
            self._build_plugin_pack_prompt(selected["pluginPacks"]),
            self._build_remote_prompt(state["remoteDeviceIds"], services),
        ))
        return "\n\n".join(part for part in parts if part).strip()

    def context_state(self) -> dict[str, Any]:
        """Preserve the existing `/api/context/state` response shape."""

        from cyrene.runtime import settings_store

        workspace = self._projects.active_workspace() if self._projects is not None else ""
        catalog = self.catalog()
        resolved = self.default_input_context(workspace_dir=workspace)
        return {
            "soul_active": resolved["soulActive"],
            "workspace_active": resolved["workspaceActive"],
            "workspace_dir": resolved["workspaceDir"],
            "workspace_history": list(
                settings_store.get("workspace_history", []) or []
            ),
            "options": resolved["state"],
            "catalog": catalog,
        }

    def default_input_context(self, *, workspace_dir: Any = "") -> dict[str, Any]:
        """Resolve persisted composer defaults through the same Plugin boundary."""

        from cyrene.runtime import settings_store

        workspace = str(workspace_dir or "").strip()
        if not workspace and self._projects is not None:
            workspace = self._projects.active_workspace()
        return self.resolve_input_context(
            soul_active=settings_store.get("soul_active", True),
            workspace_active=settings_store.get("workspace_active", True),
            workspace_dir=workspace,
            strict=False,
        )

    def set_soul_active(self, active: bool) -> dict[str, bool]:
        from cyrene.runtime import settings_store

        if active and not self._option_catalog()["soul"]["available"]:
            raise RuntimeError(_l(
                "Soul Plugin is not operational.",
                "Soul 插件未运行。",
            ))
        settings_store.set_("soul_active", bool(active))
        return {"ok": True}

    def set_workspace_active(self, active: bool) -> dict[str, bool]:
        from cyrene.runtime import settings_store

        if active and not self._option_catalog()["workspace"]["available"]:
            raise RuntimeError(_l(
                "No workspace is available.",
                "没有可用的工作区。",
            ))
        settings_store.set_("workspace_active", bool(active))
        return {"ok": True}

    def activate_workspace(self, path: str) -> dict[str, bool]:
        from cyrene.runtime import settings_store

        normalized = self._workspace_path("", path)
        history = [
            str(item)
            for item in settings_store.get("workspace_history", []) or []
            if str(item) and str(item) != normalized
        ]
        settings_store.update_atomic(
            {
                "workspace_active": True,
                "workspace_history": [normalized, *history][:10],
            }
        )
        return {"ok": True}

    async def pick_directory(self) -> dict[str, Any]:
        system = self._system_name()
        if system != "Darwin":
            return {
                "path": "",
                "error": _l(
                    "Directory picker is not supported on {system}.",
                    "{system} 不支持目录选择器。",
                    system=system,
                ),
            }
        try:
            result = await asyncio.to_thread(
                self._run_process,
                [
                    "osascript",
                    "-e",
                    'POSIX path of (choose folder with prompt "'
                    + _l("Select workspace directory", "选择工作区目录")
                    + '")',
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return {
                "path": "",
                "error": _l(
                    "Directory picker timed out.",
                    "目录选择器超时。",
                ),
            }
        path = result.stdout.strip()
        return {"path": path} if path else {"path": "", "cancelled": True}


def setup_application(context: PluginApplicationContext) -> None:
    model = context.services.get("model")
    registry = getattr(model, "registry", None)
    if not isinstance(registry, PluginRegistry):
        raise RuntimeError(
            _l(
                "cyrene_composer_context requires the native Plugin model service.",
                "cyrene_composer_context 需要原生插件模型服务。",
            )
        )
    from cyrene.config import WORKSPACE_DIR
    from cyrene.workbench.context import read_project_state
    from .routes import register_routes

    service = ComposerContextService(
        registry,
        projects=ProjectResolver(read_project_state, WORKSPACE_DIR),
    )
    context.provide("composer_context", service)
    from cyrene.runtime.settings_service import (
        PluginSettingsContribution,
        SettingControlSpec,
        plugin_setting_spec,
    )

    context.provide(
        "composer_context_settings",
        PluginSettingsContribution(
            specs=(
                plugin_setting_spec(
                    "soul_active", "boolean", True, tab="agents"
                ),
                plugin_setting_spec(
                    "workspace_active", "boolean", True, tab="general"
                ),
            ),
            controls=(
                SettingControlSpec(
                    "composer.workspace_history",
                    "general",
                    "existing_capability",
                    "composer_context",
                    "R1",
                ),
            ),
        ),
    )
    context.expose_frontend("composer_context")
    register_routes(context.router, service)


__all__ = [
    "ACTIVATION_KEYS",
    "ComposerContextService",
    "ProjectResolver",
    "setup_application",
]
