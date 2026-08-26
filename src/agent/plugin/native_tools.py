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
_LEGACY_AGGREGATE_PACK_ID = "cyrene_tools"
_LEGACY_MODEL_PACK_ID = "model"
_CANONICAL_MODEL_PACK_ID = "cyrene_model"

_CANONICAL_PACKAGE = "agent.plugin.plugin_impl"
_FROZEN_CANONICAL_RELATIVE = (
    Path("builtin_plugin_sources") / "agent" / "plugin" / "plugin_impl"
)
_UPSTREAM_MANIFEST_RELATIVE = Path(".upstream-hashes.json")
_UPSTREAM_MANIFEST_VERSION = 1
_SEED_LOCK = threading.RLock()

# The first generated pack imported every root-level ``tool_*.py`` shim.  A
# previous aggregate pack is moved wholesale to a hidden, recoverable backup
# before canonical domain packs load.  This also preserves edited shims while
# preventing their duplicate Plugin names from breaking Registry startup.
_FIRST_GENERATION_PACK_INITIALIZER = b'''"""Editable Cyrene built-in Plugin pack.

Every ``tool_*.py`` file is user-owned and loaded automatically, so a Cyrene
upgrade can add missing tools without replacing this file or existing tools.
"""

from importlib import import_module
from pathlib import Path

from agent.plugin import Plugin, PluginPack

_plugins: list[Plugin] = []
_names: set[str] = set()
for _path in sorted(Path(__file__).parent.glob("tool_*.py")):
    _module = import_module(f"{__name__}.{_path.stem}")
    _plugin = getattr(_module, "plugin", None)
    if not isinstance(_plugin, Plugin):
        raise TypeError(f"{_path.name} must export Plugin as plugin")
    if _plugin.name in _names:
        raise ValueError(f"duplicate Plugin name in cyrene_tools: {_plugin.name}")
    _names.add(_plugin.name)
    _plugins.append(_plugin)

plugin_pack = PluginPack(
    id="cyrene_tools",
    description="Cyrene's built-in application tools.",
    plugins=tuple(_plugins),
)

__all__ = ["plugin_pack"]
'''

# The two-file model pack was briefly shipped before model providers moved to
# the collision-resistant ``cyrene_model`` id.  Hashes keep this migration
# small while preventing a same-named user pack from being mistaken for it.
_LEGACY_MODEL_DEFAULT_HASHES = MappingProxyType(
    {
        "model/__init__.py": (
            "89fce6c455cfa03504eb00661e9ea6d381ab1487ec72aa7e7490842f93e4c3b1"
        ),
        "model/minimax.py": (
            "136d21eb4b0f0f85c4ffd3d2bbcf60725caad34670b930c01ceb6faec0f93adb"
        ),
    }
)


@dataclass(frozen=True, slots=True)
class BuiltinPluginSeedResult:
    """Canonical files created, upgraded, or preserved during one seed."""

    directory: Path
    created: tuple[Path, ...]
    updated: tuple[Path, ...]
    existing: tuple[Path, ...]
    manifest: Path
    legacy_backups: tuple[Path, ...] = ()
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
            # ``agent.plugin.plugin_impl`` needs this marker to be a resource
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
    required = {
        "edit.py",
        "glob.py",
        "grep.py",
    }
    missing = sorted(required - files.keys())
    if missing:
        raise RuntimeError(
            "canonical Plugin directory is incomplete: " + ", ".join(missing)
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


def _write_upstream_hashes(manifest: Path, hashes: Mapping[str, str]) -> None:
    payload = {
        "version": _UPSTREAM_MANIFEST_VERSION,
        "files": {key: hashes[key] for key in sorted(hashes)},
    }
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


def _next_legacy_backup(root: Path, contribution_name: str) -> Path:
    base = root / f".{contribution_name}-legacy"
    if not _path_present(base):
        return base
    index = 2
    while True:
        candidate = root / f".{contribution_name}-legacy-{index}"
        if not _path_present(candidate):
            return candidate
        index += 1


def _regular_file_hashes(directory: Path, root: Path) -> dict[str, str] | None:
    """Hash a flat legacy pack without following any symlink."""

    hashes: dict[str, str] = {}
    try:
        for path in directory.iterdir():
            if path.is_symlink():
                return None
            if path.is_dir():
                if path.name == "__pycache__":
                    continue
                return None
            if not path.is_file() or path.name.endswith((".pyc", ".pyo")):
                return None
            relative = path.relative_to(root).as_posix()
            hashes[relative] = _content_hash(path.read_bytes())
    except OSError:
        return None
    return hashes


def _backup_first_generation_pack(
    root: Path,
    canonical_files: Mapping[str, bytes],
    previous_hashes: Mapping[str, str],
) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    """Hide the obsolete aggregate pack without deleting any of its files."""

    legacy_prefix = f"{_LEGACY_AGGREGATE_PACK_ID}/"
    if any(relative.startswith(legacy_prefix) for relative in canonical_files):
        return (), ()
    legacy_directory = root / _LEGACY_AGGREGATE_PACK_ID
    if not _path_present(legacy_directory):
        return (), ()
    if legacy_directory.is_symlink():
        return (), (
            "preserved obsolete cyrene_tools symlink without following it; "
            f"manual migration is required: {legacy_directory}",
        )
    if not legacy_directory.is_dir():
        return (), (
            "preserved obsolete cyrene_tools path because it is not a regular "
            f"directory; manual migration is required: {legacy_directory}",
        )

    initializer = legacy_directory / "__init__.py"
    initializer_is_default = False
    if initializer.is_file() and not initializer.is_symlink():
        try:
            initializer_is_default = (
                initializer.read_bytes() == _FIRST_GENERATION_PACK_INITIALIZER
            )
        except OSError:
            pass

    baseline = {
        relative: digest
        for relative, digest in previous_hashes.items()
        if relative.startswith(legacy_prefix)
    }
    verification = "initializer modification detected"
    if baseline:
        baseline_matches = _regular_file_hashes(legacy_directory, root) == baseline
        verification = (
            "user modifications detected against the recorded baseline"
            if not baseline_matches
            else "no user modifications detected against the recorded baseline"
        )
    elif initializer_is_default:
        verification = (
            "no initializer modification detected; child-file edits could not "
            "be verified because this legacy pack has no baseline"
        )

    backup = _next_legacy_backup(root, _LEGACY_AGGREGATE_PACK_ID)
    try:
        legacy_directory.rename(backup)
    except OSError as exc:
        return (), (
            f"unable to move obsolete cyrene_tools pack {legacy_directory}: "
            f"{type(exc).__name__}: {exc}",
        )
    return (backup,), (
        f"moved the obsolete cyrene_tools pack to {backup}; "
        f"all files remain recoverable; {verification}",
    )


def _migrate_legacy_model_pack(
    root: Path,
    canonical_files: Mapping[str, bytes],
    previous_hashes: Mapping[str, str],
) -> tuple[tuple[Path, ...], tuple[str, ...], bool]:
    """Move a verified old model default aside for ``cyrene_model``.

    The boolean result asks the caller to skip ``cyrene_model`` when the old
    directory cannot be proved Cyrene-owned.  That avoids registering two
    providers with the same Plugin name while preserving user content.
    """

    canonical_prefix = f"{_CANONICAL_MODEL_PACK_ID}/"
    legacy_prefix = f"{_LEGACY_MODEL_PACK_ID}/"
    if not any(relative.startswith(canonical_prefix) for relative in canonical_files):
        return (), (), False
    if any(relative.startswith(legacy_prefix) for relative in canonical_files):
        return (), (), False

    legacy_directory = root / _LEGACY_MODEL_PACK_ID
    if not _path_present(legacy_directory):
        return (), (), False
    if legacy_directory.is_symlink() or not legacy_directory.is_dir():
        return (), (
            "preserved unmanaged legacy model path and skipped cyrene_model; "
            f"manual migration is required: {legacy_directory}",
        ), True

    existing_hashes = _regular_file_hashes(legacy_directory, root)
    baseline = {
        relative: digest
        for relative, digest in previous_hashes.items()
        if relative.startswith(legacy_prefix)
    }
    matches_baseline = bool(baseline) and existing_hashes == baseline
    matches_known_default = existing_hashes == dict(_LEGACY_MODEL_DEFAULT_HASHES)
    if not matches_baseline and not matches_known_default:
        return (), (
            "preserved an unmanaged or modified legacy model pack and skipped "
            f"cyrene_model to avoid duplicate model Plugins: {legacy_directory}",
        ), True

    backup = _next_legacy_backup(root, _LEGACY_MODEL_PACK_ID)
    try:
        legacy_directory.rename(backup)
    except OSError as exc:
        return (), (
            f"unable to move verified legacy model pack {legacy_directory}; "
            f"skipped cyrene_model: {type(exc).__name__}: {exc}",
        ), True
    ownership = "recorded baseline" if matches_baseline else "known default"
    return (backup,), (
        f"moved the legacy model pack ({ownership}) to {backup}; all files "
        "remain recoverable and cyrene_model will replace it",
    ), False


def _seed_canonical_directory(root: Path) -> BuiltinPluginSeedResult:
    canonical_files = _collect_canonical_files()
    manifest = root / _UPSTREAM_MANIFEST_RELATIVE
    previous_hashes = _load_upstream_hashes(manifest)
    next_hashes: dict[str, str] = {}
    created: list[Path] = []
    updated: list[Path] = []
    existing: list[Path] = []

    root.mkdir(parents=True, exist_ok=True)
    aggregate_backups, aggregate_diagnostics = _backup_first_generation_pack(
        root,
        canonical_files,
        previous_hashes,
    )
    model_backups, model_diagnostics, skip_canonical_model = (
        _migrate_legacy_model_pack(root, canonical_files, previous_hashes)
    )
    legacy_backups = (*aggregate_backups, *model_backups)
    diagnostics = [*aggregate_diagnostics, *model_diagnostics]

    if not aggregate_backups and _path_present(root / _LEGACY_AGGREGATE_PACK_ID):
        next_hashes.update(
            {
                relative: digest
                for relative, digest in previous_hashes.items()
                if relative.startswith(f"{_LEGACY_AGGREGATE_PACK_ID}/")
            }
        )
    if skip_canonical_model:
        next_hashes.update(
            {
                relative: digest
                for relative, digest in previous_hashes.items()
                if relative.startswith(f"{_LEGACY_MODEL_PACK_ID}/")
            }
        )

    # A canonical pack is one ownership unit.  Never merge its files into an
    # unmanaged same-name directory: that directory may be a user PluginPack.
    # Once our manifest owns a pack, individual files can be upgraded while
    # still respecting user edits at the file level.
    canonical_packs = {
        Path(relative).parts[0]
        for relative in canonical_files
        if len(Path(relative).parts) > 1
    }
    skipped_packs: set[str] = (
        {_CANONICAL_MODEL_PACK_ID} if skip_canonical_model else set()
    )
    new_packs: set[str] = set()
    for pack_name in sorted(canonical_packs):
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

        # Top-level files are standalone Plugins.  Without a prior baseline,
        # an existing same-name file belongs to the user even when its bytes
        # happen to equal the current default.
        if len(parts) == 1 and previous_hash is None:
            existing.append(target)
            diagnostics.append(
                f"preserved unmanaged standalone Plugin collision: {target}"
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

    for diagnostic in diagnostics:
        logger.warning("Plugin seed migration: %s", diagnostic)
    _write_upstream_hashes(manifest, next_hashes)
    return BuiltinPluginSeedResult(
        directory=root,
        created=tuple(created),
        updated=tuple(updated),
        existing=tuple(existing),
        manifest=manifest,
        legacy_backups=legacy_backups,
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
        from .registry import default_plugin_impl_directory

        root = default_plugin_impl_directory()
    else:
        root = Path(directory).expanduser().resolve()
    with _SEED_LOCK:
        return _seed_canonical_directory(root)


__all__ = [
    "BuiltinPluginSeedResult",
    "CORE_PLUGIN_NAMES",
    "USER_STANDALONE_PLUGIN_NAMES",
    "seed_builtin_plugin_directory",
]
