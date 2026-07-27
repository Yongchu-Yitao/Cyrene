"""Restricted LAN listener for IP + short-key Cyrene pairing."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from typing import Any, Awaitable, Callable

import httpx

from cyrene.runtime.remote_control import DIRECT_PAIRING_PORT, RemoteControlStore

_MAX_REQUEST_BYTES = 64 * 1024
_MAX_ENVELOPE_BYTES = 24 * 1024 * 1024
RemoteReceiver = Callable[[dict[str, Any]], Awaitable[None]]


def local_pairing_addresses(port: int = DIRECT_PAIRING_PORT) -> list[str]:
    """Return usable LAN IPv4 addresses without exposing wildcard addresses."""
    addresses: set[str] = set()
    probe: socket.socket | None = None
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 9))
        primary = str(probe.getsockname()[0])
        if not ipaddress.ip_address(primary).is_loopback:
            addresses.add(f"{primary}:{port}")
    except OSError:
        pass
    finally:
        if probe is not None:
            probe.close()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = str(item[4][0])
            parsed = ipaddress.ip_address(address)
            if not parsed.is_loopback and not parsed.is_unspecified:
                addresses.add(f"{address}:{port}")
    except OSError:
        pass
    return sorted(addresses)


def normalize_pairing_address(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("device IP address is required")
    if "://" in raw:
        raise ValueError("enter an IP address, not a URL")
    host, separator, port_text = raw.rpartition(":")
    if not separator:
        host, port_text = raw, str(DIRECT_PAIRING_PORT)
    try:
        parsed = ipaddress.ip_address(host.strip("[]"))
    except ValueError as exc:
        raise ValueError("device address must be an IPv4 or IPv6 address") from exc
    if not (parsed.is_private or parsed.is_link_local or parsed.is_loopback):
        raise ValueError("direct pairing is limited to local-network IP addresses")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError("pairing port is invalid") from exc
    if port < 1024 or port > 65535:
        raise ValueError("pairing port must be between 1024 and 65535")
    bracketed = f"[{parsed}]" if parsed.version == 6 else str(parsed)
    return f"{bracketed}:{port}"


class DirectPairingServer:
    """LAN pairing listener and direct E2EE envelope transport."""

    def __init__(
        self,
        store: RemoteControlStore,
        *,
        host: str = "0.0.0.0",
        port: int = DIRECT_PAIRING_PORT,
    ) -> None:
        self.store = store
        self.host = host
        self.port = int(port)
        self._server: asyncio.AbstractServer | None = None
        self._device_id = ""
        self._receiver: RemoteReceiver | None = None
        self._delivery_tasks: set[asyncio.Task[Any]] = set()

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def connected(self) -> bool:
        return self.running and self._receiver is not None

    async def start(self) -> None:
        if self._server is None:
            self._server = await asyncio.start_server(
                self._handle_connection, self.host, self.port
            )

    async def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()
            await server.wait_closed()
        deliveries = list(self._delivery_tasks)
        for task in deliveries:
            task.cancel()
        if deliveries:
            await asyncio.gather(*deliveries, return_exceptions=True)
        self._delivery_tasks.clear()

    async def register(
        self, device_id: str, receiver: RemoteReceiver
    ) -> None:
        if not self.running:
            await self.start()
        self._device_id = str(device_id)
        self._receiver = receiver

    async def unregister(self, device_id: str) -> None:
        if str(device_id) == self._device_id:
            self._device_id = ""
            self._receiver = None

    async def send(self, envelope: dict[str, Any]) -> None:
        recipient = str(envelope.get("recipient_device_id") or "")
        peer = await asyncio.to_thread(self.store.get_peer, recipient)
        if peer is None:
            raise ConnectionError("remote device is not trusted")
        address = str(peer.get("lan_address") or "")
        if not address:
            raise ConnectionError("remote device has no LAN address")
        normalized = normalize_pairing_address(address)
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(8), trust_env=False
            ) as client:
                response = await client.post(
                    f"http://{normalized}/v1/control/envelope",
                    json={"envelope": envelope},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ConnectionError(
                f"remote device is unreachable at {normalized}"
            ) from exc

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        status = 500
        payload: dict[str, Any] = {"error": "pairing request failed"}
        try:
            header = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"), timeout=5
            )
            if len(header) > 8192:
                raise ValueError("request headers are too large")
            lines = header.decode("ascii").split("\r\n")
            method, path, _version = lines[0].split(" ", 2)
            headers = {
                key.strip().lower(): value.strip()
                for line in lines[1:]
                if ":" in line
                for key, value in [line.split(":", 1)]
            }
            content_length = int(headers.get("content-length", "0"))
            if method != "POST":
                status, payload = 405, {"error": "method not allowed"}
            elif content_length < 2 or content_length > (
                _MAX_ENVELOPE_BYTES
                if path == "/v1/control/envelope"
                else _MAX_REQUEST_BYTES
            ):
                status, payload = 400, {"error": "invalid request size"}
            else:
                body = await asyncio.wait_for(
                    reader.readexactly(content_length), timeout=5
                )
                request = json.loads(body.decode("utf-8"))
                peer = writer.get_extra_info("peername")
                source = str(peer[0] if peer else "unknown")
                if path == "/v1/pairing/claim":
                    payload = self.store.claim_short_pairing_invitation(
                        str(request.get("pairing_key") or ""), source=source
                    )
                    status = 200
                elif path == "/v1/pairing/complete":
                    listener_port = int(
                        request.get("listener_port") or DIRECT_PAIRING_PORT
                    )
                    source_address = normalize_pairing_address(
                        f"{source}:{listener_port}"
                    )
                    trusted = self.store.complete_short_pairing_response(
                        str(request.get("response") or ""),
                        source=source,
                        lan_address=source_address,
                    )
                    payload, status = {"peer": trusted}, 200
                elif path == "/v1/control/envelope":
                    envelope = request.get("envelope")
                    if not isinstance(envelope, dict):
                        raise ValueError("encrypted envelope is required")
                    if (
                        str(envelope.get("recipient_device_id") or "")
                        != self.store.identity.device_id
                    ):
                        raise ValueError("envelope recipient does not match")
                    sender = str(envelope.get("sender_device_id") or "")
                    trusted_peer = self.store.get_peer(sender)
                    if trusted_peer is None:
                        status, payload = 403, {"error": "peer is not trusted"}
                    elif self._receiver is None:
                        status, payload = 503, {"error": "control gateway is offline"}
                    else:
                        saved_address = str(trusted_peer.get("lan_address") or "")
                        if saved_address:
                            saved_host = httpx.URL(
                                f"http://{normalize_pairing_address(saved_address)}"
                            ).host
                            if saved_host != source:
                                status, payload = 403, {
                                    "error": "peer LAN address does not match"
                                }
                            else:
                                status, payload = self._queue_envelope(envelope)
                        else:
                            status, payload = self._queue_envelope(envelope)
                else:
                    status, payload = 404, {"error": "not found"}
        except RuntimeError as exc:
            status, payload = 429, {"error": str(exc)}
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            status, payload = 400, {"error": str(exc)}
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            status, payload = 400, {"error": "invalid pairing request"}
        except Exception:
            status, payload = 500, {"error": "pairing request failed"}
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        reason = {
            200: "OK",
            202: "Accepted",
            400: "Bad Request",
            403: "Forbidden",
            404: "Not Found",
            405: "Method Not Allowed",
            429: "Too Many Requests",
            500: "Internal Server Error",
            503: "Service Unavailable",
        }[status]
        writer.write(
            (
                f"HTTP/1.1 {status} {reason}\r\n"
                "Content-Type: application/json; charset=utf-8\r\n"
                f"Content-Length: {len(raw)}\r\n"
                "Connection: close\r\n"
                "Cache-Control: no-store\r\n\r\n"
            ).encode()
            + raw
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    def _queue_envelope(
        self, envelope: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        assert self._receiver is not None
        task = asyncio.create_task(self._receiver(dict(envelope)))
        self._delivery_tasks.add(task)
        task.add_done_callback(self._delivery_tasks.discard)
        return 202, {"accepted": True}


async def connect_by_address(
    store: RemoteControlStore,
    *,
    address: str,
    pairing_key: str,
    listener_port: int = DIRECT_PAIRING_PORT,
    timeout_seconds: float = 8,
) -> dict[str, Any]:
    """Complete both legacy signed-bundle phases behind one local API call."""
    normalized = normalize_pairing_address(address)
    base_url = f"http://{normalized}"
    timeout = httpx.Timeout(timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        claim = await client.post(
            f"{base_url}/v1/pairing/claim",
            json={"pairing_key": pairing_key},
        )
        claim.raise_for_status()
        invitation = str(claim.json().get("invitation") or "")
        accepted = await asyncio.to_thread(
            store.accept_pairing_invitation, invitation
        )
        await asyncio.to_thread(
            store.update_peer_lan_address,
            str(accepted["peer"]["device_id"]),
            normalized,
        )
        try:
            completion = await client.post(
                f"{base_url}/v1/pairing/complete",
                json={
                    "response": accepted["response"],
                    "listener_port": int(listener_port),
                },
            )
            completion.raise_for_status()
        except Exception:
            peer = accepted.get("peer") or {}
            device_id = str(peer.get("device_id") or "")
            if device_id:
                await asyncio.to_thread(store.revoke_peer, device_id)
            raise
    return {"peer": accepted["peer"], "address": normalized}


__all__ = [
    "DirectPairingServer",
    "connect_by_address",
    "local_pairing_addresses",
    "normalize_pairing_address",
]
