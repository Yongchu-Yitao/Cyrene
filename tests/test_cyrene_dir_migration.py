"""Startup migration of legacy workspace-root Cyrene folders into .cyrene."""

from __future__ import annotations

from pathlib import Path

from cyrene.runtime.cyrene_migration import migrate_workspace_to_cyrene


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
    assert not (cyrene / ".migrated").exists()


def test_migrate_is_idempotent_and_directory_gated(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _seed_legacy_workspace(root)

    assert migrate_workspace_to_cyrene(root) == 6
    assert migrate_workspace_to_cyrene(root) == 0
    # Once .cyrene exists, the workspace is never rescanned, even if matching
    # folders appear at the root again.
    (root / "conversations").mkdir()
    (root / "conversations" / "2026-02-01.md").write_text(
        "# Conversations - 2026-02-01\n", encoding="utf-8"
    )
    # Simulate a new process so .cyrene, not the in-memory cache, gates it.
    from cyrene.runtime import cyrene_migration as migration

    migration._migrated_roots.discard(str(root.resolve()))
    assert migrate_workspace_to_cyrene(root) == 0
    assert (root / "conversations" / "2026-02-01.md").exists()


def test_existing_cyrene_dir_skips_scan_and_removes_legacy_marker(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _seed_legacy_workspace(root)
    # Simulate a fresh process with a workspace migrated by an older release.
    from cyrene.runtime import cyrene_migration as migration

    marker_root = root / ".cyrene"
    marker_root.mkdir(parents=True)
    (marker_root / ".migrated").write_text("0.7.10\n", encoding="utf-8")

    assert migration._migrated_roots.isdisjoint({str(root)})
    assert migrate_workspace_to_cyrene(root) == 0
    assert (root / "conversations" / "2026-01-01.md").exists()
    assert not (marker_root / ".migrated").exists()


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
        assert migrate_workspace_to_cyrene(root) == 0
        assert not (root / ".cyrene").exists()
    finally:
        migration.shutil.move = original_move

    # No completed .cyrene directory → a retry re-attempts the move.
    assert migrate_workspace_to_cyrene(root) >= 1
    assert (root / ".cyrene").is_dir()
    assert not (root / ".cyrene" / ".migrated").exists()
    assert not (root / "conversations").exists()


def test_partial_failure_rolls_back_before_retry(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _seed_legacy_workspace(root)

    from cyrene.runtime import cyrene_migration as migration

    original_move = migration.shutil.move
    failed_once = False

    def _fail_second_forward_move(source, target):
        nonlocal failed_once
        source_path = Path(source)
        if source_path.parent == root and source_path.name == "patterns" and not failed_once:
            failed_once = True
            raise OSError("simulated partial failure")
        return original_move(source, target)

    migration.shutil.move = _fail_second_forward_move
    try:
        assert migrate_workspace_to_cyrene(root) == 0
    finally:
        migration.shutil.move = original_move

    assert not (root / ".cyrene").exists()
    assert not (root / ".cyrene-migration").exists()
    assert (root / "conversations" / "2026-01-01.md").exists()

    assert migrate_workspace_to_cyrene(root) == 6


def test_migrate_noop_for_missing_workspace(tmp_path: Path) -> None:
    assert migrate_workspace_to_cyrene(tmp_path / "does-not-exist") == 0
