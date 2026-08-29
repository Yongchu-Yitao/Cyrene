"""Validation and accessors for Workbench-specific plugin contributions."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from cyrene.core.plugin import ExtensionPoint, PluginPack, PluginScope

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SURFACE_ACTIVITIES = frozenset({
    "read", "write", "scan", "plan", "goal", "build", "run", "test", "preview",
})
_RESOURCE_KINDS = frozenset({
    "file", "directory", "plan", "goal", "execution", "endpoint", "artifact",
})
_ACTION_KINDS = frozenset({"build", "run", "test", "preview"})
_ACTION_OUTPUTS = frozenset({"diagnostics", "artifact", "endpoint", "terminal"})


def _identifier(value: Any, label: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"invalid {label}: {normalized!r}")
    return normalized


def _i18n(value: Any, label: str) -> Mapping[str, Mapping[str, Any]]:
    if not isinstance(value, Mapping) or any(
        not isinstance(item, Mapping) for item in value.values()
    ):
        raise TypeError(f"{label} i18n must map locales to objects")
    return {
        str(locale): dict(fields)
        for locale, fields in value.items()
    }


def _enum_tuple(value: Any, allowed: frozenset[str], label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{label} must be an array")
    normalized = tuple(dict.fromkeys(str(item or "").strip() for item in value))
    invalid = tuple(item for item in normalized if item not in allowed)
    if invalid:
        raise ValueError(f"{label} contains unsupported values: {', '.join(invalid)}")
    return normalized


def _extensions(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{label} must be an array")
    normalized = tuple(
        dict.fromkeys(str(item or "").strip().lower() for item in value)
    )
    if any(
        not item.startswith(".")
        or len(item) > 32
        or not re.fullmatch(r"\.[a-z0-9][a-z0-9+_.-]*", item)
        for item in normalized
    ):
        raise ValueError(f"{label} must contain lowercase file extensions")
    return normalized


@dataclass(frozen=True, slots=True)
class WorkbenchSurfaceRenderer:
    kind: Literal["native", "plugin_view"]
    id: str

    def __post_init__(self) -> None:
        if self.kind not in {"native", "plugin_view"}:
            raise ValueError("Workbench surface renderer kind must be native or plugin_view")
        object.__setattr__(self, "id", _identifier(self.id, "Workbench surface renderer id"))


@dataclass(frozen=True, slots=True)
class WorkbenchSurfaceContribution:
    id: str
    renderer: WorkbenchSurfaceRenderer
    title: str = ""
    i18n: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    accepted_activities: tuple[str, ...] = ()
    resource_kinds: tuple[str, ...] = ()
    priority: Literal["background", "normal", "urgent"] = "normal"
    lifetime: Literal["while-active", "run", "sticky"] = "while-active"
    preferred_side: Literal["left", "right", "either"] = "either"

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _identifier(self.id, "Workbench surface id"))
        if not isinstance(self.renderer, WorkbenchSurfaceRenderer):
            raise TypeError("Workbench surface renderer must be WorkbenchSurfaceRenderer")
        object.__setattr__(self, "title", str(self.title or "").strip())
        object.__setattr__(self, "i18n", _i18n(self.i18n, "Workbench surface"))
        object.__setattr__(self, "accepted_activities", _enum_tuple(
            self.accepted_activities, _SURFACE_ACTIVITIES, "Workbench surface accepted_activities"
        ))
        object.__setattr__(self, "resource_kinds", _enum_tuple(
            self.resource_kinds, _RESOURCE_KINDS, "Workbench surface resource_kinds"
        ))
        if self.priority not in {"background", "normal", "urgent"}:
            raise ValueError("Workbench surface priority is invalid")
        if self.lifetime not in {"while-active", "run", "sticky"}:
            raise ValueError("Workbench surface lifetime is invalid")
        if self.preferred_side not in {"left", "right", "either"}:
            raise ValueError("Workbench surface preferred_side is invalid")


@dataclass(frozen=True, slots=True)
class WorkspaceFileTypeContribution:
    id: str
    extensions: tuple[str, ...]
    mime_types: tuple[str, ...] = ()
    language_id: str = ""
    editable: bool = False
    default_surface: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _identifier(self.id, "Workspace file type id"))
        object.__setattr__(self, "extensions", _extensions(
            self.extensions, "Workspace file type extensions"
        ))
        if not self.extensions:
            raise ValueError("Workspace file type must declare at least one extension")
        if not isinstance(self.mime_types, (list, tuple)):
            raise TypeError("Workspace file type mime_types must be an array")
        object.__setattr__(self, "mime_types", tuple(dict.fromkeys(
            str(item or "").strip().lower() for item in self.mime_types if str(item or "").strip()
        )))
        object.__setattr__(self, "language_id", str(self.language_id or "").strip())
        if not isinstance(self.editable, bool):
            raise TypeError("Workspace file type editable must be a boolean")
        object.__setattr__(self, "default_surface", str(self.default_surface or "").strip())


@dataclass(frozen=True, slots=True)
class WorkspaceActionContribution:
    id: str
    kind: Literal["build", "run", "test", "preview"]
    method: str
    title: str = ""
    i18n: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    file_type_ids: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    marker_files: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    default_surface: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _identifier(self.id, "Workspace action id"))
        if self.kind not in _ACTION_KINDS:
            raise ValueError("Workspace action kind must be build, run, test, or preview")
        object.__setattr__(self, "method", _identifier(self.method, "Workspace action method"))
        object.__setattr__(self, "title", str(self.title or "").strip())
        object.__setattr__(self, "i18n", _i18n(self.i18n, "Workspace action"))
        if not isinstance(self.file_type_ids, (list, tuple)):
            raise TypeError("Workspace action file_type_ids must be an array")
        object.__setattr__(self, "file_type_ids", tuple(
            dict.fromkeys(str(item or "").strip() for item in self.file_type_ids if str(item or "").strip())
        ))
        object.__setattr__(self, "extensions", _extensions(
            self.extensions, "Workspace action extensions"
        ))
        if not isinstance(self.marker_files, (list, tuple)):
            raise TypeError("Workspace action marker_files must be an array")
        markers = tuple(dict.fromkeys(str(item or "").strip() for item in self.marker_files))
        if any(
            not item
            or Path(item).is_absolute()
            or ".." in Path(item.replace("\\", "/")).parts
            for item in markers
        ):
            raise ValueError("Workspace action marker_files must stay inside the workspace")
        object.__setattr__(self, "marker_files", markers)
        object.__setattr__(self, "outputs", _enum_tuple(
            self.outputs, _ACTION_OUTPUTS, "Workspace action outputs"
        ))
        object.__setattr__(self, "default_surface", str(self.default_surface or "").strip())


WORKBENCH_SURFACE = ExtensionPoint[WorkbenchSurfaceContribution](
    "cyrene.workbench.surface",
    PluginScope.APPLICATION,
    lambda value: isinstance(value, WorkbenchSurfaceContribution),
)
WORKSPACE_FILE_TYPE = ExtensionPoint[WorkspaceFileTypeContribution](
    "cyrene.workspace.file_type",
    PluginScope.APPLICATION,
    lambda value: isinstance(value, WorkspaceFileTypeContribution),
)
WORKSPACE_ACTION = ExtensionPoint[WorkspaceActionContribution](
    "cyrene.workspace.action",
    PluginScope.APPLICATION,
    lambda value: isinstance(value, WorkspaceActionContribution),
)


def frontend_views(pack: PluginPack) -> tuple[Mapping[str, Any], ...]:
    return tuple(pack.metadata.get("frontend_views", ()))


def project_tools(pack: PluginPack) -> tuple[Mapping[str, Any], ...]:
    return tuple(pack.metadata.get("project_tools", ()))


def workbench_surfaces(pack: PluginPack) -> tuple[WorkbenchSurfaceContribution, ...]:
    return pack.extensions.values(WORKBENCH_SURFACE)


def workspace_file_types(pack: PluginPack) -> tuple[WorkspaceFileTypeContribution, ...]:
    return pack.extensions.values(WORKSPACE_FILE_TYPE)


def workspace_actions(pack: PluginPack) -> tuple[WorkspaceActionContribution, ...]:
    return pack.extensions.values(WORKSPACE_ACTION)


def _canonical_id(pack: PluginPack, local_id: str) -> str:
    return f"{pack.id}/{local_id}"


def serialize_workbench_surface(
    pack: PluginPack,
    value: WorkbenchSurfaceContribution,
) -> dict[str, Any]:
    return {
        "id": _canonical_id(pack, value.id),
        "local_id": value.id,
        "pack_id": pack.id,
        "title": value.title,
        "i18n": dict(value.i18n),
        "renderer": {"kind": value.renderer.kind, "id": value.renderer.id},
        "accepted_activities": list(value.accepted_activities),
        "resource_kinds": list(value.resource_kinds),
        "priority": value.priority,
        "lifetime": value.lifetime,
        "preferred_side": value.preferred_side,
    }


def serialize_workspace_file_type(
    pack: PluginPack,
    value: WorkspaceFileTypeContribution,
) -> dict[str, Any]:
    return {
        "id": _canonical_id(pack, value.id),
        "local_id": value.id,
        "pack_id": pack.id,
        "extensions": list(value.extensions),
        "mime_types": list(value.mime_types),
        "language_id": value.language_id,
        "editable": value.editable,
        "default_surface": value.default_surface,
    }


def serialize_workspace_action(
    pack: PluginPack,
    value: WorkspaceActionContribution,
) -> dict[str, Any]:
    return {
        "id": _canonical_id(pack, value.id),
        "local_id": value.id,
        "pack_id": pack.id,
        "kind": value.kind,
        "method": value.method,
        "title": value.title,
        "i18n": dict(value.i18n),
        "applies_to": {
            "file_type_ids": list(value.file_type_ids),
            "extensions": list(value.extensions),
            "marker_files": list(value.marker_files),
        },
        "outputs": list(value.outputs),
        "default_surface": value.default_surface,
    }


def validate_workbench_contributions(pack: PluginPack) -> None:
    """Validate metadata interpreted by the Workbench application adapter."""

    views = pack.metadata.get("frontend_views", ())
    tools = pack.metadata.get("project_tools", ())
    if not isinstance(views, (list, tuple)):
        raise TypeError("Plugin pack metadata.frontend_views must be an array")
    if not isinstance(tools, (list, tuple)):
        raise TypeError("Plugin pack metadata.project_tools must be an array")
    view_ids: set[str] = set()
    for raw in views:
        if not isinstance(raw, Mapping):
            raise TypeError("Plugin frontend view must be an object")
        view_id = str(raw.get("id") or "").strip()
        if not _IDENTIFIER.fullmatch(view_id):
            raise ValueError(f"invalid Plugin frontend view id: {view_id!r}")
        if view_id in view_ids:
            raise ValueError(f"duplicate Plugin frontend view id: {view_id}")
        view_ids.add(view_id)
        entry = str(raw.get("entry") or "").strip().replace("\\", "/")
        entry_path = Path(entry)
        if not entry or entry_path.is_absolute() or ".." in entry_path.parts:
            raise ValueError(
                f"Plugin frontend view entry must stay inside the pack: {entry!r}"
            )
        item_i18n = raw.get("i18n", {})
        if not isinstance(item_i18n, Mapping) or any(
            not isinstance(value, Mapping) for value in item_i18n.values()
        ):
            raise TypeError("Plugin frontend view i18n must map locales to objects")
    tool_ids: set[str] = set()
    for raw in tools:
        if not isinstance(raw, Mapping):
            raise TypeError("Plugin project tool must be an object")
        tool_id = str(raw.get("id") or "").strip()
        if not _IDENTIFIER.fullmatch(tool_id):
            raise ValueError(f"invalid Plugin project tool id: {tool_id!r}")
        if tool_id in tool_ids:
            raise ValueError(f"duplicate Plugin project tool id: {tool_id}")
        tool_ids.add(tool_id)
        view_id = str(raw.get("view") or "").strip()
        if view_id not in view_ids:
            raise ValueError(
                f"Plugin project tool {tool_id} references missing view: {view_id}"
            )
        item_i18n = raw.get("i18n", {})
        if not isinstance(item_i18n, Mapping) or any(
            not isinstance(value, Mapping) for value in item_i18n.values()
        ):
            raise TypeError("Plugin project tool i18n must map locales to objects")

    surface_values = workbench_surfaces(pack)
    surface_ids = [item.id for item in surface_values]
    if len(surface_ids) != len(set(surface_ids)):
        raise ValueError(f"Plugin pack contains duplicate Workbench surface ids: {pack.id}")
    for surface in surface_values:
        if surface.renderer.kind == "plugin_view" and surface.renderer.id not in view_ids:
            raise ValueError(
                f"Workbench surface {surface.id} references missing view: {surface.renderer.id}"
            )
    file_type_values = workspace_file_types(pack)
    file_type_ids = [item.id for item in file_type_values]
    if len(file_type_ids) != len(set(file_type_ids)):
        raise ValueError(f"Plugin pack contains duplicate Workspace file type ids: {pack.id}")
    action_values = workspace_actions(pack)
    action_ids = [item.id for item in action_values]
    if len(action_ids) != len(set(action_ids)):
        raise ValueError(f"Plugin pack contains duplicate Workspace action ids: {pack.id}")


__all__ = [
    "WORKBENCH_SURFACE",
    "WORKSPACE_ACTION",
    "WORKSPACE_FILE_TYPE",
    "WorkbenchSurfaceContribution",
    "WorkbenchSurfaceRenderer",
    "WorkspaceActionContribution",
    "WorkspaceFileTypeContribution",
    "frontend_views",
    "project_tools",
    "serialize_workbench_surface",
    "serialize_workspace_action",
    "serialize_workspace_file_type",
    "validate_workbench_contributions",
    "workbench_surfaces",
    "workspace_actions",
    "workspace_file_types",
]
