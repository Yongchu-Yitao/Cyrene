import json

import pytest

from cyrene.plugins import native_tools
from cyrene.plugins.plugin_restore import plan_builtin_plugin_restore, apply_builtin_plugin_restore


@pytest.fixture
def plugin_root(tmp_path, monkeypatch):
    root = tmp_path / "plugins"
    root.mkdir()
    (root / "sample").mkdir()
    (root / "sample" / "__init__.py").write_text("custom")
    (root / "sample" / "extra.txt").write_text("keep in backup")
    (root / "other.py").write_text("other custom")
    (root / ".upstream-hashes.json").write_text(json.dumps({
        "version": 1, "files": {"sample/old.py": "old", "other.py": "unchanged"},
        "deleted": ["sample", "other.py"], "extra_metadata": "preserve",
    }))
    monkeypatch.setattr(native_tools, "_collect_canonical_files", lambda: {
        "sample/__init__.py": b"bundled", "sample/new.py": b"new",
        "other.py": b"other bundled",
    })
    return root


def test_explicit_restore_only_replaces_target_and_keeps_backup(plugin_root):
    root = plugin_root
    original_manifest = (root / ".upstream-hashes.json").read_bytes()
    plan = plan_builtin_plugin_restore(root, "sample")
    assert (root / "sample/__init__.py").read_text() == "custom"
    result = apply_builtin_plugin_restore(plan)
    assert (result.target / "__init__.py").read_text() == "bundled"
    assert not (result.target / "extra.txt").exists()
    assert (result.backup_directory / "original/extra.txt").read_text() == "keep in backup"
    assert (result.backup_directory / "upstream-manifest.json").read_bytes() == original_manifest
    assert (root / "other.py").read_text() == "other custom"
    manifest = json.loads((root / ".upstream-hashes.json").read_text())
    assert manifest["files"]["other.py"] == "unchanged"
    assert "sample/old.py" not in manifest["files"]
    assert manifest["deleted"] == ["other.py"]
    assert manifest["extra_metadata"] == "preserve"


def test_stale_plan_does_not_discard_new_edits(plugin_root):
    plan = plan_builtin_plugin_restore(plugin_root, "sample")
    (plugin_root / "sample/__init__.py").write_text("new user edit")
    with pytest.raises(ValueError, match="stale"):
        apply_builtin_plugin_restore(plan)
    assert (plugin_root / "sample/__init__.py").read_text() == "new user edit"


def test_manifest_failure_rolls_back_target(plugin_root, monkeypatch):
    plan = plan_builtin_plugin_restore(plugin_root, "sample")
    before = (plugin_root / ".upstream-hashes.json").read_bytes()
    def fail(*_args):
        raise OSError("disk full")
    monkeypatch.setattr(native_tools, "_atomic_write", fail)
    with pytest.raises(OSError, match="disk full"):
        apply_builtin_plugin_restore(plan)
    assert (plugin_root / "sample/__init__.py").read_text() == "custom"
    assert (plugin_root / "sample/extra.txt").exists()
    assert (plugin_root / ".upstream-hashes.json").read_bytes() == before


def test_restore_standalone_and_missing_target(plugin_root):
    (plugin_root / "other.py").unlink()
    result = apply_builtin_plugin_restore(plan_builtin_plugin_restore(plugin_root, "other.py"))
    assert result.target.read_text() == "other bundled"
    assert not (result.backup_directory / "original").exists()


@pytest.mark.parametrize("name", ["../sample", "..", "sample/file", "sample\\file", "unknown"])
def test_restore_rejects_invalid_target(plugin_root, name):
    with pytest.raises(ValueError):
        plan_builtin_plugin_restore(plugin_root, name)


def test_restore_rejects_symlinks_and_broken_manifest(plugin_root):
    (plugin_root / "sample/link").symlink_to(plugin_root / "other.py")
    with pytest.raises(ValueError, match="Unsupported"):
        plan_builtin_plugin_restore(plugin_root, "sample")
    (plugin_root / "sample/link").unlink()
    (plugin_root / ".upstream-hashes.json").write_text('{"version": 99}')
    with pytest.raises(ValueError, match="manifest"):
        plan_builtin_plugin_restore(plugin_root, "sample")
