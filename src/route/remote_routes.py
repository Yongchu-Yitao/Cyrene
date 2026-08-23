"""Focused HTTP registrars for the remote-control application."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from cyrene.runtime.remote_control import DEFAULT_REMOTE_CAPABILITIES
from cyrene.runtime.remote_services import RemoteApplicationError, RemoteControlApplicationService
from route.remote_schemas import (
    RemoteAuditResponse,
    RemoteChatContextUpdate,
    RemotePairingAcceptRequest,
    RemotePairingCompleteRequest,
    RemotePairingInvitationRequest,
    RemotePeerGrantUpdate,
    RemoteSettingsUpdate,
    RemoteShortPairingConnectRequest,
)


async def _invoke(operation):
    try:
        return await operation
    except RemoteApplicationError as exc:
        return JSONResponse(
            {"error": exc.message, "code": exc.code, **exc.payload},
            status_code=exc.status_code,
        )


def register_settings_routes(router: APIRouter, service: RemoteControlApplicationService) -> None:
    @router.get("/api/remote/settings", tags=["Remote Settings"], operation_id="remote_settings_get")
    async def remote_settings_get():
        return await service.projection.settings()

    @router.get("/api/remote/context-devices", tags=["Remote Settings"], operation_id="remote_context_devices_get")
    async def remote_context_devices_get():
        return await service.projection.context_devices()

    @router.put("/api/remote/settings", tags=["Remote Settings"], operation_id="remote_settings_update")
    async def remote_settings_update(request: RemoteSettingsUpdate):
        return await _invoke(service.update_settings({
            "enabled": request.enabled, "relay_url": request.relay_url,
            "device_name": request.device_name,
            "default_tool_packs": request.default_tool_packs,
        }))


def register_pairing_routes(router: APIRouter, service: RemoteControlApplicationService) -> None:
    def invitation_values(request: RemotePairingInvitationRequest):
        return {
            "capabilities": request.capabilities or list(DEFAULT_REMOTE_CAPABILITIES),
            "project_scopes": request.project_scopes, "ttl_seconds": request.ttl_seconds,
        }

    @router.post("/api/remote/pairing/invitations", status_code=201, tags=["Remote Settings"], operation_id="remote_pairing_invitation_create")
    async def remote_pairing_invitation_create(request: RemotePairingInvitationRequest):
        return await _invoke(service.invitation(invitation_values(request)))

    @router.post("/api/remote/pairing/short-key", status_code=201, tags=["Remote Settings"], operation_id="remote_short_pairing_invitation_create")
    async def remote_short_pairing_invitation_create(request: RemotePairingInvitationRequest):
        return await _invoke(service.invitation(invitation_values(request), short=True))

    @router.post("/api/remote/pairing/connect", tags=["Remote Settings"], operation_id="remote_short_pairing_connect")
    async def remote_short_pairing_connect(request: RemoteShortPairingConnectRequest):
        return await _invoke(service.connect(request.address, request.pairing_key))

    @router.post("/api/remote/pairing/accept", tags=["Remote Settings"], operation_id="remote_pairing_accept")
    async def remote_pairing_accept(request: RemotePairingAcceptRequest):
        return await _invoke(service.accept(request.invitation))

    @router.post("/api/remote/pairing/complete", tags=["Remote Settings"], operation_id="remote_pairing_complete")
    async def remote_pairing_complete(request: RemotePairingCompleteRequest):
        return await _invoke(service.complete(request.response))


def register_peer_routes(router: APIRouter, service: RemoteControlApplicationService) -> None:
    @router.patch("/api/remote/peers/{device_id}", tags=["Remote Settings"], operation_id="remote_peer_grant_update")
    async def remote_peer_grant_update(device_id: str, request: RemotePeerGrantUpdate):
        return await _invoke(service.update_grant(device_id, {
            "capabilities": request.capabilities,
            "project_scopes": request.project_scopes,
        }))

    @router.delete("/api/remote/peers/{device_id}", tags=["Remote Settings"], operation_id="remote_peer_revoke")
    async def remote_peer_revoke(device_id: str):
        return await _invoke(service.revoke(device_id))

    @router.get("/api/remote/audit", response_model=RemoteAuditResponse, tags=["Remote Settings"], operation_id="remote_audit_list")
    async def remote_audit_list(limit: int = Query(default=100, ge=1, le=500)) -> RemoteAuditResponse:
        events = await asyncio.to_thread(service.store.list_audit_events, limit=limit)
        return RemoteAuditResponse(events=events)


def register_context_routes(router: APIRouter, service: RemoteControlApplicationService) -> None:
    @router.get("/api/workbench/chats/{chat_id}/remote-context", tags=["Remote Settings"], operation_id="remote_chat_context_get")
    async def remote_chat_context_get(chat_id: str):
        return await _invoke(service.chat_context(chat_id))

    @router.put("/api/workbench/chats/{chat_id}/remote-context", tags=["Remote Settings"], operation_id="remote_chat_context_update")
    async def remote_chat_context_update(chat_id: str, request: RemoteChatContextUpdate):
        return await _invoke(service.update_chat_context(chat_id, request.device_ids))


__all__ = ["register_context_routes", "register_pairing_routes", "register_peer_routes", "register_settings_routes"]
