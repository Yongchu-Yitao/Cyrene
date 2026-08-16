"""Tests for the storage usage scan (settings → Data panel)."""

import os
from pathlib import Path

import pytest

from cyrene.runtime import storage


@pytest.fixture
def isolated_categories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        storage,
        "STORAGE_CATEGORIES",
        [("alpha", (tmp_path / "alpha",)), ("beta", (tmp_path / "beta",))],
    )
    return tmp_path


def _write(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(b"x" * size)


def test_scan_sums_files_and_nested_directories(isolated_categories: Path) -> None:
    _write(isolated_categories / "alpha" / "a.bin", 100)
    _write(isolated_categories / "alpha" / "sub" / "b.bin", 250)
    _write(isolated_categories / "beta" / "c.bin", 50)

    result = storage.scan_storage()

    assert result["total"] == 400
    by_key = {item["key"]: item for item in result["categories"]}
    assert by_key["alpha"]["bytes"] == 350
    assert by_key["alpha"]["files"] == 2
    assert by_key["beta"]["bytes"] == 50
    assert by_key["beta"]["files"] == 1
    assert result["truncated"] is False


def test_scan_ignores_missing_directories(isolated_categories: Path) -> None:
    result = storage.scan_storage()

    assert result["total"] == 0
    assert all(item["bytes"] == 0 and item["files"] == 0 for item in result["categories"])


def test_scan_does_not_follow_symlinks(isolated_categories: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    _write(outside / "big.bin", 4096)
    _write(isolated_categories / "alpha" / "real.bin", 64)
    (isolated_categories / "alpha" / "link.bin").symlink_to(outside / "big.bin")
    (isolated_categories / "alpha" / "linked_dir").symlink_to(outside)

    result = storage.scan_storage()
    by_key = {item["key"]: item for item in result["categories"]}

    assert by_key["alpha"]["bytes"] == 64
    assert by_key["alpha"]["files"] == 1


def test_scan_splits_families_by_name_filter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmp_path / "store"
    _write(store / "cyrene.runtime.database", 100)
    _write(store / "kb_default.db", 30)
    _write(store / "kb_default.db-wal", 10)
    _write(store / "wb_memory_default.json", 5)
    _write(store / "project_memory_p.json", 3)
    monkeypatch.setattr(
        storage,
        "STORAGE_CATEGORIES",
        [
            ("database", (store,), storage._name_excludes("kb_*.db*", "wb_memory_*.json", "project_memory_*.json")),
            ("knowledge", (store,), storage._name_matches("kb_*.db*")),
            ("memory", (store,), storage._name_matches("wb_memory_*.json", "project_memory_*.json")),
        ],
    )

    result = storage.scan_storage()
    by_key = {item["key"]: item for item in result["categories"]}

    assert by_key["database"]["bytes"] == 100
    assert by_key["database"]["files"] == 1
    assert by_key["knowledge"]["bytes"] == 40
    assert by_key["knowledge"]["files"] == 2
    assert by_key["memory"]["bytes"] == 8
    assert by_key["memory"]["files"] == 2
    assert result["total"] == 148


def test_scan_truncates_at_entry_budget(isolated_categories: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "_MAX_SCAN_ENTRIES", 3)
    for index in range(10):
        _write(isolated_categories / "alpha" / f"f{index}.bin", 10)

    result = storage.scan_storage()

    assert result["truncated"] is True
    total = sum(item["bytes"] for item in result["categories"])
    assert total < 100
