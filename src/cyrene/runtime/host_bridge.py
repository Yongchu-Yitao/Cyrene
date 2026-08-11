"""Narrow Python client for Electron's authenticated ``/host/rpc`` bridge."""

from __future__ import annotations

import os
from typing import Any

import httpx

from cyrene.agent.context import current_run_context

_ALLOWED_METHODS = frozenset({
    "host.status",
    "window.control",
    "ui.snapshot.current",
    "ui.gesture.execute_current",
    "desktop.settings.get",
    "desktop.settings.update",
    "lifecycle.execute_approved",
})
_SURFACE_METHODS = frozenset({
    "window.control", "ui.snapshot.current", "ui.gesture.execute_current",
})


class HostBridgeError(RuntimeError):
    code = "host_error"


class HostUnavailable(HostBridgeError):
    code = "unsupported_host"


class NoCurrentSurface(HostBridgeError):
    code = "no_current_surface"


async def call_host(
    method: str,
    args: dict[str, Any] | None = None,
    *,
    timeout: float = 8.0,
) -> dict[str, Any]:
    normalized_method = str(method or "").strip()
    if normalized_method not in _ALLOWED_METHODS:
        raise HostBridgeError("host method is not allowlisted")
    payload_args = dict(args or {})
    context = current_run_context()
    port = str(os.environ.get("CYRENE_ELECTRON_RPC_PORT") or "").strip()
    token = str(os.environ.get("CYRENE_ELECTRON_RPC_TOKEN") or "").strip()
    if not port or not token:
        if normalized_method in {"ui.snapshot.current", "ui.gesture.execute_current"}:
            if not context.ui_instance_id:
                raise NoCurrentSurface("this run has no current UI surface")
            from cyrene.workbench.ui_surface import request
            result = await request(
                context.ui_instance_id,
                "snapshot" if normalized_method == "ui.snapshot.current" else "act",
                payload_args,
                timeout=timeout,
            )
            if result.get("ok") is False and result.get("error") == "no_current_surface":
                raise NoCurrentSurface("the bound UI surface is closed")
            return result
        if normalized_method == "host.status":
            return {"ok": True, "hostKind": "web", "surfaceAvailable": bool(context.ui_instance_id)}
        raise HostUnavailable("Electron host is unavailable")
    if normalized_method in _SURFACE_METHODS or normalized_method == "host.status":
        if normalized_method in _SURFACE_METHODS and not context.ui_instance_id:
            raise NoCurrentSurface("this run has no current UI surface")
        payload_args["uiInstanceId"] = context.ui_instance_id
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.post(
                f"http://127.0.0.1:{int(port)}/host/rpc",
                headers={"X-Cyrene-Token": token},
                json={"method": normalized_method, "args": payload_args},
            )
            response.raise_for_status()
            result = response.json()
    except (ValueError, httpx.HTTPError) as exc:
        raise HostUnavailable("Electron host did not respond") from exc
    if not isinstance(result, dict):
        raise HostBridgeError("invalid Electron host response")
    if result.get("ok") is False and result.get("error") == "no_current_surface":
        raise NoCurrentSurface("the bound UI surface is closed or no longer owned by Electron")
    return result


async def resolve_conversation_source(ui_instance_id: str) -> str:
    """Classify a renderer as Electron only after the host verifies ownership.

    A renderer-generated instance id is routing state, not an authentication
    claim.  Web clients therefore remain ``webui`` even when they submit an id;
    only a live surface owned and registered by Electron can become
    ``desktop_local``. Focus, visibility and minimization are observable window
    state, not authorization inputs.
    """
    normalized = str(ui_instance_id or "").strip()
    if not normalized:
        return "webui"
    from cyrene.agent.context import bind_run_context

    with bind_run_context(ui_instance_id=normalized):
        try:
            status = await call_host("host.status", {})
        except HostBridgeError:
            return "webui"
    if (
        status.get("ok") is not False
        and status.get("hostKind") == "electron"
        and status.get("surfaceAvailable") is True
    ):
        return "desktop_local"
    return "webui"


__all__ = [
    "HostBridgeError", "HostUnavailable", "NoCurrentSurface", "call_host",
    "resolve_conversation_source",
]
