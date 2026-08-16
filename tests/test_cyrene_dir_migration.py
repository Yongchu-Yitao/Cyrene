"""Startup migration of legacy workspace-root Cyrene folders into .cyrene."""

from __future__ import annotations

from pathlib import Path

from cyrene.runtime.cyrene_migration import (
    CYRENE_LAYOUT_VERSION,
    migrate_workspace_to_cyrene,
)


def _seed_legacy_workspace(root: Path) -> None:
    (root / "conversations").mkdir()
    (root / "conversations" / "2026-01-01.md").write_text(
        "# Conversations - 2026-01-01\n\n## 08:00:00 UTC\n\n**User**: hi\n\n**Cyrene**: hello\n",
        encoding="utf-8",
    )
    (root / "plan").mkdir()
    (root / "plan" / "plan_abc1234567.md").write_text(
        "# 实现计划\n\n逐步完成\n", encoding="utf-8"
    )
    (root / "patterns").mkdir()
    (root / "patterns" / "pat.json").write_text("{}", encoding="utf-8")
    (root / "projects").mkdir()
    (root / "projects" / "project_deadbeef12").mkdir()
    (root / "scratch").mkdir()
    (root / "scratch" / "tmp.txt").write_text("tmp", encoding="utf-8")
    (root / "SOUL.md").write_text(
        "# Cyrene's Soul\n\n## SELF:IDENTITY\n- I am Cyrene\n", encoding="utf-8"
    )
    # deliverables must NOT migrate: the concept was removed and legacy dirs
    # stay in place for historical artifact downloads.
    (root / "deliverables").mkdir()
    (root / "deliverables" / "old.pdf").write_bytes(b"%PDF")
    # user files stay put.
    (root / "notes.md").write_text("user\n", encoding="utf-8")


def test_migrate_moves_cyrene_folders_into_dot_cyrene(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _seed_legacy_workspace(root)

    moved = migrate_workspace_to_cyrene(root)

    assert moved == 6  # conversations, plan, patterns, projects, scratch, SOUL.md
    cyrene = root / ".cyrene"
    assert (cyrene / "conversations" / "2026-01-01.md").exists()
    assert (cyrene / "plan" / "plan_abc1234567.md").exists()
    assert (cyrene / "patterns" / "pat.json").exists()
    assert (cyrene / "projects" / "project_deadbeef12").is_dir()
    assert (cyrene / "scratch" / "tmp.txt").exists()
    assert (cyrene / "SOUL.md").read_text(encoding="utf-8").startswith("# Cyrene's Soul")
    # Legacy dirs removed from the workspace root.
    for name in ("conversations", "plan", "patterns", "projects", "scratch", "SOUL.md"):
        assert not (root / name).exists()
    # deliverables untouched, user files untouched.
    assert (root / "deliverables" / "old.pdf").read_bytes() == b"%PDF"
    assert (root / "notes.md").read_text(encoding="utf-8") == "user\n"
    # Marker written for 0.7.10+ gating.
    assert (cyrene / ".migrated").read_text(encoding="utf-8").strip() == CYRENE_LAYOUT_VERSION


def test_migrate_is_idempotent_and_marker_gated(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _seed_legacy_workspace(root)

    assert migrate_workspace_to_cyrene(root) == 6
    assert migrate_workspace_to_cyrene(root) == 0
    # 0.7.10+ workspace carrying the marker is never rescanned, even if legacy
    # folders appear at the root again.
    (root / "conversations").mkdir()
    (root / "conversations" / "2026-02-01.md").write_text(
        "# Conversations - 2026-02-01\n", encoding="utf-8"
    )
    assert migrate_workspace_to_cyrene(root) == 0
    assert (root / "conversations" / "2026-02-01.md").exists()


def test_marker_skips_scan_without_process_cache(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _seed_legacy_workspace(root)
    # Simulate a fresh process: no _migrated_roots entry, but marker present.
    from cyrene.runtime import cyrene_migration as migration

    marker_root = root / ".cyrene"
    marker_root.mkdir(parents=True)
    (marker_root / ".migrated").write_text("0.7.10\n", encoding="utf-8")

    assert migration._migrated_roots.isdisjoint({str(root)})
    assert migrate_workspace_to_cyrene(root) == 0
    assert (root / "conversations" / "2026-01-01.md").exists()


def test_user_owned_same_name_dirs_are_left_in_place(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    # User-owned directories that happen to share Cyrene folder names but do
    # not match the Cyrene output signatures must not be moved.
    (root / "plan").mkdir()
    (root / "plan" / "roadmap.md").write_text("# User roadmap\n", encoding="utf-8")
    (root / "projects").mkdir()
    (root / "projects" / "my-app").mkdir()
    (root / "projects" / "my-app" / "src").mkdir()
    (root / "conversations").mkdir()
    (root / "conversations" / "chat-log.txt").write_text("user chat\n", encoding="utf-8")
    (root / "patterns").mkdir()
    (root / "patterns" / "notes.txt").write_text("user notes\n", encoding="utf-8")
    (root / "SOUL.md").write_text("# My own notes\n\nplain text\n", encoding="utf-8")
    # scratch has no stable signature and is migrated unconditionally.
    (root / "scratch").mkdir()
    (root / "scratch" / "tmp.bin").write_bytes(b"\x00\x01")

    moved = migrate_workspace_to_cyrene(root)

    assert moved == 1  # only scratch
    assert (root / "plan" / "roadmap.md").exists()
    assert (root / "projects" / "my-app" / "src").is_dir()
    assert (root / "conversations" / "chat-log.txt").exists()
    assert (root / "patterns" / "notes.txt").exists()
    assert (root / "SOUL.md").exists()
    assert not (root / "scratch").exists()
    assert (root / ".cyrene" / "scratch" / "tmp.bin").read_bytes() == b"\x00\x01"


def test_failed_move_leaves_root_rescannable(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "conversations").mkdir()
    (root / "conversations" / "2026-01-01.md").write_text(
        "# Conversations - 2026-01-01\n", encoding="utf-8"
    )
    (root / "SOUL.md").write_text(
        "# Cyrene's Soul\n\n## SELF:IDENTITY\n- I am Cyrene\n", encoding="utf-8"
    )
    (root / "scratch").mkdir()
    (root / "scratch" / "tmp.txt").write_text("tmp", encoding="utf-8")

    from cyrene.runtime import cyrene_migration as migration

    def _fail_move(source, target):
        raise OSError("simulated failure")

    original_move = migration.shutil.move
    try:
        migration.shutil.move = _fail_move
        # scratch is first in the unconditional list; the failure marks failed.
        assert migrate_workspace_to_cyrene(root) == 0
        assert not (root / ".cyrene" / ".migrated").exists()
    finally:
        migration.shutil.move = original_move

    # Marker absent → a retry re-attempts the move.
    assert migrate_workspace_to_cyrene(root) >= 1
    assert (root / ".cyrene" / ".migrated").exists()
    assert not (root / "conversations").exists()


def test_migrate_noop_for_missing_workspace(tmp_path: Path) -> None:
    assert migrate_workspace_to_cyrene(tmp_path / "does-not-exist") == 0
