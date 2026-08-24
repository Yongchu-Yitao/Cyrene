from __future__ import annotations

import asyncio

from fastapi import APIRouter

from cyrene.workbench.composer_context import context_activation_catalog


def register_context_catalog_routes(router: APIRouter) -> None:
    @router.get("/api/workbench/context-capabilities")
    async def api_workbench_context_capabilities():
        return await asyncio.to_thread(context_activation_catalog)

    @router.get("/api/workbench/slash-commands")
    async def api_workbench_slash_commands(project_id: str = ""):
        from cyrene.workbench.slash_commands import slash_command_catalog

        return {"commands": await slash_command_catalog(project_id)}


__all__ = ["register_context_catalog_routes"]
