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

    def __init__(self, message: str, *, code: str, pin_name: str = "") -> None:
        super().__init__(message)
        self.code = str(code or "map_mutation_failed")
        self.pin_name = str(pin_name or "")


def map_database(data_directory: str | Path) -> Path:
    root = Path(data_directory).expanduser().resolve()
    return root / "plugin_data" / "cyrene_map" / "maps.sqlite3"


class MapService:
    """Store one independent pin/route document per Agent session."""

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database).expanduser().resolve()
        self._lock = threading.RLock()
        self._initialized = False

    @property
    def initialized(self) -> bool:
        return self._initialized

    def initialize(self) -> None:
        """Create the Plugin-owned store when the owning pack starts."""

        with self._lock:
            if self._initialized:
                return
            self.database.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()
            self._initialized = True

    def shutdown(self) -> None:
        """Mark this attachment inactive after its pack is disabled."""

        with self._lock:
            self._initialized = False

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError("MapService has not been initialized")

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
        self._require_initialized()
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
        self._require_initialized()
        normalized_name = str(name or "").strip()
        if not normalized_name:
            raise MapServiceError(
                "name cannot be empty",
                code="map_pin_name_required",
            )
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
        self._require_initialized()
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
                raise MapServiceError(
                    f"Origin pin was not found: {origin}",
                    code="map_origin_not_found",
                    pin_name=origin,
                )
            if destination not in pin_names:
                raise MapServiceError(
                    f"Destination pin was not found: {destination}",
                    code="map_destination_not_found",
                    pin_name=destination,
                )
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

    def storage_paths(self) -> dict[str, tuple[Path, ...]]:
        """Expose the map pack's durable directory to storage settings."""

        return {"maps": (self.database.parent,)}

    def backup_sources(self) -> dict[str, tuple[tuple[Path, str], ...]]:
        """Contribute the complete durable map store to portable backups."""

        return {
            "directories": (
                (self.database.parent, "data/plugin_data/cyrene_map"),
            ),
        }

    @staticmethod
    def editable_env_keys() -> dict[str, dict[str, object]]:
        """Publish the map provider credential only while this pack is active."""

        from cyrene.localization import localized

        return {
            "AMAP_API_KEY": {
                "label": localized("Amap API key", "高德地图 API 密钥"),
                "masked": True,
            },
        }


__all__ = ["MapService", "MapServiceError", "map_database"]
