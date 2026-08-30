"""Thin HTTP adapters for application updates."""

from fastapi import APIRouter

from cyrene.platform.update_service import UpdateApplicationError, UpdateApplicationService
from cyrene.workbench.http.errors import error_response


def register_update_routes(
    router: APIRouter, application_service: UpdateApplicationService
) -> None:
    @router.get("/api/update/check")
    async def api_update_check():
        return await application_service.check()

    @router.get("/api/update/changelog")
    async def api_update_changelog():
        return await application_service.changelog()

    @router.post("/api/update/download")
    async def api_update_download():
        return await application_service.download()

    @router.get("/api/update/progress")
    async def api_update_progress():
        return application_service.progress()

    @router.post("/api/update/restart")
    async def api_update_restart():
        try:
            return await application_service.restart()
        except UpdateApplicationError as exc:
            return error_response(str(exc), exc.status_code, exc.code)


__all__ = ["register_update_routes"]
