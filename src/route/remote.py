"""Desktop-local adapters for managing Cyrene remote-control trust."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, FastAPI, Query
from fastapi.responses import JSONResponse

from cyrene.runtime.remote_control import (
    DEFAULT_REMOTE_CAPABILITIES,
    REMOTE_CAPABILITIES,
    RemoteControlStore,
)
from cyrene.runtime.remote_commands import (
    RemoteCommandExecutor,
    RemoteControlRuntime,
)
from cyrene.runtime.remote_pairing import connect_by_address, local_pairing_addresses
from cyrene.workbench import runtime as workbench_runtime
from route.remote_schemas import (
    RemoteAuditResponse,
    RemoteChatContextUpdate,
    RemotePairingAcceptRequest,
    RemotePairingCompleteRequest,
    RemotePairingInvitationRequest,
    RemotePeerGrantUpdate,
    RemoteShortPairingConnectRequest,
    RemoteSettingsUpdate,
)


def _shared_projects() -> list[dict[str, str]]:
    payload = workbench_runtime._read_workbench_store_lightweight()
    return [
        {
            "id": str(project.get("id") or ""),
            "name": str(project.get("name") or ""),
        }
        for project in payload.get("projects") or []
        if isinstance(project, dict) and str(project.get("id") or "")
    ]


def _settings_payload(
    store: RemoteControlStore,
    runtime: RemoteControlRuntime | None = None,
) -> dict[str, Any]:
    settings = store.get_settings()
    return {
        **settings,
        "identity": store.public_identity(),
        "supported_capabilities": sorted(REMOTE_CAPABILITIES),
        "default_capabilities": list(DEFAULT_REMOTE_CAPABILITIES),
        "projects": _shared_projects(),
        "peers": store.list_peers(),
        "direct_pairing": {
            "addresses": local_pairing_addresses(
                runtime.lan_port if runtime is not None else 37841
            ),
            "port": runtime.lan_port if runtime is not None else 37841,
            "available": bool(
                runtime is not None
                and (
                    runtime.pairing_server is not None
                    and runtime.pairing_server.running
                )
            ),
        },
        "transport": (
            runtime.status()
            if runtime is not None
            else {
                "status": "configured" if settings["enabled"] else "disabled",
                "connected": False,
                "detail": (
                    "LAN control will start with the Cyrene runtime."
                    if settings["enabled"]
                    else "Remote access is disabled."
                ),
            }
        ),
    }


def register_remote_routes(
    router: APIRouter,
    app: FastAPI,
    db_path: str,
    *,
    chat_adapter: dict[str, Any] | None = None,
    project_adapter: dict[str, Any] | None = None,
    task_adapter: dict[str, Any] | None = None,
    goal_loop_adapter: dict[str, Any] | None = None,
) -> RemoteControlStore:
    store = RemoteControlStore(db_path)
    app.state.remote_control_store = store
    runtime: RemoteControlRuntime | None = None
    if chat_adapter and project_adapter and task_adapter:
        runtime = RemoteControlRuntime(
            db_path=db_path,
            store=store,
            executor=RemoteCommandExecutor(
                store=store,
                chat_adapter=chat_adapter,
                project_adapter=project_adapter,
                task_adapter=task_adapter,
                goal_loop_adapter=goal_loop_adapter,
            ),
        )
        app.state.remote_control_runtime = runtime

    @router.get(
        "/api/remote/settings",
        tags=["Remote Settings"],
        operation_id="remote_settings_get",
    )
    async def remote_settings_get():
        return await asyncio.to_thread(_settings_payload, store, runtime)

    @router.put(
        "/api/remote/settings",
        tags=["Remote Settings"],
        operation_id="remote_settings_update",
    )
    async def remote_settings_update(request: RemoteSettingsUpdate):
        try:
            await asyncio.to_thread(
                store.update_settings,
                enabled=request.enabled,
                relay_url=request.relay_url,
                device_name=request.device_name,
            )
        except ValueError as exc:
            return JSONResponse(
                {"error": str(exc), "code": "remote_settings_invalid"},
                status_code=400,
            )
        if runtime is not None:
            await runtime.reload()
        return await asyncio.to_thread(_settings_payload, store, runtime)

    @router.post(
        "/api/remote/pairing/invitations",
        status_code=201,
        tags=["Remote Settings"],
        operation_id="remote_pairing_invitation_create",
    )
    async def remote_pairing_invitation_create(
        request: RemotePairingInvitationRequest,
    ):
        capabilities = request.capabilities or list(DEFAULT_REMOTE_CAPABILITIES)
        try:
            return await asyncio.to_thread(
                store.create_pairing_invitation,
                capabilities=capabilities,
                project_scopes=request.project_scopes,
                ttl_seconds=request.ttl_seconds,
            )
        except (ValueError, RuntimeError) as exc:
            return JSONResponse(
                {"error": str(exc), "code": "remote_pairing_unavailable"},
                status_code=409 if isinstance(exc, RuntimeError) else 400,
            )

    @router.post(
        "/api/remote/pairing/short-key",
        status_code=201,
        tags=["Remote Settings"],
        operation_id="remote_short_pairing_invitation_create",
    )
    async def remote_short_pairing_invitation_create(
        request: RemotePairingInvitationRequest,
    ):
        capabilities = request.capabilities or list(DEFAULT_REMOTE_CAPABILITIES)
        try:
            return await asyncio.to_thread(
                store.create_short_pairing_invitation,
                capabilities=capabilities,
                project_scopes=request.project_scopes,
                ttl_seconds=request.ttl_seconds,
            )
        except (ValueError, RuntimeError) as exc:
            return JSONResponse(
                {"error": str(exc), "code": "remote_pairing_unavailable"},
                status_code=409 if isinstance(exc, RuntimeError) else 400,
            )

    @router.post(
        "/api/remote/pairing/connect",
        tags=["Remote Settings"],
        operation_id="remote_short_pairing_connect",
    )
    async def remote_short_pairing_connect(
        request: RemoteShortPairingConnectRequest,
    ):
        try:
            result = await connect_by_address(
                store,
                address=request.address,
                pairing_key=request.pairing_key,
                listener_port=(
                    runtime.lan_port if runtime is not None else 37841
                ),
            )
        except ValueError as exc:
            return JSONResponse(
                {"error": str(exc), "code": "remote_pairing_address_invalid"},
                status_code=400,
            )
        except (httpx.HTTPError, OSError) as exc:
            detail = str(exc)
            code = "remote_pairing_connection_failed"
            if isinstance(exc, httpx.HTTPStatusError):
                try:
                    detail = str(exc.response.json().get("error") or detail)
                except Exception:
                    pass
                if (
                    detail
                    == "direct pairing is limited to local-network IP addresses"
                ):
                    code = "remote_pairing_peer_update_required"
            return JSONResponse(
                {"error": detail, "code": code},
                status_code=409,
            )
        await load_remote_grant_sync(result["peer"])
        return result

    async def load_remote_grant_sync(peer: dict[str, Any]) -> None:
        if runtime is None or runtime.gateway is None:
            return
        try:
            await asyncio.wait_for(
                runtime.gateway.notify_grant_update(str(peer["device_id"])),
                timeout=2,
            )
        except Exception:
            store.audit(
                "peer_grant_sync_deferred",
                peer_device_id=str(peer["device_id"]),
                outcome="offline",
            )

    @router.post(
        "/api/remote/pairing/accept",
        tags=["Remote Settings"],
        operation_id="remote_pairing_accept",
    )
    async def remote_pairing_accept(request: RemotePairingAcceptRequest):
        try:
            return await asyncio.to_thread(
                store.accept_pairing_invitation, request.invitation
            )
        except ValueError as exc:
            return JSONResponse(
                {"error": str(exc), "code": "remote_pairing_invalid"},
                status_code=400,
            )

    @router.post(
        "/api/remote/pairing/complete",
        tags=["Remote Settings"],
        operation_id="remote_pairing_complete",
    )
    async def remote_pairing_complete(request: RemotePairingCompleteRequest):
        try:
            peer = await asyncio.to_thread(
                store.complete_pairing_response, request.response
            )
        except ValueError as exc:
            return JSONResponse(
                {"error": str(exc), "code": "remote_pairing_invalid"},
                status_code=400,
            )
        if runtime is not None and runtime.gateway is not None:
            try:
                await asyncio.wait_for(
                    runtime.gateway.notify_grant_update(
                        str(peer["device_id"])
                    ),
                    timeout=2,
                )
            except Exception:
                store.audit(
                    "peer_grant_sync_deferred",
                    peer_device_id=str(peer["device_id"]),
                    outcome="offline",
                )
        return {"peer": peer}

    @router.patch(
        "/api/remote/peers/{device_id}",
        tags=["Remote Settings"],
        operation_id="remote_peer_grant_update",
    )
    async def remote_peer_grant_update(
        device_id: str,
        request: RemotePeerGrantUpdate,
    ):
        try:
            peer = await asyncio.to_thread(
                store.update_peer_grant,
                device_id,
                capabilities=request.capabilities,
                project_scopes=request.project_scopes,
            )
        except KeyError:
            return JSONResponse(
                {"error": "remote peer not found", "code": "remote_peer_not_found"},
                status_code=404,
            )
        except ValueError as exc:
            return JSONResponse(
                {"error": str(exc), "code": "remote_grant_invalid"},
                status_code=400,
            )
        if runtime is not None and runtime.gateway is not None:
            try:
                await asyncio.wait_for(
                    runtime.gateway.notify_grant_update(device_id),
                    timeout=2,
                )
            except Exception:
                store.audit(
                    "peer_grant_sync_deferred",
                    peer_device_id=device_id,
                    outcome="offline",
                )
        return {"peer": peer}

    @router.delete(
        "/api/remote/peers/{device_id}",
        tags=["Remote Settings"],
        operation_id="remote_peer_revoke",
    )
    async def remote_peer_revoke(device_id: str):
        if runtime is not None and runtime.gateway is not None:
            try:
                await asyncio.wait_for(
                    runtime.gateway.notify_revocation(device_id),
                    timeout=2,
                )
            except Exception:
                store.audit(
                    "peer_revocation_notification_deferred",
                    peer_device_id=device_id,
                    outcome="offline",
                )
        revoked = await asyncio.to_thread(store.revoke_peer, device_id)
        if not revoked:
            return JSONResponse(
                {"error": "remote peer not found", "code": "remote_peer_not_found"},
                status_code=404,
            )
        return {"revoked": True, "device_id": device_id}

    @router.get(
        "/api/remote/audit",
        response_model=RemoteAuditResponse,
        tags=["Remote Settings"],
        operation_id="remote_audit_list",
    )
    async def remote_audit_list(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> RemoteAuditResponse:
        events = await asyncio.to_thread(store.list_audit_events, limit=limit)
        return RemoteAuditResponse(events=events)

    @router.get(
        "/api/workbench/chats/{chat_id}/remote-context",
        tags=["Remote Settings"],
        operation_id="remote_chat_context_get",
    )
    async def remote_chat_context_get(chat_id: str):
        from cyrene.workbench import chat as chat_service

        payload = await asyncio.to_thread(chat_service._read_chats_store)
        chat = chat_service._find_chat(payload, chat_id)
        if chat is None:
            return JSONResponse(
                {"error": "chat not found", "code": "chat_not_found"},
                status_code=404,
            )
        selected_ids = [
            str(item)
            for item in chat.get("remoteDeviceIds") or []
            if str(item)
        ]
        peers = [
            peer
            for device_id in selected_ids
            if (peer := store.get_peer(device_id)) is not None
            and bool(peer["received_capabilities"])
            and bool(peer["received_project_scopes"])
        ]
        return {
            "device_ids": [peer["device_id"] for peer in peers],
            "devices": peers,
        }

    @router.put(
        "/api/workbench/chats/{chat_id}/remote-context",
        tags=["Remote Settings"],
        operation_id="remote_chat_context_update",
    )
    async def remote_chat_context_update(
        chat_id: str,
        request: RemoteChatContextUpdate,
    ):
        from cyrene.workbench import chat as chat_service

        selected_ids = list(dict.fromkeys(request.device_ids))
        invalid = [
            device_id
            for device_id in selected_ids
            if (peer := store.get_peer(device_id)) is None
            or not bool(peer["received_capabilities"])
            or not bool(peer["received_project_scopes"])
        ]
        if invalid:
            return JSONResponse(
                {
                    "error": "one or more remote devices are unavailable",
                    "code": "remote_context_device_invalid",
                    "device_ids": invalid,
                },
                status_code=400,
            )
        payload = await asyncio.to_thread(chat_service._read_chats_store)
        chat = chat_service._find_chat(payload, chat_id)
        if chat is None:
            return JSONResponse(
                {"error": "chat not found", "code": "chat_not_found"},
                status_code=404,
            )
        chat["remoteDeviceIds"] = selected_ids
        chat["updatedAt"] = workbench_runtime._utc_now_iso()
        await asyncio.to_thread(chat_service._write_chats_store, payload)
        store.audit(
            "chat_remote_context_updated",
            outcome="updated",
            detail={"chat_id": chat_id, "device_ids": selected_ids},
        )
        return {
            "device_ids": selected_ids,
            "devices": [store.get_peer(device_id) for device_id in selected_ids],
        }

    return store


__all__ = ["register_remote_routes"]
