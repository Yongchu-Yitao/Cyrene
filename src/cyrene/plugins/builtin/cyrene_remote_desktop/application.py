"""Application contribution for the Remote Desktop Plugin pack."""

from __future__ import annotations

from typing import Any

from cyrene.plugins.context import PluginApplicationContext

from .service import RemoteDesktopError, RemoteDesktopService


def setup_application(context: PluginApplicationContext) -> None:
    service = RemoteDesktopService()
    context.provide("remote_desktop", service)

    async def call(arguments: Any, _request_context: dict[str, Any]) -> dict[str, Any]:
        try:
            return await service.controller_request(arguments)
        except RemoteDesktopError as exc:
            return {"ok": False, "code": exc.code, "error": str(exc)}

    context.provide_frontend_method("call", call)
    context.on_shutdown(service.shutdown)


__all__ = ["setup_application"]
