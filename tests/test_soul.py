"""ensure_soul must never replace a legacy root-level SOUL.md with a default."""

from pathlib import Path

import pytest

from cyrene.runtime.memory import soul as soul_module
from cyrene.runtime.memory.soul import ensure_soul

_LEGACY_SOUL = "# Old Soul\n\n## SELF:IDENTITY\n- I am the user's persona\n"


@pytest.fixture
def soul_env(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    cyrene = workspace / ".cyrene"
    cyrene.mkdir(parents=True)
    monkeypatch.setattr(soul_module, "WORKSPACE_DIR", workspace)
    return {"workspace": workspace, "cyrene": cyrene}


def _write_legacy(workspace: Path) -> Path:
    legacy = workspace / "SOUL.md"
    legacy.write_text(_LEGACY_SOUL, encoding="utf-8")
    return legacy


def test_ensure_soul_migrates_legacy_root_soul(soul_env):
    workspace, cyrene = soul_env["workspace"], soul_env["cyrene"]
    legacy = _write_legacy(workspace)
    ensure_soul()
    target = cyrene / "SOUL.md"
    assert target.read_text(encoding="utf-8") == _LEGACY_SOUL
    assert not legacy.exists()


def test_ensure_soul_writes_default_when_no_legacy(soul_env):
    cyrene = soul_env["cyrene"]
    ensure_soul()
    target = cyrene / "SOUL.md"
    assert target.exists()
    assert "## SELF:IDENTITY" in target.read_text(encoding="utf-8")


def test_ensure_soul_skips_unrelated_root_file(soul_env):
    workspace, cyrene = soul_env["workspace"], soul_env["cyrene"]
    legacy = workspace / "SOUL.md"
    legacy.write_text("# My own notes", encoding="utf-8")
    ensure_soul()
    assert legacy.exists()
    assert "## SELF:IDENTITY" in (cyrene / "SOUL.md").read_text(encoding="utf-8")


def test_ensure_soul_does_not_write_default_when_move_fails(soul_env, monkeypatch):
    workspace, cyrene = soul_env["workspace"], soul_env["cyrene"]
    legacy = _write_legacy(workspace)

    def fail_move(source, destination):
        raise OSError("file locked")

    monkeypatch.setattr(soul_module.shutil, "move", fail_move)
    ensure_soul()
    assert legacy.exists()
    assert not (cyrene / "SOUL.md").exists()


def test_ensure_soul_keeps_existing_cyrene_soul(soul_env):
    workspace, cyrene = soul_env["workspace"], soul_env["cyrene"]
    target = cyrene / "SOUL.md"
    target.write_text("existing", encoding="utf-8")
    _write_legacy(workspace)
    ensure_soul()
    assert target.read_text(encoding="utf-8") == "existing"
    assert (workspace / "SOUL.md").exists()
