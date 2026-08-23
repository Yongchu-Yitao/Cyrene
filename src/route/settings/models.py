"""Thin HTTP adapter for model settings."""

from __future__ import annotations

from fastapi import APIRouter, Request

from route.settings.model_service import ModelSettingsApplicationService


def register_model_routes(
    router: APIRouter,
    service: ModelSettingsApplicationService,
) -> None:
    @router.get("/api/settings/models")
    async def api_get_models(request: Request):
        payload = service.get_settings()
        project_id = str(request.query_params.get("project_id") or "").strip()
        if project_id:
            from cyrene.plugins.integrations import chat_model_candidates

            plugin_models = await chat_model_candidates(project_id)
            payload["selectable_models"] = list(payload.get("selectable_models") or []) + plugin_models
        return payload

    @router.put("/api/settings/models")
    async def api_update_models(request: Request):
        return await service.update_settings(await request.json())
