"""Plugin-owned conversation and Workbench search routes."""

from fastapi import APIRouter

from cyrene.workbench.artifacts.presentation_service import PresentationQueryService
from cyrene.workbench.http.errors import localized_error_payload


def register_search_routes(
    router: APIRouter,
    queries: PresentationQueryService,
) -> None:
    # ---- Workbench global search ----

    @router.get("/api/workbench/search")
    async def api_workbench_search(q: str = "", types: str = "", limit: int = 50):
        """Global search across Workbench data: projects, chats, knowledge, memory, schedule.

        ``types`` is a comma-separated filter (default: all). Per-type limits are
        derived from ``limit`` so a broad query still returns balanced groups.
        """
        query = str(q or "").strip()
        if not query:
            return {
                "ok": False,
                **localized_error_payload(
                    "A search query is required.",
                    "请输入搜索内容。",
                    "query_required",
                ),
            }

        all_types = queries.search_types
        requested = {t.strip().lower() for t in (types or "").split(",") if t.strip()}
        active_types = requested & all_types if requested else all_types
        per_type_limit = max(1, min(limit, 100))

        results = await queries.search_workbench(query, active_types, per_type_limit)
        return {"ok": True, "groups": results}
