"""Validation for the deliberately small Cyrene plugin manifest."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MANIFEST_NAMES = ("plugin.json", "cyrene.plugin.json")


class PluginManifestError(ValueError):
    pass


def require_plugin_id(value: Any) -> str:
    plugin_id = str(value or "").strip()
    if not PLUGIN_ID_RE.fullmatch(plugin_id):
        raise PluginManifestError(
            "plugin id must start with a letter or number and contain only "
            "letters, numbers, '.', '_' or '-'"
        )
    return plugin_id


def _relative_file(root: Path, raw: Any, *, field: str, required: bool) -> str:
    value = str(raw or "").strip().replace("\\", "/")
    if not value:
        if required:
            raise PluginManifestError(f"{field} is required")
        return ""
    candidate = (root / value).resolve(strict=False)
    resolved_root = root.resolve()
    if candidate == resolved_root or resolved_root not in candidate.parents:
        raise PluginManifestError(f"{field} must stay inside the plugin package")
    if not candidate.is_file():
        raise PluginManifestError(f"{field} does not exist: {value}")
    return candidate.relative_to(resolved_root).as_posix()


@dataclass(frozen=True, slots=True)
class PluginManifest:
    id: str
    name: str
    version: str
    description: str
    backend_entry: str
    frontend_entry: str
    contributions: tuple[dict[str, Any], ...]
    raw: dict[str, Any]

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "backend": {"type": "python", "entry": self.backend_entry}
            if self.backend_entry
            else None,
            "frontend": {"mode": "iframe", "entry": self.frontend_entry}
            if self.frontend_entry
            else None,
        }


def find_manifest_file(root: Path) -> Path:
    for name in MANIFEST_NAMES:
        candidate = root / name
        if candidate.is_file():
            return candidate
    raise PluginManifestError("plugin package requires plugin.json")


def load_manifest(root: Path) -> PluginManifest:
    root = Path(root).resolve()
    path = find_manifest_file(root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PluginManifestError(f"invalid plugin manifest: {exc}") from exc
    if not isinstance(raw, dict):
        raise PluginManifestError("plugin manifest must be a JSON object")
    api_version = int(raw.get("apiVersion") or raw.get("api_version") or 1)
    if api_version != 1:
        raise PluginManifestError(f"unsupported plugin apiVersion: {api_version}")
    plugin_id = require_plugin_id(raw.get("id"))
    name = str(raw.get("name") or plugin_id).strip() or plugin_id
    version = str(raw.get("version") or "0.0.0").strip() or "0.0.0"
    backend = raw.get("backend") if isinstance(raw.get("backend"), dict) else {}
    frontend = raw.get("frontend") if isinstance(raw.get("frontend"), dict) else {}
    backend_type = str(backend.get("type") or ("python" if backend else "")).strip()
    if backend_type and backend_type != "python":
        raise PluginManifestError("only python plugin backends are supported")
    frontend_mode = str(frontend.get("mode") or ("iframe" if frontend else "")).strip()
    if frontend_mode and frontend_mode != "iframe":
        raise PluginManifestError("plugin frontend mode must be iframe")
    backend_entry = _relative_file(
        root, backend.get("entry"), field="backend.entry", required=bool(backend)
    )
    frontend_entry = _relative_file(
        root, frontend.get("entry"), field="frontend.entry", required=bool(frontend)
    )
    raw_contributions = raw.get("contributes", raw.get("contributions", []))
    if not isinstance(raw_contributions, list):
        raise PluginManifestError("contributes must be an array")
    contributions: list[dict[str, Any]] = []
    for item in raw_contributions:
        if not isinstance(item, dict):
            raise PluginManifestError("each contribution must be an object")
        point = str(item.get("point") or "").strip()
        contribution_id = str(item.get("id") or "").strip()
        if not point or not contribution_id:
            raise PluginManifestError("each contribution requires point and id")
        contributions.append(dict(item))
    if not backend_entry and not frontend_entry and not contributions:
        raise PluginManifestError("plugin has no backend, frontend or contributions")
    return PluginManifest(
        id=plugin_id,
        name=name,
        version=version,
        description=str(raw.get("description") or "").strip(),
        backend_entry=backend_entry,
        frontend_entry=frontend_entry,
        contributions=tuple(contributions),
        raw=raw,
    )


__all__ = [
    "MANIFEST_NAMES",
    "PLUGIN_ID_RE",
    "PluginManifest",
    "PluginManifestError",
    "find_manifest_file",
    "load_manifest",
    "require_plugin_id",
]
