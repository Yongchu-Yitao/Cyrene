from fastapi import APIRouter, Request

from cyrene.extensions.application_service import (
    ExtensionApplicationError,
    ExtensionApplicationService,
)
from route.extension_routes.errors import extension_error


def register_skill_routes(router: APIRouter, service: ExtensionApplicationService) -> None:
    @router.post("/api/extensions/skills/inspect")
    async def api_inspect_skill_source(request: Request):
        body = await request.json()
        try:
            return await service.inspect_skill(str(body.get("url") or ""))
        except ExtensionApplicationError as exc:
            return extension_error(exc)

    @router.post("/api/extensions/skills/install")
    async def api_install_local_skill(request: Request):
        body = await request.json()
        try:
            return service.install_skill_path(str(body.get("path") or ""))
        except ExtensionApplicationError as exc:
            return extension_error(exc)

    @router.post("/api/extensions/skills/install-upload")
    async def api_install_uploaded_skill(request: Request):
        form = await request.form()
        try:
            return await service.install_skill_upload(form.get("file"))
        except ExtensionApplicationError as exc:
            return extension_error(exc)

    @router.post("/api/extensions/skills/install-picker")
    async def api_pick_and_install_local_skill():
        try:
            return await service.pick_and_install_skill()
        except ExtensionApplicationError as exc:
            return extension_error(exc)
