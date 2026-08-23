"""Settings adapters for the local Microsoft PowerPoint integration."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from cyrene.office.gateway import get_office_gateway_runtime
from cyrene.office.installation import (
    OfficeInstallationError,
    install_powerpoint_addin,
    integration_status,
    remove_powerpoint_addin,
)


def register_office_integration_routes(router: APIRouter) -> None:
    @router.get("/api/settings/integrations/office")
    async def get_office_integration_status():
        return await asyncio.to_thread(integration_status)

    @router.post("/api/settings/integrations/office/install")
    async def install_office_integration():
        runtime = get_office_gateway_runtime()
        await runtime.start()
        try:
            return await asyncio.to_thread(install_powerpoint_addin)
        except OfficeInstallationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/api/settings/integrations/office/install")
    async def remove_office_integration():
        try:
            return await asyncio.to_thread(remove_powerpoint_addin)
        except OfficeInstallationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/settings/integrations/office/manifest")
    async def download_office_manifest():
        files = get_office_gateway_runtime().files
        files.ensure()
        return FileResponse(
            files.manifest_path,
            filename="cyrene-powerpoint-addin.xml",
            media_type="application/xml",
        )


__all__ = ["register_office_integration_routes"]
