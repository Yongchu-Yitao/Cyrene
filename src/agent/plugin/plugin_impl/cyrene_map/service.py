"""Durable map state owned by the editable ``cyrene_map`` Plugin pack."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4


class MapServiceError(ValueError):
    """A map mutation could not be applied to the current session."""


def map_database(data_directory: str | Path) -> Path:
    root = Path(data_directory).expanduser().resolve()
    return root / "plugin_data" / "cyrene_map" / "maps.sqlite3"


class MapService:
    """Store one independent pin/route document per Agent session."""

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database).expanduser().resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    @staticmethod
    def _session_key(session_id: str) -> str:
        return str(session_id or "").strip() or "__default__"

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS map_documents (
                    session_id TEXT PRIMARY KEY,
                    pins_json TEXT NOT NULL,
                    routes_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    @staticmethod
    def _array(raw: Any) -> list[dict[str, Any]]:
        try:
            value = json.loads(str(raw or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]

    def _read_locked(
        self,
        connection: sqlite3.Connection,
        session_id: str,
    ) -> dict[str, list[dict[str, Any]]]:
        row = connection.execute(
            "SELECT pins_json, routes_json FROM map_documents WHERE session_id = ?",
            (self._session_key(session_id),),
        ).fetchone()
        if row is None:
            return {"pins": [], "routes": []}
        return {
            "pins": self._array(row["pins_json"]),
            "routes": self._array(row["routes_json"]),
        }

    def _write_locked(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        document: dict[str, list[dict[str, Any]]],
    ) -> None:
        connection.execute(
            """
            INSERT INTO map_documents(session_id, pins_json, routes_json, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(session_id) DO UPDATE SET
                pins_json = excluded.pins_json,
                routes_json = excluded.routes_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                self._session_key(session_id),
                json.dumps(document["pins"], ensure_ascii=False),
                json.dumps(document["routes"], ensure_ascii=False),
            ),
        )

    def snapshot(self, session_id: str = "") -> dict[str, list[dict[str, Any]]]:
        with self._lock, self._connection() as connection:
            return deepcopy(self._read_locked(connection, session_id))

    def add_pin(
        self,
        session_id: str,
        *,
        lat: float,
        lng: float,
        name: str,
        note: str = "",
    ) -> dict[str, Any]:
        normalized_name = str(name or "").strip()
        if not normalized_name:
            raise MapServiceError("name cannot be empty")
        with self._lock, self._connection() as connection:
            document = self._read_locked(connection, session_id)
            pin = {
                "id": f"pin_{uuid4().hex[:8]}",
                "lat": float(lat),
                "lng": float(lng),
                "name": normalized_name,
                "note_md": str(note or ""),
                "order": len(document["pins"]),
            }
            document["pins"].append(pin)
            self._write_locked(connection, session_id, document)
            return {"pin": deepcopy(pin), **deepcopy(document)}

    def add_route(
        self,
        session_id: str,
        *,
        from_name: str,
        to_name: str,
        transport: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        origin = str(from_name or "").strip()
        destination = str(to_name or "").strip()
        with self._lock, self._connection() as connection:
            document = self._read_locked(connection, session_id)
            pin_names = {
                str(pin.get("name") or "")
                for pin in document["pins"]
                if str(pin.get("name") or "")
            }
            if origin not in pin_names:
                raise MapServiceError(f"未找到起点标记「{origin}」")
            if destination not in pin_names:
                raise MapServiceError(f"未找到终点标记「{destination}」")
            route = {
                "id": f"route_{uuid4().hex[:8]}",
                "from_name": origin,
                "to_name": destination,
                "transport": str(transport or ""),
                "note_md": str(note or ""),
            }
            document["routes"].append(route)
            self._write_locked(connection, session_id, document)
            return {"route": deepcopy(route), **deepcopy(document)}


__all__ = ["MapService", "MapServiceError", "map_database"]
