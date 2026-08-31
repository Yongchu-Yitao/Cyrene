"""HTTP adapters for Remote Desktop state and one-shot frame upload."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .schemas import (
    DisplaySelectRequest,
    LayoutProjectionRequest,
    MicrophoneRequest,
    QualityRequest,
    SessionCreateRequest,
    SessionReconnectRequest,
)
from .service import RemoteDesktopError, RemoteDesktopService


async def _invoke(awaitable):
    try:
        return await awaitable
    except RemoteDesktopError as exc:
        return JSONResponse(
            {"ok": False, "code": exc.code, "error": exc.message},
            status_code=exc.status_code,
        )


def register_routes(router: APIRouter, service: RemoteDesktopService) -> None:
    @router.get("/api/remote-desktop/cards", tags=["Remote Desktop"])
    async def cards():
        return await _invoke(service.cards())

    @router.get("/api/remote-desktop/sessions", tags=["Remote Desktop"])
    async def sessions():
        return service.list_sessions()

    @router.post("/api/remote-desktop/sessions", tags=["Remote Desktop"])
    async def create(request: SessionCreateRequest):
        return await _invoke(service.connect(request.model_dump()))

    @router.get("/api/remote-desktop/sessions/{session_id}", tags=["Remote Desktop"])
    async def get(session_id: str):
        try:
            return service.get_session(session_id)
        except RemoteDesktopError as exc:
            return JSONResponse({"ok": False, "code": exc.code, "error": exc.message}, status_code=exc.status_code)

    @router.post("/api/remote-desktop/sessions/{session_id}/reconnect", tags=["Remote Desktop"])
    async def reconnect(session_id: str, request: SessionReconnectRequest):
        return await _invoke(service.reconnect(session_id, request.offer.model_dump()))

    @router.delete("/api/remote-desktop/sessions/{session_id}", tags=["Remote Desktop"])
    async def disconnect(session_id: str):
        return await _invoke(service.disconnect(session_id))

    @router.get("/api/remote-desktop/sessions/{session_id}/displays", tags=["Remote Desktop"])
    async def displays(session_id: str):
        return await _invoke(service.displays(session_id))

    @router.put("/api/remote-desktop/sessions/{session_id}/display", tags=["Remote Desktop"])
    async def display(session_id: str, request: DisplaySelectRequest):
        return await _invoke(service.select_display(session_id, request.display_id))

    @router.put("/api/remote-desktop/sessions/{session_id}/quality", tags=["Remote Desktop"])
    async def quality(session_id: str, request: QualityRequest):
        return await _invoke(service.set_quality(session_id, request.quality_mode))

    @router.put("/api/remote-desktop/sessions/{session_id}/microphone", tags=["Remote Desktop"])
    async def microphone(session_id: str, request: MicrophoneRequest):
        return await _invoke(service.set_microphone(session_id, request.enabled))

    @router.post("/api/remote-desktop/sessions/{session_id}/credentials/request", tags=["Remote Desktop"])
    async def credentials(session_id: str):
        return await _invoke(service.request_credentials(session_id))

    @router.post("/api/remote-desktop/layout-grants", tags=["Remote Desktop"])
    async def layout(request: LayoutProjectionRequest):
        return await _invoke(service.project_layout(request.model_dump()))

    @router.get("/api/remote-desktop/diagnostics/{device_id}", tags=["Remote Desktop"])
    async def diagnostics(device_id: str):
        return await _invoke(service.diagnostics(device_id))

    @router.get("/api/remote-desktop/sessions/{session_id}/observations", tags=["Remote Desktop"])
    async def observations(session_id: str):
        return await service.pending_observations(session_id)

    @router.post("/api/remote-desktop/observations/{observation_id}/frame", tags=["Remote Desktop"])
    async def observation_frame(observation_id: str, request: Request):
        raw = await request.body()
        return await _invoke(service.submit_observation_frame(observation_id, raw))


__all__ = ["register_routes"]
