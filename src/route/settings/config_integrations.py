"""Configuration, integration, and local-model HTTP adapters."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene.runtime.config_integration_service import (
    ConfigIntegrationApplicationService,
    ConfigIntegrationError,
)

def _error_response(exc: ConfigIntegrationError) -> JSONResponse:
    return JSONResponse(exc.payload, status_code=exc.status_code)


def register_config_read_routes(
    router: APIRouter,
    service: ConfigIntegrationApplicationService,
) -> None:
    @router.get("/api/settings/config")
    async def api_get_config():
        return service.config()

    @router.get("/api/settings/storage")
    async def api_get_storage():
        return await service.storage()



def register_namespace_routes(
    router: APIRouter,
    service: ConfigIntegrationApplicationService,
) -> None:
    @router.get("/api/settings/namespaces/{namespace}")
    async def api_get_settings_namespace(namespace: str):
        try:
            return await service.read_namespace(namespace)
        except ConfigIntegrationError as exc:
            return _error_response(exc)

    @router.put("/api/settings/namespaces/{namespace}")
    async def api_update_settings_namespace(namespace: str, request: Request):
        body = await request.json()
        try:
            return await service.update_namespace(namespace, body)
        except ConfigIntegrationError as exc:
            return _error_response(exc)

    @router.put("/api/settings/config")
    async def api_update_config(request: Request):
        body = await request.json()
        try:
            return await service.update_config(body)
        except ConfigIntegrationError as exc:
            return _error_response(exc)



def register_local_model_routes(
    router: APIRouter,
    service: ConfigIntegrationApplicationService,
) -> None:
    @router.get("/api/settings/integrations")
    async def api_get_integration_settings():
        """Return Zotero integration settings."""
        return service.integration_settings()

    @router.get("/api/settings/local-models/status")
    async def api_local_models_status():
        return service.local_model_status()

    @router.post("/api/settings/local-models/ocr-runtime/download")
    async def api_download_ocr_runtime():
        try:
            return service.download_ocr_runtime()
        except ConfigIntegrationError as exc:
            return _error_response(exc)

    @router.post("/api/settings/local-models/{model_id}/download")
    async def api_download_local_model(model_id: str):
        try:
            return service.download_local_model(model_id)
        except ConfigIntegrationError as exc:
            return _error_response(exc)

    @router.delete("/api/settings/local-models/{model_id}")
    async def api_delete_local_model(model_id: str):
        try:
            return await service.delete_local_model(model_id)
        except ConfigIntegrationError as exc:
            return _error_response(exc)



def register_integration_routes(
    router: APIRouter,
    service: ConfigIntegrationApplicationService,
) -> None:
    @router.put("/api/settings/integrations")
    async def api_update_integration_settings(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
        try:
            return service.update_integration(body)
        except ConfigIntegrationError as exc:
            return _error_response(exc)

    @router.post("/api/settings/integrations/test")
    async def api_test_integration(request: Request):
        """Probe unsaved Zotero settings and return only safe metadata."""
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
        try:
            return await service.test_integration(body)
        except ConfigIntegrationError as exc:
            return _error_response(exc)



def register_config_integration_routes(
    router: APIRouter,
    service: ConfigIntegrationApplicationService,
) -> None:
    register_config_read_routes(router, service)
    register_namespace_routes(router, service)
    register_local_model_routes(router, service)
    register_integration_routes(router, service)
