"""Status, reload and package-switch APIs for custom Python tools."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from cyrene.custom_tools.manager import get_custom_tool_manager
from cyrene.runtime import config_store
from cyrene.runtime.settings_service import SettingsServiceError


class CustomToolPackageUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool
    expected_revision: int | None = Field(default=None, ge=0, strict=True)


def _error(exc: Exception, status: int = 500) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": str(exc), "type": type(exc).__name__},
        status_code=status,
    )


def register_custom_tool_routes(
    router: APIRouter,
    _bot: Any = None,
    _db_path: str = "",
) -> None:
    """Expose discovery state; source editing uses Cyrene's normal file tools."""

    @router.get("/api/custom-tools/status")
    async def api_custom_tool_status():
        manager = get_custom_tool_manager()
        try:
            return {"ok": True, **manager.status()}
        except Exception as exc:
            return _error(exc)

    @router.post("/api/custom-tools/reload")
    async def api_reload_custom_tools():
        manager = get_custom_tool_manager()
        try:
            if not manager.running:
                await manager.start()
                status = manager.status()
            else:
                status = await manager.reload(reason="api")
            return {"ok": True, **status}
        except Exception as exc:
            return _error(exc)

    @router.put("/api/custom-tools/packages/{package_id}/enabled")
    async def api_set_custom_tool_package_enabled(
        package_id: str,
        update: CustomToolPackageUpdate,
    ):
        manager = get_custom_tool_manager()
        try:
            status = await manager.set_package_enabled(
                package_id,
                bool(update.enabled),
                expected_revision=update.expected_revision,
            )
            return {"ok": True, **status}
        except KeyError as exc:
            return _error(exc, 404)
        except config_store.SettingsRevisionConflict as exc:
            return _error(exc, 409)
        except (SettingsServiceError, TypeError, ValueError) as exc:
            return _error(exc, 400)
        except Exception as exc:
            return _error(exc)


__all__ = ["CustomToolPackageUpdate", "register_custom_tool_routes"]
