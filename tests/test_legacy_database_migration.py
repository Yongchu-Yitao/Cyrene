from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from cyrene.runtime.database_migration import (
    DatabaseMigrationResult,
    MIGRATION_ID,
    migrate_legacy_database,
)


def _create_database(path: Path, value: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS user_records "
            "(id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
        )
        if value is not None:
            connection.execute(
                "INSERT INTO user_records(value) VALUES (?)",
                (value,),
            )
        connection.commit()


def _values(path: Path) -> list[str]:
    with sqlite3.connect(path) as connection:
        return [
            str(row[0])
            for row in connection.execute(
                "SELECT value FROM user_records ORDER BY id"
            )
        ]


def test_migrates_complete_legacy_snapshot_and_retains_source(tmp_path):
    source = tmp_path / "cyrene.db"
    target = tmp_path / "cyrene.runtime.database"
    _create_database(source, "legacy")

    # Leave a committed transaction in the WAL to prove the SQLite backup API
    # is used instead of a plain file copy.
    writer = sqlite3.connect(source)
    writer.execute("PRAGMA journal_mode = WAL")
    writer.execute("PRAGMA wal_autocheckpoint = 0")
    writer.execute("INSERT INTO user_records(value) VALUES ('from-wal')")
    writer.commit()
    assert source.with_name(source.name + "-wal").exists()

    try:
        result = migrate_legacy_database(target)
    finally:
        writer.close()

    assert result.status == "migrated"
    assert result.migrated is True
    assert _values(target) == ["legacy", "from-wal"]
    assert source.exists()
    assert result.rollback_path == source
    with sqlite3.connect(target) as connection:
        marker = connection.execute(
            "SELECT migration_id FROM cyrene_runtime_migrations"
        ).fetchone()
        assert marker == (MIGRATION_ID,)
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)


def test_replaces_an_initialized_but_row_empty_target(tmp_path):
    source = tmp_path / "cyrene.db"
    target = tmp_path / "cyrene.runtime.database"
    _create_database(source, "legacy")
    _create_database(target)

    result = migrate_legacy_database(target)

    assert result.status == "migrated"
    assert _values(target) == ["legacy"]


def test_never_overwrites_a_populated_target(tmp_path):
    source = tmp_path / "cyrene.db"
    target = tmp_path / "cyrene.runtime.database"
    _create_database(source, "legacy")
    _create_database(target, "new")

    result = migrate_legacy_database(target)

    assert result.status == "target_not_empty"
    assert _values(source) == ["legacy"]
    assert _values(target) == ["new"]
    assert result.rollback_path is None


def test_ignores_an_empty_legacy_file_when_target_is_populated(tmp_path):
    source = tmp_path / "cyrene.db"
    target = tmp_path / "cyrene.runtime.database"
    source.touch()
    _create_database(target, "new")

    result = migrate_legacy_database(target)

    assert result.status == "not_needed"
    assert result.detail == "source_empty"
    assert source.stat().st_size == 0
    assert _values(target) == ["new"]


def test_migration_is_idempotent(tmp_path):
    source = tmp_path / "cyrene.db"
    target = tmp_path / "cyrene.runtime.database"
    _create_database(source, "legacy")

    first = migrate_legacy_database(target)
    second = migrate_legacy_database(target)

    assert first.status == "migrated"
    assert second.status == "already_migrated"
    assert _values(target) == ["legacy"]


def test_corrupt_source_does_not_replace_target(tmp_path):
    source = tmp_path / "cyrene.db"
    target = tmp_path / "cyrene.runtime.database"
    source.write_bytes(b"not a sqlite database")
    original = b""
    target.write_bytes(original)

    result = migrate_legacy_database(target)

    assert result.status == "source_invalid"
    assert target.read_bytes() == original
    assert source.read_bytes() == b"not a sqlite database"


@pytest.mark.asyncio
async def test_runtime_runs_migration_before_database_initialization(
    monkeypatch,
    tmp_path,
):
    from cyrene.runtime import bootstrap

    context = bootstrap.create_runtime_context(host_mode="test")
    context.paths = type(context.paths)(
        install_resources=tmp_path,
        user_data=tmp_path / "user-data",
        runtime_base=tmp_path,
        workspace=tmp_path / "workspace",
        store=tmp_path / "store",
        data=tmp_path / "data",
        cache=tmp_path / "cache",
        temp=tmp_path / "temp",
    )
    context.database_path = context.paths.store / "cyrene.runtime.database"
    context.inbox_path = context.paths.data / "inbox"
    _create_database(context.paths.store / "cyrene.db", "legacy")

    events: list[str] = []
    real_migrate = bootstrap.migrate_legacy_database
    real_init = bootstrap.init_db

    def migrate(path):
        events.append("migrate")
        return real_migrate(path)

    async def init(path):
        events.append("init")
        await real_init(path)

    monkeypatch.setattr(bootstrap, "migrate_legacy_database", migrate)
    monkeypatch.setattr(bootstrap, "init_db", init)
    monkeypatch.setattr(bootstrap, "ensure_soul", lambda: None)
    monkeypatch.setattr(bootstrap, "ensure_inbox", lambda _name: None)
    monkeypatch.setattr(bootstrap, "init_short_term", lambda _path: None)

    await bootstrap.initialize_runtime(context=context)

    assert events == ["migrate", "init"]
    assert _values(context.database_path) == ["legacy"]


@pytest.mark.asyncio
async def test_runtime_stops_before_opening_an_ambiguous_target(
    monkeypatch,
    tmp_path,
):
    from cyrene.runtime import bootstrap

    context = bootstrap.create_runtime_context(host_mode="test")
    context.paths = type(context.paths)(
        install_resources=tmp_path,
        user_data=tmp_path / "user-data",
        runtime_base=tmp_path,
        workspace=tmp_path / "workspace",
        store=tmp_path / "store",
        data=tmp_path / "data",
        cache=tmp_path / "cache",
        temp=tmp_path / "temp",
    )
    context.database_path = context.paths.store / "cyrene.runtime.database"
    context.inbox_path = context.paths.data / "inbox"
    source = context.paths.store / "cyrene.db"
    init_db = AsyncMock()

    monkeypatch.setattr(
        bootstrap,
        "migrate_legacy_database",
        lambda target: DatabaseMigrationResult(
            "target_not_empty",
            source,
            Path(target),
            detail="target contains rows",
        ),
    )
    monkeypatch.setattr(bootstrap, "init_db", init_db)

    with pytest.raises(RuntimeError, match="could not migrate it safely"):
        await bootstrap.initialize_runtime(context=context)

    init_db.assert_not_awaited()


def test_onboarding_recognizes_data_in_unmigrated_legacy_database(
    monkeypatch,
    tmp_path,
):
    from cyrene.runtime import onboarding
    from cyrene.workbench.store import write_document

    store = tmp_path / "store"
    data = tmp_path / "data"
    legacy = store / "cyrene.db"
    write_document(
        legacy,
        "chats",
        {"chats": [{"id": "legacy-chat", "messages": [{"role": "user"}]}]},
        lambda: {"chats": []},
    )

    monkeypatch.setattr(onboarding, "STORE_DIR", store)
    monkeypatch.setattr(onboarding, "DB_PATH", store / "cyrene.runtime.database")
    monkeypatch.setattr(onboarding, "DATA_DIR", data)
    monkeypatch.setattr(onboarding, "_has_runtime_activity", lambda: False)

    assert onboarding._has_existing_data() is True
