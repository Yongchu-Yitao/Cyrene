"""One-time migration of Cyrene-owned workspace folders into the hidden .cyrene dir.

Cyrene-owned collections (conversations, plan, patterns, projects, scratch,
SOUL.md) live under each workspace root's ``.cyrene`` folder so user files stay
visible at the root. Older installs wrote these directly under the workspace
root; this module moves them on first startup.

Migration is gated by a marker file: after a successful scan the workspace
root's ``.cyrene/.migrated`` marker records the migrating Cyrene version.
Workspaces created or upgraded by 0.7.10+ already write straight into
``.cyrene`` and carry the marker, so they are never rescanned. A marker-less
root whose Cyrene-signature contents sit at the root is treated as pre-0.7.10
data and migrated once.

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

# Version of Cyrene that introduced the .cyrene layout. Workspaces carrying a
# migration marker at or above this version are never rescanned.
CYRENE_LAYOUT_VERSION = "0.7.10"
_MIGRATION_MARKER = ".migrated"

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


def _looks_like_cyrene_folder(root: Path, name: str) -> bool:
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


def _looks_like_cyrene_soul(file_path: Path) -> bool:
    """Return whether the file matches Cyrene's SOUL.md structure."""
    try:
        head = file_path.read_text(encoding="utf-8", errors="replace")[:400]
    except OSError:
        logger.exception("Failed to inspect %s", file_path)
        return False
    return "## SELF:IDENTITY" in head


def _has_migration_marker(root: Path) -> bool:
    marker = root / CYRENE_DIR_NAME / _MIGRATION_MARKER
    try:
        return marker.is_file()
    except OSError:
        return False


def _write_migration_marker(root: Path) -> None:
    marker = root / CYRENE_DIR_NAME / _MIGRATION_MARKER
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(CYRENE_LAYOUT_VERSION + "\n", encoding="utf-8")
    except OSError:
        logger.exception("Failed to write migration marker %s", marker)


def migrate_workspace_to_cyrene(workspace_root: str | Path) -> int:
    """Move legacy root-level Cyrene folders/files into ``.cyrene`` (idempotent).

    A root carrying the migration marker (0.7.10+) or already scanned in this
    process is skipped entirely. Otherwise signature-matching Cyrene folders,
    scratch/, and SOUL.md are moved into ``.cyrene`` and the marker is written.
    The marker is only written after a full successful pass, so a failed move
    leaves the root rescan-able on the next attempt.

    Returns the number of entries moved.
    """
    root = Path(workspace_root).resolve()
    key = str(root)
    if key in _migrated_roots or not root.is_dir():
        return 0
    if _has_migration_marker(root):
        _migrated_roots.add(key)
        return 0
    cyrene = root / CYRENE_DIR_NAME
    moved = 0
    failed = False
    for name in _SIGNATURE_FOLDER_NAMES + _UNCONDITIONAL_FOLDER_NAMES:
        source = root / name
        if not source.is_dir():
            continue
        if name in _SIGNATURE_FOLDER_NAMES and not _looks_like_cyrene_folder(root, name):
            logger.info(
                "Leaving %s in place: contents do not match the Cyrene output signature",
                source,
            )
            continue
        target = cyrene / name
        if target.exists():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            moved += 1
        except OSError:
            logger.exception("Failed to migrate workspace folder %s", source)
            failed = True
    for name in _MIGRATED_FILE_NAMES:
        source = root / name
        if not source.is_file():
            continue
        if not _looks_like_cyrene_soul(source):
            logger.info(
                "Leaving %s in place: contents do not match the Cyrene SOUL.md signature",
                source,
            )
            continue
        target = cyrene / name
        if target.exists():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            moved += 1
        except OSError:
            logger.exception("Failed to migrate workspace file %s", source)
            failed = True
    # Only a fully successful pass writes the marker and caches the root; a
    # failed move keeps the root rescan-able so the leftover entry is retried
    # on the next call (or process restart).
    if not failed:
        _write_migration_marker(root)
        _migrated_roots.add(key)
    if moved:
        logger.info("Migrated %d Cyrene item(s) into %s", moved, cyrene)
    return moved
