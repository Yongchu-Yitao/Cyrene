from __future__ import annotations

from fastapi import APIRouter


def register_context_catalog_routes(router: APIRouter) -> None:
    @router.get("/api/workbench/slash-commands")
    async def api_workbench_slash_commands(project_id: str = ""):
        from cyrene.workbench.chat.slash_commands import slash_command_catalog

        return {"commands": await slash_command_catalog(project_id)}


__all__ = ["register_context_catalog_routes"]
