"""SQLite state for Remote Desktop sessions, grants, and preferences."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RemoteDesktopStore:
    def __init__(self, db_path: str) -> None:
        self.path = Path(str(db_path)).expanduser().resolve()
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS remote_desktop_sessions (
                    session_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    device_name TEXT NOT NULL DEFAULT '',
                    controller_device_id TEXT NOT NULL DEFAULT '',
                    remote_session_id TEXT NOT NULL DEFAULT '',
                    provider_id TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL,
                    state TEXT NOT NULL,
                    pane_card_id TEXT NOT NULL DEFAULT '',
                    pane_layout_id TEXT NOT NULL DEFAULT '',
                    selected_display_id TEXT NOT NULL DEFAULT '',
                    display_json TEXT NOT NULL DEFAULT '{}',
                    quality_mode TEXT NOT NULL DEFAULT 'auto',
                    transport_kind TEXT NOT NULL DEFAULT '',
                    secure_surface INTEGER NOT NULL DEFAULT 0,
                    microphone_enabled INTEGER NOT NULL DEFAULT 0,
                    clipboard_enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    connected_at TEXT NOT NULL DEFAULT '',
                    disconnected_at TEXT NOT NULL DEFAULT '',
                    last_error_code TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS remote_desktop_sessions_device
                    ON remote_desktop_sessions(device_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS remote_desktop_device_preferences (
                    device_id TEXT PRIMARY KEY,
                    preferred_mode TEXT NOT NULL DEFAULT 'current_desktop',
                    quality_mode TEXT NOT NULL DEFAULT 'auto',
                    preferred_display_id TEXT NOT NULL DEFAULT '',
                    clipboard_enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS remote_desktop_layout_grants (
                    pane_layout_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    origin TEXT NOT NULL,
                    granted INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(pane_layout_id, session_id, chat_id)
                );
                CREATE INDEX IF NOT EXISTS remote_desktop_grants_chat
                    ON remote_desktop_layout_grants(chat_id, granted);

                CREATE TABLE IF NOT EXISTS remote_desktop_layout_revisions (
                    pane_layout_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS remote_desktop_active_layouts (
                    projection_scope_id TEXT PRIMARY KEY,
                    pane_layout_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS remote_desktop_audit (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    session_id TEXT NOT NULL DEFAULT '',
                    device_id TEXT NOT NULL DEFAULT '',
                    chat_id TEXT NOT NULL DEFAULT '',
                    outcome TEXT NOT NULL DEFAULT '',
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                """
            )
            # A process restart never resumes media or credentials implicitly.
            connection.execute(
                """
                UPDATE remote_desktop_sessions
                SET state='reconnect_required', microphone_enabled=0,
                    disconnected_at=CASE WHEN disconnected_at='' THEN ? ELSE disconnected_at END
                WHERE state NOT IN ('disconnected', 'failed', 'reconnect_required')
                """,
                (utc_iso(),),
            )

    @staticmethod
    def _session(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        value = dict(row)
        try:
            value["display"] = json.loads(str(value.pop("display_json") or "{}"))
        except json.JSONDecodeError:
            value["display"] = {}
        for key in ("secure_surface", "microphone_enabled", "clipboard_enabled"):
            value[key] = bool(value.get(key))
        return value

    def create_session(self, values: dict[str, Any]) -> dict[str, Any]:
        now = utc_iso()
        normalized = {
            "session_id": str(values["session_id"]),
            "device_id": str(values["device_id"]),
            "device_name": str(values.get("device_name") or ""),
            "controller_device_id": str(values.get("controller_device_id") or ""),
            "remote_session_id": str(values.get("remote_session_id") or ""),
            "provider_id": str(values.get("provider_id") or ""),
            "mode": str(values.get("mode") or "current_desktop"),
            "state": str(values.get("state") or "idle"),
            "pane_card_id": str(values.get("pane_card_id") or ""),
            "pane_layout_id": str(values.get("pane_layout_id") or ""),
            "selected_display_id": str(values.get("selected_display_id") or ""),
            "display_json": json.dumps(values.get("display") or {}, ensure_ascii=False),
            "quality_mode": str(values.get("quality_mode") or "auto"),
            "transport_kind": str(values.get("transport_kind") or ""),
            "secure_surface": int(bool(values.get("secure_surface"))),
            "microphone_enabled": int(bool(values.get("microphone_enabled"))),
            "clipboard_enabled": int(values.get("clipboard_enabled", True) is not False),
            "created_at": now,
            "connected_at": str(values.get("connected_at") or ""),
            "disconnected_at": "",
            "last_error_code": str(values.get("last_error_code") or ""),
        }
        with self._lock, self._connect() as connection:
            columns = tuple(normalized)
            connection.execute(
                f"INSERT INTO remote_desktop_sessions({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                tuple(normalized[column] for column in columns),
            )
        return self.get_session(normalized["session_id"]) or normalized

    def update_session(self, session_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {
            "device_name", "controller_device_id", "remote_session_id", "provider_id",
            "mode", "state", "pane_card_id", "pane_layout_id",
            "selected_display_id", "quality_mode", "transport_kind",
            "secure_surface", "microphone_enabled", "clipboard_enabled",
            "connected_at", "disconnected_at", "last_error_code", "display",
        }
        values: dict[str, Any] = {}
        for key, value in changes.items():
            if key not in allowed:
                raise ValueError(f"unsupported remote desktop session field: {key}")
            target = "display_json" if key == "display" else key
            if key == "display":
                value = json.dumps(value or {}, ensure_ascii=False)
            elif key in {"secure_surface", "microphone_enabled", "clipboard_enabled"}:
                value = int(bool(value))
            values[target] = value
        if not values:
            current = self.get_session(session_id)
            if current is None:
                raise KeyError(session_id)
            return current
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE remote_desktop_sessions SET "
                + ",".join(f"{key}=?" for key in values)
                + " WHERE session_id=?",
                (*values.values(), str(session_id)),
            )
            if not cursor.rowcount:
                raise KeyError(session_id)
        return self.get_session(session_id) or {}

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM remote_desktop_sessions WHERE session_id=?",
                (str(session_id),),
            ).fetchone()
        return self._session(row)

    def list_sessions(self, *, device_id: str = "") -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            if device_id:
                rows = connection.execute(
                    "SELECT * FROM remote_desktop_sessions WHERE device_id=? ORDER BY created_at DESC",
                    (str(device_id),),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM remote_desktop_sessions ORDER BY created_at DESC"
                ).fetchall()
        return [value for row in rows if (value := self._session(row)) is not None]

    def current_session_for_device(self, device_id: str) -> dict[str, Any] | None:
        sessions = self.list_sessions(device_id=device_id)
        return next(
            (
                item for item in sessions
                if item["state"] not in {"disconnected", "failed"}
            ),
            None,
        )

    def preference(self, device_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM remote_desktop_device_preferences WHERE device_id=?",
                (str(device_id),),
            ).fetchone()
        if row is None:
            return {
                "device_id": str(device_id),
                "preferred_mode": "current_desktop",
                "quality_mode": "auto",
                "preferred_display_id": "",
                "clipboard_enabled": True,
                "updated_at": "",
            }
        value = dict(row)
        value["clipboard_enabled"] = bool(value["clipboard_enabled"])
        return value

    def update_preference(self, device_id: str, **changes: Any) -> dict[str, Any]:
        current = self.preference(device_id)
        for key in (
            "preferred_mode", "quality_mode", "preferred_display_id", "clipboard_enabled"
        ):
            if key in changes:
                current[key] = changes[key]
        now = utc_iso()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO remote_desktop_device_preferences(
                    device_id,preferred_mode,quality_mode,preferred_display_id,
                    clipboard_enabled,updated_at
                ) VALUES(?,?,?,?,?,?)
                ON CONFLICT(device_id) DO UPDATE SET
                    preferred_mode=excluded.preferred_mode,
                    quality_mode=excluded.quality_mode,
                    preferred_display_id=excluded.preferred_display_id,
                    clipboard_enabled=excluded.clipboard_enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    str(device_id), str(current["preferred_mode"]),
                    str(current["quality_mode"]), str(current["preferred_display_id"]),
                    int(bool(current["clipboard_enabled"])), now,
                ),
            )
        return self.preference(device_id)

    def replace_layout_grants(
        self,
        pane_layout_id: str,
        revision: int,
        grants: list[dict[str, Any]],
        *,
        projection_scope_id: str = "",
    ) -> None:
        layout_id = str(pane_layout_id)
        scope_id = str(projection_scope_id or "").strip()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT revision FROM remote_desktop_layout_revisions WHERE pane_layout_id=?",
                (layout_id,),
            ).fetchone()
            if row is not None and int(revision) <= int(row["revision"]):
                raise ValueError("stale_layout_revision")
            active = None
            if scope_id:
                active = connection.execute(
                    "SELECT pane_layout_id,revision FROM remote_desktop_active_layouts WHERE projection_scope_id=?",
                    (scope_id,),
                ).fetchone()
                if (
                    active is not None
                    and str(active["pane_layout_id"] or "") != layout_id
                    and int(revision) <= int(active["revision"])
                ):
                    raise ValueError("stale_layout_revision")
            connection.execute(
                "INSERT INTO remote_desktop_layout_revisions(pane_layout_id,revision,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(pane_layout_id) DO UPDATE SET revision=excluded.revision,updated_at=excluded.updated_at",
                (layout_id, int(revision), utc_iso()),
            )
            connection.execute(
                "DELETE FROM remote_desktop_layout_grants WHERE pane_layout_id=?",
                (layout_id,),
            )
            if scope_id:
                previous_layout_id = str(active["pane_layout_id"] or "") if active else ""
                if previous_layout_id and previous_layout_id != layout_id:
                    connection.execute(
                        "DELETE FROM remote_desktop_layout_grants WHERE pane_layout_id=?",
                        (previous_layout_id,),
                    )
                connection.execute(
                    "INSERT INTO remote_desktop_active_layouts(projection_scope_id,pane_layout_id,revision,updated_at) "
                    "VALUES(?,?,?,?) ON CONFLICT(projection_scope_id) DO UPDATE SET "
                    "pane_layout_id=excluded.pane_layout_id,revision=excluded.revision,updated_at=excluded.updated_at",
                    (scope_id, layout_id, int(revision), utc_iso()),
                )
            for grant in grants:
                connection.execute(
                    """
                    INSERT INTO remote_desktop_layout_grants(
                        pane_layout_id,session_id,chat_id,revision,origin,granted,updated_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        layout_id, str(grant["session_id"]), str(grant["chat_id"]),
                        int(revision), str(grant.get("origin") or "unknown"),
                        int(bool(grant.get("granted"))), utc_iso(),
                    ),
                )

    def authorized_sessions(self, chat_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT s.* FROM remote_desktop_layout_grants g
                JOIN remote_desktop_sessions s ON s.session_id=g.session_id
                WHERE g.chat_id=? AND g.granted=1
                  AND s.state IN ('connected','reconnecting')
                ORDER BY s.created_at DESC
                """,
                (str(chat_id),),
            ).fetchall()
        return [value for row in rows if (value := self._session(row)) is not None]

    def is_authorized(self, chat_id: str, session_id: str) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM remote_desktop_layout_grants WHERE chat_id=? AND session_id=? AND granted=1",
                (str(chat_id), str(session_id)),
            ).fetchone()
        return row is not None

    def revoke_session_grants(self, session_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM remote_desktop_layout_grants WHERE session_id=?",
                (str(session_id),),
            )

    def audit(
        self,
        event_type: str,
        *,
        session_id: str = "",
        device_id: str = "",
        chat_id: str = "",
        outcome: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        safe_detail = {
            str(key): value
            for key, value in dict(detail or {}).items()
            if str(key).lower() not in {"password", "credential", "offer", "answer", "sdp"}
        }
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO remote_desktop_audit(event_type,session_id,device_id,chat_id,outcome,detail_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    str(event_type), str(session_id), str(device_id), str(chat_id),
                    str(outcome), json.dumps(safe_detail, ensure_ascii=False), utc_iso(),
                ),
            )


__all__ = ["RemoteDesktopStore", "utc_iso"]
