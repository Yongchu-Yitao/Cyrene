"""Minimal untrusted WebSocket router for Cyrene E2EE envelopes."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import time
from collections import defaultdict, deque
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PublicKey,
)
from websockets.asyncio.server import ServerConnection, serve

logger = logging.getLogger(__name__)

_MAX_CLOCK_SKEW_SECONDS = 300
_MAX_ENVELOPE_BYTES = 24 * 1024 * 1024
_MAX_CONNECTIONS_PER_IP = 20
_MAX_MESSAGES_PER_SECOND = 60
_REMOTE_PROTOCOL_VERSION = 2


def _json_dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _b64decode(value: str) -> bytes:
    raw = str(value or "").encode("ascii")
    return base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4))


def _device_id(signing_public_key: str) -> str:
    digest = hashlib.sha256(_b64decode(signing_public_key)).digest()
    encoded = base64.urlsafe_b64encode(digest[:18]).decode("ascii").rstrip("=")
    return "dev_" + encoded


class CyreneRelayServer:
    """Route opaque encrypted envelopes by public device id."""

    def __init__(
        self,
        *,
        max_connections_per_ip: int = _MAX_CONNECTIONS_PER_IP,
        max_messages_per_second: int = _MAX_MESSAGES_PER_SECOND,
    ) -> None:
        self._connections: dict[str, ServerConnection] = {}
        self._lock = asyncio.Lock()
        self._connections_by_ip: dict[str, int] = defaultdict(int)
        self._registration_nonces: dict[str, float] = {}
        self.max_connections_per_ip = max(1, int(max_connections_per_ip))
        self.max_messages_per_second = max(1, int(max_messages_per_second))

    async def handle(self, websocket: ServerConnection) -> None:
        device_id = ""
        client_ip = str((websocket.remote_address or ("unknown",))[0])
        counted = False
        try:
            async with self._lock:
                if (
                    self._connections_by_ip[client_ip]
                    >= self.max_connections_per_ip
                ):
                    await websocket.close(
                        code=1013,
                        reason="connection limit exceeded",
                    )
                    return
                self._connections_by_ip[client_ip] += 1
                counted = True
            raw = await asyncio.wait_for(websocket.recv(), timeout=10)
            registration = json.loads(raw)
            verified = await self._verify_registration(registration)
            if verified is None:
                await websocket.close(code=1008, reason="invalid registration")
                return
            device_id, public_key = verified
            async with self._lock:
                previous = self._connections.get(device_id)
                self._connections[device_id] = websocket
            if previous is not None and previous is not websocket:
                await previous.close(code=4001, reason="device reconnected")
            await websocket.send(
                _json_dumps({"type": "registered", "device_id": device_id})
            )

            recent_messages: deque[float] = deque()
            async for incoming in websocket:
                now = time.monotonic()
                while recent_messages and recent_messages[0] < now - 1:
                    recent_messages.popleft()
                if len(recent_messages) >= self.max_messages_per_second:
                    await websocket.close(
                        code=1013,
                        reason="message rate exceeded",
                    )
                    return
                recent_messages.append(now)
                if (
                    not isinstance(incoming, str)
                    or len(incoming.encode("utf-8")) > _MAX_ENVELOPE_BYTES
                ):
                    await websocket.close(code=1009, reason="message too large")
                    return
                await self._route(
                    incoming,
                    device_id,
                    public_key,
                    websocket,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("Relay client disconnected", exc_info=True)
        finally:
            async with self._lock:
                if device_id:
                    if self._connections.get(device_id) is websocket:
                        self._connections.pop(device_id, None)
                if counted:
                    remaining = self._connections_by_ip[client_ip] - 1
                    if remaining > 0:
                        self._connections_by_ip[client_ip] = remaining
                    else:
                        self._connections_by_ip.pop(client_ip, None)

    async def _verify_registration(
        self,
        registration: Any,
    ) -> tuple[str, Ed25519PublicKey] | None:
        if (
            not isinstance(registration, dict)
            or registration.get("type") != "register"
            or int(registration.get("protocol_version") or 0)
            != _REMOTE_PROTOCOL_VERSION
        ):
            return None
        device_id = str(registration.get("device_id") or "").strip()
        public_key_text = str(
            registration.get("signing_public_key") or ""
        ).strip()
        timestamp = int(registration.get("timestamp") or 0)
        nonce = str(registration.get("nonce") or "")
        if (
            not device_id.startswith("dev_")
            or len(device_id) > 100
            or len(nonce) < 16
            or abs(int(time.time()) - timestamp) > _MAX_CLOCK_SKEW_SECONDS
        ):
            return None
        try:
            if _device_id(public_key_text) != device_id:
                return None
            public_key = Ed25519PublicKey.from_public_bytes(
                _b64decode(public_key_text)
            )
            unsigned = {
                key: value
                for key, value in registration.items()
                if key != "signature"
            }
            public_key.verify(
                _b64decode(str(registration.get("signature") or "")),
                _json_dumps(unsigned).encode("utf-8"),
            )
        except Exception:
            return None
        now = time.monotonic()
        async with self._lock:
            expired = [
                key
                for key, observed in self._registration_nonces.items()
                if observed < now - _MAX_CLOCK_SKEW_SECONDS
            ]
            for key in expired:
                self._registration_nonces.pop(key, None)
            nonce_key = f"{device_id}:{nonce}"
            if nonce_key in self._registration_nonces:
                return None
            self._registration_nonces[nonce_key] = now
        return device_id, public_key

    async def _route(
        self,
        raw: str,
        device_id: str,
        public_key: Ed25519PublicKey,
        source: ServerConnection,
    ) -> None:
        try:
            message = json.loads(raw)
        except Exception:
            return
        if (
            not isinstance(message, dict)
            or message.get("type") != "envelope"
            or not isinstance(message.get("envelope"), dict)
        ):
            return
        envelope = dict(message["envelope"])
        if (
            int(envelope.get("version") or 0) != _REMOTE_PROTOCOL_VERSION
            or str(envelope.get("sender_device_id") or "") != device_id
            or abs(int(time.time()) - int(envelope.get("timestamp") or 0))
            > _MAX_CLOCK_SKEW_SECONDS
        ):
            return
        try:
            unsigned = {
                key: value
                for key, value in envelope.items()
                if key != "signature"
            }
            public_key.verify(
                _b64decode(str(envelope.get("signature") or "")),
                _json_dumps(unsigned).encode("utf-8"),
            )
        except Exception:
            return
        recipient = str(envelope.get("recipient_device_id") or "")
        if not recipient.startswith("dev_") or len(recipient) > 100:
            return
        async with self._lock:
            target = self._connections.get(recipient)
        message_id = str(envelope.get("message_id") or "")
        if target is None:
            await source.send(
                _json_dumps(
                    {
                        "type": "delivery_receipt",
                        "message_id": message_id,
                        "delivered": False,
                    }
                )
            )
            return
        try:
            await target.send(
                json.dumps(
                    {"type": "envelope", "envelope": envelope},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        except Exception:
            await source.send(
                _json_dumps(
                    {
                        "type": "delivery_receipt",
                        "message_id": message_id,
                        "delivered": False,
                    }
                )
            )
            return
        await source.send(
            _json_dumps(
                {
                    "type": "delivery_receipt",
                    "message_id": message_id,
                    "delivered": True,
                }
            )
        )


async def run_relay(host: str, port: int) -> None:
    relay = CyreneRelayServer()
    async with serve(
        relay.handle,
        host,
        port,
        max_size=_MAX_ENVELOPE_BYTES,
        ping_interval=20,
        ping_timeout=20,
    ):
        logger.info("Cyrene relay listening on ws://%s:%s", host, port)
        await asyncio.Future()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a Cyrene E2EE routing relay")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9876)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    from cyrene.observability.logging_setup import setup_persistent_logging

    setup_persistent_logging()
    asyncio.run(run_relay(args.host, args.port))


__all__ = ["CyreneRelayServer", "main", "run_relay"]
