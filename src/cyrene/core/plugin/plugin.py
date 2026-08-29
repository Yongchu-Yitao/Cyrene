"""Small, runtime-independent Plugin value objects."""

from __future__ import annotations

import re
from collections.abc import (
    Awaitable,
    Callable,
    Mapping,
    MutableMapping,
)
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypeAlias
from uuid import uuid4

from .validation import check_input_schema
from .extensions import (
    APPLICATION_SETUP,
    SESSION_SETUP,
    ExtensionContribution,
    ExtensionRegistry,
)

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
PluginApplicationSetupHandler: TypeAlias = Callable[[Any], None]


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
    contributions: tuple[ExtensionContribution[Any], ...] = field(default_factory=tuple)
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
        contributions = tuple(self.contributions)
        ExtensionRegistry(contributions)
        if not isinstance(self.metadata, Mapping):
            raise TypeError("Plugin pack metadata must be a mapping")
        translations = self.metadata.get("i18n", {})
        if not isinstance(translations, Mapping) or any(
            not isinstance(value, Mapping) for value in translations.values()
        ):
            raise TypeError("Plugin pack metadata.i18n must map locales to objects")
        metadata = deepcopy(dict(self.metadata))
        object.__setattr__(self, "id", pack_id)
        object.__setattr__(self, "description", str(self.description).strip())
        object.__setattr__(self, "plugins", plugins)
        object.__setattr__(self, "contributions", contributions)
        object.__setattr__(self, "metadata", metadata)

    def localized(self, locale: str) -> tuple[str, str]:
        translations = self.metadata.get("i18n", {})
        value = _localized_i18n_fields(translations, locale)
        return (
            str(value.get("name") or self.id),
            str(value.get("description") or self.description),
        )

    @property
    def extensions(self) -> ExtensionRegistry:
        """Return every package contribution indexed by typed extension point.

        ``setup`` and ``application_setup`` are constructor conveniences.  They
        are normalized here, so every host consumes one extension mechanism.
        """

        contributions = list(self.contributions)
        if self.setup is not None:
            contributions.append(ExtensionContribution(SESSION_SETUP, self.setup))
        if self.application_setup is not None:
            contributions.append(
                ExtensionContribution(APPLICATION_SETUP, self.application_setup)
            )
        return ExtensionRegistry(contributions)

    @property
    def session_setups(self) -> tuple[PluginSetupHandler, ...]:
        return self.extensions.values(SESSION_SETUP)

    @property
    def application_setups(self) -> tuple[PluginApplicationSetupHandler, ...]:
        return self.extensions.values(APPLICATION_SETUP)

    @property
    def has_session_contributions(self) -> bool:
        return bool(self.session_setups)

    @property
    def has_application_contributions(self) -> bool:
        return bool(self.application_setups)


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
    "PluginApplicationSetupHandler",
    "PluginCall",
    "PluginCallResult",
    "PluginContext",
    "PluginHandler",
    "PluginPack",
    "PluginSetupContext",
    "PluginSetupHandler",
    "merge_plugin_pack_metadata",
]
