"""Small, runtime-independent Plugin value objects."""

from __future__ import annotations

import re
from collections.abc import (
    Awaitable,
    Callable,
    Mapping,
    MutableMapping,
    MutableSequence,
)
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypeAlias
from uuid import uuid4

from .validation import check_input_schema

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _localized_i18n_fields(
    translations: Any,
    locale: str,
) -> Mapping[str, Any]:
    """Resolve a locale tag while preserving an exact authored override."""

    if not isinstance(translations, Mapping):
        return {}
    requested = str(locale or "").strip()
    normalized = requested.replace("_", "-").lower()
    language = normalized.split("-", 1)[0]
    candidates = tuple(
        dict.fromkeys(
            candidate
            for candidate in (requested, normalized, language)
            if candidate
        )
    )
    for candidate in candidates:
        value = translations.get(candidate)
        if isinstance(value, Mapping):
            return value
    for authored_locale, value in translations.items():
        authored = str(authored_locale or "").replace("_", "-").lower()
        if authored in {normalized, language} and isinstance(value, Mapping):
            return value
    return {}


@dataclass(frozen=True, slots=True)
class PluginContext:
    """Handles a Plugin may use while deciding how to apply its result.

    The Plugin system does not interpret or mutate these values. In particular,
    mounting a result into ``tree`` remains the Plugin's responsibility.
    """

    workspace: Path | None = None
    tree: Any = None
    tree_id: str | None = None
    node_id: str | None = None
    hooks: Any = None
    data: Mapping[str, Any] = field(default_factory=dict)
    services: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PluginSetupContext:
    """Session-local resources exposed while a PluginPack is being attached.

    Setup callbacks run synchronously when an :class:`AgentSession` opens. They
    may publish services for the pack's executable Plugins and bind tree-local
    Hooks. Expensive or asynchronous work belongs in those Hooks, not setup.
    """

    data_directory: Path
    plugin_directory: Path
    workspace: Path
    tree: Any
    tree_id: str
    root_id: str
    hooks: Any
    data: Mapping[str, Any]
    services: MutableMapping[str, Any]
    agent_id: str = "main"
    parent_agent_id: str = ""

    def provide(self, name: str, service: Any, *, replace: bool = False) -> None:
        """Publish one named service to every Plugin call in this session."""

        normalized = str(name or "").strip()
        if not normalized:
            raise ValueError("Plugin service name cannot be empty")
        if normalized in self.services and not replace:
            raise ValueError(f"Plugin service already exists: {normalized}")
        self.services[normalized] = service


PluginHandler: TypeAlias = Callable[
    [dict[str, Any], PluginContext],
    Any | Awaitable[Any],
]
PluginSetupHandler: TypeAlias = Callable[[PluginSetupContext], None]
PluginLifecycleHandler: TypeAlias = Callable[[], Any | Awaitable[Any]]
PluginSearchHandler: TypeAlias = Callable[[str, int], Any | Awaitable[Any]]
PluginFrontendHandler: TypeAlias = Callable[
    [Any, Mapping[str, Any]],
    Any | Awaitable[Any],
]


@dataclass(slots=True)
class PluginApplicationContext:
    """Process-level capabilities exposed while a PluginPack is attached.

    This is the application counterpart to :class:`PluginSetupContext`.  A
    pack may install routes on its isolated child router, publish services for
    host integrations, register lifecycle callbacks, expose Workbench modules,
    and contribute global-search providers.  The host commits these values only
    after the setup callback returns successfully.
    """

    app: Any
    router: Any
    bot: Any
    db_path: str
    data_directory: Path
    plugin_directory: Path
    services: MutableMapping[str, Any]
    frontend_modules: MutableSequence[str]
    search_providers: MutableMapping[str, PluginSearchHandler]
    startup_handlers: MutableSequence[PluginLifecycleHandler]
    shutdown_handlers: MutableSequence[PluginLifecycleHandler]
    frontend_methods: MutableMapping[str, PluginFrontendHandler] = field(
        default_factory=dict
    )
    registry: Any | None = None

    @staticmethod
    def _name(value: str, label: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{label} cannot be empty")
        return normalized

    def provide(self, name: str, service: Any, *, replace: bool = False) -> None:
        normalized = self._name(name, "Plugin application service name")
        if normalized in self.services and not replace:
            raise ValueError(f"Plugin application service already exists: {normalized}")
        self.services[normalized] = service

    def expose_frontend(self, module: str) -> None:
        normalized = self._name(module, "Plugin frontend module")
        if normalized not in self.frontend_modules:
            self.frontend_modules.append(normalized)

    def provide_frontend_method(
        self,
        name: str,
        handler: PluginFrontendHandler,
        *,
        replace: bool = False,
    ) -> None:
        """Expose one pack-scoped JSON RPC method to sandboxed Plugin views."""

        normalized = self._name(name, "Plugin frontend method name")
        if not callable(handler):
            raise TypeError("Plugin frontend method handler must be callable")
        if normalized in self.frontend_methods and not replace:
            raise ValueError(f"Plugin frontend method already exists: {normalized}")
        self.frontend_methods[normalized] = handler

    def provide_search(
        self,
        result_type: str,
        handler: PluginSearchHandler,
        *,
        replace: bool = False,
    ) -> None:
        normalized = self._name(result_type, "Plugin search result type")
        if not callable(handler):
            raise TypeError("Plugin search handler must be callable")
        if normalized in self.search_providers and not replace:
            raise ValueError(f"Plugin search provider already exists: {normalized}")
        self.search_providers[normalized] = handler

    def on_startup(self, handler: PluginLifecycleHandler) -> None:
        if not callable(handler):
            raise TypeError("Plugin startup handler must be callable")
        self.startup_handlers.append(handler)

    def on_shutdown(self, handler: PluginLifecycleHandler) -> None:
        if not callable(handler):
            raise TypeError("Plugin shutdown handler must be callable")
        self.shutdown_handlers.append(handler)


PluginApplicationSetupHandler: TypeAlias = Callable[[PluginApplicationContext], None]


@dataclass(frozen=True, slots=True)
class Plugin:
    """One executable component that returns an opaque result."""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    handler: PluginHandler = field(repr=False, compare=False)
    kind: Literal["tool", "model"] = "tool"
    allow_parallel: bool = False
    timeout_seconds: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        if not _IDENTIFIER.fullmatch(name):
            raise ValueError(f"invalid Plugin name: {self.name!r}")
        if not callable(self.handler):
            raise TypeError("Plugin handler must be callable")
        if self.kind not in {"tool", "model"}:
            raise ValueError("Plugin kind must be 'tool' or 'model'")
        if not isinstance(self.allow_parallel, bool):
            raise TypeError("Plugin allow_parallel must be a boolean")
        if self.timeout_seconds is not None:
            if isinstance(self.timeout_seconds, bool) or not isinstance(
                self.timeout_seconds, (int, float)
            ):
                raise TypeError("Plugin timeout_seconds must be a number or None")
            if float(self.timeout_seconds) <= 0:
                raise ValueError("Plugin timeout_seconds must be greater than zero")
        schema = deepcopy(dict(self.input_schema))
        if not isinstance(self.metadata, Mapping):
            raise TypeError("Plugin metadata must be a mapping")
        metadata = deepcopy(dict(self.metadata))
        model_visible = metadata.get("model_visible", True)
        if not isinstance(model_visible, bool):
            raise TypeError("Plugin metadata.model_visible must be a boolean")
        main_only = metadata.get("main_only", False)
        if not isinstance(main_only, bool):
            raise TypeError("Plugin metadata.main_only must be a boolean")
        required = metadata.get("required", False)
        if not isinstance(required, bool):
            raise TypeError("Plugin metadata.required must be a boolean")
        exposure = metadata.get("agent_exposure")
        if exposure is not None and exposure not in {
            "direct",
            "discoverable",
            "hidden",
        }:
            raise ValueError(
                "Plugin metadata.agent_exposure must be direct, discoverable, or hidden"
            )
        translations = metadata.get("i18n", {})
        if not isinstance(translations, Mapping) or any(
            not isinstance(value, Mapping) for value in translations.values()
        ):
            raise TypeError("Plugin metadata.i18n must map locales to objects")
        if schema.get("type", "object") != "object":
            raise ValueError("Plugin input_schema must describe an object")
        check_input_schema(schema)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", str(self.description).strip())
        object.__setattr__(self, "input_schema", schema)
        object.__setattr__(self, "metadata", metadata)
        if self.timeout_seconds is not None:
            object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))

    @property
    def model_visible(self) -> bool:
        """Whether model-facing discovery may list, describe, or invoke it."""

        return bool(self.metadata.get("model_visible", True))

    @property
    def main_only(self) -> bool:
        """Whether only the main Agent may discover or execute this Plugin."""

        return bool(self.metadata.get("main_only", False))

    @property
    def required(self) -> bool:
        """Whether the Plugin is a fixed capability while its pack is enabled."""

        return bool(self.metadata.get("required", False))

    @property
    def canonical_name(self) -> str:
        """Stable identity retained when the user edits the model-facing name."""

        return str(self.metadata.get("canonical_name") or self.name)

    @property
    def agent_exposure(self) -> str:
        """How the Agent receives this tool: direct, discoverable, or hidden."""

        if not self.model_visible:
            return "hidden"
        return str(self.metadata.get("agent_exposure") or "discoverable")

    def localized(self, locale: str) -> tuple[str, str]:
        """Return localized authored metadata with source text as fallback."""

        translations = self.metadata.get("i18n", {})
        value = _localized_i18n_fields(translations, locale)
        return (
            str(value.get("name") or self.name),
            str(
                self.description
                if self.metadata.get("customized_description") is True
                else value.get("description") or self.description
            ),
        )

    def tool_definition(self) -> dict[str, Any]:
        """Return a fresh function definition suitable for a model call."""

        if self.kind != "tool":
            raise ValueError(f"model Plugin has no tool definition: {self.name}")

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": deepcopy(dict(self.input_schema)),
            },
        }


@dataclass(frozen=True, slots=True)
class PluginPack:
    """A user-visible directory grouping related Plugins."""

    id: str
    description: str
    plugins: tuple[Plugin, ...]
    setup: PluginSetupHandler | None = field(default=None, repr=False, compare=False)
    application_setup: PluginApplicationSetupHandler | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        pack_id = str(self.id).strip()
        if not _IDENTIFIER.fullmatch(pack_id):
            raise ValueError(f"invalid Plugin pack id: {self.id!r}")
        plugins = tuple(self.plugins)
        names = [plugin.name for plugin in plugins]
        if len(names) != len(set(names)):
            raise ValueError(f"Plugin pack contains duplicate names: {pack_id}")
        if self.setup is not None and not callable(self.setup):
            raise TypeError("Plugin pack setup must be callable or None")
        if self.application_setup is not None and not callable(self.application_setup):
            raise TypeError("Plugin pack application_setup must be callable or None")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("Plugin pack metadata must be a mapping")
        translations = self.metadata.get("i18n", {})
        if not isinstance(translations, Mapping) or any(
            not isinstance(value, Mapping) for value in translations.values()
        ):
            raise TypeError("Plugin pack metadata.i18n must map locales to objects")
        metadata = deepcopy(dict(self.metadata))
        views = metadata.get("frontend_views", ())
        tools = metadata.get("project_tools", ())
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
        object.__setattr__(self, "id", pack_id)
        object.__setattr__(self, "description", str(self.description).strip())
        object.__setattr__(self, "plugins", plugins)
        object.__setattr__(self, "metadata", metadata)

    @property
    def frontend_views(self) -> tuple[Mapping[str, Any], ...]:
        """Sandboxed Workbench views contributed by this pack."""

        return tuple(self.metadata.get("frontend_views", ()))

    @property
    def project_tools(self) -> tuple[Mapping[str, Any], ...]:
        """Workbench project-tool entries that open contributed views."""

        return tuple(self.metadata.get("project_tools", ()))

    def localized(self, locale: str) -> tuple[str, str]:
        translations = self.metadata.get("i18n", {})
        value = _localized_i18n_fields(translations, locale)
        return (
            str(value.get("name") or self.id),
            str(value.get("description") or self.description),
        )


def merge_plugin_pack_metadata(
    pack: PluginPack,
    metadata_by_plugin: Mapping[str, Mapping[str, Any]],
) -> PluginPack:
    """Return a pack whose selected Plugins carry additional protocol metadata."""

    if not isinstance(metadata_by_plugin, Mapping):
        raise TypeError("Plugin metadata overrides must be a mapping")
    overrides = {
        str(name): dict(metadata)
        for name, metadata in metadata_by_plugin.items()
    }
    known_names = {plugin.name for plugin in pack.plugins}
    unknown_names = sorted(set(overrides) - known_names)
    if unknown_names:
        raise ValueError(
            "Plugin metadata override names are not in pack "
            f"{pack.id}: {', '.join(unknown_names)}"
        )
    return replace(
        pack,
        plugins=tuple(
            replace(
                plugin,
                metadata={**dict(plugin.metadata), **overrides[plugin.name]},
            )
            if plugin.name in overrides
            else plugin
            for plugin in pack.plugins
        ),
    )


@dataclass(frozen=True, slots=True)
class PluginCall:
    """One Plugin invocation requested by a model component."""

    name: str
    arguments: Mapping[str, Any]
    id: str = field(default_factory=lambda: f"call_{uuid4().hex}")

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        if not name:
            raise ValueError("PluginCall name cannot be empty")
        call_id = str(self.id).strip()
        if not call_id:
            raise ValueError("PluginCall id cannot be empty")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "id", call_id)
        object.__setattr__(self, "arguments", deepcopy(dict(self.arguments)))


@dataclass(frozen=True, slots=True)
class PluginCallResult:
    """Runtime envelope around the opaque value returned by a Plugin."""

    call_id: str
    name: str
    success: bool
    value: Any
    error: str
    time: datetime


__all__ = [
    "Plugin",
    "PluginApplicationContext",
    "PluginApplicationSetupHandler",
    "PluginCall",
    "PluginCallResult",
    "PluginContext",
    "PluginFrontendHandler",
    "PluginHandler",
    "PluginLifecycleHandler",
    "PluginPack",
    "PluginSetupContext",
    "PluginSetupHandler",
    "PluginSearchHandler",
    "merge_plugin_pack_metadata",
]
