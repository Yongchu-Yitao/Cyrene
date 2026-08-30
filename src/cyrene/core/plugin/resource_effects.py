"""Declarative resource effects attached to executable Plugins.

The core validates metadata and provides workspace-safe location projection.
Product adapters remain responsible for presentation and every UI side effect.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ResourceKind = Literal["file", "directory"]
ResourceAccess = Literal["read", "write", "scan", "execute"]
ResourceEffectPhase = Literal["started", "completed", "both"]

_ARGUMENT_SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}$")
_RESOURCE_KINDS = frozenset({"file", "directory"})
_RESOURCE_ACCESS = frozenset({"read", "write", "scan", "execute"})
_RESOURCE_PHASES = frozenset({"started", "completed", "both"})

RESOURCE_REVEAL_ARGUMENT = "reveal"
RESOURCE_REVEAL_DESCRIPTION = (
    "Set true when the user explicitly asked to edit, open, show, or view this "
    "exact file, or inspect this exact directory. If a requested edit is already "
    "satisfied, still set true to complete the file-display request. Omit it for "
    "incidental reads, searches, dependency analysis, and background scans."
)


@dataclass(frozen=True, slots=True)
class PluginResourceEffect:
    """One resource location derived from a Plugin argument object."""

    argument_path: tuple[str, ...]
    kind: ResourceKind
    access: ResourceAccess
    phase: ResourceEffectPhase = "both"

    def as_metadata(self) -> dict[str, Any]:
        return {
            "argument_path": self.argument_path,
            "kind": self.kind,
            "access": self.access,
            "phase": self.phase,
        }


def normalize_resource_effects(value: Any) -> tuple[PluginResourceEffect, ...]:
    """Validate JSON-like Plugin metadata into immutable effects."""

    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise TypeError("Plugin metadata.resource_effects must be an array")
    effects: list[PluginResourceEffect] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            raise TypeError("Plugin resource effect must be an object")
        raw_path = raw.get("argument_path")
        if (
            not isinstance(raw_path, Sequence)
            or isinstance(raw_path, (str, bytes, bytearray))
        ):
            raise TypeError("Plugin resource effect argument_path must be an array")
        argument_path = tuple(str(item or "").strip() for item in raw_path)
        if not 1 <= len(argument_path) <= 4 or any(
            not _ARGUMENT_SEGMENT.fullmatch(item) for item in argument_path
        ):
            raise ValueError(
                "Plugin resource effect argument_path must contain 1 to 4 valid segments"
            )
        kind = str(raw.get("kind") or "").strip()
        if kind not in _RESOURCE_KINDS:
            raise ValueError(
                "Plugin resource effect kind must be file or directory"
            )
        access = str(raw.get("access") or "").strip()
        if access not in _RESOURCE_ACCESS:
            raise ValueError(
                "Plugin resource effect access must be read, write, scan, or execute"
            )
        phase = str(raw.get("phase") or "both").strip()
        if phase not in _RESOURCE_PHASES:
            raise ValueError(
                "Plugin resource effect phase must be started, completed, or both"
            )
        effects.append(
            PluginResourceEffect(
                argument_path=argument_path,
                kind=kind,  # type: ignore[arg-type]
                access=access,  # type: ignore[arg-type]
                phase=phase,  # type: ignore[arg-type]
            )
        )
    return tuple(effects)


def resolve_resource_effect_values(
    effects: Sequence[PluginResourceEffect],
    arguments: Mapping[str, Any],
    *,
    phase: Literal["started", "completed"],
) -> tuple[dict[str, Any], ...]:
    """Resolve declared argument paths without interpreting them as filesystem paths."""

    resolved: list[dict[str, Any]] = []
    for effect in effects:
        if effect.phase not in {phase, "both"}:
            continue
        value: Any = arguments
        for segment in effect.argument_path:
            if not isinstance(value, Mapping) or segment not in value:
                value = None
                break
            value = value[segment]
        if not isinstance(value, str) or not value.strip():
            continue
        resolved.append(
            {
                "value": value.strip(),
                "kind": effect.kind,
                "access": effect.access,
                "phase": phase,
            }
        )
    return tuple(resolved)


def resource_effect_input_schema(
    schema: Mapping[str, Any],
    *,
    effects: Sequence[PluginResourceEffect],
    allow_reveal: bool,
) -> dict[str, Any]:
    """Return a model-facing schema with the host-owned reveal hint."""

    prepared = dict(schema)
    if not effects or not allow_reveal:
        return prepared
    properties = dict(prepared.get("properties") or {})
    if RESOURCE_REVEAL_ARGUMENT in properties:
        raise ValueError(
            "Plugin resource tools reserve the reveal input property for the host"
        )
    properties[RESOURCE_REVEAL_ARGUMENT] = {
        "type": "boolean",
        "description": RESOURCE_REVEAL_DESCRIPTION,
    }
    prepared["properties"] = properties
    return prepared


def split_resource_reveal(
    arguments: Mapping[str, Any],
    *,
    effects: Sequence[PluginResourceEffect],
    allow_reveal: bool,
) -> tuple[dict[str, Any], bool]:
    """Strip the host-only reveal hint before Plugin validation and execution."""

    prepared = dict(arguments)
    if not effects or not allow_reveal:
        return prepared, False
    raw = prepared.pop(RESOURCE_REVEAL_ARGUMENT, False)
    if not isinstance(raw, bool):
        raise TypeError("Plugin resource reveal must be a boolean")
    return prepared, raw


def workspace_resource_locations(
    effects: Sequence[PluginResourceEffect],
    arguments: Mapping[str, Any],
    *,
    workspace: Path,
    project_id: str,
    phase: Literal["started", "completed"],
) -> tuple[dict[str, Any], ...]:
    """Resolve effects into canonical workspace-relative presentation locations."""

    root = Path(workspace).expanduser().resolve()
    locations: list[dict[str, Any]] = []
    for effect in resolve_resource_effect_values(effects, arguments, phase=phase):
        raw_path = Path(str(effect["value"])).expanduser()
        candidate = raw_path.resolve() if raw_path.is_absolute() else (root / raw_path).resolve()
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            continue
        normalized = relative.as_posix()
        if not normalized or normalized == ".":
            normalized = "."
        locations.append({
            "kind": effect["kind"],
            "access": effect["access"],
            "phase": phase,
            "projectId": str(project_id or ""),
            "path": normalized,
        })
    return tuple(locations)


__all__ = [
    "PluginResourceEffect",
    "RESOURCE_REVEAL_ARGUMENT",
    "RESOURCE_REVEAL_DESCRIPTION",
    "ResourceAccess",
    "ResourceEffectPhase",
    "ResourceKind",
    "normalize_resource_effects",
    "resource_effect_input_schema",
    "resolve_resource_effect_values",
    "split_resource_reveal",
    "workspace_resource_locations",
]
