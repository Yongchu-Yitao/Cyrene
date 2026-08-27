"""Portable, validated backup and rollback-safe restore for Cyrene state."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from cyrene.config import BASE_DIR, DATA_DIR, STORE_DIR, TEMP_DIR, WORKSPACE_DIR, cyrene_dir

logger = logging.getLogger(__name__)

_FORMAT_VERSION = "0.5"
_MANIFEST_ENTRY = "manifest.json"
_PORTABLE_CONFIG_ENTRY = "_cyrene/config.json"
_BACKUP_DIR = BASE_DIR / "backups"

# These directories are Cyrene-owned collections. A v0.5 restore replaces each
# collection as a unit, so files created after the backup do not survive a
# rollback. Root-level files are restored individually.
#
# Workspace collections live under the workspace's hidden .cyrene dir. Arcnames
# keep the legacy "workspace/<name>" form, but _resolve_target rewrites that
# prefix straight back into .cyrene — the location the app reads and writes
# today — so archives restore without depending on the startup migration sweep.
# Backups created before a collection was removed (e.g. workspace/deliverables)
# carry a replace root no longer accepted and are rejected by _inspect_archive.
_MANAGED_DIRECTORIES: list[tuple[Path, str]] = [
    (cyrene_dir(WORKSPACE_DIR) / "patterns", "workspace/patterns"),
    # Plan records persist markdownPath values into chat state. Keep the
    # generated markdown mirrors so those stored paths remain valid.
    (cyrene_dir(WORKSPACE_DIR) / "plan", "workspace/plan"),
    # Projects created without a user-selected directory live here. Their task
    # sessions persist relative artifact paths and download directly from these
    # workspaces. User-selected external project folders remain user-owned and
    # intentionally stay outside the Cyrene state backup.
    (cyrene_dir(WORKSPACE_DIR) / "projects", "workspace/projects"),
    (DATA_DIR / "sessions", "data/sessions"),
    (DATA_DIR / "inbox", "data/inbox"),
    (DATA_DIR / "installed_skills", "data/installed_skills"),
    (DATA_DIR / "learned_skill_scripts", "data/learned_skill_scripts"),
    (DATA_DIR / "behavior-media", "data/behavior-media"),
    (DATA_DIR / "webui_uploads", "data/webui_uploads"),
    # register_generated_attachment copies every file exposed by
    # /api/chat/export here.  These copies are also used as knowledge-library
    # document paths and therefore are not a disposable download cache.
    (DATA_DIR / "webui_exports", "data/webui_exports"),
]
_RESTORABLE_REPLACE_ROOTS = {arcname for _, arcname in _MANAGED_DIRECTORIES}

# Browser state, derived indexes, and diagnostics are caches rather than
# durable agent state. Everything else at the root of data/ and store/ is
# included.
_EXCLUDED_DATA_ROOT_NAMES = {
    "config.enc", ".config_key", "code_index.db",
    # MCP declarations are migrated into the encrypted portable config
    # snapshot. Never copy a legacy plaintext declaration file into backups.
}
_EXCLUDED_DATA_DIRECTORIES = {
    "attachment_cache", "browser_profile", "generated_reports",
}

_ALLOWED_ROOTS: list[Path] = [STORE_DIR.resolve(), DATA_DIR.resolve(), WORKSPACE_DIR.resolve()]

# Export and restore use the same limits, which prevents Cyrene from creating
# an archive that its own restore path must reject.
_MAX_ENTRIES = 100_000
_MAX_DECOMPRESSED_BYTES = 8 * 1024 * 1024 * 1024  # 8 GiB
_MAX_SINGLE_ENTRY_BYTES = 8 * 1024 * 1024 * 1024
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024
_MAX_CONFIG_SNAPSHOT_BYTES = 4 * 1024 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_backup_operation_lock = asyncio.Lock()


@dataclass
class _Source:
    path: Path | None
    arcname: str
    data: bytes | None = None


@dataclass
class _Operation:
    source: Path
    target: Path
    kind: str


@dataclass
class _StagePlan:
    operations: list[_Operation]
    restored: list[str]
    version: str
    config_snapshot: dict[str, Any] | None


def ensure_backup_dir() -> Path:
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return _BACKUP_DIR


def _failure(error: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": error, **extra}


def _resolve_target(name: str) -> Path:
    """Map a validated archive name to its live destination."""
    if name.startswith("store/"):
        return STORE_DIR / name[len("store/"):]
    if name.startswith("data/"):
        return DATA_DIR / name[len("data/"):]
    if name.startswith("workspace/"):
        return cyrene_dir(WORKSPACE_DIR) / name[len("workspace/"):]
    # Compatibility with v0.4 conversation entries, which were stored at the
    # workspace root; the startup migration sweeps those into .cyrene.
    return WORKSPACE_DIR / name


def _is_within_allowed_root(target: Path) -> bool:
    resolved = target.resolve(strict=False)
    return any(resolved.is_relative_to(root) for root in _ALLOWED_ROOTS)


def _validate_archive_name(name: str, *, allow_legacy_workspace: bool) -> Path | None:
    if name == _PORTABLE_CONFIG_ENTRY:
        return None
    if not name or "\x00" in name or "\\" in name:
        raise ValueError(f"invalid archive entry name: {name!r}")
    pure = PurePosixPath(name)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"invalid archive entry name: {name!r}")
    if not allow_legacy_workspace and pure.parts[0] not in {"data", "store", "workspace"}:
        raise ValueError(f"unsupported archive entry root: {name}")
    target = _resolve_target(name)
    if not _is_within_allowed_root(target):
        raise ValueError(f"{name}: path traversal blocked")
    return target


def _is_zip_symlink(info: ZipInfo) -> bool:
    return stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF)


def _looks_like_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _sqlite_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        dst = sqlite3.connect(destination, timeout=30)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _validate_sqlite(path: Path) -> None:
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()
    finally:
        conn.close()
    if result is None or result[0] != "ok":
        raise ValueError(f"SQLite integrity check failed for {path.name}: {result}")


def _config_snapshot_bytes() -> bytes:
    from cyrene.runtime import config_store

    snapshot = json.dumps(
        config_store.export_snapshot(), ensure_ascii=False, indent=2
    ).encode("utf-8")
    if len(snapshot) > _MAX_CONFIG_SNAPSHOT_BYTES:
        raise ValueError("portable configuration snapshot exceeds size limit")
    return snapshot


def _prepare_config_restore(raw: bytes) -> tuple[dict[str, Any], bytes]:
    from cyrene.runtime import config_store

    snapshot = json.loads(raw.decode("utf-8"))
    return config_store.prepare_restored_snapshot(snapshot)


def _activate_config_snapshot(snapshot: dict[str, Any]) -> None:
    from cyrene.runtime import config_store

    config_store.activate_restored_snapshot(snapshot)


def _iter_directory_sources(root: Path, arc_root: str) -> list[_Source]:
    if not root.is_dir():
        return []
    result: list[_Source] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            rel = path.relative_to(root).as_posix()
            result.append(_Source(path=path, arcname=f"{arc_root}/{rel}"))
    return result


def _plugin_backup_descriptors(
    *,
    include_sources: bool = True,
) -> tuple[list[_Source], list[str]]:
    """Collect backup contributions through the generic Plugin service API."""

    from agent.plugin import active_plugin_application_host

    host = active_plugin_application_host()
    if host is None:
        return [], []
    sources: list[_Source] = []
    replace_roots: list[str] = []
    for service_name, service in sorted(host.services.items()):
        provider = getattr(service, "backup_sources", None)
        if not callable(provider):
            continue
        contribution = provider()
        if not isinstance(contribution, dict):
            raise TypeError(
                f"Plugin service {service_name!r} returned invalid backup sources"
            )
        for raw_path, raw_arcname in contribution.get("files", ()):
            path = Path(raw_path).expanduser()
            arcname = str(raw_arcname or "").strip()
            _validate_archive_name(arcname, allow_legacy_workspace=False)
            if not _is_within_allowed_root(path):
                raise ValueError(
                    f"Plugin service {service_name!r} exposed a backup path "
                    f"outside Cyrene state: {path}"
                )
            if include_sources and path.is_file() and not path.is_symlink():
                sources.append(_Source(path=path, arcname=arcname))
        for raw_path, raw_arc_root in contribution.get("directories", ()):
            path = Path(raw_path).expanduser()
            arc_root = str(raw_arc_root or "").strip()
            _validate_archive_name(arc_root, allow_legacy_workspace=False)
            if not _is_within_allowed_root(path):
                raise ValueError(
                    f"Plugin service {service_name!r} exposed a backup directory "
                    f"outside Cyrene state: {path}"
                )
            replace_roots.append(arc_root)
            if include_sources:
                sources.extend(_iter_directory_sources(path, arc_root))
    return sources, replace_roots


def _restorable_replace_roots() -> set[str]:
    """Return core and currently installed Plugin collection roots."""

    _sources, plugin_roots = _plugin_backup_descriptors(include_sources=False)
    return {*_RESTORABLE_REPLACE_ROOTS, *plugin_roots}


def _iter_export_sources() -> tuple[list[_Source], list[str]]:
    sources: list[_Source] = [
        _Source(path=None, arcname=_PORTABLE_CONFIG_ENTRY, data=_config_snapshot_bytes())
    ]
    replace_roots: list[str] = []

    for root, arc_root in _MANAGED_DIRECTORIES:
        replace_roots.append(arc_root)
        sources.extend(_iter_directory_sources(root, arc_root))

    plugin_sources, plugin_replace_roots = _plugin_backup_descriptors()
    sources.extend(plugin_sources)
    replace_roots.extend(plugin_replace_roots)

    if DATA_DIR.is_dir():
        for path in sorted(DATA_DIR.iterdir()):
            if not path.is_file() or path.is_symlink():
                continue
            if path.name in _EXCLUDED_DATA_ROOT_NAMES:
                continue
            if path.name.startswith("debug_") or path.name.endswith(("-wal", "-shm", ".tmp")):
                continue
            sources.append(_Source(path=path, arcname=f"data/{path.name}"))

    if STORE_DIR.is_dir():
        for path in sorted(STORE_DIR.iterdir()):
            if not path.is_file() or path.is_symlink():
                continue
            if path.name.endswith(("-wal", "-shm", ".tmp")):
                continue
            sources.append(_Source(path=path, arcname=f"store/{path.name}"))

    # A directory may be added to the managed list while also present under
    # data/. Deduplicate defensively by archive name.
    unique: dict[str, _Source] = {}
    for source in sources:
        if source.arcname in unique:
            previous = unique[source.arcname]
            if previous.path == source.path and previous.data == source.data:
                continue
            raise ValueError(f"duplicate export entry: {source.arcname}")
        unique[source.arcname] = source
    return list(unique.values()), list(dict.fromkeys(replace_roots))


def _stream_into_zip(zf: ZipFile, arcname: str, source: BinaryIO) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with zf.open(arcname, "w", force_zip64=True) as destination:
        while True:
            chunk = source.read(_COPY_CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            if size > _MAX_SINGLE_ENTRY_BYTES:
                raise ValueError(f"{arcname} exceeds the per-entry backup limit")
            digest.update(chunk)
            destination.write(chunk)
    return size, digest.hexdigest()


def _write_source(zf: ZipFile, source: _Source) -> tuple[dict[str, Any], Path | None]:
    temporary_snapshot: Path | None = None
    kind = "file"
    if source.data is not None:
        handle: BinaryIO = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
        handle.write(source.data)
        handle.seek(0)
        kind = "config" if source.arcname == _PORTABLE_CONFIG_ENTRY else "file"
    else:
        assert source.path is not None
        path_to_write = source.path
        if _looks_like_sqlite(source.path):
            TEMP_DIR.mkdir(parents=True, exist_ok=True)
            fd, snapshot_name = tempfile.mkstemp(prefix="cyrene_backup_db_", suffix=".db", dir=TEMP_DIR)
            os.close(fd)
            temporary_snapshot = Path(snapshot_name)
            _sqlite_backup(source.path, temporary_snapshot)
            path_to_write = temporary_snapshot
            kind = "sqlite"
        handle = path_to_write.open("rb")

    try:
        size, sha256 = _stream_into_zip(zf, source.arcname, handle)
    finally:
        handle.close()
    return {
        "name": source.arcname,
        "size": size,
        "sha256": sha256,
        "kind": kind,
    }, temporary_snapshot


async def export_backup(
    *, include_db: bool = True, target_path: str | Path | None = None
) -> dict[str, Any]:
    """Create a consistent backup without blocking the application event loop."""
    async with _backup_operation_lock:
        return await asyncio.to_thread(
            _export_backup_sync, include_db=include_db, target_path=target_path
        )


def _export_backup_sync(
    *, include_db: bool = True, target_path: str | Path | None = None
) -> dict[str, Any]:
    if target_path is not None:
        backup_path = Path(target_path)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        ensure_backup_dir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = _BACKUP_DIR / f"cyrene_backup_{timestamp}_{uuid.uuid4().hex[:8]}.zip"

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{backup_path.name}.", suffix=".tmp", dir=backup_path.parent
    )
    os.close(fd)
    temporary_archive = Path(temporary_name)
    snapshots: list[Path] = []

    try:
        sources, replace_roots = _iter_export_sources()
        if not include_db:
            sources = [
                source for source in sources
                if source.path is None or not _looks_like_sqlite(source.path)
            ]
        if len(sources) > _MAX_ENTRIES:
            raise ValueError(f"backup has {len(sources)} entries, limit is {_MAX_ENTRIES}")

        entries: list[dict[str, Any]] = []
        total_size = 0
        with ZipFile(temporary_archive, "w", ZIP_DEFLATED, allowZip64=True) as zf:
            for source in sources:
                entry, snapshot = _write_source(zf, source)
                if snapshot is not None:
                    snapshots.append(snapshot)
                total_size += int(entry["size"])
                if total_size > _MAX_DECOMPRESSED_BYTES:
                    raise ValueError(
                        f"backup payload exceeds {_MAX_DECOMPRESSED_BYTES // (1024**3)} GiB limit"
                    )
                entries.append(entry)

            manifest = {
                "format": "cyrene-backup",
                "version": _FORMAT_VERSION,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "contains_secrets": True,
                "entries": entries,
                "replace_roots": replace_roots,
            }
            manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
            if len(manifest_bytes) > _MAX_MANIFEST_BYTES:
                raise ValueError("backup manifest exceeds size limit")
            zf.writestr(_MANIFEST_ENTRY, manifest_bytes)

        os.replace(temporary_archive, backup_path)
        size = backup_path.stat().st_size
        logger.info("Backup created: %s (%d bytes, %d entries)", backup_path, size, len(entries))
        return {"ok": True, "path": str(backup_path), "size": size, "entries": entries}
    except Exception as exc:
        logger.exception("Backup failed")
        temporary_archive.unlink(missing_ok=True)
        return _failure(str(exc))
    finally:
        for snapshot in snapshots:
            snapshot.unlink(missing_ok=True)


def _inspect_archive(zf: ZipFile) -> tuple[list[ZipInfo], dict[str, dict[str, Any]], list[str], str, bool]:
    infos = zf.infolist()
    if len(infos) > _MAX_ENTRIES + 1:
        raise ValueError(f"archive has {len(infos)} entries, limit is {_MAX_ENTRIES + 1}")

    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise ValueError("archive contains duplicate entry names")
    for info in infos:
        if _is_zip_symlink(info):
            raise ValueError(f"symbolic-link archive entry is not allowed: {info.filename}")

    manifest_info = next((info for info in infos if info.filename == _MANIFEST_ENTRY), None)
    if manifest_info is not None and manifest_info.file_size > _MAX_MANIFEST_BYTES:
        raise ValueError("backup manifest exceeds size limit")

    payload = [
        info for info in infos
        if not info.is_dir() and info.filename != _MANIFEST_ENTRY
    ]
    if len(payload) > _MAX_ENTRIES:
        raise ValueError(f"archive has {len(payload)} payload entries, limit is {_MAX_ENTRIES}")
    for info in payload:
        if info.file_size > _MAX_SINGLE_ENTRY_BYTES:
            raise ValueError(f"{info.filename} exceeds the per-entry restore limit")
    total = sum(info.file_size for info in payload)
    if total > _MAX_DECOMPRESSED_BYTES:
        raise ValueError(
            f"archive decompressed size {total // (1024 * 1024)} MB exceeds limit of "
            f"{_MAX_DECOMPRESSED_BYTES // (1024 * 1024)} MB"
        )

    manifest: dict[str, Any] = {}
    if manifest_info is not None:
        manifest = json.loads(zf.read(manifest_info).decode("utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("backup manifest must be an object")

    version = str(manifest.get("version", "unknown"))
    modern = version == _FORMAT_VERSION or manifest.get("format") == "cyrene-backup"
    declared = manifest.get("entries")
    metadata: dict[str, dict[str, Any]] = {}
    payload_names = [info.filename for info in payload]

    if modern:
        if version != _FORMAT_VERSION:
            raise ValueError(f"unsupported backup version: {version}")
        if not isinstance(declared, list) or not all(isinstance(item, dict) for item in declared):
            raise ValueError("backup manifest entries are invalid")
        for item in declared:
            name = str(item.get("name") or "")
            if not name or name in metadata:
                raise ValueError("backup manifest contains an invalid or duplicate name")
            sha256 = str(item.get("sha256") or "")
            if not _SHA256_RE.fullmatch(sha256):
                raise ValueError(f"backup manifest has an invalid digest for {name}")
            kind = str(item.get("kind") or "file")
            if kind not in {"file", "sqlite", "config"}:
                raise ValueError(f"backup manifest has an invalid kind for {name}")
            metadata[name] = {
                "size": int(item.get("size", -1)), "sha256": sha256, "kind": kind
            }
        if set(metadata) != set(payload_names) or len(metadata) != len(payload_names):
            raise ValueError("backup payload does not match manifest entries")
        for info in payload:
            if metadata[info.filename]["size"] != info.file_size:
                raise ValueError(f"backup manifest size mismatch for {info.filename}")
    elif declared is not None:
        if not isinstance(declared, list) or not all(isinstance(item, str) for item in declared):
            raise ValueError("legacy backup manifest entries are invalid")
        if set(declared) != set(payload_names) or len(declared) != len(payload_names):
            raise ValueError("backup payload does not match manifest entries")

    roots = manifest.get("replace_roots", []) if modern else []
    if not isinstance(roots, list) or not all(isinstance(root, str) for root in roots):
        raise ValueError("backup replace_roots is invalid")
    allowed_replace_roots = _restorable_replace_roots()
    if len(roots) != len(set(roots)) or any(root not in allowed_replace_roots for root in roots):
        raise ValueError("backup requests an unsupported replace root")

    return payload, metadata, roots, version, modern


def _copy_zip_entry(zf: ZipFile, info: ZipInfo, destination: Path) -> tuple[int, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with zf.open(info, "r") as source, destination.open("wb") as output:
        while True:
            chunk = source.read(_COPY_CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            if size > info.file_size or size > _MAX_SINGLE_ENTRY_BYTES:
                raise ValueError(f"expanded entry exceeds declared size: {info.filename}")
            digest.update(chunk)
            output.write(chunk)
    if size != info.file_size:
        raise ValueError(f"expanded entry size mismatch: {info.filename}")
    return size, digest.hexdigest()


def _entry_is_under_root(name: str, root: str) -> bool:
    return name == root or name.startswith(root + "/")


def _stage_archive(zf: ZipFile, stage: Path) -> _StagePlan:
    payload, metadata, replace_roots, version, modern = _inspect_archive(zf)
    prepared = stage / "prepared"
    prepared.mkdir()
    targets: set[Path] = set()
    ordinary_names: list[str] = []
    config_snapshot: dict[str, Any] | None = None

    for info in payload:
        name = info.filename
        target = _validate_archive_name(name, allow_legacy_workspace=not modern)
        if target is not None:
            resolved_target = target.resolve(strict=False)
            if resolved_target in targets:
                raise ValueError(f"multiple archive entries map to {target}")
            targets.add(resolved_target)

        extracted = prepared / name
        _, digest = _copy_zip_entry(zf, info, extracted)
        entry_meta = metadata.get(name, {})
        expected_digest = entry_meta.get("sha256")
        if expected_digest and digest != expected_digest:
            raise ValueError(f"SHA-256 mismatch for {name}")

        if name == _PORTABLE_CONFIG_ENTRY:
            if info.file_size > _MAX_CONFIG_SNAPSHOT_BYTES:
                raise ValueError("portable configuration snapshot exceeds size limit")
            if modern and entry_meta.get("kind") != "config":
                raise ValueError("portable configuration entry has the wrong kind")
            raw = extracted.read_bytes()
            config_snapshot, encrypted = _prepare_config_restore(raw)
            config_target = DATA_DIR / "config.enc"
            if not _is_within_allowed_root(config_target):
                raise ValueError("configuration target is outside the allowed roots")
            encrypted_path = prepared / "data" / "config.enc"
            encrypted_path.parent.mkdir(parents=True, exist_ok=True)
            encrypted_path.write_bytes(encrypted)
            extracted.unlink()
            continue

        kind = str(entry_meta.get("kind") or ("sqlite" if _looks_like_sqlite(extracted) else "file"))
        if kind == "sqlite":
            _validate_sqlite(extracted)
        ordinary_names.append(name)

    operations: list[_Operation] = []
    for root in replace_roots:
        root_target = _validate_archive_name(root + "/.placeholder", allow_legacy_workspace=False)
        assert root_target is not None
        target_dir = root_target.parent
        source_dir = prepared / root
        source_dir.mkdir(parents=True, exist_ok=True)
        operations.append(_Operation(source=source_dir, target=target_dir, kind="directory"))

    for name in ordinary_names:
        if any(_entry_is_under_root(name, root) for root in replace_roots):
            continue
        target = _validate_archive_name(name, allow_legacy_workspace=not modern)
        assert target is not None
        kind = str(metadata.get(name, {}).get("kind") or ("sqlite" if _looks_like_sqlite(prepared / name) else "file"))
        operations.append(_Operation(source=prepared / name, target=target, kind=kind))

    if config_snapshot is not None:
        operations.append(
            _Operation(source=prepared / "data" / "config.enc", target=DATA_DIR / "config.enc", kind="file")
        )

    operation_targets = [operation.target.resolve(strict=False) for operation in operations]
    if len(operation_targets) != len(set(operation_targets)):
        raise ValueError("backup contains conflicting restore targets")

    return _StagePlan(
        operations=operations,
        restored=[info.filename for info in payload],
        version=version,
        config_snapshot=config_snapshot,
    )


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _replace_from_stage(source: Path, target: Path) -> None:
    os.replace(source, target)


def _restore_sqlite(source: Path, target: Path) -> None:
    if target.exists() and not _looks_like_sqlite(target):
        _remove_path(target)
    _sqlite_backup(source, target)


def _commit_stage(plan: _StagePlan, stage: Path) -> None:
    rollback = stage / "rollback"
    rollback.mkdir()
    swapped: list[tuple[Path, Path, bool]] = []
    database_rollbacks: list[tuple[Path, Path, bool, bool]] = []

    try:
        # Path swaps are same-filesystem renames because staging lives below
        # BASE_DIR. Each previous target remains available for rollback.
        for index, operation in enumerate(op for op in plan.operations if op.kind != "sqlite"):
            operation.target.parent.mkdir(parents=True, exist_ok=True)
            previous = rollback / f"path_{index}"
            existed = operation.target.exists() or operation.target.is_symlink()
            swapped.append((operation.target, previous, existed))
            if existed:
                os.replace(operation.target, previous)
            _replace_from_stage(operation.source, operation.target)

        # SQLite is restored through its backup API rather than replacing the
        # inode underneath live connections. Snapshot every old database first.
        sqlite_operations = [op for op in plan.operations if op.kind == "sqlite"]
        for index, operation in enumerate(sqlite_operations):
            operation.target.parent.mkdir(parents=True, exist_ok=True)
            previous = rollback / f"db_{index}.bak"
            existed = operation.target.exists()
            previous_was_sqlite = existed and _looks_like_sqlite(operation.target)
            if existed:
                if previous_was_sqlite:
                    _sqlite_backup(operation.target, previous)
                else:
                    shutil.copy2(operation.target, previous)
            database_rollbacks.append((operation.target, previous, existed, previous_was_sqlite))
        for operation in sqlite_operations:
            _restore_sqlite(operation.source, operation.target)

        if plan.config_snapshot is not None:
            _activate_config_snapshot(plan.config_snapshot)
    except Exception as exc:
        rollback_errors: list[str] = []
        for target, previous, existed, previous_was_sqlite in reversed(database_rollbacks):
            try:
                if existed:
                    if previous_was_sqlite:
                        _restore_sqlite(previous, target)
                    else:
                        _remove_path(target)
                        os.replace(previous, target)
                else:
                    _remove_path(target)
            except Exception as rollback_exc:
                rollback_errors.append(f"{target}: {rollback_exc}")
        for target, previous, existed in reversed(swapped):
            try:
                _remove_path(target)
                if existed:
                    os.replace(previous, target)
            except Exception as rollback_exc:
                rollback_errors.append(f"{target}: {rollback_exc}")
        if rollback_errors:
            raise RuntimeError(
                f"restore failed ({exc}); rollback also failed: {'; '.join(rollback_errors)}"
            ) from exc
        raise


def _restore_archive_sync(path: Path, *, dry_run: bool) -> dict[str, Any]:
    try:
        # Staging below BASE_DIR guarantees same-filesystem atomic path swaps.
        with tempfile.TemporaryDirectory(prefix=".cyrene_restore_", dir=BASE_DIR) as stage_name:
            stage = Path(stage_name)
            with ZipFile(path, "r") as zf:
                plan = _stage_archive(zf, stage)
            if not dry_run:
                _commit_stage(plan, stage)
            return {
                "ok": True,
                "restored": plan.restored,
                "errors": [],
                "version": plan.version,
                "dry_run": dry_run,
                "restart_required": not dry_run,
            }
    except Exception as exc:
        logger.exception("Restore failed")
        return _failure(str(exc), restored=[], errors=[str(exc)])


async def restore_backup(zip_path: str, *, dry_run: bool = False) -> dict[str, Any]:
    path = Path(zip_path).resolve()
    if not path.is_file():
        return _failure(f"backup file not found: {zip_path}")

    async with _backup_operation_lock:
        if dry_run:
            return await asyncio.to_thread(_restore_archive_sync, path, dry_run=True)
        return await _restore_with_locks(path)


async def _restore_with_locks(path: Path) -> dict[str, Any]:
    try:
        from apscheduler.schedulers.base import STATE_RUNNING
        from cyrene.runtime import scheduler as scheduler_module

        scheduler = getattr(scheduler_module, "_scheduler", None)
        scheduler_was_running = scheduler is not None and getattr(scheduler, "state", None) == STATE_RUNNING
    except Exception:
        scheduler = None
        scheduler_was_running = False

    from cyrene.config import DB_PATH
    from cyrene.runtime.run_coordinator import run_coordinator_for

    active_runs = run_coordinator_for(str(DB_PATH)).active_leases()
    if active_runs:
        return _failure(
            "backup restore requires all conversation and task runs to be idle",
            restored=[],
            errors=["active Agent runs must finish or be cancelled before restore"],
        )

    if scheduler_was_running:
        scheduler.pause()
        logger.info("Scheduler paused for restore")
    try:
        return await asyncio.to_thread(_restore_archive_sync, path, dry_run=False)
    finally:
        if scheduler_was_running:
            scheduler.resume()
            logger.info("Scheduler resumed after restore")


def list_backups() -> list[dict[str, Any]]:
    ensure_backup_dir()
    backups: list[dict[str, Any]] = []
    for path in _BACKUP_DIR.glob("cyrene_backup_*.zip"):
        try:
            item = {
                "name": path.name,
                "path": str(path),
                "size": path.stat().st_size,
                "modified": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            }
            backups.append(item)
        except OSError:
            continue
    return sorted(backups, key=lambda item: (item["modified"], item["name"]), reverse=True)


async def delete_backup(name: str) -> bool:
    if Path(name).name != name or not name.startswith("cyrene_backup_") or not name.endswith(".zip"):
        return False
    target = (_BACKUP_DIR / name).resolve()
    if target.parent != _BACKUP_DIR.resolve() or not target.is_file():
        return False
    try:
        target.unlink()
        return True
    except OSError:
        return False


class BackupDownloadError(ValueError):
    """A requested backup cannot be exposed for download."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class BackupRepository:
    """Public repository boundary for backup operations and archive files."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or _BACKUP_DIR

    def list(self) -> list[dict[str, Any]]:
        self.root.mkdir(parents=True, exist_ok=True)
        backups: list[dict[str, Any]] = []
        for path in self.root.glob("cyrene_backup_*.zip"):
            try:
                stat_result = path.stat()
            except OSError:
                continue
            backups.append({
                "name": path.name,
                "path": str(path),
                "size": stat_result.st_size,
                "modified": datetime.fromtimestamp(
                    stat_result.st_mtime,
                    tz=timezone.utc,
                ).isoformat(),
            })
        return sorted(
            backups,
            key=lambda item: (item["modified"], item["name"]),
            reverse=True,
        )

    async def export(self, target_path: str = "") -> dict[str, Any]:
        if target_path:
            return await export_backup(target_path=target_path)
        self.root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        generated = self.root / (
            f"cyrene_backup_{timestamp}_{uuid.uuid4().hex[:8]}.zip"
        )
        return await export_backup(target_path=generated)

    async def restore(self, path: str) -> dict[str, Any]:
        return await restore_backup(path)

    async def delete(self, name: str) -> bool:
        if Path(name).name != name or not name.startswith("cyrene_backup_"):
            return False
        if not name.endswith(".zip"):
            return False
        target = (self.root / name).resolve()
        if target.parent != self.root.resolve() or not target.is_file():
            return False
        try:
            target.unlink()
            return True
        except OSError:
            return False

    def download(self, name: str) -> Path:
        target = (self.root / name).resolve()
        if self.root.resolve() not in target.parents:
            raise BackupDownloadError("invalid backup path", 400)
        if not target.is_file():
            raise BackupDownloadError("backup not found", 404)
        return target
