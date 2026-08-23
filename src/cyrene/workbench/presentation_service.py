"""Public query boundary for legacy Workbench presentation projections."""

from __future__ import annotations

from typing import Any

from cyrene.workbench import presentation_runtime


class PresentationQueryService:
    """Expose read-only UI projections without leaking runtime internals to HTTP."""

    async def search_workbench(
        self,
        query: str,
        types: set[str],
        per_type_limit: int,
    ) -> dict[str, list[dict[str, Any]]]:
        return await presentation_runtime._search_workbench_items(
            query,
            types,
            per_type_limit,
        )

    async def memory(self) -> dict[str, Any]:
        return await presentation_runtime._build_memory()

    async def ui_data(self, timezone_name: str = "") -> dict[str, Any]:
        return await presentation_runtime._build_ui_data(timezone_name)

    async def dashboard(self, timezone_name: str = "") -> dict[str, Any]:
        timezone = presentation_runtime._resolve_ui_tz(timezone_name)
        return await presentation_runtime._build_dashboard(timezone)

    def config(self) -> dict[str, Any]:
        return presentation_runtime._build_config()

    def user(self) -> dict[str, Any]:
        return presentation_runtime._build_user()

    def search_config(self) -> dict[str, Any]:
        return presentation_runtime._build_search_config()
