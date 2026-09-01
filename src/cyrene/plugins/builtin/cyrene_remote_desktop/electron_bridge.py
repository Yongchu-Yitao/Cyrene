"""Authenticated loopback bridge to Cyrene's Electron desktop host."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx


def electron_desktop_available() -> bool:
    return bool(
        str(os.environ.get("CYRENE_ELECTRON_RPC_PORT") or "").strip()
        and str(os.environ.get("CYRENE_ELECTRON_RPC_TOKEN") or "").strip()
    )


async def electron_desktop_rpc(
    method: str,
    arguments: dict[str, Any] | None = None,
    *,
    timeout: float = 45.0,
) -> dict[str, Any]:
    port = str(os.environ.get("CYRENE_ELECTRON_RPC_PORT") or "").strip()
    token = str(os.environ.get("CYRENE_ELECTRON_RPC_TOKEN") or "").strip()
    if not port or not token:
        return {
            "ok": False,
            "code": "desktop_host_unavailable",
            "error": "Remote Desktop requires the Cyrene Electron desktop host.",
        }
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.post(
                f"http://127.0.0.1:{port}/desktop/rpc",
                headers={
                    "X-Cyrene-Token": token,
                    "Content-Type": "application/json",
                },
                content=json.dumps(
                    {"method": str(method or ""), "args": dict(arguments or {})},
                    ensure_ascii=False,
                ),
            )
            response.raise_for_status()
            value = response.json()
    except httpx.TimeoutException:
        return {
            "ok": False,
            "code": "desktop_host_timeout",
            "error": "The desktop host did not respond before the timeout.",
        }
    except Exception:
        return {
            "ok": False,
            "code": "desktop_host_error",
            "error": "The desktop host bridge failed.",
        }
    if not isinstance(value, dict):
        return {
            "ok": False,
            "code": "desktop_host_invalid_result",
            "error": "The desktop host returned an invalid result.",
        }
    return value


__all__ = ["electron_desktop_available", "electron_desktop_rpc"]
