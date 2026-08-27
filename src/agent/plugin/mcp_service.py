"""Native MCP configuration, lifecycle, and dynamic Plugin registration."""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import re
import shutil
import threading
import urllib.parse
import weakref
from collections.abc import Awaitable, Callable, Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any, TypeVar

from cyrene.config import DATA_DIR

from .mcp_content import serialize_mcp_result
from .mcp_transport import BLOCKED_EXECUTABLES, MCPServerConnection
from .plugin import Plugin, PluginContext, PluginPack
from .registry import PluginRegistry

logger = logging.getLogger(__name__)

_CONFIG_KEY = "mcp_servers"
_REDACTED = "[configured]"
_SUPPORTED_TRANSPORTS = {
    "stdio",
    "sse",
    "streamable_http",
    "streamable-http",
    "http",
}
_IDENTITY_PART = re.compile(r"[^A-Za-z0-9_.-]+")
_T = TypeVar("_T")


def _load_configs() -> list[dict[str, Any]]:
    from cyrene.runtime.settings_store import get as get_setting

    stored = get_setting(_CONFIG_KEY, [])
    if not isinstance(stored, list):
        return []
    return [dict(item) for item in stored if isinstance(item, Mapping)]


def _save_configs(configs: list[dict[str, Any]]) -> None:
    from cyrene.runtime.settings_store import set_ as set_setting

    set_setting(_CONFIG_KEY, configs)


def _canonical_transport(value: Any) -> str:
    transport = str(value or "stdio").strip().lower()
    if transport in {"streamable-http", "http"}:
        return "streamable_http"
    return transport


def validate_mcp_configs(configs: Any) -> list[dict[str, Any]]:
    """Validate and normalize persisted MCP server declarations."""

    if not isinstance(configs, list):
        raise ValueError("MCP servers must be an array")
    normalized: list[dict[str, Any]] = []
    names: set[str] = set()
    for raw in configs:
        if not isinstance(raw, Mapping):
            raise ValueError("MCP server configuration must be an object")
        server = dict(raw)
        name = str(server.get("name") or "").strip()
        if not name:
            raise ValueError("MCP server name is required")
        if len(name) > 200:
            raise ValueError("MCP server name is too long")
        if name in names:
            raise ValueError(f"Duplicate MCP server name: {name}")
        names.add(name)
        server["name"] = name
        server["enabled"] = bool(server.get("enabled", True))
        transport = _canonical_transport(server.get("transport"))
        if transport not in _SUPPORTED_TRANSPORTS:
            raise ValueError(f"Unsupported MCP transport: {transport}")
        server["transport"] = transport
        if transport == "stdio":
            command = str(server.get("command") or "").strip()
            args = server.get("args") or []
            if not isinstance(args, list):
                raise ValueError(f"MCP server '{name}' args must be an array")
            server["args"] = [str(item) for item in args]
            env = server.get("env") or {}
            if not isinstance(env, Mapping):
                raise ValueError(f"MCP server '{name}' env must be an object")
            server["env"] = {str(key): str(value) for key, value in env.items()}
            if server["enabled"] and not command:
                raise ValueError(f"MCP server '{name}' command is required")
            if command:
                executable = pathlib.Path(command).stem.lower()
                if executable in BLOCKED_EXECUTABLES:
                    raise ValueError(f"MCP command is not allowed: {command}")
                invocation = " ".join([command, *server["args"]]).casefold()
                if "@latest" in invocation:
                    raise ValueError(
                        "MCP runtime downloads must use an exact version, not @latest"
                    )
                if not pathlib.Path(command).is_absolute():
                    resolved = shutil.which(command)
                    if not resolved:
                        raise ValueError(
                            "MCP command must be an existing deterministic executable"
                        )
                    command = str(pathlib.Path(resolved).resolve())
            server["command"] = command
        else:
            url = str(server.get("url") or "").strip()
            parsed = urllib.parse.urlparse(url)
            local_http = parsed.scheme == "http" and parsed.hostname in {
                "127.0.0.1",
                "localhost",
                "::1",
            }
            if server["enabled"] and (
                not url or (parsed.scheme != "https" and not local_http)
            ):
                raise ValueError(
                    "Remote MCP URLs must use HTTPS (local loopback HTTP is allowed)"
                )
            if parsed.username or parsed.password:
                raise ValueError("Remote MCP URL must not embed credentials")
            headers = server.get("headers") or {}
            if not isinstance(headers, Mapping):
                raise ValueError(f"MCP server '{name}' headers must be an object")
            server["url"] = url
            server["headers"] = {
                str(key): str(value) for key, value in headers.items()
            }
        normalized.append(server)
    return normalized


def redact_mcp_configs(configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in configs:
        server = dict(raw)
        for field in ("env", "headers"):
            values = server.get(field)
            if isinstance(values, Mapping) and values:
                server[field] = {str(key): _REDACTED for key in values}
        result.append(server)
    return result


def merge_redacted_mcp_configs(
    existing: list[dict[str, Any]],
    incoming: Any,
) -> list[dict[str, Any]]:
    if not isinstance(incoming, list):
        raise ValueError("MCP servers must be an array")
    previous = {
        str(item.get("name") or ""): item
        for item in existing
        if isinstance(item, Mapping)
    }
    merged: list[dict[str, Any]] = []
    for raw in incoming:
        if not isinstance(raw, Mapping):
            raise ValueError("MCP server configuration must be an object")
        server = dict(raw)
        old = previous.get(str(server.get("name") or ""), {})
        for field in ("env", "headers"):
            values = server.get(field)
            old_values = old.get(field) if isinstance(old.get(field), Mapping) else {}
            if isinstance(values, Mapping):
                server[field] = {
                    str(key): old_values.get(key) if value == _REDACTED else value
                    for key, value in values.items()
                    if value != _REDACTED or key in old_values
                }
        merged.append(server)
    return merged


def _identity_part(value: str, *, maximum: int = 72) -> str:
    normalized = _IDENTITY_PART.sub("_", str(value or "").strip()).strip("_.-")
    if not normalized or not normalized[0].isalnum():
        normalized = "item_" + normalized
    if len(normalized) <= maximum:
        return normalized
    digest = sha256(str(value).encode("utf-8")).hexdigest()[:10]
    return f"{normalized[: maximum - 11]}_{digest}"


def _deduplicated_identity(
    base: str,
    source: str,
    used: set[str],
    *,
    maximum: int = 128,
) -> str:
    candidate = base[:maximum]
    if candidate not in used:
        used.add(candidate)
        return candidate
    digest = sha256(source.encode("utf-8")).hexdigest()[:10]
    candidate = f"{base[: maximum - 11]}_{digest}"
    counter = 2
    while candidate in used:
        suffix = f"_{digest}_{counter}"
        candidate = f"{base[: maximum - len(suffix)]}{suffix}"
        counter += 1
    used.add(candidate)
    return candidate


class MCPPluginService:
    """Own configured MCP connections and project them into Plugin registries."""

    def __init__(self, *, data_directory: str | Path = DATA_DIR) -> None:
        self.data_directory = Path(data_directory).expanduser().resolve()
        self.content_directory = (
            self.data_directory / "plugin_data" / "cyrene_mcp" / "content"
        )
        self._state_lock = threading.RLock()
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self._lifecycle_lock: asyncio.Lock | None = None
        self._started = False
        self._connections: dict[str, MCPServerConnection] = {}
        self._packs: dict[str, PluginPack] = {}
        self._server_pack_ids: dict[str, str] = {}
        self._server_errors: dict[str, str] = {}
        self._registries: weakref.WeakKeyDictionary[
            PluginRegistry, set[str]
        ] = weakref.WeakKeyDictionary()
        self._registry_errors: dict[str, str] = {}

    @property
    def started(self) -> bool:
        with self._state_lock:
            return self._started

    def configs(self, *, redacted: bool = False) -> list[dict[str, Any]]:
        configs = _load_configs()
        return redact_mcp_configs(configs) if redacted else configs

    def pack_id_for_server(self, server_name: str) -> str:
        name = str(server_name or "").strip()
        with self._state_lock:
            existing = self._server_pack_ids.get(name)
        if existing:
            return existing
        return self._allocated_pack_ids(self.configs()).get(
            name,
            f"mcp.{_identity_part(name)}",
        )

    @staticmethod
    def _allocated_pack_ids(
        configs: list[dict[str, Any]],
    ) -> dict[str, str]:
        used: set[str] = set()
        result: dict[str, str] = {}
        names = sorted(
            {
                str(config.get("name") or "").strip()
                for config in configs
                if str(config.get("name") or "").strip()
            }
        )
        for name in names:
            result[name] = _deduplicated_identity(
                f"mcp.{_identity_part(name)}",
                name,
                used,
            )
        return result

    def attach_registry(self, registry: PluginRegistry) -> None:
        """Attach a live Registry and immediately publish connected server packs."""

        if not isinstance(registry, PluginRegistry):
            raise TypeError("registry must be a PluginRegistry")
        with self._state_lock:
            self._registries.setdefault(registry, set())
            packs = dict(self._packs)
        self._sync_registry(registry, packs)

    async def _on_owner(
        self,
        operation: Callable[[], Awaitable[_T]],
        *,
        adopt: bool = False,
    ) -> _T:
        current = asyncio.get_running_loop()
        with self._state_lock:
            owner = self._owner_loop
            if owner is not None and owner.is_closed():
                owner = None
                self._owner_loop = None
                self._lifecycle_lock = None
            if owner is None:
                if not adopt:
                    adopt = True
                self._owner_loop = current
                self._lifecycle_lock = asyncio.Lock()
                owner = current
        if owner is current:
            return await operation()
        if not owner.is_running():
            raise RuntimeError("MCP Plugin service owner loop is not running")
        future = asyncio.run_coroutine_threadsafe(operation(), owner)
        return await asyncio.wrap_future(future)

    async def startup(self) -> None:
        await self._on_owner(self._startup_owned, adopt=True)

    async def restart(self) -> None:
        await self._on_owner(self._restart_owned, adopt=True)

    async def shutdown(self) -> None:
        await self._on_owner(self._shutdown_owned, adopt=True)

    def shutdown_sync(self) -> None:
        """Best-effort synchronous shutdown for non-async host finalizers."""

        with self._state_lock:
            owner = self._owner_loop
        if owner is not None and owner.is_running():
            try:
                current = asyncio.get_running_loop()
            except RuntimeError:
                current = None
            if current is owner:
                owner.create_task(self._shutdown_owned())
                return
            asyncio.run_coroutine_threadsafe(self._shutdown_owned(), owner).result(
                timeout=15
            )
            return
        asyncio.run(self._shutdown_owned())

    async def _startup_owned(self) -> None:
        lock = self._lifecycle_lock
        if lock is None:
            lock = asyncio.Lock()
            self._lifecycle_lock = lock
        async with lock:
            if self._started:
                return
            await self._connect_owned()
            with self._state_lock:
                self._started = True

    async def _restart_owned(self) -> None:
        lock = self._lifecycle_lock
        if lock is None:
            lock = asyncio.Lock()
            self._lifecycle_lock = lock
        async with lock:
            await self._disconnect_owned()
            await self._connect_owned()
            with self._state_lock:
                self._started = True

    async def _shutdown_owned(self) -> None:
        lock = self._lifecycle_lock
        if lock is None:
            lock = asyncio.Lock()
            self._lifecycle_lock = lock
        async with lock:
            await self._disconnect_owned()
            with self._state_lock:
                self._started = False
                self._owner_loop = None
                self._lifecycle_lock = None

    async def _connect_owned(self) -> None:
        connections: dict[str, MCPServerConnection] = {}
        errors: dict[str, str] = {}
        configs = self.configs()
        pack_ids = self._allocated_pack_ids(configs)
        for config in configs:
            name = str(config.get("name") or "").strip()
            if not name or not config.get("enabled", True):
                continue
            connection = MCPServerConnection(config)
            try:
                await connection.start()
            except asyncio.CancelledError:
                await connection.stop()
                for staged in connections.values():
                    await staged.stop()
                raise
            except Exception as exc:
                errors[name] = str(exc)
                await connection.stop()
                logger.warning("Failed to connect MCP server '%s': %s", name, exc)
            else:
                connections[name] = connection
        packs, pack_errors = self._build_packs(connections, pack_ids)
        errors.update(pack_errors)
        with self._state_lock:
            self._connections = connections
            self._packs = packs
            self._server_pack_ids = pack_ids
            self._server_errors = errors
            registries = tuple(self._registries)
        for registry in registries:
            self._sync_registry(registry, packs)

    async def _disconnect_owned(self) -> None:
        with self._state_lock:
            connections = tuple(self._connections.values())
            self._connections = {}
            self._packs = {}
            self._server_pack_ids = {}
            self._server_errors = {}
            self._registry_errors = {}
            registries = tuple(self._registries)
        for connection in connections:
            try:
                await connection.stop()
            except Exception:
                logger.exception(
                    "Failed to disconnect MCP server '%s'", connection.name
                )
        for registry in registries:
            self._sync_registry(registry, {})

    def _build_packs(
        self,
        connections: dict[str, MCPServerConnection],
        server_pack_ids: dict[str, str],
    ) -> tuple[dict[str, PluginPack], dict[str, str]]:
        packs: dict[str, PluginPack] = {}
        errors: dict[str, str] = {}
        used_plugins: set[str] = set()
        for server_name in sorted(connections):
            connection = connections[server_name]
            pack_id = server_pack_ids[server_name]
            server_part = _identity_part(pack_id.removeprefix("mcp."))
            plugins: list[Plugin] = []
            tool_errors: list[str] = []
            for tool in sorted(
                connection.tools,
                key=lambda item: str(item.get("name") or ""),
            ):
                tool_name = str(tool.get("name") or "").strip()
                if not tool_name:
                    continue
                plugin_name = _deduplicated_identity(
                    f"mcp__{server_part}__{_identity_part(tool_name)}",
                    f"{server_name}\0{tool_name}",
                    used_plugins,
                )
                schema = dict(tool.get("input_schema") or {"type": "object"})
                schema.setdefault("type", "object")

                async def invoke(
                    arguments: dict[str, Any],
                    _context: PluginContext,
                    *,
                    target_server: str = server_name,
                    target_tool: str = tool_name,
                ) -> Any:
                    return await self.invoke(target_server, target_tool, arguments)

                try:
                    plugins.append(
                        Plugin(
                            name=plugin_name,
                            description=(
                                "External MCP integration metadata (untrusted): "
                                + str(tool.get("description") or tool_name)
                            ),
                            input_schema=schema,
                            handler=invoke,
                            timeout_seconds=connection.tool_timeout_seconds + 2.0,
                            metadata={
                                "mcp": True,
                                "mcp_server": server_name,
                                "mcp_tool": tool_name,
                            },
                        )
                    )
                except Exception as exc:
                    tool_errors.append(f"{tool_name}: {exc}")
            packs[server_name] = PluginPack(
                id=pack_id,
                description=f"Tools provided by MCP server {server_name}.",
                plugins=tuple(plugins),
            )
            if tool_errors:
                errors[server_name] = "; ".join(tool_errors)
        return packs, errors

    def _sync_registry(
        self,
        registry: PluginRegistry,
        packs: dict[str, PluginPack],
    ) -> None:
        with self._state_lock:
            previous = set(self._registries.get(registry, set()))
        desired = {pack.id for pack in packs.values()}
        retained = set(previous)
        for pack_id in sorted(previous - desired):
            try:
                registry.unregister_pack(pack_id)
            except Exception:
                logger.exception("Failed to remove stale MCP Plugin pack %s", pack_id)
            else:
                retained.discard(pack_id)
        errors: dict[str, str] = {}
        for server_name, pack in packs.items():
            try:
                registry.register_pack(
                    pack,
                    source=f"mcp:{server_name}",
                    replace=pack.id in previous,
                )
            except Exception as exc:
                errors[pack.id] = str(exc)
                logger.warning(
                    "Failed to publish MCP Plugin pack %s: %s", pack.id, exc
                )
            else:
                retained.add(pack.id)
        with self._state_lock:
            self._registries[registry] = retained
            for pack_id in previous | desired:
                self._registry_errors.pop(pack_id, None)
            self._registry_errors.update(errors)

    async def invoke(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        return await self._on_owner(
            lambda: self._invoke_owned(server_name, tool_name, arguments),
            adopt=True,
        )

    async def _invoke_owned(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        raw = await self._invoke_raw_owned(server_name, tool_name, arguments)
        value = serialize_mcp_result(
            server_name,
            tool_name,
            raw,
            content_directory=self.content_directory,
        )
        if raw.get("is_error"):
            detail = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            raise RuntimeError(detail)
        return value

    async def invoke_raw(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Call a server-scoped MCP tool for native host integrations."""

        return await self._on_owner(
            lambda: self._invoke_raw_owned(server_name, tool_name, arguments),
            adopt=True,
        )

    async def _invoke_raw_owned(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._started:
            await self._startup_owned()
        with self._state_lock:
            connection = self._connections.get(str(server_name or "").strip())
        if connection is None:
            raise ValueError(f"MCP server '{server_name}' is not connected")
        available = {str(tool.get("name") or "") for tool in connection.tools}
        if tool_name not in available:
            raise ValueError(
                f"MCP tool '{tool_name}' not found on server '{server_name}'"
            )
        return await connection.call_tool(tool_name, dict(arguments or {}))

    def status(self) -> list[dict[str, Any]]:
        with self._state_lock:
            connections = dict(self._connections)
            errors = dict(self._server_errors)
            pack_ids = dict(self._server_pack_ids)
            packs = dict(self._packs)
            registry_errors = dict(self._registry_errors)
        result: list[dict[str, Any]] = []
        for config in self.configs():
            name = str(config.get("name") or "").strip()
            connection = connections.get(name)
            pack_id = pack_ids.get(name) or f"mcp.{_identity_part(name)}"
            error = (
                errors.get(name)
                or (connection.error if connection is not None else "")
                or registry_errors.get(pack_id, "")
            )
            enabled = bool(config.get("enabled", True))
            if not enabled:
                state = "disabled"
            elif connection is None:
                state = "error" if error else "disconnected"
            else:
                state = connection.status
            tools = []
            if connection is not None:
                pack = packs.get(name)
                plugin_by_tool = {
                    str(plugin.metadata.get("mcp_tool") or ""): plugin.name
                    for plugin in (pack.plugins if pack is not None else ())
                }
                tools = [
                    {
                        "name": str(tool.get("name") or ""),
                        "plugin": plugin_by_tool.get(str(tool.get("name") or ""), ""),
                        "description": str(tool.get("description") or ""),
                    }
                    for tool in connection.tools
                ]
            result.append(
                {
                    "name": name,
                    "transport": config.get("transport", "stdio"),
                    "command": config.get("command", ""),
                    "url": config.get("url", ""),
                    "enabled": enabled,
                    "status": state,
                    "error": error,
                    "pack_id": pack_id,
                    "tool_count": len(tools),
                    "tools": tools,
                }
            )
        return result

    def server_status(self, server_name: str) -> dict[str, Any] | None:
        target = str(server_name or "").strip()
        return next(
            (item for item in self.status() if item.get("name") == target),
            None,
        )

    def capabilities_for_server(self, server_name: str) -> list[dict[str, Any]]:
        target = str(server_name or "").strip()
        with self._state_lock:
            pack = self._packs.get(target)
        if pack is None:
            return []
        return [
            {
                "name": plugin.name,
                "description": plugin.description,
                "input_schema": dict(plugin.input_schema),
                "mcp_server": target,
                "mcp_tool": str(plugin.metadata.get("mcp_tool") or ""),
            }
            for plugin in pack.plugins
        ]

    async def replace_configs(
        self,
        configs: Any,
        *,
        merge_redacted: bool = False,
    ) -> list[dict[str, Any]]:
        incoming = (
            merge_redacted_mcp_configs(self.configs(), configs)
            if merge_redacted
            else configs
        )
        normalized = validate_mcp_configs(incoming)
        _save_configs(normalized)
        await self.restart()
        return self.status()

    async def set_enabled(self, server_name: str, enabled: bool) -> dict[str, Any]:
        target = str(server_name or "").strip()
        configs = self.configs()
        matched = False
        for config in configs:
            if str(config.get("name") or "") == target:
                config["enabled"] = bool(enabled)
                matched = True
                break
        if not matched:
            raise ValueError("MCP server not found")
        await self.replace_configs(configs)
        return self.server_status(target) or {
            "name": target,
            "enabled": bool(enabled),
            "status": "disabled" if not enabled else "disconnected",
        }

    async def upsert(self, config: Mapping[str, Any]) -> dict[str, Any]:
        server = dict(config)
        name = str(server.get("name") or "").strip()
        if not name:
            raise ValueError("MCP server name is required")
        configs = [
            item
            for item in self.configs()
            if str(item.get("name") or "") != name
        ]
        await self.replace_configs([*configs, server])
        return self.server_status(name) or {"name": name, "status": "disconnected"}

    async def remove(self, server_name: str) -> dict[str, Any] | None:
        target = str(server_name or "").strip()
        configs = self.configs()
        existing = next(
            (item for item in configs if str(item.get("name") or "") == target),
            None,
        )
        if existing is None:
            return None
        await self.replace_configs(
            [item for item in configs if str(item.get("name") or "") != target]
        )
        return existing


_SERVICE_LOCK = threading.RLock()
_SERVICE: MCPPluginService | None = None


def get_mcp_service() -> MCPPluginService:
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = MCPPluginService()
        return _SERVICE


__all__ = [
    "MCPPluginService",
    "get_mcp_service",
    "merge_redacted_mcp_configs",
    "redact_mcp_configs",
    "validate_mcp_configs",
]
