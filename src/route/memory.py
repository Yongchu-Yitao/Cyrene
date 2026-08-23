"""Legacy memory routes."""

from fastapi import APIRouter

from cyrene.workbench.presentation_service import PresentationQueryService


def register_memory_routes(
    router: APIRouter,
    queries: PresentationQueryService,
) -> None:
    # ---- Memory API ----

    @router.get("/api/memory")
    async def api_memory():
        return await queries.memory()
