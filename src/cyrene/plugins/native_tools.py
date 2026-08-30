"""Seed Cyrene's canonical editable Plugins into the user data directory."""

from __future__ import annotations

import importlib.resources
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType

logger = logging.getLogger(__name__)

USER_STANDALONE_PLUGIN_NAMES = frozenset({"Glob", "Grep", "Edit"})
CORE_PLUGIN_NAMES = frozenset({"Bash", "Read", "Write"})

_CANONICAL_PACKAGE = "cyrene.plugins.builtin"
_FROZEN_CANONICAL_RELATIVE = (
    Path("builtin_plugin_sources") / "cyrene" / "plugins" / "builtin"
)
_UPSTREAM_MANIFEST_RELATIVE = Path(".upstream-hashes.json")
_UPSTREAM_MANIFEST_VERSION = 1
_CANONICAL_FILE_RENAMES = MappingProxyType(
    {
        "cyrene_system_prompt/prompt.py": "cyrene_system_prompt/system_prompt.py",
    }
)
_SEED_LOCK = threading.RLock()

@dataclass(frozen=True, slots=True)
class BuiltinPluginSeedResult:
    """Canonical files created, upgraded, or preserved during one seed."""

    directory: Path
    created: tuple[Path, ...]
    updated: tuple[Path, ...]
    existing: tuple[Path, ...]
    manifest: Path
    removed: tuple[Path, ...] = ()
    diagnostics: tuple[str, ...] = ()


def _canonical_root():
    if getattr(sys, "frozen", False):
        root = Path(str(getattr(sys, "_MEIPASS", "") or ""))
        canonical = root / _FROZEN_CANONICAL_RELATIVE
        if not canonical.is_dir():
            raise RuntimeError(
                "frozen canonical Plugin directory is missing: "
                f"{canonical}"
            )
        return canonical
    return importlib.resources.files(_CANONICAL_PACKAGE)


def _collect_canonical_files() -> Mapping[str, bytes]:
    """Read the canonical Plugin tree without importing any Plugin module."""

    files: dict[str, bytes] = {}

    def visit(directory, parts: tuple[str, ...]) -> None:
        for child in sorted(directory.iterdir(), key=lambda item: item.name):
            if child.name == "__pycache__" or child.name.endswith((".pyc", ".pyo")):
                continue
            relative_parts = (*parts, child.name)
            if child.is_dir():
                visit(child, relative_parts)
                continue
            if not child.is_file():
                continue
            # ``cyrene.plugins.builtin`` needs this marker to be a resource
            # package, but the user's top-level Plugin directory does not.
            if not parts and child.name == "__init__.py":
                continue
            relative = Path(*relative_parts)
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError(f"invalid canonical Plugin path: {relative}")
            files[relative.as_posix()] = child.read_bytes()

    visit(_canonical_root(), ())
    if not files:
        raise RuntimeError("canonical Plugin directory contains no seedable files")
    missing_standalone = sorted(
        {"edit.py", "glob.py", "grep.py"} - files.keys()
    )
    if missing_standalone:
        raise RuntimeError(
            "canonical standalone Plugins are incomplete: "
            + ", ".join(missing_standalone)
        )
    return MappingProxyType(files)


def _content_hash(content: bytes) -> str:
    return sha256(content).hexdigest()


def _path_present(path: Path) -> bool:
    """Return true for normal paths and dangling symlinks alike."""

    return path.exists() or path.is_symlink()


def _load_upstream_hashes(manifest: Path) -> dict[str, str]:
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return {}
    raw_files = payload.get("files")
    if not isinstance(raw_files, dict):
        return {}
    return {
        str(relative): str(digest)
        for relative, digest in raw_files.items()
        if isinstance(relative, str)
        and isinstance(digest, str)
        and len(digest) == 64
    }


def _load_deleted_contributions(manifest: Path) -> set[str]:
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return set()
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return set()
    raw_deleted = payload.get("deleted", ())
    if not isinstance(raw_deleted, list):
        return set()
    return {
        item
        for raw_item in raw_deleted
        if (item := str(raw_item or "").strip())
        and len(Path(item).parts) == 1
        and item not in {".", ".."}
    }


def _migrate_canonical_file_renames(
    root: Path,
    canonical_files: Mapping[str, bytes],
    previous_hashes: dict[str, str],
) -> None:
    """Move user-owned files when a canonical Plugin file is renamed.

    Moving the existing bytes before synchronization preserves local edits. The
    old upstream hash follows the file, so an unmodified file remains eligible
    for normal upgrades while a customized file remains protected.
    """

    for old_relative, new_relative in _CANONICAL_FILE_RENAMES.items():
        if (
            old_relative not in previous_hashes
            or old_relative in canonical_files
            or new_relative not in canonical_files
        ):
            continue
        old_target = root / old_relative
        new_target = root / new_relative
        if _path_present(old_target) and not _path_present(new_target):
            if old_target.is_file() and not old_target.is_symlink():
                new_target.parent.mkdir(parents=True, exist_ok=True)
                old_target.replace(new_target)
            else:
                continue
        if not _path_present(old_target):
            previous_hashes[new_relative] = previous_hashes.pop(old_relative)


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=f".{path.name}.cyrene-seed-",
        dir=path.parent,
    )
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
        temporary.replace(path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_upstream_hashes(
    manifest: Path,
    hashes: Mapping[str, str],
    *,
    deleted: set[str] | frozenset[str] = frozenset(),
) -> None:
    payload = {
        "version": _UPSTREAM_MANIFEST_VERSION,
        "files": {key: hashes[key] for key in sorted(hashes)},
    }
    if deleted:
        payload["deleted"] = sorted(deleted)
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    ).encode("utf-8")
    _atomic_write(manifest, encoded)


def _stage_new_pack(
    root: Path,
    pack_name: str,
    files: Mapping[str, bytes],
) -> bool:
    """Publish one new pack only after every canonical file is on disk."""

    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{pack_name}.cyrene-seed-",
            dir=root,
        )
    )
    published = False
    try:
        prefix = f"{pack_name}/"
        for relative, content in sorted(files.items()):
            if not relative.startswith(prefix):
                continue
            destination = staging / Path(relative).relative_to(pack_name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        target = root / pack_name
        if _path_present(target):
            return False
        staging.rename(target)
        published = True
        return True
    finally:
        if not published:
            shutil.rmtree(staging, ignore_errors=True)


def _seed_canonical_directory(root: Path) -> BuiltinPluginSeedResult:
    canonical_files = _collect_canonical_files()
    manifest = root / _UPSTREAM_MANIFEST_RELATIVE
    previous_hashes = _load_upstream_hashes(manifest)
    _migrate_canonical_file_renames(root, canonical_files, previous_hashes)
    deleted_contributions = _load_deleted_contributions(manifest)
    modified_owned_packs: set[str] = set()
    for relative, baseline_hash in previous_hashes.items():
        parts = Path(relative).parts
        if len(parts) <= 1:
            continue
        target = root / Path(relative)
        if not target.is_file() or target.is_symlink():
            continue
        try:
            if _content_hash(target.read_bytes()) != baseline_hash:
                modified_owned_packs.add(parts[0])
        except OSError:
            modified_owned_packs.add(parts[0])
    next_hashes: dict[str, str] = {}
    created: list[Path] = []
    updated: list[Path] = []
    existing: list[Path] = []
    removed: list[Path] = []

    root.mkdir(parents=True, exist_ok=True)
    diagnostics: list[str] = []

    # A canonical pack is one ownership unit.  Never merge its files into an
    # unmanaged same-name directory: that directory may be a user PluginPack.
    # Once our manifest owns a pack, individual files can be upgraded while
    # still respecting user edits at the file level.
    canonical_packs = {
        Path(relative).parts[0]
        for relative in canonical_files
        if len(Path(relative).parts) > 1
    }
    skipped_packs: set[str] = set()
    new_packs: set[str] = set()
    for pack_name in sorted(canonical_packs):
        if pack_name in deleted_contributions:
            skipped_packs.add(pack_name)
            continue
        if pack_name in skipped_packs:
            continue
        target_pack = root / pack_name
        if not _path_present(target_pack):
            new_packs.add(pack_name)
            continue
        prefix = f"{pack_name}/"
        baseline = {
            relative: digest
            for relative, digest in previous_hashes.items()
            if relative.startswith(prefix)
        }
        safe_directory = target_pack.is_dir() and not target_pack.is_symlink()
        if baseline and safe_directory:
            continue
        skipped_packs.add(pack_name)
        existing.append(target_pack)
        next_hashes.update(baseline)
        reason = (
            "is no longer a regular directory"
            if baseline
            else "has no Cyrene upstream-hash baseline"
        )
        diagnostics.append(
            f"preserved canonical pack collision {target_pack}: {reason}; "
            "no files were merged"
        )

    published_packs: set[str] = set()
    for pack_name in sorted(new_packs):
        target_pack = root / pack_name
        if _stage_new_pack(root, pack_name, canonical_files):
            published_packs.add(pack_name)
            for relative, upstream_content in canonical_files.items():
                if Path(relative).parts[0] != pack_name:
                    continue
                target = root / Path(relative)
                created.append(target)
                next_hashes[relative] = _content_hash(upstream_content)
            continue
        skipped_packs.add(pack_name)
        existing.append(target_pack)
        diagnostics.append(
            f"preserved canonical pack collision {target_pack}: it appeared "
            "while staging; no files were merged"
        )

    for relative, upstream_content in canonical_files.items():
        parts = Path(relative).parts
        if parts[0] in deleted_contributions:
            continue
        if len(parts) > 1 and (
            parts[0] in skipped_packs or parts[0] in published_packs
        ):
            continue
        target = root / Path(relative)
        upstream_hash = _content_hash(upstream_content)
        previous_hash = previous_hashes.get(relative)

        if not _path_present(target):
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with target.open("xb") as stream:
                    stream.write(upstream_content)
            except FileExistsError:
                pass
            else:
                created.append(target)
                next_hashes[relative] = upstream_hash
                continue

        # Top-level files include standalone Plugins and editable support data
        # such as i18n.json. Without a prior baseline, an existing same-name
        # file belongs to the user even when it matches the current default.
        if len(parts) == 1 and previous_hash is None:
            existing.append(target)
            diagnostics.append(
                f"preserved unmanaged top-level Plugin file collision: {target}"
            )
            continue

        if not target.is_file() or target.is_symlink():
            existing.append(target)
            if previous_hash is not None:
                next_hashes[relative] = previous_hash
            continue
        try:
            existing_content = target.read_bytes()
        except OSError:
            existing.append(target)
            if previous_hash is not None:
                next_hashes[relative] = previous_hash
            continue

        existing_hash = _content_hash(existing_content)
        if existing_hash == upstream_hash:
            existing.append(target)
            next_hashes[relative] = upstream_hash
            continue

        managed = previous_hash is not None and existing_hash == previous_hash
        if managed:
            _atomic_write(target, upstream_content)
            updated.append(target)
            next_hashes[relative] = upstream_hash
            continue

        # The bytes no longer match Cyrene's recorded upstream.  Keep both the
        # user's file and the old baseline so reverting that edit later makes
        # the file safely upgradeable again.
        existing.append(target)
        if previous_hash is not None:
            next_hashes[relative] = previous_hash

    # Retire defaults removed by a newer Cyrene version only when their bytes
    # still match the recorded upstream baseline. User-edited obsolete files are
    # preserved and remain tracked so a later manual revert can retire them.
    for relative in sorted(set(previous_hashes) - set(canonical_files)):
        if relative in next_hashes:
            continue
        target = root / Path(relative)
        if not _path_present(target):
            continue
        previous_hash = previous_hashes[relative]
        parts = Path(relative).parts
        if parts[0] in deleted_contributions:
            continue
        if len(parts) > 1 and parts[0] in modified_owned_packs:
            existing.append(target)
            next_hashes[relative] = previous_hash
            continue
        if not target.is_file() or target.is_symlink():
            existing.append(target)
            next_hashes[relative] = previous_hash
            continue
        try:
            content = target.read_bytes()
        except OSError:
            existing.append(target)
            next_hashes[relative] = previous_hash
            continue
        if _content_hash(content) != previous_hash:
            existing.append(target)
            next_hashes[relative] = previous_hash
            continue
        try:
            target.unlink()
        except OSError:
            existing.append(target)
            next_hashes[relative] = previous_hash
            continue
        removed.append(target)
        parent = target.parent
        while parent != root:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    for diagnostic in diagnostics:
        logger.warning("Plugin seed synchronization: %s", diagnostic)
    _write_upstream_hashes(
        manifest,
        next_hashes,
        deleted=deleted_contributions,
    )
    return BuiltinPluginSeedResult(
        directory=root,
        created=tuple(created),
        updated=tuple(updated),
        existing=tuple(existing),
        manifest=manifest,
        removed=tuple(removed),
        diagnostics=tuple(diagnostics),
    )


def seed_builtin_plugin_directory(
    directory: str | Path | None = None,
) -> BuiltinPluginSeedResult:
    """Synchronize unmodified canonical defaults into a user Plugin directory.

    A hidden upstream-hash manifest lets later Cyrene versions update files
    that still match the last shipped default.  Any user-modified file is left
    byte-for-byte intact.  Missing files are supplemented only inside new or
    manifest-managed canonical contributions; unmanaged collisions are kept
    intact and reported.
    """

    if directory is None:
        from cyrene.core.plugin.registry import default_plugin_impl_directory

        root = default_plugin_impl_directory()
    else:
        root = Path(directory).expanduser().resolve()
    with _SEED_LOCK:
        return _seed_canonical_directory(root)


def restore_builtin_plugin(
    directory: str | Path,
    contribution_name: str,
) -> BuiltinPluginSeedResult:
    """Restore one bundled Plugin pack that was previously removed.

    The operation only clears Cyrene's explicit tombstone. Normal seeding
    collision and user-edit protections still apply, so an unrelated
    same-named user directory is never overwritten.
    """

    root = Path(directory).expanduser().resolve()
    normalized = str(contribution_name or "").strip()
    if (
        not normalized
        or len(Path(normalized).parts) != 1
        or normalized in {".", ".."}
    ):
        raise ValueError("Plugin contribution name must be one top-level entry")
    with _SEED_LOCK:
        canonical_files = _collect_canonical_files()
        canonical_entries = {Path(relative).parts[0] for relative in canonical_files}
        if normalized not in canonical_entries:
            raise ValueError(f"Bundled Plugin pack is unavailable: {normalized}")
        root.mkdir(parents=True, exist_ok=True)
        manifest = root / _UPSTREAM_MANIFEST_RELATIVE
        hashes = _load_upstream_hashes(manifest)
        deleted = _load_deleted_contributions(manifest)
        if normalized in deleted:
            deleted.remove(normalized)
            _write_upstream_hashes(manifest, hashes, deleted=deleted)
        return _seed_canonical_directory(root)


def mark_builtin_plugin_deleted(
    directory: str | Path,
    contribution_name: str,
) -> bool:
    """Persist an explicit top-level tombstone for one canonical contribution.

    The marker is intentionally separate from absence on disk: missing files
    are normally repaired by seeding, while only a user-authorized deletion is
    allowed to suppress a built-in contribution on later startups/upgrades.
    """

    root = Path(directory).expanduser().resolve()
    normalized = str(contribution_name or "").strip()
    if (
        not normalized
        or len(Path(normalized).parts) != 1
        or normalized in {".", ".."}
    ):
        raise ValueError("Plugin contribution name must be one top-level entry")
    with _SEED_LOCK:
        canonical_files = _collect_canonical_files()
        canonical_entries = {Path(relative).parts[0] for relative in canonical_files}
        if normalized not in canonical_entries:
            return False
        root.mkdir(parents=True, exist_ok=True)
        manifest = root / _UPSTREAM_MANIFEST_RELATIVE
        hashes = {
            relative: digest
            for relative, digest in _load_upstream_hashes(manifest).items()
            if Path(relative).parts[0] != normalized
        }
        deleted = _load_deleted_contributions(manifest)
        deleted.add(normalized)
        _write_upstream_hashes(manifest, hashes, deleted=deleted)
        return True


__all__ = [
    "BuiltinPluginSeedResult",
    "CORE_PLUGIN_NAMES",
    "USER_STANDALONE_PLUGIN_NAMES",
    "mark_builtin_plugin_deleted",
    "restore_builtin_plugin",
    "seed_builtin_plugin_directory",
]
