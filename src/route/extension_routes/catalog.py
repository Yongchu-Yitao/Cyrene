from fastapi import APIRouter, Request

from cyrene.extensions.application_service import ExtensionApplicationError, ExtensionApplicationService
from route.extension_routes.errors import extension_error


def register_catalog_routes(router: APIRouter, service: ExtensionApplicationService) -> None:
    @router.get("/api/extensions")
    async def api_extensions():
        return service.list()

    @router.get("/api/extensions/search")
    async def api_search_extensions(kind: str, q: str = "", advanced: bool = False, cursor: str = ""):
        try:
            return await service.search(kind, q, advanced=advanced, cursor=cursor)
        except ExtensionApplicationError as exc:
            return extension_error(exc)

    @router.get("/api/extensions/{kind}/{extension_id}/versions")
    async def api_extension_versions(kind: str, extension_id: str):
        try:
            return await service.versions(kind, extension_id)
        except ExtensionApplicationError as exc:
            return extension_error(exc)

    @router.post("/api/extensions/agents/install-proposals")
    async def api_create_agent_install_proposal(request: Request):
        body = await request.json()
        try:
            return await service.propose_agent(
                body.get("source"), str(body.get("requestedVersion") or "")
            )
        except ExtensionApplicationError as exc:
            return extension_error(exc)

    @router.post("/api/extensions/agents/install-proposals/{proposal_id}/confirm")
    async def api_confirm_agent_install_proposal(proposal_id: str):
        try:
            return await service.confirm_agent(proposal_id)
        except ExtensionApplicationError as exc:
            return extension_error(exc)
