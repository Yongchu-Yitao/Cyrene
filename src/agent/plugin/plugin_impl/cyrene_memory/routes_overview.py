"""Memory overview and persona HTTP routes owned by the memory Plugin."""

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field


class SoulUpdateBody(BaseModel):
    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)

    content: str = Field(default="", max_length=200_000)


def register_memory_routes(
    router: APIRouter,
    memory_service,
) -> None:
    # ---- Memory API ----

    @router.get("/api/memory")
    async def api_memory():
        return await memory_service.overview()


def register_soul_routes(
    router: APIRouter,
    memory_service,
) -> None:
    @router.get("/api/settings/soul")
    async def api_get_soul():
        return {"content": memory_service.read_soul()}

    @router.put("/api/settings/soul")
    async def api_update_soul(body: SoulUpdateBody):
        memory_service.write_soul(body.content)
        return {"ok": True}


def register_conversation_search_routes(
    router: APIRouter,
    memory_service,
) -> None:
    @router.get("/api/search/conversations")
    async def api_search_conversations(q: str = "", limit: int = 30):
        query = str(q or "").strip()
        if not query:
            return {"ok": False, "error": "query is required"}
        results = await memory_service.search_conversations(query, limit)
        return {"ok": True, "results": results}


__all__ = [
    "register_conversation_search_routes",
    "register_memory_routes",
    "register_soul_routes",
]
