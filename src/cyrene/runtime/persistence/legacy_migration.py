"""One-time migration from the legacy Cyrene SQLite filename.

The package-layout refactor changed the primary database filename from
``cyrene.db`` to ``cyrene.runtime.database``.  Existing installations must copy
the complete SQLite snapshot, including committed WAL data, before the new
runtime opens its database.
"""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

LEGACY_DATABASE_NAME = "cyrene.db"
MIGRATION_ID = "legacy-database-filename-v1"
LEGACY_KNOWLEDGE_FTS_COMPACTION_ID = "legacy-knowledge-fts-compaction-v1"
_MARKER_TABLE = "cyrene_runtime_migrations"
_LEGACY_FTS_MIN_DATA_BLOCKS = 4096
_LEGACY_FTS_DATA_BLOCKS_PER_ROW = 32


@dataclass(frozen=True, slots=True)
class DatabaseMigrationResult:
    """Outcome of checking or migrating the legacy database."""

    status: str
    source_path: Path
    target_path: Path
    rollback_path: Path | None = None
    detail: str = ""

    @property
    def migrated(self) -> bool:
        return self.status == "migrated"


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _connect_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)


def _quick_check(connection: sqlite3.Connection) -> str:
    row = connection.execute("PRAGMA quick_check").fetchone()
    return str(row[0]) if row else "missing quick_check result"


def _has_migration_marker(path: Path, migration_id: str = MIGRATION_ID) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        with _connect_read_only(path) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (_MARKER_TABLE,),
            ).fetchone()
            if table is None:
                return False
            return (
                connection.execute(
                    f"SELECT 1 FROM {_quote_identifier(_MARKER_TABLE)} "
                    "WHERE migration_id = ? LIMIT 1",
                    (migration_id,),
                ).fetchone()
                is not None
            )
    except (OSError, sqlite3.Error):
        return False


def _database_has_rows(path: Path) -> bool:
    """Return whether a target database contains data that must not be replaced."""
    if not path.is_file() or path.stat().st_size == 0:
        return False
    with _connect_read_only(path) as connection:
        if _quick_check(connection) != "ok":
            raise sqlite3.DatabaseError(f"target database failed quick_check: {path}")
        tables = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND name != ?",
            (_MARKER_TABLE,),
        ).fetchall()
        for (name,) in tables:
            try:
                row = connection.execute(
                    f"SELECT 1 FROM {_quote_identifier(str(name))} LIMIT 1"
                ).fetchone()
            except sqlite3.OperationalError:
                # Some extension-backed virtual tables cannot be queried when
                # the extension is unavailable. Their shadow tables are still
                # inspected, so skipping the virtual table itself is safe.
                continue
            if row is not None:
                return True
    return False


def _record_marker(
    connection: sqlite3.Connection,
    source_path: Path,
    migration_id: str = MIGRATION_ID,
) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_quote_identifier(_MARKER_TABLE)} (
            migration_id TEXT PRIMARY KEY,
            source_path TEXT NOT NULL,
            migrated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        f"""
        INSERT OR REPLACE INTO {_quote_identifier(_MARKER_TABLE)}
            (migration_id, source_path, migrated_at)
        VALUES (?, ?, ?)
        """,
        (
            migration_id,
            str(source_path),
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def _write_marker(path: Path, source_path: Path) -> None:
    with sqlite3.connect(path, timeout=30) as connection:
        # Keep the marker in the main file. The normal runtime switches the
        # migrated database back to WAL mode in init_db().
        connection.execute("PRAGMA journal_mode = DELETE")
        _record_marker(connection, source_path)
        connection.commit()
        if _quick_check(connection) != "ok":
            raise sqlite3.DatabaseError("migrated database failed quick_check")


def compact_legacy_knowledge_fts(database_path: str | Path) -> bool:
    """Compact a pathologically large legacy knowledge FTS index once.

    This runs before the runtime opens the database, so rebuilding and
    vacuuming cannot delay live message or status writes. Failures leave the
    database usable and unmarked so a later startup can retry.
    """
    path = Path(database_path).expanduser().resolve()
    if not path.is_file() or path.stat().st_size == 0:
        return False
    if _has_migration_marker(path, LEGACY_KNOWLEDGE_FTS_COMPACTION_ID):
        return False

    size_before = path.stat().st_size
    rebuilt = False
    try:
        with sqlite3.connect(path, timeout=30) as connection:
            connection.execute("PRAGMA busy_timeout = 30000")
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name IN (?, ?)",
                    ("kb_chunks_fts", "kb_chunks_fts_data"),
                )
            }
            if tables != {"kb_chunks_fts", "kb_chunks_fts_data"}:
                return False

            live_rows = int(
                connection.execute("SELECT COUNT(*) FROM kb_chunks_fts").fetchone()[0]
            )
            data_blocks = int(
                connection.execute(
                    "SELECT COUNT(*) FROM kb_chunks_fts_data"
                ).fetchone()[0]
            )
            if data_blocks > max(
                _LEGACY_FTS_MIN_DATA_BLOCKS,
                live_rows * _LEGACY_FTS_DATA_BLOCKS_PER_ROW,
            ):
                logger.info(
                    "Rebuilding bloated legacy knowledge FTS index "
                    "[rows=%s blocks=%s]",
                    live_rows,
                    data_blocks,
                )
                connection.execute(
                    "INSERT INTO kb_chunks_fts(kb_chunks_fts) VALUES('rebuild')"
                )
                connection.commit()
                rebuilt = True
                connection.execute("VACUUM")

            integrity = _quick_check(connection)
            if integrity != "ok":
                raise sqlite3.DatabaseError(
                    f"database failed quick_check after FTS compaction: {integrity}"
                )
            _record_marker(
                connection,
                path,
                LEGACY_KNOWLEDGE_FTS_COMPACTION_ID,
            )
            connection.commit()

        if rebuilt:
            logger.info(
                "Legacy knowledge FTS compaction complete "
                "[before_bytes=%s after_bytes=%s]",
                size_before,
                path.stat().st_size,
            )
        return rebuilt
    except (OSError, sqlite3.Error):
        logger.warning(
            "Legacy knowledge FTS compaction failed for %s; startup will continue",
            path,
            exc_info=True,
        )
        return False


def _unlink_retry(path: Path, *, attempts: int = 5, delay: float = 0.2) -> None:
    """Unlink a file, tolerating transient Windows sharing violations.

    Antivirus scanners and lingering processes can briefly hold a file open.
    A leftover staging file is harmless, so clean-up failures must never
    propagate and crash startup.
    """
    for attempt in range(attempts):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt < attempts - 1:
                time.sleep(delay)
    logger.warning(
        "Unable to remove %s (file is locked); leaving it for cleanup on next start",
        path,
    )


def _replace_retry(staging: Path, target: Path, *, attempts: int = 5, delay: float = 0.2) -> None:
    for attempt in range(attempts):
        try:
            staging.replace(target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


def _cleanup_stale_staging(target: Path) -> None:
    """Remove staging files left by a previously interrupted migration."""
    for stale in target.parent.glob(f".{target.name}.migration-*.tmp"):
        _unlink_retry(stale)


def _remove_sqlite_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        _unlink_retry(path.with_name(path.name + suffix))


def migrate_legacy_database(
    target_path: str | Path,
    *,
    source_path: str | Path | None = None,
) -> DatabaseMigrationResult:
    """Migrate a legacy database into ``target_path`` when it is safe.

    The source is copied with SQLite's backup API so committed data still in a
    WAL file is included.  The target is replaced atomically only when absent or
    row-empty.  A populated target is never overwritten automatically.
    """
    target = Path(target_path).expanduser().resolve()
    source = (
        Path(source_path).expanduser().resolve()
        if source_path is not None
        else target.with_name(LEGACY_DATABASE_NAME)
    )

    if source == target:
        return DatabaseMigrationResult("not_needed", source, target, detail="same_path")

    if _has_migration_marker(target):
        return DatabaseMigrationResult(
            "already_migrated",
            source,
            target,
            rollback_path=source if source.is_file() else None,
            detail="migration marker exists",
        )

    if not source.is_file():
        return DatabaseMigrationResult("not_needed", source, target, detail="source_missing")

    # SQLite can leave an empty database file behind when a legacy path is
    # opened but never initialized.  It contains nothing to migrate and must
    # not turn an already-populated target into an ambiguous migration error.
    if source.stat().st_size == 0:
        return DatabaseMigrationResult("not_needed", source, target, detail="source_empty")

    try:
        if _database_has_rows(target):
            logger.warning(
                "Legacy database migration skipped because target contains data: %s",
                target,
            )
            return DatabaseMigrationResult(
                "target_not_empty",
                source,
                target,
                detail="target contains rows",
            )
    except (OSError, sqlite3.Error) as exc:
        logger.exception("Unable to inspect database migration target %s", target)
        return DatabaseMigrationResult(
            "target_invalid",
            source,
            target,
            detail=str(exc),
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    _cleanup_stale_staging(target)
    staging = target.with_name(f".{target.name}.migration-{uuid.uuid4().hex}.tmp")
    try:
        with _connect_read_only(source) as old_connection:
            integrity = _quick_check(old_connection)
            if integrity != "ok":
                raise sqlite3.DatabaseError(
                    f"legacy database failed quick_check: {integrity}"
                )
            with sqlite3.connect(staging, timeout=30) as new_connection:
                old_connection.backup(new_connection)
                new_connection.commit()

        _write_marker(staging, source)
        _remove_sqlite_sidecars(target)
        _replace_retry(staging, target)
        logger.info("Migrated legacy database %s to %s", source, target)
        return DatabaseMigrationResult(
            "migrated",
            source,
            target,
            rollback_path=source,
            detail="complete SQLite snapshot migrated; legacy source retained",
        )
    except (OSError, sqlite3.Error) as exc:
        logger.exception("Legacy database migration failed: %s -> %s", source, target)
        return DatabaseMigrationResult(
            "source_invalid",
            source,
            target,
            detail=str(exc),
        )
    finally:
        _unlink_retry(staging)
        _remove_sqlite_sidecars(staging)


__all__ = [
    "DatabaseMigrationResult",
    "LEGACY_DATABASE_NAME",
    "LEGACY_KNOWLEDGE_FTS_COMPACTION_ID",
    "MIGRATION_ID",
    "compact_legacy_knowledge_fts",
    "migrate_legacy_database",
]
