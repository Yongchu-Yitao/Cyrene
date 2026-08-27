"""Installation-keyed ACP stdio process lifecycle.

Phase 1 keeps one shared ACP process per installation id.  The manager is the
only place that turns an installation record into a running transport; it
re-validates the record (driver, enabled, install state, bare command) and
applies the runtime's own argument allowlist so Manifest-provided args are
never executed (handoff §17).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Awaitable, Callable

from cyrene.agent_runtime.acp_transport import (
    AcpStdioTransport,
    build_safe_env,
    is_valid_bare_command,
)
from cyrene.agent_runtime.errors import AgentRuntimeError
from cyrene.localization import localized

logger = logging.getLogger(__name__)

ACP_STDIO_DRIVER = "acp_stdio"

# Built-in ACP stdio invocation profiles.  These are the *only* argument
# sources in phase 1: known recommended agents map to fixed built-in args, and
# every other agent runs with zero args.  Manifest/installation args are
# deliberately ignored and logged (never executed) until a reviewed profile
# mechanism lands.
BUILTIN_PROFILE_ARGS: dict[str, tuple[str, ...]] = {
    "opencode": ("acp",),
    "codex-acp": (),
    "pi-acp": (),
}


def configured_agent_proxy_environment() -> dict[str, str]:
    """Return the explicit HTTP proxy selected in Settings, or nothing."""
    from cyrene.runtime.network_proxy import proxy_environment

    return proxy_environment()


def profile_args_for(agent_id: str) -> tuple[str, ...]:
    return BUILTIN_PROFILE_ARGS.get(str(agent_id or "").strip(), ())


_MANAGED_BIN_CHECKED = False
_MANAGED_BIN_RESULT: str | None = None


def _managed_runtime_bin_dir() -> str | None:
    """Bin directory holding the managed Node.js/npm runtime, if resolvable.

    ACP adapter shims are ``#!/usr/bin/env node`` wrappers, so the managed
    Node runtime must be visible to the child even when the parent process was
    launched from a minimal GUI PATH. The optional environment Plugin exposes
    this through the application service port. The result is memoized per
    process because it only affects newly spawned children.
    """
    global _MANAGED_BIN_CHECKED, _MANAGED_BIN_RESULT
    if _MANAGED_BIN_CHECKED:
        return _MANAGED_BIN_RESULT
    try:
        from agent.plugin import active_plugin_service

        service = active_plugin_service("extensions")
        provider = getattr(service, "managed_runtime_bin_directory", None)
        managed_bin = str(provider() or "") if callable(provider) else ""
    except Exception:
        logger.debug("managed npm runtime unavailable for ACP PATH injection", exc_info=True)
        managed_bin = ""
    _MANAGED_BIN_CHECKED = True
    _MANAGED_BIN_RESULT = managed_bin or None
    return _MANAGED_BIN_RESULT


def agent_child_path_dirs(installation: dict[str, Any]) -> list[str]:
    """Extra PATH directories to prepend for an ACP child process.

    Managed installs live under ``extensions/agents/<agent_id>/<version>``;
    the adapter shim and any bundled runtime dependency (e.g. the ``pi``
    executable for pi-acp) sit on that prefix's ``node_modules/.bin``.
    Prepending that directory plus the managed Node bin directory lets
    adapters resolve their own executables regardless of the parent PATH.
    """
    dirs: list[str] = []
    managed_path = str(installation.get("managed_path") or "").strip()
    if managed_path:
        # Resolve only the directory itself, not the shim file: npm's
        # node_modules/.bin shims are symlinks to the real script, so
        # resolving the file would inject the package's dist/ dir instead of
        # the .bin directory that holds sibling executables (e.g. pi).
        shim_dir = Path(managed_path).parent.resolve()
        if shim_dir.is_dir() and str(shim_dir) not in dirs:
            dirs.append(str(shim_dir))
    managed_bin = _managed_runtime_bin_dir()
    if managed_bin and managed_bin not in dirs:
        dirs.append(managed_bin)
    return dirs


def prepend_path_dirs(path: str, dirs: list[str]) -> str:
    """Return ``path`` with ``dirs`` prepended, order preserved and deduped."""
    from cyrene.runtime.user_path import merge_path_entries

    return merge_path_entries(*dirs, path)


def agent_child_path(installation: dict[str, Any]) -> str:
    """Return the effective PATH used to validate and spawn an Agent.

    Managed bins stay first for reproducibility, while the complete user PATH
    remains available as a fallback for every Agent command and runtime
    dependency. ``ensure_user_path`` augments ``os.environ`` during normal app
    startup, including GUI launches whose inherited PATH is minimal.
    """
    return prepend_path_dirs(
        os.environ.get("PATH", ""),
        agent_child_path_dirs(installation),
    )


class AcpProcessManager:
    """Spawns, caches, and reaps ACP stdio transports keyed by installation."""

    def __init__(
        self,
        *,
        transport_factory: Callable[..., AcpStdioTransport] | None = None,
        which_fn: Callable[[str], str | None] | None = None,
    ) -> None:
        self._transports: dict[str, AcpStdioTransport] = {}
        self._transport_signatures: dict[str, str] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
        self._transport_factory = transport_factory or AcpStdioTransport
        self._which_fn = which_fn

    # ------------------------------------------------------------------
    # Validation (stable AgentRuntimeError kinds)
    # ------------------------------------------------------------------

    def validate_installation(self, installation: dict[str, Any] | None) -> None:
        """Validate the states that must hold *before* any process starts.

        Raises ``AgentRuntimeError`` with stable kinds: ``protocol_mismatch``
        (wrong driver), ``agent_disabled`` (explicitly disabled),
        ``agent_crashed`` (runtime in a failed state), and
        ``dependency_missing`` (incomplete install / unresolvable command).
        """
        if not isinstance(installation, dict):
            raise AgentRuntimeError(
                "dependency_missing",
                localized("agent installation record is missing", "缺少智能体安装记录"),
            )
        driver = str(installation.get("driver") or "").strip()
        if driver and driver != ACP_STDIO_DRIVER:
            raise AgentRuntimeError(
                "protocol_mismatch",
                localized(
                    f"installation driver {driver!r} is not {ACP_STDIO_DRIVER!r}",
                    f"安装驱动 {driver!r} 不是 {ACP_STDIO_DRIVER!r}",
                ),
                detail={"driver": driver, "expected": ACP_STDIO_DRIVER},
            )
        if installation.get("enabled") is False:
            raise AgentRuntimeError(
                "agent_disabled",
                localized(
                    "agent is disabled; enable it before connecting",
                    "智能体已停用，请先启用再连接",
                ),
                detail={"installationId": installation.get("installation_id")},
            )
        install_state = str(installation.get("install_state") or "installed")
        if install_state != "installed":
            raise AgentRuntimeError(
                "dependency_missing",
                localized(
                    f"agent is not installed (install_state={install_state!r})",
                    f"智能体尚未安装（install_state={install_state!r}）",
                ),
                detail={"install_state": install_state},
            )
        runtime_state = str(installation.get("runtime_state") or "").strip().lower()
        if runtime_state in {"error", "crashed", "failed"}:
            raise AgentRuntimeError(
                "agent_crashed",
                localized(
                    f"agent runtime is in a failed state ({runtime_state!r}); restart the agent",
                    f"智能体运行时处于失败状态（{runtime_state!r}），请重启智能体",
                ),
                detail={"runtime_state": runtime_state},
                retryable=True,
            )
        command = str(installation.get("command") or "").strip()
        if not is_valid_bare_command(command):
            raise AgentRuntimeError(
                "dependency_missing",
                localized(
                    "agent command is not a bare executable name",
                    "智能体命令不是有效的可执行文件名",
                ),
                detail={"command": command},
            )
        managed_path = str(installation.get("managed_path") or "").strip()
        search_path = agent_child_path(installation)
        managed_command_available = bool(managed_path and Path(managed_path).is_file())
        path_command = (
            self._which(command, search_path)
            if managed_path and not managed_command_available
            else None
        )
        if managed_path and not managed_command_available and not path_command:
            raise AgentRuntimeError(
                "dependency_missing",
                localized(
                    f"Agent executable {command!r} is unavailable in its managed install and on PATH",
                    f"智能体可执行文件 {command!r} 在托管安装目录和 PATH 中均不可用",
                ),
                detail={"command": command},
            )
        # The owning Plugin records executable dependencies declaratively.
        # The runtime validates those names without knowing which Plugin or
        # catalog produced the installation record.
        raw_dependencies = installation.get("runtime_dependencies")
        dependencies = raw_dependencies if isinstance(raw_dependencies, list) else []
        for raw_dependency in dependencies:
            dependency_bin = str(raw_dependency or "").strip()
            if not is_valid_bare_command(dependency_bin):
                raise AgentRuntimeError(
                    "dependency_missing",
                    localized(
                        "Agent runtime dependency is not a bare executable name",
                        "智能体运行时依赖不是有效的可执行文件名",
                    ),
                    detail={"command": command, "dependency": dependency_bin},
                )
            shim_name = dependency_bin + (".cmd" if os.name == "nt" else "")
            managed_dependency_available = bool(
                managed_path and (Path(managed_path).parent / shim_name).is_file()
            )
            if not managed_dependency_available and not self._which(dependency_bin, search_path):
                raise AgentRuntimeError(
                    "dependency_missing",
                    localized(
                        f"Agent runtime dependency {dependency_bin!r} is unavailable in its managed install and on PATH",
                        f"智能体运行时依赖 {dependency_bin!r} 在托管安装目录和 PATH 中均不可用",
                    ),
                    detail={"command": command, "dependency": dependency_bin},
                )

    def _which(self, command: str, search_path: str) -> str | None:
        """Resolve a bare command with the same PATH policy used for spawning."""
        if self._which_fn is not None:
            return self._which_fn(command)
        return shutil.which(command, path=search_path)

    def resolve_args(self, installation: dict[str, Any]) -> tuple[str, ...]:
        """Return the built-in profile args for this installation.

        Manifest-provided args are never used in phase 1; they are logged so an
        operator can see the profile is pending review.
        """
        agent_id = str(installation.get("agent_id") or "").strip()
        args = profile_args_for(agent_id)
        manifest = installation.get("manifest")
        declared_args: Any = None
        if isinstance(manifest, dict):
            drivers = manifest.get("drivers")
            if isinstance(drivers, list):
                for driver in drivers:
                    if isinstance(driver, dict) and str(driver.get("kind") or "") == ACP_STDIO_DRIVER:
                        declared_args = driver.get("args")
                        break
        if declared_args:
            logger.warning(
                "ignoring manifest-provided args for agent %r (profile args %r used; "
                "manifest args not executed in phase 1)",
                agent_id,
                args,
            )
        return args

    # ------------------------------------------------------------------
    # Transport lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    def _spawn_signature(
        *,
        command: str,
        args: tuple[str, ...],
        env: dict[str, str],
        cwd: str | None,
    ) -> str:
        """Fingerprint process inputs without retaining credential values.

        Cyrene-managed model access is represented by a short-lived gateway
        token in the child environment.  Reusing an ACP process after that
        token changes leaves the Agent holding a revoked credential because a
        running process cannot have its environment updated.
        """
        payload = json.dumps(
            {
                "command": command,
                "args": list(args),
                "cwd": cwd or "",
                # PATH carries only the injected runtime dirs, which cannot be
                # changed inside a running process; excluding it avoids tearing
                # down live transports when npm discoverability changes
                # (extension enablement, nvm installs) mid-session.
                "env": sorted((key, value) for key, value in env.items() if key != "PATH"),
            },
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    async def get_transport(
        self,
        installation: dict[str, Any],
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> AcpStdioTransport:
        """Return the shared transport for an installation, creating it on demand.

        The installation record is re-validated on every request. A running
        transport is reused only when its complete spawn configuration still
        matches; scoped model credentials and proxy changes force a restart.
        """
        installation_id = str(installation.get("installation_id") or "")
        if not installation_id:
            raise AgentRuntimeError(
                "dependency_missing",
                localized(
                    "installation record has no installation_id",
                    "安装记录缺少 installation_id",
                ),
            )
        self.validate_installation(installation)
        args = self.resolve_args(installation)
        command = str(installation.get("command") or "").strip()
        child_env = build_safe_env(
            base=os.environ,
            extra={**configured_agent_proxy_environment(), **(env or {})},
        )
        child_env["PATH"] = prepend_path_dirs(
            child_env.get("PATH", ""),
            agent_child_path_dirs(installation),
        )
        signature = self._spawn_signature(
            command=command,
            args=args,
            env=child_env,
            cwd=cwd,
        )
        transport = self._transports.get(installation_id)
        if (
            transport is not None
            and not transport.is_closed
            and self._transport_signatures.get(installation_id) == signature
        ):
            return transport
        async with self._global_lock:
            transport = self._transports.get(installation_id)
            if (
                transport is not None
                and not transport.is_closed
                and self._transport_signatures.get(installation_id) == signature
            ):
                return transport
            if transport is not None:
                self._transports.pop(installation_id, None)
                self._transport_signatures.pop(installation_id, None)
                try:
                    await transport.close()
                except Exception:
                    logger.exception(
                        "Failed to replace stale ACP transport for %s",
                        installation_id,
                    )
            managed_path = str(installation.get("managed_path") or "").strip()
            which_fn = self._which_fn
            if which_fn is None:
                def which_fn(name: str) -> str | None:
                    if managed_path and name == command and Path(managed_path).is_file():
                        return managed_path
                    return shutil.which(name, path=child_env.get("PATH"))

            transport = self._transport_factory(
                command,
                args,
                env=child_env,
                cwd=cwd,
                which_fn=which_fn,
            )
            await transport.start()
            self._transports[installation_id] = transport
            self._transport_signatures[installation_id] = signature
            return transport

    async def acquire_transport(
        self,
        installation: dict[str, Any],
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> tuple[AcpStdioTransport, Callable[[], Awaitable[None]]]:
        """Lease one installation transport exclusively to a connection.

        ACP stdio has one notification stream, so sharing a transport between
        concurrent sessions would misroute events. The lease serializes turns,
        probes and authentication while preserving the process between turns.
        """
        installation_id = str(installation.get("installation_id") or "")
        lock = self._locks.setdefault(installation_id, asyncio.Lock())
        await lock.acquire()
        try:
            transport = await self.get_transport(installation, env=env, cwd=cwd)
        except BaseException:
            lock.release()
            raise

        async def release_lease() -> None:
            try:
                await self.release(installation_id)
            finally:
                if lock.locked():
                    lock.release()

        return transport, release_lease

    async def release(self, installation_id: str) -> None:
        """Close and forget the transport for one installation."""
        target = str(installation_id or "")
        transport = self._transports.pop(target, None)
        self._transport_signatures.pop(target, None)
        if transport is not None:
            try:
                await transport.close()
            except Exception:
                logger.exception("Failed to close ACP transport for %s", target)

    async def close_all(self) -> None:
        """Gracefully shut down every managed transport."""
        transports = list(self._transports.values())
        self._transports.clear()
        self._transport_signatures.clear()
        if not transports:
            return
        results = await asyncio.gather(
            *(transport.close() for transport in transports),
            return_exceptions=True,
        )
        for transport, result in zip(transports, results):
            if isinstance(result, Exception):
                logger.warning("ACP transport close failed: %s", result)

    def active_count(self) -> int:
        return sum(1 for transport in self._transports.values() if not transport.is_closed)

    def get(self, installation_id: str) -> AcpStdioTransport | None:
        transport = self._transports.get(str(installation_id or ""))
        if transport is not None and not transport.is_closed:
            return transport
        return None


_DEFAULT_PROCESS_MANAGER: AcpProcessManager | None = None


def get_process_manager() -> AcpProcessManager:
    """Return the process-wide ACP process manager singleton."""
    global _DEFAULT_PROCESS_MANAGER
    if _DEFAULT_PROCESS_MANAGER is None:
        _DEFAULT_PROCESS_MANAGER = AcpProcessManager()
    return _DEFAULT_PROCESS_MANAGER
