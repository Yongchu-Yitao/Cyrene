"""Memory overview and conversation HTTP routes owned by the memory Plugin."""

from fastapi import APIRouter


def register_memory_routes(
    router: APIRouter,
    memory_service,
) -> None:
    # ---- Memory API ----

    @router.get("/api/memory")
    async def api_memory():
        return await memory_service.overview()


def register_conversation_search_routes(
    router: APIRouter,
    memory_service,
) -> None:
    @router.get("/api/search/conversations")
    async def api_search_conversations(q: str = "", limit: int = 30):
        query = str(q or "").strip()
        if not query:
            return {"ok": False, "error": "query_required"}
        results = await memory_service.search_conversations(query, limit)
        return {"ok": True, "results": results}


__all__ = [
    "register_conversation_search_routes",
    "register_memory_routes",
]
