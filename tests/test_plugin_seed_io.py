"""Regression coverage for synchronization I/O reuse and edit preservation."""

import os
from collections import Counter
from pathlib import Path

import pytest

from cyrene.plugins import native_tools


def test_unchanged_sync_reads_pack_once_and_keeps_manifest(tmp_path, monkeypatch):
    sources = {"pack/__init__.py": b"original", "pack/tool.py": b"tool"}
    monkeypatch.setattr(native_tools, "_collect_canonical_files", lambda: sources)
    native_tools.seed_builtin_plugin_directory(tmp_path)
    manifest = tmp_path / ".upstream-hashes.json"
    before = manifest.stat()
    reads = Counter()
    original = Path.read_bytes

    def read(path):
        reads[path] += 1
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    result = native_tools.seed_builtin_plugin_directory(tmp_path)
    assert not result.updated
    assert all(reads[tmp_path / relative] == 1 for relative in sources)
    after = manifest.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


@pytest.mark.parametrize("change", ["edit", "replace", "symlink"])
def test_sync_detects_changes_after_first_read(tmp_path, monkeypatch, change):
    sources = {"pack/__init__.py": b"original"}
    monkeypatch.setattr(native_tools, "_collect_canonical_files", lambda: sources)
    native_tools.seed_builtin_plugin_directory(tmp_path)
    target = tmp_path / "pack/__init__.py"
    original_info = target.stat()
    sources["pack/__init__.py"] = b"upstream"
    classify = native_tools._classify_canonical_packs

    def change_then_classify(*args):
        if change == "edit":
            target.write_bytes(b"personal")
            os.utime(target, ns=(original_info.st_atime_ns, original_info.st_mtime_ns))
        else:
            replacement = tmp_path / "replacement"
            replacement.write_bytes(b"personal")
            if change == "replace":
                replacement.replace(target)
            else:
                target.unlink()
                target.symlink_to(replacement)
        return classify(*args)

    monkeypatch.setattr(native_tools, "_classify_canonical_packs", change_then_classify)
    result = native_tools.seed_builtin_plugin_directory(tmp_path)
    assert not result.updated
    assert target.read_bytes() == b"personal"
    assert target.is_symlink() == (change == "symlink")


def test_unstable_read_is_not_cached(tmp_path, monkeypatch):
    target = tmp_path / "tool.py"
    target.write_bytes(b"original")
    cache = native_tools._SeedFileHashes()
    original = Path.read_bytes

    def read_then_edit(path):
        content = original(path)
        path.write_bytes(b"personal-change")
        return content

    monkeypatch.setattr(Path, "read_bytes", read_then_edit)
    with pytest.raises(OSError, match="changed while reading"):
        cache.digest(target)
    monkeypatch.setattr(Path, "read_bytes", original)
    assert cache.digest(target) == native_tools._content_hash(b"personal-change")


def test_manifest_updates_changes_and_replaces_identical_symlink(tmp_path):
    manifest = tmp_path / "manifest.json"
    native_tools._write_upstream_hashes(manifest, {"tool.py": "old"})
    before = manifest.read_bytes()
    native_tools._write_upstream_hashes(manifest, {"tool.py": "new"}, deleted={"pack"})
    expected = manifest.read_bytes()
    assert expected != before
    outside = tmp_path / "outside.json"
    manifest.replace(outside)
    manifest.symlink_to(outside)
    native_tools._write_upstream_hashes(manifest, {"tool.py": "new"}, deleted={"pack"})
    assert not manifest.is_symlink()
    assert manifest.read_bytes() == outside.read_bytes() == expected
