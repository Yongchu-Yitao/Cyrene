from fastapi import APIRouter, Request

from cyrene.extensions.application_service import ExtensionApplicationError, ExtensionApplicationService
from route.extension_routes.errors import extension_error


def register_lifecycle_routes(router: APIRouter, service: ExtensionApplicationService) -> None:
    @router.post("/api/extensions/install")
    async def api_install_extension(request: Request):
        body = await request.json()
        try:
            return service.start_install(body)
        except ExtensionApplicationError as exc:
            return extension_error(exc)

    @router.delete("/api/extensions/{kind}/{extension_id}")
    async def api_uninstall_extension(kind: str, extension_id: str, version: str = ""):
        try:
            return await service.uninstall(kind, extension_id, version)
        except ExtensionApplicationError as exc:
            return extension_error(exc)

    @router.post("/api/extensions/toolchains/{extension_id}/default")
    async def api_set_default_toolchain(extension_id: str, request: Request):
        body = await request.json()
        try:
            return await service.set_default(extension_id, str(body.get("version") or ""))
        except ExtensionApplicationError as exc:
            return extension_error(exc)

    @router.post("/api/extensions/{kind}/{extension_id}/enabled")
    async def api_set_extension_enabled(kind: str, extension_id: str, request: Request):
        body = await request.json()
        try:
            return await service.set_enabled(kind, extension_id, body.get("enabled"))
        except ExtensionApplicationError as exc:
            return extension_error(exc)

    @router.post("/api/extensions/bind")
    async def api_bind_system_extension(request: Request):
        body = await request.json()
        try:
            return service.bind(
                str(body.get("extension_id") or ""), str(body.get("path") or "")
            )
        except ExtensionApplicationError as exc:
            return extension_error(exc)

    @router.post("/api/extensions/unbind")
    async def api_unbind_system_extension(request: Request):
        body = await request.json()
        return service.unbind(str(body.get("extension_id") or ""))
