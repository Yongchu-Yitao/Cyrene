"""One-time migration of Cyrene-owned workspace folders into the hidden .cyrene dir.

Cyrene-owned collections (conversations, plan, patterns, projects, scratch,
SOUL.md) live under each workspace root's ``.cyrene`` folder so user files stay
visible at the root. Older installs wrote these directly under the workspace
root; this module moves them on first startup.

The ``.cyrene`` directory itself is the durable completion signal. New
workspaces write there from the beginning; legacy workspaces do not have the
directory until this migration finishes. This keeps the migration one-shot
across process restarts without exposing a separate marker file to users.

Moves are staged in a sibling directory and committed by renaming that
directory to ``.cyrene``. If a move fails, completed moves are rolled back so a
later startup can safely retry instead of mistaking a partial migration for a
completed one.

To avoid touching user-owned content, most candidates are only moved when
their contents match Cyrene's output signatures — a user's own ``plan/`` or
``projects/`` folder is left alone. ``scratch/`` has no stable signature (the
agent writes arbitrary files there) and is migrated unconditionally, matching
the agent prompt that directs temp files into the hidden dir.
``deliverables`` is deliberately absent: that concept was removed (send_file
pins a durable ``webui_exports`` copy instead), so legacy ``deliverables/``
dirs are left untouched.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from cyrene.runtime.paths import CYRENE_DIR_NAME

logger = logging.getLogger(__name__)

_LEGACY_MIGRATION_MARKER = ".migrated"
_MIGRATION_STAGING_SUFFIX = "-migration"

# Folders migrated when their content matches the Cyrene output signature.
# scratch has no stable signature and is always migrated (prompt directs the
# agent to write temp files into .cyrene/scratch).
_SIGNATURE_FOLDER_NAMES = ("conversations", "patterns", "plan", "projects")
_UNCONDITIONAL_FOLDER_NAMES = ("scratch",)
_MIGRATED_FILE_NAMES = ("SOUL.md",)

_PROJECT_DIR_RE = re.compile(r"^project_[0-9a-f]{10}$")
_PLAN_FILE_RE = re.compile(r"^plan_[0-9a-f]{10}\.md$")

# Roots already scanned this process. New code never writes these folders at
# the workspace root again, so once scanned a root never needs re-checking.
_migrated_roots: set[str] = set()


def looks_like_cyrene_folder(root: Path, name: str) -> bool:
    """Return whether the folder's contents match Cyrene's output signature.

    Signature checks keep user-owned directories of the same name (e.g. a
    monorepo's ``projects/`` or a user's planning docs in ``plan/``) in place.
    """
    folder = root / name
    try:
        if name == "conversations":
            # Legacy daily archives start "# Conversations - <date>"; Workbench
            # per-session archives start "# Conversation <session_id>".
            for file in folder.glob("*.md"):
                head = file.read_text(encoding="utf-8", errors="replace")[:80]
                if head.startswith("# Conversations - ") or head.startswith("# Conversation "):
                    return True
            return False
        if name == "plan":
            return any(file.name for file in folder.glob("*.md") if _PLAN_FILE_RE.match(file.name))
        if name == "projects":
            return any(
                child.is_dir() and bool(_PROJECT_DIR_RE.match(child.name))
                for child in folder.iterdir()
            )
        if name == "patterns":
            # Behavior-learning emits JSON pattern files.
            return any(child.is_file() and child.suffix == ".json" for child in folder.iterdir())
    except OSError:
        logger.exception("Failed to inspect %s for Cyrene signature", folder)
        return False
    return False


def looks_like_cyrene_soul(file_path: Path) -> bool:
    """Return whether the file matches Cyrene's SOUL.md structure."""
    try:
        head = file_path.read_text(encoding="utf-8", errors="replace")[:400]
    except OSError:
        logger.exception("Failed to inspect %s", file_path)
        return False
    return "## SELF:IDENTITY" in head


def _remove_legacy_migration_marker(cyrene: Path) -> None:
    """Remove the marker written by releases that predate directory gating."""
    marker = cyrene / _LEGACY_MIGRATION_MARKER
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        logger.warning("Failed to remove legacy migration marker %s", marker, exc_info=True)


def _rollback_staged_moves(root: Path, staging: Path, names: list[str]) -> bool:
    """Restore staged entries to the workspace root after an interrupted pass."""
    ok = True
    for name in reversed(names):
        staged = staging / name
        if not staged.exists():
            continue
        source = root / name
        if source.exists():
            logger.error("Cannot roll back %s because %s already exists", staged, source)
            ok = False
            continue
        try:
            shutil.move(str(staged), str(source))
        except OSError:
            logger.exception("Failed to roll back staged workspace entry %s", staged)
            ok = False
    if ok:
        try:
            staging.rmdir()
        except FileNotFoundError:
            pass
        except OSError:
            logger.exception("Failed to remove migration staging directory %s", staging)
            ok = False
    return ok


def _recover_interrupted_migration(root: Path, staging: Path) -> bool:
    """Roll back a staging directory left behind by a terminated process."""
    if not staging.is_dir():
        return True
    names = [child.name for child in staging.iterdir()]
    logger.warning("Recovering interrupted Cyrene workspace migration in %s", root)
    return _rollback_staged_moves(root, staging, names)


def migrate_workspace_to_cyrene(workspace_root: str | Path) -> int:
    """Move legacy root-level Cyrene folders/files into ``.cyrene`` (idempotent).

    A root that already contains ``.cyrene`` or was already scanned in this
    process is skipped entirely. Otherwise signature-matching Cyrene folders,
    scratch/, and SOUL.md are staged and committed into ``.cyrene``. A failed
    pass is rolled back and remains retryable.

    Returns the number of entries moved.
    """
    root = Path(workspace_root).resolve()
    key = str(root)
    if key in _migrated_roots or not root.is_dir():
        return 0
    cyrene = root / CYRENE_DIR_NAME
    if cyrene.exists() or cyrene.is_symlink():
        if cyrene.is_dir():
            _remove_legacy_migration_marker(cyrene)
        _migrated_roots.add(key)
        return 0

    staging = root / f"{CYRENE_DIR_NAME}{_MIGRATION_STAGING_SUFFIX}"
    if not _recover_interrupted_migration(root, staging):
        return 0

    candidates: list[Path] = []
    for name in _SIGNATURE_FOLDER_NAMES + _UNCONDITIONAL_FOLDER_NAMES:
        source = root / name
        if not source.is_dir():
            continue
        if name in _SIGNATURE_FOLDER_NAMES and not looks_like_cyrene_folder(root, name):
            logger.info(
                "Leaving %s in place: contents do not match the Cyrene output signature",
                source,
            )
            continue
        candidates.append(source)
    for name in _MIGRATED_FILE_NAMES:
        source = root / name
        if not source.is_file():
            continue
        if not looks_like_cyrene_soul(source):
            logger.info(
                "Leaving %s in place: contents do not match the Cyrene SOUL.md signature",
                source,
            )
            continue
        candidates.append(source)

    staged_names: list[str] = []
    try:
        staging.mkdir()
        for source in candidates:
            shutil.move(str(source), str(staging / source.name))
            staged_names.append(source.name)
        staging.rename(cyrene)
    except OSError:
        logger.exception("Failed to migrate workspace entries in %s", root)
        _rollback_staged_moves(root, staging, staged_names)
        return 0

    moved = len(staged_names)
    _migrated_roots.add(key)
    if moved:
        logger.info("Migrated %d Cyrene item(s) into %s", moved, cyrene)
    return moved
