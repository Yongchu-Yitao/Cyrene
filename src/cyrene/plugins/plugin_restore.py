"""Explicit, backed-up replacement of one editable bundled contribution.

Unlike normal seeding, this operation discards edits in the selected entry.
Callers must present the plan before applying it and coordinate live Plugin
shutdown/reload. This module only handles the on-disk transaction.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from . import native_tools as native


@dataclass(frozen=True)
class BuiltinPluginRestorePlan:
    directory: Path
    contribution_name: str
    fingerprint: str
    replaced_files: tuple[str, ...]
    bundled_files: tuple[str, ...]


@dataclass(frozen=True)
class BuiltinPluginRestoreResult:
    target: Path
    backup_directory: Path


def _snapshot(path: Path) -> dict[str, str]:
    if path.is_symlink():
        raise ValueError(f"Plugin restore does not follow symbolic links: {path}")
    if not path.exists():
        return {}
    entries = [path, *sorted(path.rglob("*"))] if path.is_dir() else [path]
    result = {}
    for item in entries:
        if item.is_symlink() or not (item.is_file() or item.is_dir()):
            raise ValueError(f"Unsupported Plugin restore entry: {item}")
        relative = item.relative_to(path).as_posix()
        result[relative] = sha256(item.read_bytes()).hexdigest() if item.is_file() else "directory"
    return result


def _inputs(directory: str | Path, name: str):
    root = Path(directory).expanduser().resolve()
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("Plugin contribution name must be one top-level entry")
    canonical = {
        key: value for key, value in native._collect_canonical_files().items()
        if Path(key).parts[0] == name
    }
    if not canonical:
        raise ValueError(f"Bundled Plugin contribution is unavailable: {name}")
    target = root / name
    snapshot = _snapshot(target)
    manifest = root / native._UPSTREAM_MANIFEST_RELATIVE
    _snapshot(manifest)
    raw = manifest.read_bytes() if manifest.exists() else None
    payload = json.loads(raw) if raw is not None else {"version": 1, "files": {}}
    if (
        not isinstance(payload, dict) or payload.get("version") != 1
        or not isinstance(payload.get("files"), dict)
        or not isinstance(payload.get("deleted", []), list)
    ):
        raise ValueError("Cannot restore a Plugin with an invalid upstream manifest")
    if any(not key or not isinstance(value, str) for key, value in payload["files"].items()):
        raise ValueError("Cannot restore a Plugin with invalid upstream hashes")
    fingerprint = sha256(json.dumps({
        "entry": snapshot,
        "manifest": sha256(raw).hexdigest() if raw is not None else None,
        "bundled": {key: sha256(value).hexdigest() for key, value in canonical.items()},
    }, sort_keys=True).encode()).hexdigest()
    return root, target, canonical, snapshot, manifest, raw, payload, fingerprint


def plan_builtin_plugin_restore(
    directory: str | Path, contribution_name: str,
) -> BuiltinPluginRestorePlan:
    """Read a target and release baseline without loading code or writing files."""
    with native._SEED_LOCK:
        root, _, canonical, snapshot, _, _, _, fingerprint = _inputs(directory, contribution_name)
        return BuiltinPluginRestorePlan(
            root, contribution_name, fingerprint, tuple(snapshot), tuple(canonical),
        )


def apply_builtin_plugin_restore(plan: BuiltinPluginRestorePlan) -> BuiltinPluginRestoreResult:
    """Replace exactly one entry; preserve its previous bytes and manifest.

    A stale plan is rejected. Ordinary transaction errors roll back the target;
    backups remain available if rollback itself fails or the process is killed.
    """
    with native._SEED_LOCK:
        root, target, canonical, _, manifest, raw, payload, fingerprint = _inputs(
            plan.directory, plan.contribution_name,
        )
        if fingerprint != plan.fingerprint:
            raise ValueError("Plugin restore plan is stale; generate a new plan")
        root.mkdir(parents=True, exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix=".cyrene-plugin-restore-", dir=root.parent))
        staged = backup / "staged" / plan.contribution_name
        for relative, content in canonical.items():
            output = backup / "staged" / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(content)
        if raw is not None:
            (backup / "upstream-manifest.json").write_bytes(raw)
        (backup / "restore.json").write_text(json.dumps({
            "target": str(target), "fingerprint": fingerprint,
            "manifest_existed": raw is not None,
            "target_existed": target.exists(),
        }), encoding="utf-8")
        # Staging can take time; don't discard edits made while it was built.
        if _inputs(root, plan.contribution_name)[-1] != fingerprint:
            raise ValueError("Plugin restore plan changed while staging")
        original = backup / "original"
        moved = installed = False
        try:
            if target.exists():
                target.replace(original)
                moved = True
            staged.replace(target)
            installed = True
            payload["files"] = {
                key: value for key, value in payload["files"].items()
                if Path(key).parts[0] != plan.contribution_name
            }
            payload["files"].update({key: sha256(value).hexdigest() for key, value in canonical.items()})
            payload["deleted"] = [item for item in payload.get("deleted", []) if item != plan.contribution_name]
            native._atomic_write(manifest, (json.dumps(payload, indent=2) + "\n").encode())
        except Exception:
            if installed:
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            if moved:
                original.replace(target)
            raise
        return BuiltinPluginRestoreResult(target, backup)


def rollback_builtin_plugin_restore(plan: BuiltinPluginRestorePlan, backup_directory: Path) -> None:
    """Restore retained bytes only while the post-repair fingerprint matches."""
    with native._SEED_LOCK:
        root, target, _, _, manifest, _, _, fingerprint = _inputs(plan.directory, plan.contribution_name)
        if fingerprint != plan.fingerprint:
            raise ValueError("Plugin changed after repair; automatic rollback is unsafe")
        backup = Path(backup_directory).resolve()
        metadata = json.loads((backup / "restore.json").read_text())
        if metadata.get("target") != str(target):
            raise ValueError("Backup belongs to a different target")
        original = backup / "original"
        _snapshot(original)
        if metadata["target_existed"] and not original.exists():
            raise ValueError("Original Plugin backup is missing")
        previous_manifest = (backup / "upstream-manifest.json").read_bytes() if metadata["manifest_existed"] else None
        with tempfile.TemporaryDirectory(prefix=".rollback-", dir=root.parent) as temporary:
            stage = Path(temporary)
            candidate = stage / "original"
            if original.is_dir():
                shutil.copytree(original, candidate)
            elif original.is_file():
                shutil.copy2(original, candidate)
            target.replace(stage / "current")
            try:
                if candidate.exists():
                    candidate.replace(target)
                if previous_manifest is not None:
                    native._atomic_write(manifest, previous_manifest)
                else:
                    manifest.unlink(missing_ok=True)
            except Exception:
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
                (stage / "current").replace(target)
                raise


__all__ = [
    "BuiltinPluginRestorePlan", "BuiltinPluginRestoreResult",
    "plan_builtin_plugin_restore", "apply_builtin_plugin_restore",
    "rollback_builtin_plugin_restore",
]
