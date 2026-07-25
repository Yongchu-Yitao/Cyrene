"""Usage routes."""

# ruff: noqa: F403,F405

from cyrene.workbench_runtime import *


def register_usage_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    global _bot, _db_path
    _bot = bot
    _db_path = db_path

    # ---- Token Usage API ----

    @router.get("/api/usage/tokens")
    async def api_token_usage(days: int = 7, model: str = ""):
        from cyrene.db import get_token_usage_stats
        stats = await get_token_usage_stats(str(DB_PATH), days=max(1, min(days, 90)), model=model.strip())
        return {"ok": True, "stats": stats}
