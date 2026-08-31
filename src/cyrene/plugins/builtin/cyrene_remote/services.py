"""Remote Plugin application services for paired-device control and projections."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from cyrene.localization import localized
from .control import (
    DEFAULT_REMOTE_CAPABILITIES,
    REMOTE_CAPABILITIES,
    REMOTE_PLUGIN_PACK_PREFIX,
    RemoteControlStore,
    remote_plugin_pack_ids,
)
from .pairing import connect_by_address, local_pairing_addresses

logger = logging.getLogger(__name__)


class RemoteApplicationError(Exception):
    def __init__(self, message: str, code: str, status_code: int, **payload: Any) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.payload = payload


class RemoteDeviceProjectionService:
    def __init__(self, store: RemoteControlStore, projects: Any, runtime: Any = None) -> None:
        self.store = store
        self.projects = projects
        self.runtime = runtime

    async def settings(self) -> dict[str, Any]:
        settings, projects = await asyncio.gather(
            asyncio.to_thread(self.store.get_settings), self.projects.list_projects()
        )
        port = self.runtime.lan_port if self.runtime is not None else 37841
        return {
            **settings,
            "identity": await asyncio.to_thread(self.store.public_identity),
            "supported_capabilities": sorted(REMOTE_CAPABILITIES),
            "default_capabilities": list(DEFAULT_REMOTE_CAPABILITIES),
            "remote_plugin_packs": [
                {
                    "id": pack_id,
                    "grant": REMOTE_PLUGIN_PACK_PREFIX + pack_id,
                }
                for pack_id in remote_plugin_pack_ids()
            ],
            "projects": [
                {"id": str(item.get("id") or ""), "name": str(item.get("name") or "")}
                for item in projects if str(item.get("id") or "")
            ],
            "peers": await asyncio.to_thread(self.store.list_peers),
            "direct_pairing": {
                "addresses": local_pairing_addresses(port), "port": port,
                "available": bool(self.runtime is not None and self.runtime.pairing_server is not None and self.runtime.pairing_server.running),
            },
            "transport": self._transport(settings),
        }

    def _transport(self, settings: dict[str, Any]) -> dict[str, Any]:
        if self.runtime is not None:
            return self.runtime.status()
        enabled = bool(settings["enabled"])
        return {
            "status": "configured" if enabled else "disabled", "connected": False,
            "detail": localized(
                "LAN control will start with the Cyrene runtime.",
                "局域网控制将在 Cyrene 运行时启动。",
            ) if enabled else localized("Remote access is disabled.", "远程访问已停用。"),
        }

    async def context_devices(self) -> dict[str, Any]:
        peers = await asyncio.to_thread(self.store.list_peers)
        devices = [self._device(peer) for peer in peers]
        gateway = self.runtime.gateway if self.runtime is not None else None
        return {
            "revision": await asyncio.to_thread(self.store.catalog_revision),
            "devices": devices,
            "transport_connected": bool(gateway is not None and gateway.connected),
        }

    @staticmethod
    def _device(peer: dict[str, Any]) -> dict[str, Any]:
        capabilities = list(peer.get("received_capabilities") or [])
        scopes = list(peer.get("received_project_scopes") or [])
        seen_raw = str(peer.get("last_seen_at") or "")
        seen = datetime.fromisoformat(seen_raw) if seen_raw else None
        if seen is not None and seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        stale = bool(seen is not None and seen < datetime.now(timezone.utc) - timedelta(seconds=90))
        state = "syncing_grants" if not capabilities or not scopes else "offline" if stale else "ready"
        workspace = all(item in capabilities for item in ("workspace_file:metadata", "workspace_file:read", "workspace_file:write"))
        jobs = all(item in capabilities for item in ("remote_job:read", "remote_job:run"))
        desktop = all(item in capabilities for item in ("desktop:session_connect", "desktop:screen_view_user"))
        return {
            **peer, "state": state, "eligible": bool(capabilities and scopes),
            "online": state == "ready",
            "features": {"workspace_files_v1": workspace, "remote_jobs_v1": jobs, "remote_desktop_v1": desktop, "remote_authorization_v1": workspace or jobs or desktop},
        }


class RemoteControlApplicationService:
    def __init__(
        self,
        store: RemoteControlStore,
        projection: RemoteDeviceProjectionService,
        runtime: Any = None,
    ) -> None:
        self.store = store
        self.projection = projection
        self.runtime = runtime

    async def execute_scoped_file(
        self,
        peer_device_id: str,
        command: str,
        scope_id: str,
        root: Any,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if self.runtime is None or getattr(self.runtime, "executor", None) is None:
            raise RuntimeError("Remote file channel is unavailable")
        return await self.runtime.executor.execute_scoped_file(
            peer_device_id,
            command,
            scope_id,
            root,
            payload,
        )

    async def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        try:
            await asyncio.to_thread(self.store.update_settings, **values)
        except ValueError as exc:
            logger.info("Invalid remote settings: %s", exc)
            raise RemoteApplicationError(
                localized("Remote settings are invalid.", "远程访问设置无效。"),
                "remote_settings_invalid",
                400,
            ) from exc
        if self.runtime is not None:
            await self.runtime.reload()
        return await self.projection.settings()

    async def invitation(self, values: dict[str, Any], *, short: bool = False) -> dict[str, Any]:
        operation = self.store.create_short_pairing_invitation if short else self.store.create_pairing_invitation
        try:
            return await asyncio.to_thread(operation, **values)
        except ValueError as exc:
            logger.info("Remote pairing invitation is unavailable: %s", exc)
            raise RemoteApplicationError(
                localized("Remote pairing is unavailable.", "远程配对当前不可用。"),
                "remote_pairing_unavailable",
                400,
            ) from exc
        except RuntimeError as exc:
            logger.info("Remote pairing invitation conflict: %s", exc)
            raise RemoteApplicationError(
                localized("Remote pairing is unavailable.", "远程配对当前不可用。"),
                "remote_pairing_unavailable",
                409,
            ) from exc

    async def connect(self, address: str, pairing_key: str) -> dict[str, Any]:
        try:
            result = await connect_by_address(self.store, address=address, pairing_key=pairing_key, listener_port=self.runtime.lan_port if self.runtime is not None else 37841)
        except ValueError as exc:
            logger.info("Invalid remote pairing address: %s", exc)
            raise RemoteApplicationError(
                localized("The remote pairing address is invalid.", "远程配对地址无效。"),
                "remote_pairing_address_invalid",
                400,
            ) from exc
        except httpx.HTTPStatusError as exc:
            payload = exc.response.json()
            detail = str(payload.get("error") or exc)
            peer_code = str(payload.get("code") or "")
            code = (
                "remote_pairing_peer_update_required"
                if peer_code == "pairing_lan_only"
                or detail == "direct pairing is limited to local-network IP addresses"
                else "remote_pairing_connection_failed"
            )
            logger.info("Remote pairing peer rejected the connection: %s", detail)
            message = (
                localized(
                    "Direct pairing is limited to local-network IP addresses. Update the peer and retry.",
                    "直接配对仅支持局域网 IP 地址，请更新对端后重试。",
                )
                if code == "remote_pairing_peer_update_required"
                else localized(
                    "Could not connect to the remote device.",
                    "无法连接到远程设备。",
                )
            )
            raise RemoteApplicationError(message, code, 409) from exc
        except (httpx.HTTPError, OSError) as exc:
            logger.info("Remote pairing connection failed: %s", exc)
            raise RemoteApplicationError(
                localized("Could not connect to the remote device.", "无法连接到远程设备。"),
                "remote_pairing_connection_failed",
                409,
            ) from exc
        peer = result["peer"]
        await self._sync_grant(peer)
        await self._publish("paired", str(peer.get("device_id") or ""))
        return result

    async def accept(self, invitation: str) -> dict[str, Any]:
        try:
            result = await asyncio.to_thread(self.store.accept_pairing_invitation, invitation)
        except ValueError as exc:
            logger.info("Invalid remote pairing invitation: %s", exc)
            raise RemoteApplicationError(
                localized("The pairing invitation is invalid.", "配对邀请无效。"),
                "remote_pairing_invalid",
                400,
            ) from exc
        peer = result.get("peer") or {}
        await self._publish("pairing_accepted", str(peer.get("device_id") or ""))
        return result

    async def complete(self, response: str) -> dict[str, Any]:
        try:
            peer = await asyncio.to_thread(self.store.complete_pairing_response, response)
        except ValueError as exc:
            logger.info("Invalid remote pairing response: %s", exc)
            raise RemoteApplicationError(
                localized("The pairing response is invalid.", "配对响应无效。"),
                "remote_pairing_invalid",
                400,
            ) from exc
        await self._sync_grant(peer)
        await self._publish("paired", str(peer.get("device_id") or ""))
        return {"peer": peer}

    async def update_grant(self, device_id: str, values: dict[str, Any]) -> dict[str, Any]:
        try:
            peer = await asyncio.to_thread(self.store.update_peer_grant, device_id, **values)
        except KeyError as exc:
            raise RemoteApplicationError(
                localized("Remote device not found.", "未找到远程设备。"),
                "remote_peer_not_found",
                404,
            ) from exc
        except ValueError as exc:
            logger.info("Invalid remote grant: %s", exc)
            raise RemoteApplicationError(
                localized("The remote access grant is invalid.", "远程访问授权无效。"),
                "remote_grant_invalid",
                400,
            ) from exc
        await self._sync_grant(peer)
        await self._publish("grant_updated", device_id)
        return {"peer": peer}

    async def revoke(self, device_id: str) -> dict[str, Any]:
        await self._notify_revocation(device_id)
        if not await asyncio.to_thread(self.store.revoke_peer, device_id):
            raise RemoteApplicationError(
                localized("Remote device not found.", "未找到远程设备。"),
                "remote_peer_not_found",
                404,
            )
        await self._publish("revoked", device_id)
        return {"revoked": True, "device_id": device_id}

    async def _sync_grant(self, peer: dict[str, Any]) -> None:
        gateway = self.runtime.gateway if self.runtime is not None else None
        if gateway is None:
            return
        try:
            await asyncio.wait_for(gateway.notify_grant_update(str(peer["device_id"])), timeout=2)
        except Exception:
            self.store.audit("peer_grant_sync_deferred", peer_device_id=str(peer["device_id"]), outcome="offline")

    async def _notify_revocation(self, device_id: str) -> None:
        gateway = self.runtime.gateway if self.runtime is not None else None
        if gateway is None:
            return
        try:
            await asyncio.wait_for(gateway.notify_revocation(device_id), timeout=2)
        except Exception:
            self.store.audit("peer_revocation_notification_deferred", peer_device_id=device_id, outcome="offline")

    async def _publish(self, reason: str, device_id: str) -> None:
        from cyrene.observability import debug
        await debug.publish_event({"type": "remote_devices_changed", "revision": await asyncio.to_thread(self.store.catalog_revision), "reason": reason, "device_id": device_id})


__all__ = [
    "RemoteApplicationError",
    "RemoteControlApplicationService",
    "RemoteDeviceProjectionService",
]
