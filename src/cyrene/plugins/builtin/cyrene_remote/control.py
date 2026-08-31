"""Secure device identity, pairing, grants, and relay-independent Remote Plugin control.

The desktop HTTP API remains loopback-only.  Cross-device control is carried
through :class:`RemoteGateway`, which accepts only typed commands and asks the
controlled Cyrene's local policy store to authorize every request.

The transport is injected deliberately.  Production can use an outbound WSS
relay while tests use :class:`InMemoryRemoteRelay`; both carry the same signed,
end-to-end encrypted envelopes.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import platform
import re
import secrets
import sqlite3
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol
from urllib.parse import urlparse
from uuid import uuid4

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cyrene.localization import localized

logger = logging.getLogger(__name__)

REMOTE_PROTOCOL_VERSION = 2
# The on-disk identity schema is independent from the wire protocol.  Protocol
# v2 did not change the identity payload, but older releases wrote the protocol
# version here, so accept both values while keeping future writes on schema v1.
_REMOTE_IDENTITY_VERSION = 1
_SUPPORTED_REMOTE_IDENTITY_VERSIONS = frozenset({1, 2})
DEFAULT_RELAY_URL = "wss://relay.cyrene.invalid/v1"
PAIRING_TTL_SECONDS = 120
DIRECT_PAIRING_PORT = 37841
DIRECT_PAIRING_CODE_LENGTH = 10
DIRECT_PAIRING_MAX_ATTEMPTS = 5
ENVELOPE_MAX_SKEW_SECONDS = 300
BASE_REMOTE_CAPABILITIES = frozenset(
    {
        "projects:list_shared",
        "chat:read",
        "chat:create",
        "chat:send",
        "chat:guide",
        "chat:interrupt",
        "goal:read",
        "goal:control",
        "approval:clarification",
        "approval:respond",
        "artifact:read",
        "settings:read",
        "settings:update",
    }
)
REMOTE_EXTENSION_CAPABILITIES = frozenset(
    {
        "workspace_file:metadata",
        "workspace_file:read",
        "workspace_file:write",
        "workspace_file:overwrite",
        "workspace_file:move",
        "workspace_file:delete",
        "workspace_directory:transfer",
        "remote_job:read",
        "remote_job:run",
        "remote_job:control",
    }
)
REMOTE_CAPABILITIES = BASE_REMOTE_CAPABILITIES | REMOTE_EXTENSION_CAPABILITIES
DEFAULT_REMOTE_CAPABILITIES = (
    "projects:list_shared",
    "chat:read",
    "chat:create",
    "chat:send",
    "chat:guide",
    "chat:interrupt",
    "goal:read",
    "goal:control",
    "approval:clarification",
    "approval:respond",
    "artifact:read",
    "workspace_file:metadata",
    "workspace_file:read",
    "workspace_file:write",
    "workspace_file:overwrite",
    "workspace_file:move",
    "workspace_file:delete",
    "workspace_directory:transfer",
    "remote_job:read",
    "remote_job:run",
    "remote_job:control",
    "settings:read",
    "settings:update",
)
REMOTE_PLUGIN_PACK_PREFIX = "pluginpack:"
_REMOTE_PLUGIN_PACK_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,99}$")
_REMOTE_STORE_SUFFIX = ".remote-control"


def remote_plugin_pack_ids(*, enabled_only: bool = False) -> tuple[str, ...]:
    """Return remotely callable packs from the live Plugin registry."""

    from cyrene.core.plugin import application_plugin_scope

    host = application_plugin_scope()
    if host is None:
        return ()
    return tuple(
        sorted(
            pack.id
            for pack in host.registry.list_packs()
            if pack.plugins
            and not host.registry.pack_locked(pack.id)
            and (not enabled_only or host.registry.pack_enabled(pack.id))
        )
    )


def remote_plugin_pack_capabilities() -> frozenset[str]:
    return frozenset(
        REMOTE_PLUGIN_PACK_PREFIX + pack_id
        for pack_id in remote_plugin_pack_ids()
    )


def _valid_remote_grant(capability: str) -> bool:
    if capability in REMOTE_CAPABILITIES:
        return True
    if not capability.startswith(REMOTE_PLUGIN_PACK_PREFIX):
        return False
    pack_id = capability.removeprefix(REMOTE_PLUGIN_PACK_PREFIX)
    # A device must never grant the remote-control pack to itself.  The
    # remote pack owns the transport and command executor; exposing it as a
    # remotely invokable pack would permit recursive relay chains and bypass
    # the explicit service boundary.
    return (
        pack_id != "cyrene_remote"
        and bool(_REMOTE_PLUGIN_PACK_ID.fullmatch(pack_id))
    )


_COMMAND_CAPABILITIES = {
    "capabilities.read": "",
    "projects.list": "projects:list_shared",
    "chats.list": "chat:read",
    "chats.create": "chat:create",
    "chats.update": "chat:create",
    "chats.delete": "chat:create",
    "chats.read": "chat:read",
    "changes.read": "chat:read",
    "chats.send": "chat:send",
    "runs.read": "chat:read",
    "runs.events": "chat:read",
    "runs.wait": "chat:read",
    "runs.guide": "chat:guide",
    "runs.interrupt": "chat:interrupt",
    "goals.read": "goal:read",
    "goals.update": "goal:control",
    "goals.confirm": "goal:control",
    "goals.pause": "goal:control",
    "goals.resume": "goal:control",
    "goals.abort": "goal:control",
    "goals.accept": "goal:control",
    "approvals.respond": "approval:respond",
    "attachments.read": "artifact:read",
    "files.stat": "workspace_file:metadata",
    "files.list": "workspace_file:metadata",
    "files.manifest": "workspace_file:metadata",
    "files.hash": "workspace_file:metadata",
    "files.read": "workspace_file:read",
    "files.download": "workspace_file:read",
    "files.upload.begin": "workspace_file:write",
    "files.upload.chunk": "workspace_file:write",
    "files.upload.commit": "workspace_file:write",
    "files.upload.abort": "workspace_file:write",
    "files.mkdir": "workspace_file:write",
    "files.touch": "workspace_file:write",
    "files.apply_patch": "workspace_file:write",
    "files.copy": "workspace_file:write",
    "files.move": "workspace_file:move",
    "files.delete": "workspace_file:delete",
    "files.delete_tree": "workspace_file:delete",
    "files.sync.prepare": "workspace_directory:transfer",
    "files.sync.diff": "workspace_directory:transfer",
    "files.sync.apply": "workspace_directory:transfer",
    "files.sync.commit": "workspace_directory:transfer",
    "files.sync.abort": "workspace_directory:transfer",
    "jobs.start": "remote_job:run",
    "jobs.read": "remote_job:read",
    "jobs.wait": "remote_job:read",
    "jobs.artifacts": "remote_job:read",
    "jobs.cancel": "remote_job:control",
    "jobs.interrupt": "remote_job:control",
    "settings.read": "settings:read",
    "settings.models.copy": "settings:read",
    "settings.update": "settings:update",
    "settings.openai_oauth.read": "settings:read",
    "settings.openai_oauth.login": "settings:update",
    "settings.openai_oauth.logout": "settings:update",
    "shell.open": "pluginpack:cyrene_code",
    "shell.read": "pluginpack:cyrene_code",
    "shell.write": "pluginpack:cyrene_code",
    "shell.interrupt": "pluginpack:cyrene_code",
    "shell.close": "pluginpack:cyrene_code",
    "harness.list": "",
    "harness.describe": "",
    "harness.invoke": "",
}
_REMOTE_SHELL_COMMANDS = frozenset(
    {"shell.open", "shell.read", "shell.write", "shell.interrupt", "shell.close"}
)


def _remote_shell_plugin_available() -> bool:
    """Require the live application contribution, not a stale remote grant."""

    from cyrene.core.plugin import application_plugin_service

    return application_plugin_service("remote_shell") is not None
_PROJECT_SCOPED_COMMANDS = frozenset(
    command
    for command in _COMMAND_CAPABILITIES
    if command not in {
        "capabilities.read",
        "projects.list",
        "settings.read",
        "settings.models.copy",
        "settings.update",
        "settings.openai_oauth.read",
        "settings.openai_oauth.login",
        "settings.openai_oauth.logout",
    }
)
_SIDE_EFFECT_COMMANDS = frozenset(
    {
        "chats.create",
        "chats.update",
        "chats.delete",
        "chats.send",
        "runs.guide",
        "runs.interrupt",
        "goals.update",
        "goals.confirm",
        "goals.pause",
        "goals.resume",
        "goals.abort",
        "goals.accept",
        "approvals.respond",
        "settings.update",
        "settings.openai_oauth.login",
        "settings.openai_oauth.logout",
        "shell.open",
        "shell.write",
        "shell.interrupt",
        "shell.close",
        "harness.invoke",
        "files.upload.begin",
        "files.upload.chunk",
        "files.upload.commit",
        "files.upload.abort",
        "files.mkdir",
        "files.touch",
        "files.apply_patch",
        "files.copy",
        "files.move",
        "files.delete",
        "files.delete_tree",
        "files.sync.prepare",
        "files.sync.apply",
        "files.sync.commit",
        "files.sync.abort",
        "jobs.start",
        "jobs.cancel",
        "jobs.interrupt",
    }
)

_GATEWAYS: dict[str, "RemoteGateway"] = {}
_GATEWAYS_LOCK = threading.RLock()


def _gateway_key(db_path: str) -> str:
    return str(Path(db_path).expanduser().resolve())


def register_remote_gateway(db_path: str, gateway: "RemoteGateway") -> None:
    """Expose the active outbound gateway to local Agent tools."""
    with _GATEWAYS_LOCK:
        _GATEWAYS[_gateway_key(db_path)] = gateway


def get_remote_gateway(db_path: str) -> "RemoteGateway | None":
    with _GATEWAYS_LOCK:
        return _GATEWAYS.get(_gateway_key(db_path))


def unregister_remote_gateway(
    db_path: str,
    gateway: "RemoteGateway | None" = None,
) -> None:
    key = _gateway_key(db_path)
    with _GATEWAYS_LOCK:
        current = _GATEWAYS.get(key)
        if current is not None and (gateway is None or current is gateway):
            _GATEWAYS.pop(key, None)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    raw = str(value or "").encode("ascii")
    return base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4))


def _encode_bundle(value: dict[str, Any]) -> str:
    return _b64encode(_json_dumps(value).encode("utf-8"))


def _decode_bundle(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(_b64decode(value).decode("utf-8"))
    except Exception as exc:
        raise ValueError("invalid pairing bundle") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid pairing bundle")
    return payload


def _validate_relay_url(value: str) -> None:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme == "wss" and parsed.netloc:
        return
    if (
        parsed.scheme == "ws"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and parsed.netloc
    ):
        return
    raise ValueError(
        "relay URL must use wss:// (ws:// is allowed only for localhost)"
    )


def _public_bytes(key: Ed25519PublicKey | X25519PublicKey) -> bytes:
    return key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _private_bytes(key: Ed25519PrivateKey | X25519PrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _identity_signature(
    identity: "RemoteIdentity",
    payload: dict[str, Any],
) -> str:
    return _b64encode(
        identity.signing_private_key.sign(
            _json_dumps(payload).encode("utf-8")
        )
    )


def _verify_identity_signature(
    signing_public_key: str,
    payload: dict[str, Any],
    signature: str,
    *,
    error: str,
) -> None:
    try:
        Ed25519PublicKey.from_public_bytes(
            _b64decode(signing_public_key)
        ).verify(
            _b64decode(signature),
            _json_dumps(payload).encode("utf-8"),
        )
    except Exception as exc:
        raise ValueError(error) from exc


@dataclass(frozen=True)
class RemoteIdentity:
    signing_private_key: Ed25519PrivateKey
    exchange_private_key: X25519PrivateKey

    @property
    def signing_public_key(self) -> str:
        return _b64encode(_public_bytes(self.signing_private_key.public_key()))

    @property
    def exchange_public_key(self) -> str:
        return _b64encode(_public_bytes(self.exchange_private_key.public_key()))

    @property
    def device_id(self) -> str:
        digest = hashlib.sha256(_b64decode(self.signing_public_key)).digest()
        return "dev_" + _b64encode(digest[:18])

    @property
    def fingerprint(self) -> str:
        digest = hashlib.sha256(_b64decode(self.signing_public_key)).hexdigest()
        return " ".join(digest[index : index + 4] for index in range(0, 32, 4))

    def export_private(self) -> str:
        return _encode_bundle(
            {
                "version": _REMOTE_IDENTITY_VERSION,
                "signing_private_key": _b64encode(
                    _private_bytes(self.signing_private_key)
                ),
                "exchange_private_key": _b64encode(
                    _private_bytes(self.exchange_private_key)
                ),
            }
        )

    @classmethod
    def generate(cls) -> RemoteIdentity:
        return cls(
            signing_private_key=Ed25519PrivateKey.generate(),
            exchange_private_key=X25519PrivateKey.generate(),
        )

    @classmethod
    def from_private(cls, value: str) -> RemoteIdentity:
        payload = _decode_bundle(value)
        if (
            int(payload.get("version") or 0)
            not in _SUPPORTED_REMOTE_IDENTITY_VERSIONS
        ):
            raise ValueError("unsupported remote identity version")
        return cls(
            signing_private_key=Ed25519PrivateKey.from_private_bytes(
                _b64decode(str(payload.get("signing_private_key") or ""))
            ),
            exchange_private_key=X25519PrivateKey.from_private_bytes(
                _b64decode(str(payload.get("exchange_private_key") or ""))
            ),
        )


class RemoteIdentityStore:
    """Store private device identity in a local owner-only file."""

    def __init__(self, db_path: str, *, fallback_path: Path | None = None) -> None:
        resolved = Path(db_path).expanduser().resolve()
        self._fallback_path = fallback_path or resolved.with_suffix(
            resolved.suffix + ".remote-identity"
        )
        self._cached: RemoteIdentity | None = None

    def _read_secret(self) -> str:
        if self._fallback_path.exists():
            os.chmod(self._fallback_path, 0o600)
            return self._fallback_path.read_text(encoding="utf-8").strip()
        return ""

    def _write_secret(self, value: str) -> None:
        self._fallback_path.parent.mkdir(parents=True, exist_ok=True)
        self._fallback_path.write_text(value, encoding="utf-8")
        os.chmod(self._fallback_path, 0o600)

    def get_or_create(self) -> RemoteIdentity:
        if self._cached is not None:
            return self._cached
        secret = self._read_secret()
        if secret:
            self._cached = RemoteIdentity.from_private(secret)
            return self._cached
        self._cached = RemoteIdentity.generate()
        self._write_secret(self._cached.export_private())
        return self._cached


class RemoteControlStore:
    """SQLite-backed remote settings, trust grants, dedupe, replay, and audit."""

    def __init__(
        self,
        db_path: str,
        *,
        identity_store: RemoteIdentityStore | None = None,
    ) -> None:
        if str(db_path) == ":memory:":
            self.db_path = str(
                Path(tempfile.gettempdir())
                / f"cyrene-remote-openapi-{uuid4().hex}.sqlite3"
            )
        else:
            self.db_path = str(Path(db_path).expanduser().resolve())
        self.remote_db_path = self.db_path + _REMOTE_STORE_SUFFIX
        self.identity_store = identity_store or RemoteIdentityStore(self.db_path)
        self._lock = threading.RLock()
        self._short_pairings: dict[str, dict[str, Any]] = {}
        self._short_pairing_attempts: dict[str, list[float]] = {}
        self._initialize()

    @property
    def identity(self) -> RemoteIdentity:
        return self.identity_store.get_or_create()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.remote_db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def catalog_revision(self) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT revision FROM remote_catalog_state WHERE singleton = 1"
            ).fetchone()
        return int(row["revision"] if row is not None else 1)

    def _bump_catalog_revision(
        self,
        conn: sqlite3.Connection,
        *,
        reason: str,
        peer_device_id: str = "",
    ) -> int:
        conn.execute(
            """
            INSERT INTO remote_catalog_state(singleton, revision, updated_at)
            VALUES (1, 2, ?)
            ON CONFLICT(singleton) DO UPDATE SET
                revision = remote_catalog_state.revision + 1,
                updated_at = excluded.updated_at
            """,
            (utc_iso(),),
        )
        row = conn.execute(
            "SELECT revision FROM remote_catalog_state WHERE singleton = 1"
        ).fetchone()
        revision = int(row["revision"] if row is not None else 1)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return revision

        async def publish() -> None:
            try:
                from cyrene.observability import debug

                await debug.publish_event(
                    {
                        "type": "remote_devices_changed",
                        "revision": revision,
                        "reason": str(reason or "updated"),
                        "device_id": str(peer_device_id or ""),
                    }
                )
            except Exception:
                logger.debug("Failed to publish remote device change", exc_info=True)

        loop.create_task(publish())
        return revision

    def _initialize(self) -> None:
        Path(self.remote_db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS remote_settings (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    enabled INTEGER NOT NULL DEFAULT 0,
                    relay_url TEXT NOT NULL DEFAULT '',
                    device_name TEXT NOT NULL DEFAULT '',
                    listen_port INTEGER NOT NULL DEFAULT 37841,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS remote_peers (
                    device_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    signing_public_key TEXT NOT NULL,
                    exchange_public_key TEXT NOT NULL,
                    lan_address TEXT NOT NULL DEFAULT '',
                    granted_capabilities_json TEXT NOT NULL DEFAULT '[]',
                    granted_project_scopes_json TEXT NOT NULL DEFAULT '[]',
                    received_capabilities_json TEXT NOT NULL DEFAULT '[]',
                    received_project_scopes_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL DEFAULT '',
                    revoked_at TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS remote_pairings (
                    pairing_id TEXT PRIMARY KEY,
                    secret_hash TEXT NOT NULL,
                    granted_capabilities_json TEXT NOT NULL,
                    granted_project_scopes_json TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    used_at TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS remote_replay_nonces (
                    peer_device_id TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    PRIMARY KEY(peer_device_id, nonce)
                );

                CREATE TABLE IF NOT EXISTS remote_command_dedupe (
                    peer_device_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'complete',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(peer_device_id, idempotency_key)
                );

                CREATE TABLE IF NOT EXISTS remote_audit_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    peer_device_id TEXT NOT NULL DEFAULT '',
                    command TEXT NOT NULL DEFAULT '',
                    outcome TEXT NOT NULL DEFAULT '',
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS remote_catalog_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    revision INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS remote_file_transfers (
                    transfer_id TEXT PRIMARY KEY,
                    peer_device_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    staging_path TEXT NOT NULL,
                    expected_size INTEGER NOT NULL DEFAULT 0,
                    expected_sha256 TEXT NOT NULL DEFAULT '',
                    received_size INTEGER NOT NULL DEFAULT 0,
                    conflict_policy TEXT NOT NULL DEFAULT 'fail',
                    state TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS remote_jobs (
                    job_id TEXT PRIMARY KEY,
                    peer_device_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    command_hash TEXT NOT NULL,
                    origin_chat_id TEXT NOT NULL DEFAULT '',
                    cwd_relative TEXT NOT NULL DEFAULT '.',
                    outside_workspace INTEGER NOT NULL DEFAULT 0,
                    log_path TEXT NOT NULL,
                    pid INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    exit_code INTEGER,
                    artifact_paths_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT '',
                    completed_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO remote_settings(
                    singleton, enabled, relay_url, device_name, updated_at
                ) VALUES (1, 0, ?, ?, ?)
                """,
                (
                    DEFAULT_RELAY_URL,
                    platform.node() or "Cyrene device",
                    utc_iso(),
                ),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO remote_catalog_state(
                    singleton, revision, updated_at
                ) VALUES (1, 1, ?)
                """,
                (utc_iso(),),
            )

    def public_identity(self) -> dict[str, str]:
        identity = self.identity
        settings = self.get_settings()
        return {
            "device_id": identity.device_id,
            "device_name": str(settings["device_name"]),
            "signing_public_key": identity.signing_public_key,
            "exchange_public_key": identity.exchange_public_key,
            "fingerprint": identity.fingerprint,
        }

    def get_settings(self) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT enabled, relay_url, device_name, listen_port, updated_at "
                "FROM remote_settings WHERE singleton = 1"
            ).fetchone()
        assert row is not None
        return {
            "enabled": bool(row["enabled"]),
            "relay_url": str(row["relay_url"]),
            "device_name": str(row["device_name"]),
            "listen_port": int(row["listen_port"] or DIRECT_PAIRING_PORT),
            "updated_at": str(row["updated_at"]),
        }

    def update_listen_port(self, listen_port: int) -> dict[str, Any]:
        port = int(listen_port)
        if port < 1024 or port > 65535:
            raise ValueError("listener port must be between 1024 and 65535")
        current = self.get_settings()
        if int(current["listen_port"]) == port:
            return current
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE remote_settings
                SET listen_port = ?, updated_at = ?
                WHERE singleton = 1
                """,
                (port, utc_iso()),
            )
        self.audit(
            "remote_listener_port_selected",
            outcome="fallback" if port != DIRECT_PAIRING_PORT else "default",
            detail={"listen_port": port},
        )
        return self.get_settings()

    def update_settings(
        self,
        *,
        enabled: bool,
        relay_url: str,
        device_name: str,
    ) -> dict[str, Any]:
        relay = str(relay_url or "").strip()
        if relay:
            _validate_relay_url(relay)
        name = str(device_name or "").strip()
        if not name:
            raise ValueError("device name is required")
        if len(name) > 120:
            raise ValueError("device name is too long")
        now = utc_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE remote_settings
                SET enabled = ?, relay_url = ?, device_name = ?, updated_at = ?
                WHERE singleton = 1
                """,
                (
                    int(bool(enabled)),
                    relay,
                    name,
                    now,
                ),
            )
        self.audit(
            "remote_settings_updated",
            outcome="enabled" if enabled else "disabled",
            detail={"relay_url_configured": bool(relay)},
        )
        return self.get_settings()

    @staticmethod
    def _normalize_capabilities(values: list[str] | tuple[str, ...]) -> list[str]:
        normalized = sorted({str(item) for item in values if str(item)})
        invalid = [item for item in normalized if not _valid_remote_grant(item)]
        if invalid:
            raise ValueError(f"unsupported remote capabilities: {', '.join(invalid)}")
        return sorted(set(normalized) | BASE_REMOTE_CAPABILITIES)

    @staticmethod
    def _normalize_scopes(values: list[str] | tuple[str, ...]) -> list[str]:
        scopes = sorted({str(item).strip() for item in values if str(item).strip()})
        if any(len(item) > 200 for item in scopes):
            raise ValueError("project scope is too long")
        return scopes

    def create_pairing_invitation(
        self,
        *,
        capabilities: list[str] | tuple[str, ...] = DEFAULT_REMOTE_CAPABILITIES,
        project_scopes: list[str] | tuple[str, ...] = (),
        ttl_seconds: int = PAIRING_TTL_SECONDS,
    ) -> dict[str, Any]:
        settings = self.get_settings()
        if not settings["enabled"]:
            raise RuntimeError("remote access is disabled")
        caps = self._normalize_capabilities(capabilities)
        scopes = self._normalize_scopes(project_scopes)
        ttl = max(30, min(int(ttl_seconds), 600))
        pairing_id = "pair_" + uuid4().hex
        secret = secrets.token_urlsafe(32)
        now = _utc_now()
        expires = now + timedelta(seconds=ttl)
        secret_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO remote_pairings(
                    pairing_id, secret_hash, granted_capabilities_json,
                    granted_project_scopes_json, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    pairing_id,
                    secret_hash,
                    _json_dumps(caps),
                    _json_dumps(scopes),
                    utc_iso(expires),
                    utc_iso(now),
                ),
            )
        identity = self.public_identity()
        invitation = {
            "version": REMOTE_PROTOCOL_VERSION,
            "kind": "cyrene_pairing_invitation",
            "pairing_id": pairing_id,
            "secret": secret,
            "expires_at": utc_iso(expires),
            "relay_url": settings["relay_url"],
            "device": identity,
            "granted_capabilities": caps,
            "granted_project_scopes": scopes,
        }
        invitation["signature"] = _identity_signature(
            self.identity,
            invitation,
        )
        self.audit(
            "pairing_invitation_created",
            detail={"pairing_id": pairing_id, "expires_at": invitation["expires_at"]},
        )
        return {
            "pairing_id": pairing_id,
            "expires_at": invitation["expires_at"],
            "invitation": _encode_bundle(invitation),
            "fingerprint": identity["fingerprint"],
        }

    @staticmethod
    def normalize_short_pairing_code(value: str) -> str:
        code = "".join(character for character in str(value or "").upper() if character.isalnum())
        if len(code) != DIRECT_PAIRING_CODE_LENGTH:
            raise ValueError("pairing key must contain 10 letters or digits")
        return code

    def create_short_pairing_invitation(
        self,
        *,
        capabilities: list[str] | tuple[str, ...] = DEFAULT_REMOTE_CAPABILITIES,
        project_scopes: list[str] | tuple[str, ...] = (),
        ttl_seconds: int = PAIRING_TTL_SECONDS,
    ) -> dict[str, Any]:
        invitation = self.create_pairing_invitation(
            capabilities=capabilities,
            project_scopes=project_scopes,
            ttl_seconds=ttl_seconds,
        )
        alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
        code = "".join(
            secrets.choice(alphabet) for _ in range(DIRECT_PAIRING_CODE_LENGTH)
        )
        normalized = self.normalize_short_pairing_code(code)
        with self._lock:
            self._purge_short_pairings()
            self._short_pairings[hashlib.sha256(normalized.encode()).hexdigest()] = {
                "pairing_id": invitation["pairing_id"],
                "invitation": invitation["invitation"],
                "expires_at": invitation["expires_at"],
                "claimed_by": "",
            }
        return {
            "pairing_id": invitation["pairing_id"],
            "expires_at": invitation["expires_at"],
            "pairing_key": f"{code[:5]}-{code[5:]}",
            "fingerprint": invitation["fingerprint"],
        }

    def _purge_short_pairings(self) -> None:
        now = _utc_now()
        expired = [
            key
            for key, value in self._short_pairings.items()
            if datetime.fromisoformat(str(value["expires_at"])) <= now
        ]
        for key in expired:
            self._short_pairings.pop(key, None)

    def claim_short_pairing_invitation(
        self, pairing_key: str, *, source: str
    ) -> dict[str, Any]:
        normalized = self.normalize_short_pairing_code(pairing_key)
        source_key = str(source or "unknown")
        now = time.monotonic()
        with self._lock:
            attempts = [
                observed
                for observed in self._short_pairing_attempts.get(source_key, [])
                if now - observed < 60
            ]
            if len(attempts) >= DIRECT_PAIRING_MAX_ATTEMPTS:
                raise RuntimeError("too many pairing attempts; try again later")
            attempts.append(now)
            self._short_pairing_attempts[source_key] = attempts
            self._purge_short_pairings()
            key_hash = hashlib.sha256(normalized.encode()).hexdigest()
            offer = self._short_pairings.get(key_hash)
            if offer is None:
                raise ValueError("pairing key is invalid or expired")
            claimed_by = str(offer.get("claimed_by") or "")
            if claimed_by and claimed_by != source_key:
                raise ValueError("pairing key is already being used")
            offer["claimed_by"] = source_key
            invitation = str(offer["invitation"])
            pairing_id = str(offer["pairing_id"])
        self.audit(
            "short_pairing_claimed",
            outcome="pending_completion",
            detail={"pairing_id": pairing_id, "source": source_key},
        )
        return {"invitation": invitation, "pairing_id": pairing_id}

    def complete_short_pairing_response(
        self, response: str, *, source: str, lan_address: str = ""
    ) -> dict[str, Any]:
        payload = _decode_bundle(response)
        pairing_id = str(payload.get("pairing_id") or "")
        source_key = str(source or "unknown")
        with self._lock:
            self._purge_short_pairings()
            matching_key = next(
                (
                    key
                    for key, value in self._short_pairings.items()
                    if str(value.get("pairing_id") or "") == pairing_id
                ),
                "",
            )
            offer = self._short_pairings.get(matching_key)
            if offer is None:
                raise ValueError("pairing key is invalid or expired")
            if str(offer.get("claimed_by") or "") != source_key:
                raise ValueError("pairing completion source does not match")
        peer = self.complete_pairing_response(response)
        if lan_address:
            self.update_peer_lan_address(str(peer["device_id"]), lan_address)
        with self._lock:
            self._short_pairings.pop(matching_key, None)
        return peer

    def accept_pairing_invitation(self, invitation: str) -> dict[str, Any]:
        payload = _decode_bundle(invitation)
        if payload.get("kind") != "cyrene_pairing_invitation":
            raise ValueError("invalid pairing invitation")
        if int(payload.get("version") or 0) != REMOTE_PROTOCOL_VERSION:
            raise ValueError("unsupported pairing protocol")
        expires = datetime.fromisoformat(str(payload.get("expires_at") or ""))
        if expires <= _utc_now():
            raise ValueError("pairing invitation expired")
        device = payload.get("device")
        if not isinstance(device, dict):
            raise ValueError("pairing invitation has no device identity")
        peer_id = str(device.get("device_id") or "")
        if peer_id == self.identity.device_id:
            raise ValueError("cannot pair a device with itself")
        signing_key = str(device.get("signing_public_key") or "")
        exchange_key = str(device.get("exchange_public_key") or "")
        self._validate_public_identity(peer_id, signing_key, exchange_key)
        _verify_identity_signature(
            signing_key,
            {
                key: value
                for key, value in payload.items()
                if key != "signature"
            },
            str(payload.get("signature") or ""),
            error="pairing invitation signature mismatch",
        )
        received_caps = self._normalize_capabilities(
            list(payload.get("granted_capabilities") or [])
        )
        received_scopes = self._normalize_scopes(
            list(payload.get("granted_project_scopes") or [])
        )
        self._upsert_peer(
            device_id=peer_id,
            display_name=str(device.get("device_name") or peer_id),
            signing_public_key=signing_key,
            exchange_public_key=exchange_key,
            received_capabilities=received_caps,
            received_project_scopes=received_scopes,
        )
        response_body = {
            "version": REMOTE_PROTOCOL_VERSION,
            "kind": "cyrene_pairing_response",
            "pairing_id": str(payload.get("pairing_id") or ""),
            "secret": str(payload.get("secret") or ""),
            "device": self.public_identity(),
        }
        proof_payload = {
            key: value for key, value in response_body.items() if key != "secret"
        }
        response_body["proof"] = hmac.new(
            str(response_body["secret"]).encode("utf-8"),
            _json_dumps(proof_payload).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        response_body["signature"] = _identity_signature(
            self.identity,
            response_body,
        )
        self.audit(
            "pairing_invitation_accepted",
            peer_device_id=peer_id,
            outcome="pending_remote_completion",
        )
        return {
            "peer": self.get_peer(peer_id),
            "response": _encode_bundle(response_body),
        }

    def complete_pairing_response(self, response: str) -> dict[str, Any]:
        payload = _decode_bundle(response)
        if payload.get("kind") != "cyrene_pairing_response":
            raise ValueError("invalid pairing response")
        if int(payload.get("version") or 0) != REMOTE_PROTOCOL_VERSION:
            raise ValueError("unsupported pairing protocol")
        pairing_id = str(payload.get("pairing_id") or "")
        secret = str(payload.get("secret") or "")
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM remote_pairings WHERE pairing_id = ?",
                (pairing_id,),
            ).fetchone()
            if row is None:
                raise ValueError("pairing invitation not found")
            if str(row["used_at"] or ""):
                raise ValueError("pairing invitation already used")
            if datetime.fromisoformat(str(row["expires_at"])) <= _utc_now():
                raise ValueError("pairing invitation expired")
            actual_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()
            if not hmac.compare_digest(str(row["secret_hash"]), actual_hash):
                raise ValueError("pairing secret mismatch")
            proof_payload = {
                key: value
                for key, value in payload.items()
                if key not in {"secret", "proof", "signature"}
            }
            expected_proof = hmac.new(
                secret.encode("utf-8"),
                _json_dumps(proof_payload).encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(
                expected_proof, str(payload.get("proof") or "")
            ):
                raise ValueError("pairing proof mismatch")
            device = payload.get("device")
            if not isinstance(device, dict):
                raise ValueError("pairing response has no device identity")
            peer_id = str(device.get("device_id") or "")
            signing_key = str(device.get("signing_public_key") or "")
            exchange_key = str(device.get("exchange_public_key") or "")
            self._validate_public_identity(peer_id, signing_key, exchange_key)
            _verify_identity_signature(
                signing_key,
                {
                    key: value
                    for key, value in payload.items()
                    if key != "signature"
                },
                str(payload.get("signature") or ""),
                error="pairing response signature mismatch",
            )
            caps = json.loads(str(row["granted_capabilities_json"]))
            scopes = json.loads(str(row["granted_project_scopes_json"]))
            self._upsert_peer(
                device_id=peer_id,
                display_name=str(device.get("device_name") or peer_id),
                signing_public_key=signing_key,
                exchange_public_key=exchange_key,
                granted_capabilities=caps,
                granted_project_scopes=scopes,
                connection=conn,
            )
            conn.execute(
                "UPDATE remote_pairings SET used_at = ? WHERE pairing_id = ?",
                (utc_iso(), pairing_id),
            )
        self.audit(
            "pairing_completed",
            peer_device_id=peer_id,
            outcome="trusted",
        )
        return self.get_peer(peer_id)

    def _validate_public_identity(
        self,
        device_id: str,
        signing_public_key: str,
        exchange_public_key: str,
    ) -> None:
        try:
            signing_raw = _b64decode(signing_public_key)
            exchange_raw = _b64decode(exchange_public_key)
            Ed25519PublicKey.from_public_bytes(signing_raw)
            X25519PublicKey.from_public_bytes(exchange_raw)
        except Exception as exc:
            raise ValueError("invalid peer public keys") from exc
        expected = "dev_" + _b64encode(hashlib.sha256(signing_raw).digest()[:18])
        if not hmac.compare_digest(expected, str(device_id or "")):
            raise ValueError("peer device ID does not match its signing key")

    def _upsert_peer(
        self,
        *,
        device_id: str,
        display_name: str,
        signing_public_key: str,
        exchange_public_key: str,
        granted_capabilities: list[str] | None = None,
        granted_project_scopes: list[str] | None = None,
        received_capabilities: list[str] | None = None,
        received_project_scopes: list[str] | None = None,
        lan_address: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        own_connection = connection is None
        conn = connection or self._connect()
        try:
            existing = conn.execute(
                "SELECT * FROM remote_peers WHERE device_id = ?", (device_id,)
            ).fetchone()
            now = utc_iso()
            granted_caps = (
                self._normalize_capabilities(granted_capabilities)
                if granted_capabilities is not None
                else json.loads(str(existing["granted_capabilities_json"]))
                if existing
                else []
            )
            granted_scopes = (
                self._normalize_scopes(granted_project_scopes)
                if granted_project_scopes is not None
                else json.loads(str(existing["granted_project_scopes_json"]))
                if existing
                else []
            )
            received_caps = (
                self._normalize_capabilities(received_capabilities)
                if received_capabilities is not None
                else json.loads(str(existing["received_capabilities_json"]))
                if existing
                else []
            )
            received_scopes = (
                self._normalize_scopes(received_project_scopes)
                if received_project_scopes is not None
                else json.loads(str(existing["received_project_scopes_json"]))
                if existing
                else []
            )
            resolved_lan_address = (
                str(lan_address or "")
                if lan_address is not None
                else str(existing["lan_address"] or "")
                if existing
                else ""
            )
            conn.execute(
                """
                INSERT INTO remote_peers(
                    device_id, display_name, signing_public_key,
                    exchange_public_key, lan_address, granted_capabilities_json,
                    granted_project_scopes_json, received_capabilities_json,
                    received_project_scopes_json, created_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '')
                ON CONFLICT(device_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    signing_public_key = excluded.signing_public_key,
                    exchange_public_key = excluded.exchange_public_key,
                    lan_address = excluded.lan_address,
                    granted_capabilities_json = excluded.granted_capabilities_json,
                    granted_project_scopes_json = excluded.granted_project_scopes_json,
                    received_capabilities_json = excluded.received_capabilities_json,
                    received_project_scopes_json = excluded.received_project_scopes_json,
                    revoked_at = ''
                """,
                (
                    device_id,
                    str(display_name or device_id)[:120],
                    signing_public_key,
                    exchange_public_key,
                    resolved_lan_address,
                    _json_dumps(granted_caps),
                    _json_dumps(granted_scopes),
                    _json_dumps(received_caps),
                    _json_dumps(received_scopes),
                    str(existing["created_at"]) if existing else now,
                ),
            )
            self._bump_catalog_revision(
                conn,
                reason="paired" if existing is None else "peer_updated",
                peer_device_id=device_id,
            )
            if own_connection:
                conn.commit()
        finally:
            if own_connection:
                conn.close()

    def list_peers(self, *, include_revoked: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM remote_peers"
        if not include_revoked:
            query += " WHERE revoked_at = ''"
        query += " ORDER BY created_at DESC"
        with self._lock, self._connect() as conn:
            rows = conn.execute(query).fetchall()
        return [self._peer_from_row(row) for row in rows]

    def get_peer(
        self, device_id: str, *, include_revoked: bool = False
    ) -> dict[str, Any] | None:
        query = "SELECT * FROM remote_peers WHERE device_id = ?"
        values: tuple[Any, ...] = (str(device_id),)
        if not include_revoked:
            query += " AND revoked_at = ''"
        with self._lock, self._connect() as conn:
            row = conn.execute(query, values).fetchone()
        return self._peer_from_row(row) if row is not None else None

    def update_peer_lan_address(self, device_id: str, lan_address: str) -> None:
        address = str(lan_address or "").strip()
        if not address or len(address) > 200:
            raise ValueError("LAN address is invalid")
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE remote_peers
                SET lan_address = ?, last_seen_at = ?
                WHERE device_id = ? AND revoked_at = ''
                """,
                (address, utc_iso(), str(device_id)),
            )
            if cursor.rowcount != 1:
                raise KeyError(device_id)

    def update_peer_listener_port(
        self,
        device_id: str,
        listener_port: int,
    ) -> None:
        port = int(listener_port)
        if port < 1024 or port > 65535:
            raise ValueError("peer listener port is invalid")
        peer = self.get_peer(device_id)
        if peer is None:
            raise KeyError(device_id)
        address = str(peer.get("lan_address") or "")
        if not address:
            return
        if address.startswith("["):
            host = address[1:].split("]", 1)[0]
            updated = f"[{host}]:{port}"
        else:
            host = address.rsplit(":", 1)[0]
            updated = f"{host}:{port}"
        self.update_peer_lan_address(device_id, updated)

    @staticmethod
    def _peer_from_row(row: sqlite3.Row) -> dict[str, Any]:
        signing_key = str(row["signing_public_key"])
        fingerprint = hashlib.sha256(_b64decode(signing_key)).hexdigest()
        return {
            "device_id": str(row["device_id"]),
            "display_name": str(row["display_name"]),
            "signing_public_key": signing_key,
            "exchange_public_key": str(row["exchange_public_key"]),
            "lan_address": str(row["lan_address"] or ""),
            "fingerprint": " ".join(
                fingerprint[index : index + 4] for index in range(0, 32, 4)
            ),
            "granted_capabilities": json.loads(
                str(row["granted_capabilities_json"])
            ),
            "granted_project_scopes": json.loads(
                str(row["granted_project_scopes_json"])
            ),
            "received_capabilities": json.loads(
                str(row["received_capabilities_json"])
            ),
            "received_project_scopes": json.loads(
                str(row["received_project_scopes_json"])
            ),
            "created_at": str(row["created_at"]),
            "last_seen_at": str(row["last_seen_at"]),
            "revoked_at": str(row["revoked_at"]),
        }

    def update_peer_grant(
        self,
        device_id: str,
        *,
        capabilities: list[str],
        project_scopes: list[str],
    ) -> dict[str, Any]:
        caps = self._normalize_capabilities(capabilities)
        scopes = self._normalize_scopes(project_scopes)
        current = self.get_peer(device_id)
        if current is None:
            raise KeyError(device_id)
        if (
            current["granted_capabilities"] == caps
            and current["granted_project_scopes"] == scopes
        ):
            return current
        with self._lock, self._connect() as conn:
            result = conn.execute(
                """
                UPDATE remote_peers
                SET granted_capabilities_json = ?,
                    granted_project_scopes_json = ?
                WHERE device_id = ? AND revoked_at = ''
                """,
                (_json_dumps(caps), _json_dumps(scopes), str(device_id)),
            )
            if result.rowcount != 1:
                raise KeyError("remote peer not found")
            self._bump_catalog_revision(
                conn,
                reason="grant_updated",
                peer_device_id=device_id,
            )
        self.audit(
            "peer_grant_updated",
            peer_device_id=device_id,
            outcome="updated",
            detail={"capabilities": caps, "project_scopes": scopes},
        )
        peer = self.get_peer(device_id)
        assert peer is not None
        return peer

    def update_peer_received_grant(
        self,
        device_id: str,
        *,
        capabilities: list[str],
        project_scopes: list[str],
    ) -> dict[str, Any]:
        caps = self._normalize_capabilities(capabilities)
        scopes = self._normalize_scopes(project_scopes)
        current = self.get_peer(device_id)
        if current is None:
            raise KeyError(device_id)
        if (
            current["received_capabilities"] == caps
            and current["received_project_scopes"] == scopes
        ):
            return current
        with self._lock, self._connect() as conn:
            result = conn.execute(
                """
                UPDATE remote_peers
                SET received_capabilities_json = ?,
                    received_project_scopes_json = ?
                WHERE device_id = ? AND revoked_at = ''
                """,
                (_json_dumps(caps), _json_dumps(scopes), str(device_id)),
            )
            if result.rowcount:
                self._bump_catalog_revision(
                    conn,
                    reason="received_grant_updated",
                    peer_device_id=device_id,
                )
        if not result.rowcount:
            raise KeyError(device_id)
        self.audit(
            "peer_received_grant_synchronized",
            peer_device_id=device_id,
            outcome="updated",
            detail={"capabilities": caps, "project_scopes": scopes},
        )
        peer = self.get_peer(device_id)
        assert peer is not None
        return peer

    def revoke_peer(self, device_id: str) -> bool:
        now = utc_iso()
        with self._lock, self._connect() as conn:
            result = conn.execute(
                "UPDATE remote_peers SET revoked_at = ? "
                "WHERE device_id = ? AND revoked_at = ''",
                (now, str(device_id)),
            )
            if result.rowcount:
                self._bump_catalog_revision(
                    conn,
                    reason="revoked",
                    peer_device_id=device_id,
                )
        if result.rowcount:
            self.audit(
                "peer_revoked",
                peer_device_id=device_id,
                outcome="revoked",
            )
        return bool(result.rowcount)

    def touch_peer(self, device_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE remote_peers
                SET last_seen_at = ?
                WHERE device_id = ? AND revoked_at = ''
                """,
                (utc_iso(), str(device_id)),
            )

    def authorize_inbound(
        self,
        peer_device_id: str,
        command: str,
        project_id: str = "",
    ) -> tuple[bool, str]:
        settings = self.get_settings()
        if not settings["enabled"]:
            return False, "remote_access_disabled"
        peer = self.get_peer(peer_device_id)
        if peer is None:
            return False, "peer_not_trusted"
        required = _COMMAND_CAPABILITIES.get(str(command))
        if required is None:
            return False, "command_not_allowed"
        if command in _REMOTE_SHELL_COMMANDS and not _remote_shell_plugin_available():
            return False, "plugin_pack_unavailable"
        if required and required not in peer["granted_capabilities"]:
            return False, "capability_denied"
        if command in _PROJECT_SCOPED_COMMANDS and not project_id:
            return False, "project_scope_required"
        scopes = peer["granted_project_scopes"]
        if project_id and project_id not in scopes:
            return False, "project_scope_denied"
        return True, ""

    def authorize_outbound(
        self,
        peer_device_id: str,
        command: str,
        project_id: str = "",
    ) -> tuple[bool, str]:
        peer = self.get_peer(peer_device_id)
        if peer is None:
            return False, "peer_not_trusted"
        required = _COMMAND_CAPABILITIES.get(str(command))
        if required is None:
            return False, "command_not_supported"
        if command in _REMOTE_SHELL_COMMANDS and not _remote_shell_plugin_available():
            return False, "plugin_pack_unavailable"
        if required and required not in peer["received_capabilities"]:
            return False, "capability_not_granted_by_peer"
        if command in _PROJECT_SCOPED_COMMANDS and not project_id:
            return False, "project_scope_required"
        scopes = peer["received_project_scopes"]
        if project_id and project_id not in scopes:
            return False, "project_not_shared_by_peer"
        return True, ""

    def mark_nonce(self, peer_device_id: str, nonce: str) -> bool:
        cutoff = utc_iso(_utc_now() - timedelta(seconds=ENVELOPE_MAX_SKEW_SECONDS))
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM remote_replay_nonces WHERE observed_at < ?", (cutoff,)
            )
            try:
                conn.execute(
                    """
                    INSERT INTO remote_replay_nonces(
                        peer_device_id, nonce, observed_at
                    ) VALUES (?, ?, ?)
                    """,
                    (str(peer_device_id), str(nonce), utc_iso()),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def claim_dedupe(
        self,
        peer_device_id: str,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> tuple[str, dict[str, Any] | None]:
        if not idempotency_key:
            return "execute", None
        digest = hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()
        peer_id = str(peer_device_id)
        key = str(idempotency_key)
        now = _utc_now()
        stale_before = utc_iso(now - timedelta(minutes=10))
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT payload_hash, result_json, state, created_at
                FROM remote_command_dedupe
                WHERE peer_device_id = ? AND idempotency_key = ?
                """,
                (peer_id, key),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO remote_command_dedupe(
                        peer_device_id, idempotency_key, payload_hash,
                        result_json, state, created_at
                    ) VALUES (?, ?, ?, '', 'in_progress', ?)
                    """,
                    (peer_id, key, digest, utc_iso(now)),
                )
                return "execute", None
            if not hmac.compare_digest(str(row["payload_hash"]), digest):
                raise ValueError("idempotency key reused with different payload")
            if str(row["state"]) == "complete":
                return "duplicate", json.loads(str(row["result_json"]))
            if str(row["created_at"]) < stale_before:
                conn.execute(
                    """
                    UPDATE remote_command_dedupe
                    SET created_at = ?
                    WHERE peer_device_id = ? AND idempotency_key = ?
                    """,
                    (utc_iso(now), peer_id, key),
                )
                return "execute", None
            return "in_progress", None

    def store_dedupe_result(
        self,
        peer_device_id: str,
        idempotency_key: str,
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        if not idempotency_key:
            return
        digest = hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO remote_command_dedupe(
                    peer_device_id, idempotency_key, payload_hash,
                    result_json, state, created_at
                ) VALUES (?, ?, ?, ?, 'complete', ?)
                ON CONFLICT(peer_device_id, idempotency_key) DO UPDATE SET
                    payload_hash = excluded.payload_hash,
                    result_json = excluded.result_json,
                    state = 'complete',
                    created_at = excluded.created_at
                """,
                (
                    str(peer_device_id),
                    str(idempotency_key),
                    digest,
                    _json_dumps(result),
                    utc_iso(),
                ),
            )

    def audit(
        self,
        event_type: str,
        *,
        peer_device_id: str = "",
        command: str = "",
        outcome: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO remote_audit_events(
                    event_id, event_type, peer_device_id, command,
                    outcome, detail_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "audit_" + uuid4().hex,
                    str(event_type),
                    str(peer_device_id),
                    str(command),
                    str(outcome),
                    _json_dumps(detail or {}),
                    utc_iso(),
                ),
            )

    def list_audit_events(self, *, limit: int = 200) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, event_type, peer_device_id, command,
                       outcome, detail_json, created_at
                FROM remote_audit_events
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [
            {
                "event_id": str(row["event_id"]),
                "event_type": str(row["event_type"]),
                "peer_device_id": str(row["peer_device_id"]),
                "command": str(row["command"]),
                "outcome": str(row["outcome"]),
                "detail": json.loads(str(row["detail_json"])),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]


class RemoteEnvelopeCodec:
    """Signed Ed25519 envelope with X25519/HKDF/ChaCha20-Poly1305 payload."""

    @staticmethod
    def _shared_key(
        identity: RemoteIdentity,
        peer_exchange_public_key: str,
        sender_id: str,
        recipient_id: str,
    ) -> bytes:
        peer_key = X25519PublicKey.from_public_bytes(
            _b64decode(peer_exchange_public_key)
        )
        shared = identity.exchange_private_key.exchange(peer_key)
        context = "|".join(sorted((sender_id, recipient_id))).encode("utf-8")
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=hashlib.sha256(context).digest(),
            info=b"cyrene-remote-envelope-v1",
        ).derive(shared)

    @classmethod
    def encode(
        cls,
        *,
        identity: RemoteIdentity,
        peer: dict[str, Any],
        kind: str,
        payload: dict[str, Any],
        message_id: str | None = None,
    ) -> dict[str, Any]:
        recipient_id = str(peer["device_id"])
        message = str(message_id or "msg_" + uuid4().hex)
        nonce = os.urandom(12)
        header = {
            "version": REMOTE_PROTOCOL_VERSION,
            "message_id": message,
            "sender_device_id": identity.device_id,
            "recipient_device_id": recipient_id,
            "kind": str(kind),
            "timestamp": int(time.time()),
            "nonce": _b64encode(nonce),
        }
        aad = _json_dumps(header).encode("utf-8")
        key = cls._shared_key(
            identity,
            str(peer["exchange_public_key"]),
            identity.device_id,
            recipient_id,
        )
        ciphertext = ChaCha20Poly1305(key).encrypt(
            nonce, _json_dumps(payload).encode("utf-8"), aad
        )
        unsigned = {**header, "ciphertext": _b64encode(ciphertext)}
        signature = identity.signing_private_key.sign(
            _json_dumps(unsigned).encode("utf-8")
        )
        return {**unsigned, "signature": _b64encode(signature)}

    @classmethod
    def decode(
        cls,
        *,
        identity: RemoteIdentity,
        peer: dict[str, Any],
        envelope: dict[str, Any],
        mark_nonce: Callable[[str, str], bool],
    ) -> tuple[str, dict[str, Any]]:
        if int(envelope.get("version") or 0) != REMOTE_PROTOCOL_VERSION:
            raise ValueError("unsupported remote envelope version")
        sender = str(envelope.get("sender_device_id") or "")
        recipient = str(envelope.get("recipient_device_id") or "")
        if sender != str(peer["device_id"]) or recipient != identity.device_id:
            raise ValueError("remote envelope device mismatch")
        timestamp = int(envelope.get("timestamp") or 0)
        if abs(int(time.time()) - timestamp) > ENVELOPE_MAX_SKEW_SECONDS:
            raise ValueError("remote envelope timestamp outside replay window")
        nonce_text = str(envelope.get("nonce") or "")
        if not mark_nonce(sender, nonce_text):
            raise ValueError("remote envelope replay detected")
        unsigned = {
            key: value for key, value in envelope.items() if key != "signature"
        }
        public_key = Ed25519PublicKey.from_public_bytes(
            _b64decode(str(peer["signing_public_key"]))
        )
        try:
            public_key.verify(
                _b64decode(str(envelope.get("signature") or "")),
                _json_dumps(unsigned).encode("utf-8"),
            )
        except Exception as exc:
            raise ValueError("invalid remote envelope signature") from exc
        header = {
            key: unsigned[key]
            for key in (
                "version",
                "message_id",
                "sender_device_id",
                "recipient_device_id",
                "kind",
                "timestamp",
                "nonce",
            )
        }
        key = cls._shared_key(
            identity,
            str(peer["exchange_public_key"]),
            sender,
            recipient,
        )
        try:
            plaintext = ChaCha20Poly1305(key).decrypt(
                _b64decode(nonce_text),
                _b64decode(str(envelope.get("ciphertext") or "")),
                _json_dumps(header).encode("utf-8"),
            )
            payload = json.loads(plaintext.decode("utf-8"))
        except Exception as exc:
            raise ValueError("remote envelope decryption failed") from exc
        if not isinstance(payload, dict):
            raise ValueError("remote envelope payload must be an object")
        return str(envelope.get("kind") or ""), payload


RemoteReceiver = Callable[[dict[str, Any]], Awaitable[None]]


class RemoteRelay(Protocol):
    async def register(self, device_id: str, receiver: RemoteReceiver) -> None: ...

    async def unregister(self, device_id: str) -> None: ...

    async def send(self, envelope: dict[str, Any]) -> None: ...


class InMemoryRemoteRelay:
    """Deterministic relay used for local two-device simulation and tests."""

    def __init__(self) -> None:
        self._receivers: dict[str, RemoteReceiver] = {}
        self._lock = asyncio.Lock()

    async def register(self, device_id: str, receiver: RemoteReceiver) -> None:
        async with self._lock:
            self._receivers[str(device_id)] = receiver

    async def unregister(self, device_id: str) -> None:
        async with self._lock:
            self._receivers.pop(str(device_id), None)

    async def send(self, envelope: dict[str, Any]) -> None:
        recipient = str(envelope.get("recipient_device_id") or "")
        async with self._lock:
            receiver = self._receivers.get(recipient)
        if receiver is None:
            raise ConnectionError("remote device is offline")
        await receiver(dict(envelope))


class WebSocketRemoteRelay:
    """Outbound reconnecting WSS client for an untrusted routing relay."""

    def __init__(
        self,
        relay_url: str,
        *,
        connect_timeout: float = 10,
    ) -> None:
        self.relay_url = str(relay_url or "").strip()
        self.connect_timeout = max(1.0, float(connect_timeout))
        self._device_id = ""
        self._receiver: RemoteReceiver | None = None
        self._connection: Any = None
        self._runner: asyncio.Task[Any] | None = None
        self._delivery_tasks: set[asyncio.Task[Any]] = set()
        self._delivery_receipts: dict[str, asyncio.Future[bool]] = {}
        self._connected = asyncio.Event()
        self._stopping = False
        self._send_lock = asyncio.Lock()
        self._identity: RemoteIdentity | None = None
        self._validate_url()

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    async def wait_connected(self, timeout: float = 10) -> None:
        await asyncio.wait_for(
            self._connected.wait(),
            timeout=max(0.1, float(timeout)),
        )

    def _validate_url(self) -> None:
        _validate_relay_url(self.relay_url)

    def set_identity(self, identity: RemoteIdentity) -> None:
        if identity.device_id != self._device_id and self._device_id:
            raise ValueError("relay identity does not match registered device")
        self._identity = identity

    async def register(
        self,
        device_id: str,
        receiver: RemoteReceiver,
    ) -> None:
        if self._runner is not None and not self._runner.done():
            if self._device_id != str(device_id):
                raise RuntimeError("relay is already registered to another device")
            self._receiver = receiver
            return
        self._device_id = str(device_id)
        self._receiver = receiver
        self._stopping = False
        self._runner = asyncio.create_task(
            self._connection_loop(),
            name=f"remote-relay-{self._device_id[:16]}",
        )

    async def unregister(self, device_id: str) -> None:
        if str(device_id) != self._device_id:
            return
        self._stopping = True
        self._connected.clear()
        connection = self._connection
        if connection is not None:
            try:
                await connection.close()
            except Exception:
                pass
        runner, self._runner = self._runner, None
        if runner is not None and not runner.done():
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)
        deliveries = list(self._delivery_tasks)
        for task in deliveries:
            task.cancel()
        if deliveries:
            await asyncio.gather(*deliveries, return_exceptions=True)
        self._delivery_tasks.clear()
        for receipt in self._delivery_receipts.values():
            if not receipt.done():
                receipt.set_exception(
                    ConnectionError("remote relay stopped")
                )
        self._delivery_receipts.clear()
        self._connection = None
        self._receiver = None
        self._device_id = ""

    async def send(self, envelope: dict[str, Any]) -> None:
        try:
            await asyncio.wait_for(
                self._connected.wait(),
                timeout=self.connect_timeout,
            )
        except TimeoutError as exc:
            raise ConnectionError("remote relay is offline") from exc
        async with self._send_lock:
            connection = self._connection
            if connection is None:
                raise ConnectionError("remote relay is offline")
            message_id = str(envelope.get("message_id") or "")
            loop = asyncio.get_running_loop()
            receipt: asyncio.Future[bool] = loop.create_future()
            if message_id:
                self._delivery_receipts[message_id] = receipt
            try:
                await connection.send(
                    _json_dumps({"type": "envelope", "envelope": envelope})
                )
            except Exception:
                self._delivery_receipts.pop(message_id, None)
                raise
        if not message_id:
            return
        try:
            delivered = await asyncio.wait_for(
                receipt,
                timeout=self.connect_timeout,
            )
            if not delivered:
                raise ConnectionError("remote device is offline")
        finally:
            self._delivery_receipts.pop(message_id, None)

    async def _connection_loop(self) -> None:
        from websockets.asyncio.client import connect

        backoff = 0.5
        while not self._stopping:
            try:
                async with connect(
                    self.relay_url,
                    open_timeout=self.connect_timeout,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=24 * 1024 * 1024,
                    proxy=(
                        None
                        if urlparse(self.relay_url).hostname
                        in {"127.0.0.1", "localhost", "::1"}
                        else True
                    ),
                ) as connection:
                    self._connection = connection
                    identity = self._identity
                    if identity is None:
                        raise RuntimeError("remote relay identity is not configured")
                    registration = {
                        "type": "register",
                        "protocol_version": REMOTE_PROTOCOL_VERSION,
                        "device_id": self._device_id,
                        "signing_public_key": identity.signing_public_key,
                        "timestamp": int(time.time()),
                        "nonce": _b64encode(os.urandom(18)),
                    }
                    signature = identity.signing_private_key.sign(
                        _json_dumps(registration).encode("utf-8")
                    )
                    await connection.send(
                        _json_dumps(
                            {
                                **registration,
                                "signature": _b64encode(signature),
                            }
                        )
                    )
                    raw_ack = await asyncio.wait_for(
                        connection.recv(),
                        timeout=self.connect_timeout,
                    )
                    ack = json.loads(raw_ack)
                    if (
                        not isinstance(ack, dict)
                        or ack.get("type") != "registered"
                        or ack.get("device_id") != self._device_id
                    ):
                        raise ConnectionError("remote relay rejected registration")
                    self._connected.set()
                    backoff = 0.5
                    async for raw in connection:
                        await self._queue_delivery(raw)
            except asyncio.CancelledError:
                raise
            except Exception:
                if not self._stopping:
                    logger.warning(
                        "Remote relay disconnected; reconnecting",
                        exc_info=True,
                    )
            finally:
                self._connected.clear()
                self._connection = None
                for receipt in self._delivery_receipts.values():
                    if not receipt.done():
                        receipt.set_exception(
                            ConnectionError("remote relay disconnected")
                        )
            if not self._stopping:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15.0)

    async def _queue_delivery(self, raw: Any) -> None:
        try:
            message = json.loads(raw)
        except Exception:
            logger.warning("Remote relay delivered invalid JSON")
            return
        if (
            not isinstance(message, dict)
            or not isinstance(message.get("type"), str)
        ):
            return
        if message.get("type") == "delivery_receipt":
            receipt = self._delivery_receipts.get(
                str(message.get("message_id") or "")
            )
            if receipt is not None and not receipt.done():
                receipt.set_result(bool(message.get("delivered")))
            return
        if (
            message.get("type") != "envelope"
            or not isinstance(message.get("envelope"), dict)
            or self._receiver is None
        ):
            return
        task = asyncio.create_task(self._receiver(dict(message["envelope"])))
        self._delivery_tasks.add(task)
        task.add_done_callback(self._delivery_tasks.discard)


RemoteCommandHandler = Callable[
    [str, str, dict[str, Any], str],
    Awaitable[dict[str, Any]],
]


class RemoteGateway:
    """Relay client that enforces local grants before invoking command handlers."""

    def __init__(
        self,
        store: RemoteControlStore,
        relay: RemoteRelay,
        command_handler: RemoteCommandHandler,
    ) -> None:
        self.store = store
        self.relay = relay
        self.command_handler = command_handler
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._started = False
        self._grant_sync_task: asyncio.Task[Any] | None = None

    @property
    def device_id(self) -> str:
        return self.store.identity.device_id

    @property
    def started(self) -> bool:
        return self._started

    @property
    def connected(self) -> bool:
        relay_connected = getattr(self.relay, "connected", None)
        return bool(relay_connected if relay_connected is not None else self._started)

    async def start(self) -> None:
        if self._started:
            return
        set_identity = getattr(self.relay, "set_identity", None)
        if callable(set_identity):
            set_identity(self.store.identity)
        await self.relay.register(self.device_id, self._receive)
        set_inline_receiver = getattr(
            self.relay,
            "set_inline_request_receiver",
            None,
        )
        if callable(set_inline_receiver):
            set_inline_receiver(self.handle_inline_request)
        self._started = True
        self._grant_sync_task = asyncio.create_task(
            self._grant_sync_loop(),
            name=f"remote-grant-sync-{self.device_id[:16]}",
        )
        self.store.audit("remote_gateway_started", outcome="online")

    async def stop(self) -> None:
        if not self._started:
            return
        set_inline_receiver = getattr(
            self.relay,
            "set_inline_request_receiver",
            None,
        )
        if callable(set_inline_receiver):
            set_inline_receiver(None)
        await self.relay.unregister(self.device_id)
        self._started = False
        sync_task, self._grant_sync_task = self._grant_sync_task, None
        if sync_task is not None and not sync_task.done():
            sync_task.cancel()
            await asyncio.gather(sync_task, return_exceptions=True)
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("remote gateway stopped"))
        self._pending.clear()
        self.store.audit("remote_gateway_stopped", outcome="offline")

    async def _send_peer_state(
        self,
        peer: dict[str, Any],
        *,
        kind: str,
        payload: dict[str, Any],
    ) -> None:
        envelope = RemoteEnvelopeCodec.encode(
            identity=self.store.identity,
            peer=peer,
            kind=kind,
            payload=payload,
        )
        await self.relay.send(envelope)

    async def notify_grant_update(self, peer_device_id: str) -> None:
        peer = self.store.get_peer(peer_device_id)
        if peer is None:
            raise KeyError(peer_device_id)
        await self._send_peer_state(
            peer,
            kind="grant_update",
            payload={
                "capabilities": peer["granted_capabilities"],
                "project_scopes": peer["granted_project_scopes"],
                "listener_port": int(
                    getattr(self.relay, "port", DIRECT_PAIRING_PORT)
                ),
            },
        )

    async def notify_revocation(self, peer_device_id: str) -> None:
        peer = self.store.get_peer(peer_device_id)
        if peer is None:
            raise KeyError(peer_device_id)
        await self._send_peer_state(
            peer,
            kind="peer_revoked",
            payload={},
        )

    async def send_event(
        self,
        peer_device_id: str,
        event: dict[str, Any],
    ) -> None:
        peer = self.store.get_peer(peer_device_id)
        if peer is None:
            raise KeyError(peer_device_id)
        await self._send_peer_state(
            peer,
            kind="event",
            payload={"event": dict(event)},
        )

    async def _grant_sync_loop(self) -> None:
        while self._started:
            for peer in self.store.list_peers():
                try:
                    await self.notify_grant_update(
                        str(peer["device_id"])
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.debug(
                        "Remote grant synchronization deferred for %s",
                        peer.get("device_id"),
                        exc_info=True,
                    )
            await asyncio.sleep(30)

    async def request(
        self,
        peer_device_id: str,
        *,
        command: str,
        payload: dict[str, Any] | None = None,
        project_id: str = "",
        idempotency_key: str = "",
        timeout: float = 30,
    ) -> dict[str, Any]:
        if not self._started:
            raise RuntimeError("remote gateway is not running")
        allowed, reason = self.store.authorize_outbound(
            peer_device_id, command, project_id
        )
        if not allowed:
            raise PermissionError(reason)
        peer = self.store.get_peer(peer_device_id)
        if peer is None:
            raise PermissionError("peer_not_trusted")
        request_id = "request_" + uuid4().hex
        body = {
            "request_id": request_id,
            "command": str(command),
            "project_id": str(project_id),
            "idempotency_key": str(idempotency_key),
            "payload": dict(payload or {}),
        }
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        envelope = RemoteEnvelopeCodec.encode(
            identity=self.store.identity,
            peer=peer,
            kind="command",
            payload=body,
        )
        self.store.audit(
            "remote_command_sent",
            peer_device_id=peer_device_id,
            command=command,
            outcome="pending",
        )
        try:
            await self.relay.send(envelope)
            result = await asyncio.wait_for(
                future,
                timeout=max(0.1, float(timeout)),
            )
            self.store.audit(
                "remote_command_completed",
                peer_device_id=peer_device_id,
                command=command,
                outcome=(
                    str(result.get("code") or "remote_error")
                    if result.get("ok") is False
                    else "ok"
                ),
            )
            return result
        except Exception as exc:
            self.store.audit(
                "remote_command_failed",
                peer_device_id=peer_device_id,
                command=command,
                outcome=exc.__class__.__name__,
            )
            raise
        finally:
            self._pending.pop(request_id, None)

    async def _receive(self, envelope: dict[str, Any]) -> None:
        sender = str(envelope.get("sender_device_id") or "")
        peer = self.store.get_peer(sender)
        if peer is None:
            self.store.audit(
                "remote_envelope_rejected",
                peer_device_id=sender,
                outcome="peer_not_trusted",
            )
            return
        try:
            kind, payload = RemoteEnvelopeCodec.decode(
                identity=self.store.identity,
                peer=peer,
                envelope=envelope,
                mark_nonce=self.store.mark_nonce,
            )
        except Exception as exc:
            logger.info(
                "Rejected invalid remote envelope from %s",
                sender,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            self.store.audit(
                "remote_envelope_rejected",
                peer_device_id=sender,
                outcome="invalid_envelope",
                detail={"code": "invalid_remote_envelope"},
            )
            return
        self.store.touch_peer(sender)
        if kind == "grant_update":
            try:
                self.store.update_peer_received_grant(
                    sender,
                    capabilities=list(payload.get("capabilities") or []),
                    project_scopes=list(payload.get("project_scopes") or []),
                )
                listener_port = int(payload.get("listener_port") or 0)
                if listener_port:
                    self.store.update_peer_listener_port(
                        sender,
                        listener_port,
                    )
            except (KeyError, ValueError):
                self.store.audit(
                    "peer_grant_sync_rejected",
                    peer_device_id=sender,
                    outcome="invalid",
                )
            return
        if kind == "peer_revoked":
            self.store.revoke_peer(sender)
            return
        if kind == "event":
            event = payload.get("event")
            if isinstance(event, dict) and str(event.get("type") or "") in {
                "remote_job_update",
                "remote_transfer_update",
            }:
                from cyrene.observability import debug

                await debug.publish_event(
                    {
                        **event,
                        "remote_device_id": sender,
                        "remote_event": True,
                    },
                    session_id=str(
                        event.get("session_id")
                        or event.get("chat_id")
                        or ""
                    ),
                )
            return
        if kind == "response":
            grant = payload.get("grant")
            if isinstance(grant, dict):
                try:
                    self.store.update_peer_received_grant(
                        sender,
                        capabilities=list(
                            grant.get("capabilities") or []
                        ),
                        project_scopes=list(
                            grant.get("project_scopes") or []
                        ),
                    )
                    listener_port = int(grant.get("listener_port") or 0)
                    if listener_port:
                        self.store.update_peer_listener_port(
                            sender,
                            listener_port,
                        )
                except (KeyError, ValueError):
                    pass
            request_id = str(payload.get("request_id") or "")
            future = self._pending.get(request_id)
            if future is not None and not future.done():
                future.set_result(dict(payload.get("result") or {}))
            return
        if kind != "command":
            self.store.audit(
                "remote_envelope_rejected",
                peer_device_id=sender,
                outcome="unsupported_kind",
            )
            return
        await self._handle_command(sender, peer, payload, deliver=True)

    async def handle_inline_request(
        self,
        envelope: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute one trusted command and return its E2EE response inline."""
        sender = str(envelope.get("sender_device_id") or "")
        peer = self.store.get_peer(sender)
        if peer is None:
            raise PermissionError("peer_not_trusted")
        try:
            kind, payload = RemoteEnvelopeCodec.decode(
                identity=self.store.identity,
                peer=peer,
                envelope=envelope,
                mark_nonce=self.store.mark_nonce,
            )
        except Exception as exc:
            logger.info(
                "Rejected invalid inline remote envelope from %s",
                sender,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            self.store.audit(
                "remote_envelope_rejected",
                peer_device_id=sender,
                outcome="invalid_envelope",
                detail={"code": "invalid_remote_envelope"},
            )
            raise ValueError("invalid remote envelope") from exc
        if kind != "command":
            raise ValueError(
                "inline endpoint accepts command envelopes only"
            )
        self.store.touch_peer(sender)
        return await self._handle_command(
            sender,
            peer,
            payload,
            deliver=False,
        )

    async def _handle_command(
        self,
        sender: str,
        peer: dict[str, Any],
        request: dict[str, Any],
        *,
        deliver: bool,
    ) -> dict[str, Any]:
        request_id = str(request.get("request_id") or "")
        command = str(request.get("command") or "")
        project_id = str(request.get("project_id") or "")
        idempotency_key = str(request.get("idempotency_key") or "")
        command_payload = request.get("payload")
        if not isinstance(command_payload, dict):
            command_payload = {}
        allowed, _reason = self.store.authorize_inbound(
            sender, command, project_id
        )
        if not allowed:
            result = {
                "ok": False,
                "error": localized(
                    "The remote operation is not authorized.",
                    "远程操作未获授权。",
                ),
                "code": "remote_permission_denied",
            }
        elif command in _SIDE_EFFECT_COMMANDS and not idempotency_key:
            result = {
                "ok": False,
                "error": localized(
                    "An idempotency key is required for remote actions.",
                    "远程操作必须提供幂等键。",
                ),
                "code": "remote_idempotency_key_required",
            }
        else:
            dedupe_payload = {
                "command": command,
                "project_id": project_id,
                "payload": command_payload,
            }
            try:
                dedupe_state, cached = self.store.claim_dedupe(
                    sender, idempotency_key, dedupe_payload
                )
            except ValueError as exc:
                logger.info(
                    "Remote idempotency conflict",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
                result = {
                    "ok": False,
                    "error": localized(
                        "The idempotency key conflicts with another request.",
                        "幂等键与另一请求冲突。",
                    ),
                    "code": "remote_idempotency_conflict",
                }
            else:
                if dedupe_state == "duplicate":
                    result = {**dict(cached or {}), "duplicate": True}
                elif dedupe_state == "in_progress":
                    result = {
                        "ok": False,
                        "error": localized(
                            "An identical remote command is still running.",
                            "相同的远程命令仍在执行。",
                        ),
                        "code": "remote_command_in_progress",
                        "retryable": True,
                    }
                else:
                    try:
                        result = await self.command_handler(
                            sender, command, command_payload, project_id
                        )
                    except ValueError as exc:
                        logger.info(
                            "Invalid remote command %s",
                            command,
                            exc_info=(type(exc), exc, exc.__traceback__),
                        )
                        result = {
                            "ok": False,
                            "error": localized(
                                "The remote command is invalid.",
                                "远程命令无效。",
                            ),
                            "code": "remote_command_invalid",
                        }
                    except Exception:
                        logger.exception(
                            "Remote command handler failed: %s",
                            command,
                        )
                        result = {
                            "ok": False,
                            "error": localized(
                                "The remote command failed.",
                                "远程命令执行失败。",
                            ),
                            "code": "remote_command_failed",
                        }
                    if not isinstance(result, dict):
                        result = {"ok": True, "data": result}
                    self.store.store_dedupe_result(
                        sender,
                        idempotency_key,
                        dedupe_payload,
                        result,
                    )
        self.store.audit(
            "remote_command_completed",
            peer_device_id=sender,
            command=command,
            outcome="ok" if result.get("ok", True) else "error",
            detail={"request_id": request_id},
        )
        response = RemoteEnvelopeCodec.encode(
            identity=self.store.identity,
            peer=peer,
            kind="response",
            payload={
                "request_id": request_id,
                "result": result,
                "grant": {
                    "capabilities": peer["granted_capabilities"],
                    "project_scopes": peer["granted_project_scopes"],
                    "listener_port": int(
                        getattr(self.relay, "port", DIRECT_PAIRING_PORT)
                    ),
                },
            },
        )
        if deliver:
            await self.relay.send(response)
        return response


__all__ = [
    "BASE_REMOTE_CAPABILITIES",
    "DEFAULT_REMOTE_CAPABILITIES",
    "ENVELOPE_MAX_SKEW_SECONDS",
    "InMemoryRemoteRelay",
    "PAIRING_TTL_SECONDS",
    "REMOTE_CAPABILITIES",
    "REMOTE_EXTENSION_CAPABILITIES",
    "REMOTE_PLUGIN_PACK_PREFIX",
    "REMOTE_PROTOCOL_VERSION",
    "RemoteControlStore",
    "RemoteEnvelopeCodec",
    "RemoteGateway",
    "RemoteIdentity",
    "RemoteIdentityStore",
    "WebSocketRemoteRelay",
    "get_remote_gateway",
    "remote_plugin_pack_capabilities",
    "remote_plugin_pack_ids",
    "register_remote_gateway",
    "unregister_remote_gateway",
]
