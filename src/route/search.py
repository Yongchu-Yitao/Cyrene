"""Conversation and Workbench search routes."""

# ruff: noqa: F403,F405

from cyrene.workbench_runtime import *


def register_search_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    global _bot, _db_path
    _bot = bot
    _db_path = db_path

    # ---- Search API ----

    @router.get("/api/search/conversations")
    async def api_search_conversations(q: str = "", limit: int = 30):
        if not q.strip():
            return {"ok": False, "error": "query is required"}
        results = await search_conversations_structured(q.strip(), limit=max(1, min(limit, 100)))
        return {"ok": True, "results": results}

    # ---- Workbench global search ----

    @router.get("/api/workbench/search")
    async def api_workbench_search(q: str = "", types: str = "", limit: int = 50):
        """Global search across Workbench data: projects, tasks, chats, knowledge, memory, schedule.

        ``types`` is a comma-separated filter (default: all). Per-type limits are
        derived from ``limit`` so a broad query still returns balanced groups.
        """
        query = str(q or "").strip()
        if not query:
            return {"ok": False, "error": "query is required"}

        all_types = {"project", "task", "chat", "knowledge", "memory", "schedule"}
        requested = {t.strip().lower() for t in (types or "").split(",") if t.strip()}
        active_types = requested & all_types if requested else all_types
        per_type_limit = max(1, min(limit, 100))

        results = await _search_workbench_items(query, active_types, per_type_limit)
        return {"ok": True, "groups": results}
