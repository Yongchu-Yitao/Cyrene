"""Usage routes."""

from typing import Any

from fastapi import APIRouter


def register_usage_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    # ---- Token Usage API ----

    @router.get("/api/usage/tokens")
    async def api_token_usage(days: int = 7, model: str = ""):
        from cyrene.runtime.database import get_token_usage_stats
        stats = await get_token_usage_stats(db_path, days=max(1, min(days, 90)), model=model.strip())
        return {"ok": True, "stats": stats}
