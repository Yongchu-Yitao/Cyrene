"""Tests for the storage usage scan (settings → Data panel)."""

from pathlib import Path

import pytest

from cyrene.runtime import storage


@pytest.fixture
def isolated_categories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        storage,
        "STORAGE_CATEGORIES",
        [
            ("alpha", (tmp_path / "alpha",), None),
            ("beta", (tmp_path / "beta",), None),
        ],
    )
    return tmp_path


def _write(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(b"x" * size)


def _by_key(result: dict) -> dict[str, dict]:
    return {item["key"]: item for item in result["categories"]}


def test_scan_sums_files_and_nested_directories(isolated_categories: Path) -> None:
    _write(isolated_categories / "alpha" / "a.bin", 100)
    _write(isolated_categories / "alpha" / "sub" / "b.bin", 250)
    _write(isolated_categories / "beta" / "c.bin", 50)

    result = storage.scan_storage(plugin_storage={})

    assert result["total"] == 400
    by_key = _by_key(result)
    assert by_key["alpha"]["bytes"] == 350
    assert by_key["alpha"]["files"] == 2
    assert by_key["beta"]["bytes"] == 50
    assert by_key["beta"]["files"] == 1
    assert result["truncated"] is False


def test_scan_ignores_missing_directories(isolated_categories: Path) -> None:
    result = storage.scan_storage(plugin_storage={})

    assert result["total"] == 0
    assert all(item["bytes"] == 0 and item["files"] == 0 for item in result["categories"])


def test_scan_does_not_follow_symlinks(isolated_categories: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    _write(outside / "big.bin", 4096)
    _write(isolated_categories / "alpha" / "real.bin", 64)
    (isolated_categories / "alpha" / "link.bin").symlink_to(outside / "big.bin")
    (isolated_categories / "alpha" / "linked_dir").symlink_to(outside)

    result = storage.scan_storage(plugin_storage={})
    by_key = _by_key(result)

    assert by_key["alpha"]["bytes"] == 64
    assert by_key["alpha"]["files"] == 1


def test_scan_splits_store_families_and_plugin_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = tmp_path / "store"
    _write(store / "cyrene.runtime.database", 100)
    _write(store / "kb_default.db", 30)
    _write(store / "kb_default.db-wal", 10)
    memory = tmp_path / "plugin-data" / "short_term.json"
    conversations = tmp_path / "workspace" / ".cyrene" / "conversations"
    _write(memory, 8)
    _write(conversations / "chat.md", 12)
    monkeypatch.setattr(
        storage,
        "STORAGE_CATEGORIES",
        [
            ("database", (store,), storage._name_excludes("kb_*.db*")),
            ("knowledge", (store,), storage._name_matches("kb_*.db*")),
            ("memory", (), None),
            ("conversations", (), None),
        ],
    )

    result = storage.scan_storage(
        plugin_storage={
            "memory": (memory,),
            "conversations": (conversations,),
        }
    )
    by_key = _by_key(result)

    assert by_key["database"]["bytes"] == 100
    assert by_key["database"]["files"] == 1
    assert by_key["knowledge"]["bytes"] == 40
    assert by_key["knowledge"]["files"] == 2
    assert by_key["memory"]["bytes"] == 8
    assert by_key["memory"]["files"] == 1
    assert by_key["conversations"]["bytes"] == 12
    assert by_key["conversations"]["files"] == 1
    assert result["total"] == 160


def test_scan_truncates_at_entry_budget(isolated_categories: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "_MAX_SCAN_ENTRIES", 3)
    for index in range(10):
        _write(isolated_categories / "alpha" / f"f{index}.bin", 10)

    result = storage.scan_storage(plugin_storage={})

    assert result["truncated"] is True
    total = sum(item["bytes"] for item in result["categories"])
    assert total < 100
