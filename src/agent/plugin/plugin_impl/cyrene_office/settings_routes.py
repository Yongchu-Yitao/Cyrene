"""Settings adapters for the local Microsoft PowerPoint integration."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import FileResponse

from .gateway import get_office_gateway_runtime
from .installation import (
    OfficeInstallationError,
    install_powerpoint_addin,
    integration_status,
    remove_powerpoint_addin,
)
from route.errors import localized_error_response


logger = logging.getLogger(__name__)

_INSTALLATION_ERROR_ZH = {
    "Certificate trust confirmation timed out.": "证书信任确认已超时。",
    (
        "The local Office certificate could not be added to the current user's "
        "trust store."
    ): "无法将本地 Office 证书添加到当前用户的信任存储。",
    (
        "Automatic certificate trust is supported on macOS and Windows only."
    ): "仅 macOS 和 Windows 支持自动信任证书。",
    (
        "Automatic add-in removal is supported on macOS only."
    ): "仅 macOS 支持自动移除加载项。",
}


def _installation_error(exc: OfficeInstallationError, *, action: str):
    message = str(exc)
    translated = _INSTALLATION_ERROR_ZH.get(message)
    logger.info("Office integration %s failed", action, exc_info=True)
    if translated is not None:
        en = message
        zh = translated
    elif action == "install":
        en = "PowerPoint integration installation failed."
        zh = "PowerPoint 集成安装失败。"
    else:
        en = "PowerPoint integration removal failed."
        zh = "PowerPoint 集成移除失败。"
    return localized_error_response(
        en,
        zh,
        400,
        f"office_{action}_failed",
    )


def register_office_integration_routes(router: APIRouter) -> None:
    @router.get("/api/settings/integrations/office")
    async def get_office_integration_status():
        try:
            return await asyncio.to_thread(integration_status)
        except (OSError, RuntimeError) as exc:
            logger.info("Unable to inspect Office integration", exc_info=True)
            return localized_error_response(
                "PowerPoint integration status is unavailable.",
                "PowerPoint 集成状态不可用。",
                503,
                "office_status_unavailable",
            )

    @router.post("/api/settings/integrations/office/install")
    async def install_office_integration():
        runtime = get_office_gateway_runtime()
        was_running = runtime.running
        try:
            await runtime.start()
        except (OSError, RuntimeError) as exc:
            logger.info("Unable to start Office gateway", exc_info=True)
            return localized_error_response(
                "PowerPoint integration service is unavailable.",
                "PowerPoint 集成服务不可用。",
                503,
                "office_service_unavailable",
            )
        try:
            return await asyncio.to_thread(install_powerpoint_addin)
        except OfficeInstallationError as exc:
            if not was_running:
                try:
                    await runtime.stop()
                except (OSError, RuntimeError):
                    logger.warning(
                        "Unable to stop Office gateway after installation failure",
                        exc_info=True,
                    )
            return _installation_error(exc, action="install")

    @router.delete("/api/settings/integrations/office/install")
    async def remove_office_integration():
        try:
            return await asyncio.to_thread(remove_powerpoint_addin)
        except OfficeInstallationError as exc:
            return _installation_error(exc, action="remove")

    @router.get("/api/settings/integrations/office/manifest")
    async def download_office_manifest():
        files = get_office_gateway_runtime().files
        try:
            files.ensure()
        except OSError as exc:
            logger.info("Unable to prepare Office manifest", exc_info=True)
            return localized_error_response(
                "PowerPoint integration manifest is unavailable.",
                "PowerPoint 集成清单不可用。",
                503,
                "office_manifest_unavailable",
            )
        return FileResponse(
            files.manifest_path,
            filename="cyrene-powerpoint-addin.xml",
            media_type="application/xml",
        )


__all__ = ["register_office_integration_routes"]
