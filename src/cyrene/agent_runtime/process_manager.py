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
    """Return the explicit localhost proxy selected in Settings, or nothing."""
    from cyrene.runtime.config_store import get_setting

    if get_setting("external_agent_proxy_enabled", False) is not True:
        return {}
    try:
        port = int(get_setting("external_agent_proxy_port", 7897))
    except (TypeError, ValueError):
        return {}
    if not 1 <= port <= 65535:
        return {}
    proxy_url = f"http://127.0.0.1:{port}"
    return {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "ALL_PROXY": proxy_url,
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "all_proxy": proxy_url,
        # Cyrene-managed model access uses a loopback gateway.  It must never
        # leave the machine or be routed back through the user's proxy.
        "NO_PROXY": "127.0.0.1,localhost,::1",
        "no_proxy": "127.0.0.1,localhost,::1",
    }


def profile_args_for(agent_id: str) -> tuple[str, ...]:
    return BUILTIN_PROFILE_ARGS.get(str(agent_id or "").strip(), ())


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
                "agent installation record is missing",
            )
        driver = str(installation.get("driver") or "").strip()
        if driver and driver != ACP_STDIO_DRIVER:
            raise AgentRuntimeError(
                "protocol_mismatch",
                f"installation driver {driver!r} is not {ACP_STDIO_DRIVER!r}",
                detail={"driver": driver, "expected": ACP_STDIO_DRIVER},
            )
        if installation.get("enabled") is False:
            raise AgentRuntimeError(
                "agent_disabled",
                "agent is disabled; enable it before connecting",
                detail={"installationId": installation.get("installation_id")},
            )
        install_state = str(installation.get("install_state") or "installed")
        if install_state != "installed":
            raise AgentRuntimeError(
                "dependency_missing",
                f"agent is not installed (install_state={install_state!r})",
                detail={"install_state": install_state},
            )
        runtime_state = str(installation.get("runtime_state") or "").strip().lower()
        if runtime_state in {"error", "crashed", "failed"}:
            raise AgentRuntimeError(
                "agent_crashed",
                f"agent runtime is in a failed state ({runtime_state!r}); restart the agent",
                detail={"runtime_state": runtime_state},
                retryable=True,
            )
        command = str(installation.get("command") or "").strip()
        if not is_valid_bare_command(command):
            raise AgentRuntimeError(
                "dependency_missing",
                "agent command is not a bare executable name",
                detail={"command": command},
            )
        managed_path = str(installation.get("managed_path") or "").strip()
        if managed_path and not Path(managed_path).is_file():
            raise AgentRuntimeError(
                "dependency_missing",
                "managed Agent executable is missing",
                detail={"command": command},
            )

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
                "env": sorted(env.items()),
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
                "installation record has no installation_id",
            )
        self.validate_installation(installation)
        args = self.resolve_args(installation)
        command = str(installation.get("command") or "").strip()
        child_env = build_safe_env(
            base=os.environ,
            extra={**configured_agent_proxy_environment(), **(env or {})},
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
                which_fn = lambda name: (
                    managed_path
                    if managed_path and name == command
                    else shutil.which(name, path=child_env.get("PATH"))
                )
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
