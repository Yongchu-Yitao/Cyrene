"""Declarative resource effects attached to executable Plugins.

The core validates and resolves argument values only.  Product adapters remain
responsible for workspace containment, presentation, and any UI side effects.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

ResourceKind = Literal["file", "directory"]
ResourceAccess = Literal["read", "write", "scan", "execute"]
ResourceEffectPhase = Literal["started", "completed", "both"]

_ARGUMENT_SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}$")
_RESOURCE_KINDS = frozenset({"file", "directory"})
_RESOURCE_ACCESS = frozenset({"read", "write", "scan", "execute"})
_RESOURCE_PHASES = frozenset({"started", "completed", "both"})


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


__all__ = [
    "PluginResourceEffect",
    "ResourceAccess",
    "ResourceEffectPhase",
    "ResourceKind",
    "normalize_resource_effects",
    "resolve_resource_effect_values",
]
