"""Stable facade for legacy SQLite migration services."""

from __future__ import annotations

from pathlib import Path

from cyrene.runtime.persistence import legacy_migration as _service

DatabaseMigrationResult = _service.DatabaseMigrationResult
LEGACY_DATABASE_NAME = _service.LEGACY_DATABASE_NAME
MIGRATION_ID = _service.MIGRATION_ID
LEGACY_KNOWLEDGE_FTS_COMPACTION_ID = _service.LEGACY_KNOWLEDGE_FTS_COMPACTION_ID
_LEGACY_FTS_MIN_DATA_BLOCKS = _service._LEGACY_FTS_MIN_DATA_BLOCKS
_LEGACY_FTS_DATA_BLOCKS_PER_ROW = _service._LEGACY_FTS_DATA_BLOCKS_PER_ROW


def compact_legacy_knowledge_fts(database_path: str | Path) -> bool:
    # The two thresholds were historically patchable diagnostics knobs.
    _service._LEGACY_FTS_MIN_DATA_BLOCKS = _LEGACY_FTS_MIN_DATA_BLOCKS
    _service._LEGACY_FTS_DATA_BLOCKS_PER_ROW = _LEGACY_FTS_DATA_BLOCKS_PER_ROW
    return _service.compact_legacy_knowledge_fts(database_path)


def migrate_legacy_database(
    target_path: str | Path,
    *,
    source_path: str | Path | None = None,
) -> DatabaseMigrationResult:
    return _service.migrate_legacy_database(target_path, source_path=source_path)


__all__ = [
    "DatabaseMigrationResult",
    "LEGACY_DATABASE_NAME",
    "LEGACY_KNOWLEDGE_FTS_COMPACTION_ID",
    "MIGRATION_ID",
    "compact_legacy_knowledge_fts",
    "migrate_legacy_database",
]
