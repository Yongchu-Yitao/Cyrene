"""Legacy memory routes."""

# ruff: noqa: F403,F405

from cyrene.workbench.runtime import *


def register_memory_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    global _bot, _db_path
    _bot = bot
    _db_path = db_path

    # ---- Memory API ----

    @router.get("/api/memory")
    async def api_memory():
        return await _build_memory()
