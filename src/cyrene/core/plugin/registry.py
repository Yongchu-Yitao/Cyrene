"""Thread-safe registration and discovery of Plugin packs and standalone Plugins."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, replace as dataclass_replace
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, Mapping
from uuid import uuid4

from cyrene.path_policy import user_data_dir

from ..observability import log_operation
from .activation import (
    PluginActivationState,
    active_plugin_activation_state,
)
from .customization import (
    PluginCustomizationState,
    active_plugin_customization_state,
)
from .plugin import Plugin, PluginPack

logger = logging.getLogger(__name__)

_SOURCE_AVAILABILITY_LOCK = threading.RLock()
_USER_SOURCE_STATES: dict[str, tuple[int, bool]] = {}

_I18N_FILE_NAME = "i18n.json"
_I18N_LOCALES = ("en", "zh")
_IDENTITY_BOUNDARY = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])"
)
_IDENTITY_SEPARATOR = re.compile(r"[._-]+")


class PluginRegistryError(RuntimeError):
    """Raised when Plugin identities or packages are invalid."""


class PluginNotFoundError(PluginRegistryError):
    """Raised when no registered Plugin has the requested name."""


class PluginUnavailableError(PluginRegistryError):
    """Raised when activation or Agent scope prevents Plugin execution."""


@dataclass(frozen=True, slots=True)
class RegisteredPlugin:
    plugin: Plugin
    pack_id: str | None
    source: str


@dataclass(frozen=True, slots=True)
class PluginLoadFailure:
    path: Path
    error: str


@dataclass(frozen=True, slots=True)
class _LoadedContribution:
    module_name: str
    kind: Literal["pack", "plugin"]
    identity: str
    source_signature: str


def _set_user_source_available(source: str, available: bool) -> int | None:
    """Publish reload health so stale session/background registries fail closed."""

    normalized = str(source or "").strip()
    if not normalized or normalized == "core" or normalized.startswith("mcp:"):
        return None
    with _SOURCE_AVAILABILITY_LOCK:
        previous = _USER_SOURCE_STATES.get(normalized)
        if previous is None:
            revision = 0
        elif previous[1] == available:
            revision = previous[0]
        else:
            revision = previous[0] + 1
        _USER_SOURCE_STATES[normalized] = (revision, available)
        return revision


def _user_source_available(source: str, loaded_revision: int | None = None) -> bool:
    normalized = str(source or "").strip()
    if not normalized or normalized == "core" or normalized.startswith("mcp:"):
        return True
    with _SOURCE_AVAILABILITY_LOCK:
        state = _USER_SOURCE_STATES.get(normalized)
    if state is None:
        return True
    revision, available = state
    return available and (
        loaded_revision is None or loaded_revision == revision
    )


def default_plugin_impl_directory() -> Path:
    """Return Cyrene's editable, user-owned Plugin implementation directory."""

    override = str(os.environ.get("CYRENE_PLUGIN_IMPL_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (user_data_dir() / "plugin_impl").resolve()


def _contains_python_source(directory: Path) -> bool:
    """Ignore empty/cache-only folders left after a managed pack is retired."""

    try:
        return any(
            candidate.is_file()
            for candidate in directory.rglob("*.py")
            if "__pycache__" not in candidate.parts
        )
    except OSError:
        # Let the normal loader surface an actionable failure for unreadable
        # directories instead of silently hiding them.
        return True


def _python_source_signature(entry: Path) -> str:
    """Return a stable digest for the Python source owned by one contribution."""

    files = (
        tuple(
            candidate
            for candidate in sorted(entry.rglob("*.py"))
            if "__pycache__" not in candidate.parts
        )
        if entry.is_dir()
        else (entry,)
    )
    digest = hashlib.sha256()
    for candidate in files:
        relative = (
            candidate.relative_to(entry)
            if entry.is_dir()
            else Path(candidate.name)
        )
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(candidate.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _contribution_signature(entry: Path, catalog: Path) -> str:
    """Include shared catalog changes in each contribution's reload key."""

    digest = hashlib.sha256(_python_source_signature(entry).encode("ascii"))
    if catalog.is_file():
        digest.update(catalog.read_bytes())
    return digest.hexdigest()


def _humanize_identity(identity: str) -> str:
    """Turn a stable Plugin identifier into a readable fallback label."""

    separated = _IDENTITY_SEPARATOR.sub(" ", str(identity or "").strip())
    separated = _IDENTITY_BOUNDARY.sub(" ", separated)
    words = [word for word in separated.split() if word]
    if not words:
        return "Plugin"
    return " ".join(
        word.upper()
        if len(word) <= 3 and word.isupper()
        else word[:1].upper() + word[1:]
        for word in words
    )


class PluginRegistry:
    """Keep a short lock around snapshots; Plugin code never runs under it."""

    def __init__(
        self,
        *,
        include_core: bool = True,
        activation: PluginActivationState | None = None,
        customizations: PluginCustomizationState | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._reload_lock = threading.RLock()
        self._revision = 0
        self._directory_revision = 0
        self._activation = (
            activation
            or active_plugin_activation_state()
            or PluginActivationState()
        )
        self._customizations = (
            customizations
            or active_plugin_customization_state()
            or PluginCustomizationState()
        )
        self._packs: dict[str, PluginPack] = {}
        self._plugins: dict[str, RegisteredPlugin] = {}
        self._pack_sources: dict[str, str] = {}
        self._user_contributions: dict[Path, _LoadedContribution] = {}
        # Application services, Hooks, routes, and background jobs can retain
        # functions or instances from an older contribution after a Registry
        # refresh.  Their deferred relative imports still require the old
        # package name to remain in sys.modules.  Pin replaced generations for
        # this process instead of invalidating those live objects underneath
        # their owners.
        self._retained_module_names: set[str] = set()
        self._user_source_revisions: dict[str, int] = {}
        self._user_directories: set[Path] = set()
        self._i18n_catalogs: dict[Path, dict[str, Any]] = {}
        if include_core:
            from .core_impl import create_core_plugin_pack

            self.register_pack(create_core_plugin_pack(self), source="core")
        log_operation(
            logger,
            "plugin.registry",
            "initialize",
            phase="completed",
            include_core=include_core,
            plugin_count=len(self._plugins),
            pack_count=len(self._packs),
        )

    @property
    def activation(self) -> PluginActivationState:
        """Return the live activation state shared by sibling registries."""

        return self._activation

    @property
    def revision(self) -> int:
        """Monotonic version of registrations owned by this registry."""

        with self._lock:
            return self._revision

    @property
    def directory_revision(self) -> int:
        """Monotonic version of editable-directory load attempts."""

        with self._lock:
            return self._directory_revision

    @property
    def sync_token(self) -> tuple[int, int, int, int]:
        """Return every version a live Agent session must reconcile."""

        with self._lock:
            revision = self._revision
            directory_revision = self._directory_revision
        return (
            revision,
            directory_revision,
            self._activation.revision,
            self._customizations.revision,
        )

    def _bump_revision_locked(self) -> None:
        self._revision += 1

    def configure_activation(
        self,
        *,
        plugins: dict[str, bool],
        packs: dict[str, bool],
    ) -> None:
        """Replace persisted activation overrides without changing registrations."""

        self._activation.replace(plugins=plugins, packs=packs)

    @property
    def customizations(self) -> PluginCustomizationState:
        return self._customizations

    def configure_customizations(
        self,
        values: Mapping[str, Mapping[str, Any]],
    ) -> None:
        self._customizations.replace(values)

    @staticmethod
    def _validated_i18n_catalog(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError("Plugin i18n catalog must be a JSON object")
        if value.get("version", 1) != 1:
            raise ValueError("Unsupported Plugin i18n catalog version")
        normalized: dict[str, Any] = {"version": 1, "packs": {}, "plugins": {}}
        for section in ("packs", "plugins"):
            raw_section = value.get(section, {})
            if not isinstance(raw_section, Mapping):
                raise ValueError(f"Plugin i18n {section} must be an object")
            for raw_identity, raw_translations in raw_section.items():
                identity = str(raw_identity or "").strip()
                if not identity or not isinstance(raw_translations, Mapping):
                    raise ValueError(
                        f"Plugin i18n {section} entries must map ids to objects"
                    )
                translations: dict[str, dict[str, str]] = {}
                for locale, raw_fields in raw_translations.items():
                    locale_name = str(locale or "").strip()
                    if not locale_name or not isinstance(raw_fields, Mapping):
                        raise ValueError(
                            f"Plugin i18n entry {section}.{identity} has invalid locale data"
                        )
                    fields: dict[str, str] = {}
                    for field in ("name", "description"):
                        if field not in raw_fields:
                            continue
                        field_value = raw_fields[field]
                        if not isinstance(field_value, str):
                            raise ValueError(
                                f"Plugin i18n {section}.{identity}.{locale_name}.{field} "
                                "must be a string"
                            )
                        if field_value.strip():
                            fields[field] = field_value.strip()
                    translations[locale_name] = fields
                normalized[section][identity] = translations
        return normalized

    def _catalog_i18n(self, section: str, identity: str) -> dict[str, Any]:
        merged: dict[str, dict[str, str]] = {}
        with self._lock:
            catalogs = tuple(
                self._i18n_catalogs[root]
                for root in sorted(self._i18n_catalogs, key=str)
            )
        for catalog in catalogs:
            translations = catalog.get(section, {}).get(identity, {})
            if not isinstance(translations, Mapping):
                continue
            for locale, fields in translations.items():
                if isinstance(fields, Mapping):
                    merged.setdefault(str(locale), {}).update(
                        {
                            str(field): str(value)
                            for field, value in fields.items()
                            if str(value or "").strip()
                        }
                    )
        return merged

    def _complete_i18n(
        self,
        *,
        section: str,
        identity: str,
        authored: Any,
    ) -> dict[str, dict[str, str]]:
        authored_translations = (
            {
                str(locale): dict(fields)
                for locale, fields in authored.items()
                if isinstance(fields, Mapping)
            }
            if isinstance(authored, Mapping)
            else {}
        )
        catalog = self._catalog_i18n(section, identity)
        readable_name = _humanize_identity(identity)
        completed = {
            locale: dict(fields)
            for locale, fields in authored_translations.items()
        }
        for locale in _I18N_LOCALES:
            fields = {
                "name": readable_name,
            }
            catalog_fields = catalog.get(locale)
            if locale == "zh" and not isinstance(catalog_fields, Mapping):
                catalog_fields = catalog.get("zh-CN")
            if isinstance(catalog_fields, Mapping):
                fields.update(
                    {
                        field: str(value).strip()
                        for field, value in catalog_fields.items()
                        if field in {"name", "description"}
                        and str(value or "").strip()
                    }
                )
            authored_fields = authored_translations.get(locale)
            if locale == "zh" and not isinstance(authored_fields, Mapping):
                authored_fields = authored_translations.get("zh-CN")
            if isinstance(authored_fields, Mapping):
                fields.update(
                    {
                        field: str(value).strip()
                        for field, value in authored_fields.items()
                        if field in {"name", "description"}
                        and str(value or "").strip()
                    }
                )
            completed[locale] = fields
        return completed

    def _localized_plugin(self, plugin: Plugin) -> Plugin:
        metadata = dict(plugin.metadata)
        metadata["i18n"] = self._complete_i18n(
            section="plugins",
            identity=plugin.name,
            authored=metadata.get("i18n"),
        )
        return dataclass_replace(plugin, metadata=metadata)

    def _localized_pack(self, pack: PluginPack) -> PluginPack:
        metadata = dict(pack.metadata)
        metadata["i18n"] = self._complete_i18n(
            section="packs",
            identity=pack.id,
            authored=metadata.get("i18n"),
        )
        return dataclass_replace(
            pack,
            plugins=tuple(self._localized_plugin(plugin) for plugin in pack.plugins),
            metadata=metadata,
        )

    def _reload_i18n_catalog(self, root: Path) -> PluginLoadFailure | None:
        path = root / _I18N_FILE_NAME
        previous = self._i18n_catalogs.get(root)
        try:
            if path.is_file():
                catalog = self._validated_i18n_catalog(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            else:
                catalog = {"version": 1, "packs": {}, "plugins": {}}
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            return PluginLoadFailure(path, str(exc))
        with self._lock:
            self._i18n_catalogs[root] = catalog
        try:
            if self._pack_sources.get("core") == "core":
                from .core_impl import create_core_plugin_pack

                self.register_pack(
                    create_core_plugin_pack(self),
                    source="core",
                    replace=True,
                )
        except Exception as exc:
            with self._lock:
                if previous is None:
                    self._i18n_catalogs.pop(root, None)
                else:
                    self._i18n_catalogs[root] = previous
            return PluginLoadFailure(path, str(exc))
        return None

    def _customized_plugin(self, plugin: Plugin, source: str) -> Plugin | None:
        metadata = dict(plugin.metadata)
        canonical = str(metadata.get("canonical_name") or plugin.name)
        metadata.setdefault("canonical_name", canonical)
        metadata.setdefault("source_name", plugin.name)
        metadata.setdefault("source_description", plugin.description)
        if source == "core":
            if source == "core" and plugin.kind == "tool":
                metadata["agent_exposure"] = "direct"
            return dataclass_replace(plugin, metadata=metadata)
        override = self._customizations.get(canonical)
        if override.get("deleted") is True:
            return None
        if plugin.kind == "tool" and "agent_exposure" in override:
            metadata["agent_exposure"] = override["agent_exposure"]
            metadata["model_visible"] = override["agent_exposure"] != "hidden"
        metadata["customized_description"] = "description" in override
        return dataclass_replace(
            plugin,
            name=str(override.get("name") or plugin.name),
            description=(
                str(override["description"])
                if "description" in override
                else plugin.description
            ),
            metadata=metadata,
        )

    def register_pack(
        self,
        pack: PluginPack,
        *,
        source: str,
        replace: bool = False,
    ) -> None:
        log_operation(
            logger,
            "plugin.registry",
            "register_pack",
            phase="requested",
            pack_id=getattr(pack, "id", None),
            source=source,
            replace=replace,
        )
        if not isinstance(pack, PluginPack):
            raise TypeError("pack must be a PluginPack")
        normalized_source = str(source).strip()
        if not normalized_source:
            raise ValueError("Plugin pack source cannot be empty")
        pack = self._localized_pack(pack)
        customized_plugins = tuple(
            customized
            for plugin in pack.plugins
            if (customized := self._customized_plugin(plugin, normalized_source))
            is not None
        )
        pack = dataclass_replace(pack, plugins=customized_plugins)
        with self._lock:
            existing_pack = self._packs.get(pack.id)
            existing_source = self._pack_sources.get(pack.id)
            if existing_pack is not None and not replace:
                raise PluginRegistryError(f"Plugin pack id already exists: {pack.id}")
            if (
                existing_pack is not None
                and existing_source == "core"
                and normalized_source != "core"
            ):
                raise PluginRegistryError(
                    f"Core Plugin pack cannot be replaced: {pack.id}"
                )
            if (
                existing_pack is not None
                and existing_source != normalized_source
            ):
                raise PluginRegistryError(
                    f"Plugin pack id already belongs to source {existing_source}: {pack.id}"
                )
            replacing_names = (
                {plugin.name for plugin in existing_pack.plugins}
                if existing_pack is not None
                else set()
            )
            for plugin in pack.plugins:
                existing = self._plugins.get(plugin.name)
                if existing is not None and plugin.name not in replacing_names:
                    owner = (
                        f"pack {existing.pack_id}"
                        if existing.pack_id is not None
                        else f"source {existing.source}"
                    )
                    raise PluginRegistryError(
                        f"Plugin name already belongs to {owner}: {plugin.name}"
                    )
            if existing_pack is not None:
                for plugin in existing_pack.plugins:
                    self._plugins.pop(plugin.name, None)
            self._packs[pack.id] = pack
            self._pack_sources[pack.id] = normalized_source
            for plugin in pack.plugins:
                self._plugins[plugin.name] = RegisteredPlugin(
                    plugin=plugin,
                    pack_id=pack.id,
                    source=normalized_source,
                )
            self._bump_revision_locked()
        log_operation(
            logger,
            "plugin.registry",
            "register_pack",
            phase="completed",
            pack_id=pack.id,
            source=normalized_source,
            replace=replace,
            plugins=[plugin.name for plugin in pack.plugins],
        )

    def register_plugin(
        self,
        plugin: Plugin,
        *,
        source: str,
        replace: bool = False,
    ) -> None:
        """Register one standalone Plugin without assigning it to a pack."""

        log_operation(
            logger,
            "plugin.registry",
            "register_plugin",
            phase="requested",
            plugin=getattr(plugin, "name", None),
            source=source,
            replace=replace,
        )
        self._register_plugin(
            plugin,
            source=source,
            replace=replace,
        )

    def _register_plugin(
        self,
        plugin: Plugin,
        *,
        source: str,
        replace: bool,
    ) -> None:
        if not isinstance(plugin, Plugin):
            raise TypeError("plugin must be a Plugin")
        normalized_source = str(source).strip()
        if not normalized_source:
            raise ValueError("Plugin source cannot be empty")
        plugin = self._localized_plugin(plugin)
        customized = self._customized_plugin(plugin, normalized_source)
        if customized is None:
            return
        plugin = customized
        with self._lock:
            existing = self._plugins.get(plugin.name)
            if existing is not None:
                if existing.pack_id is not None:
                    raise PluginRegistryError(
                        f"Plugin name belongs to pack {existing.pack_id}: {plugin.name}"
                    )
                if existing.source != normalized_source:
                    raise PluginRegistryError(
                        f"Plugin name already belongs to source {existing.source}: {plugin.name}"
                    )
                if not replace:
                    raise PluginRegistryError(
                        f"Standalone Plugin name already exists: {plugin.name}"
                    )
                if existing.source == "core" and normalized_source != "core":
                    raise PluginRegistryError(
                        f"Core Plugin cannot be replaced: {plugin.name}"
                    )
            self._plugins[plugin.name] = RegisteredPlugin(
                plugin=plugin,
                pack_id=None,
                source=normalized_source,
            )
            self._bump_revision_locked()
        log_operation(
            logger,
            "plugin.registry",
            "register_plugin",
            phase="completed",
            plugin=plugin.name,
            plugin_kind=plugin.kind,
            source=normalized_source,
            replace=replace,
        )

    def unregister_pack(self, pack_id: str) -> bool:
        normalized_id = str(pack_id)
        log_operation(
            logger,
            "plugin.registry",
            "unregister_pack",
            phase="requested",
            pack_id=normalized_id,
        )
        with self._lock:
            if self._pack_sources.get(normalized_id) == "core":
                raise PluginRegistryError(
                    f"Core Plugin pack cannot be unregistered: {normalized_id}"
                )
            pack = self._packs.pop(normalized_id, None)
            self._pack_sources.pop(normalized_id, None)
            if pack is None:
                log_operation(
                    logger,
                    "plugin.registry",
                    "unregister_pack",
                    phase="completed",
                    pack_id=normalized_id,
                    removed=False,
                )
                return False
            for plugin in pack.plugins:
                self._plugins.pop(plugin.name, None)
            self._bump_revision_locked()
        log_operation(
            logger,
            "plugin.registry",
            "unregister_pack",
            phase="completed",
            pack_id=normalized_id,
            removed=True,
            plugins=[plugin.name for plugin in pack.plugins],
        )
        return True

    def unregister_plugin(self, name: str) -> bool:
        """Remove one standalone Plugin while protecting pack-owned and core entries."""

        normalized_name = str(name)
        log_operation(
            logger,
            "plugin.registry",
            "unregister_plugin",
            phase="requested",
            plugin=normalized_name,
        )
        with self._lock:
            registered = self._plugins.get(normalized_name)
            if registered is None:
                log_operation(
                    logger,
                    "plugin.registry",
                    "unregister_plugin",
                    phase="completed",
                    plugin=normalized_name,
                    removed=False,
                )
                return False
            if registered.pack_id is not None:
                raise PluginRegistryError(
                    f"Plugin belongs to pack {registered.pack_id}: {normalized_name}"
                )
            if registered.source == "core":
                raise PluginRegistryError(
                    f"Core Plugin cannot be unregistered: {normalized_name}"
                )
            self._plugins.pop(normalized_name, None)
            self._bump_revision_locked()
        log_operation(
            logger,
            "plugin.registry",
            "unregister_plugin",
            phase="completed",
            plugin=normalized_name,
            removed=True,
            source=registered.source,
        )
        return True

    def _remove_loaded_locked(
        self,
        contribution: _LoadedContribution,
        source: str,
    ) -> None:
        if contribution.kind == "pack":
            if self._pack_sources.get(contribution.identity) != source:
                return
            pack = self._packs.pop(contribution.identity, None)
            self._pack_sources.pop(contribution.identity, None)
            if pack is not None:
                for plugin in pack.plugins:
                    self._plugins.pop(plugin.name, None)
                self._bump_revision_locked()
            return
        registered = self._plugins.get(contribution.identity)
        if not (
            registered is not None
            and registered.pack_id is None
            and registered.source == source
        ):
            registered = next(
                (
                    item
                    for item in self._plugins.values()
                    if item.pack_id is None
                    and item.source == source
                    and item.plugin.canonical_name == contribution.identity
                ),
                None,
            )
        if registered is not None:
            self._plugins.pop(registered.plugin.name, None)
            self._bump_revision_locked()

    def _retire_loaded_entry(
        self,
        entry: Path,
        *,
        reason: str,
    ) -> _LoadedContribution | None:
        source = str(entry)
        with self._lock:
            contribution = self._user_contributions.pop(entry, None)
            if contribution is not None:
                self._remove_loaded_locked(contribution, source)
        if contribution is None:
            return None
        self._retain_module_tree(contribution.module_name)
        _set_user_source_available(source, False)
        log_operation(
            logger,
            "plugin.registry",
            "remove_contribution",
            phase="completed",
            path=entry,
            kind=contribution.kind,
            identity=contribution.identity,
            reason=reason,
        )
        return contribution

    def _retain_module_tree(self, module_name: str) -> None:
        """Pin a replaced module tree while external lifecycle objects may use it."""

        if not module_name:
            return
        with self._lock:
            self._retained_module_names.add(module_name)
        log_operation(
            logger,
            "plugin.registry",
            "retain_module_tree",
            phase="completed",
            module=module_name,
        )

    def _install_loaded_contribution(
        self,
        entry: Path,
        contribution: Plugin | PluginPack,
        *,
        kind: Literal["pack", "plugin"],
        replace: bool,
    ) -> None:
        """Atomically replace the contribution previously loaded from one path."""

        source = str(entry)
        with self._lock:
            previous = self._user_contributions.get(entry)
            packs_before = dict(self._packs)
            plugins_before = dict(self._plugins)
            sources_before = dict(self._pack_sources)
            if previous is not None and replace:
                self._remove_loaded_locked(previous, source)
            try:
                allow_existing = replace and previous is None
                if kind == "pack":
                    assert isinstance(contribution, PluginPack)
                    self.register_pack(
                        contribution,
                        source=source,
                        replace=allow_existing,
                    )
                else:
                    assert isinstance(contribution, Plugin)
                    self.register_plugin(
                        contribution,
                        source=source,
                        replace=allow_existing,
                    )
            except Exception:
                self._packs = packs_before
                self._plugins = plugins_before
                self._pack_sources = sources_before
                raise

    @staticmethod
    def _main_agent(agent_id: str | None) -> bool:
        return not str(agent_id or "main").strip() or str(
            agent_id or "main"
        ).strip() == "main"

    @staticmethod
    def _plugin_locked(registered: RegisteredPlugin) -> bool:
        return (
            registered.source == "core"
            or registered.plugin.required
        )

    def _registered_value(self, name: str) -> RegisteredPlugin:
        with self._lock:
            registered = self._plugins.get(str(name))
            if registered is None:
                registered = next(
                    (
                        item
                        for item in self._plugins.values()
                        if item.plugin.canonical_name == str(name)
                    ),
                    None,
                )
        if registered is None:
            raise PluginNotFoundError(f"Plugin is not registered: {name}")
        return registered

    def _registered_enabled(self, registered: RegisteredPlugin) -> bool:
        if registered.source == "core":
            return True
        if not _user_source_available(
            registered.source,
            self._user_source_revisions.get(registered.source),
        ):
            return False
        if (
            registered.pack_id is not None
            and not self.pack_configured_enabled(registered.pack_id)
        ):
            return False
        if registered.plugin.required:
            return True
        return self._activation.plugin_enabled(
            registered.plugin.canonical_name,
            default=bool(registered.plugin.metadata.get("default_enabled", True)),
        )

    def plugin_locked(self, name: str) -> bool:
        return self._plugin_locked(self._registered_value(name))

    def pack_locked(self, pack_id: str) -> bool:
        normalized_id = str(pack_id)
        with self._lock:
            pack = self._packs.get(normalized_id)
            source = self._pack_sources.get(normalized_id)
        if pack is None:
            raise PluginNotFoundError(f"Plugin pack is not registered: {pack_id}")
        return (
            source == "core"
            or bool(pack.metadata.get("required"))
        )

    def plugin_configured_enabled(self, name: str) -> bool:
        registered = self._registered_value(name)
        if self._plugin_locked(registered):
            return True
        return self._activation.plugin_enabled(
            registered.plugin.canonical_name,
            default=bool(registered.plugin.metadata.get("default_enabled", True)),
        )

    def pack_configured_enabled(self, pack_id: str) -> bool:
        if self.pack_locked(pack_id):
            return True
        with self._lock:
            pack = self._packs[str(pack_id)]
        return self._activation.pack_enabled(
            str(pack_id),
            default=bool(pack.metadata.get("default_enabled", True)),
        )

    def pack_enabled(self, pack_id: str) -> bool:
        """Return the effective pack switch, including locked core packs."""

        normalized_id = str(pack_id)
        with self._lock:
            source = self._pack_sources.get(normalized_id)
        if source is None:
            raise PluginNotFoundError(
                f"Plugin pack is not registered: {pack_id}"
            )
        return (
            _user_source_available(
                source,
                self._user_source_revisions.get(source),
            )
            and self.pack_configured_enabled(normalized_id)
        )

    def plugin_enabled(self, name: str) -> bool:
        """Return effective activation after pack and Plugin switches."""

        return self._registered_enabled(self._registered_value(name))

    def plugin_accessible(
        self,
        name: str,
        *,
        agent_id: str = "main",
    ) -> bool:
        """Return whether activation and Agent scope permit execution."""

        registered = self._registered_value(name)
        if not self._registered_enabled(registered):
            return False
        is_main = self._main_agent(agent_id)
        return not (
            (registered.plugin.main_only and not is_main)
            or (registered.plugin.subagent_only and is_main)
        )

    def set_plugin_enabled(self, name: str, enabled: bool) -> None:
        """Update one non-locked Plugin override in the shared state."""

        if not isinstance(enabled, bool):
            raise TypeError("Plugin enabled value must be a boolean")
        registered = self._registered_value(name)
        if self._plugin_locked(registered):
            raise PluginRegistryError(
                f"Plugin activation is locked: {registered.plugin.name}"
            )
        snapshot = self._activation.snapshot()
        snapshot.plugins[registered.plugin.canonical_name] = enabled
        self._activation.replace(plugins=snapshot.plugins, packs=snapshot.packs)

    def set_pack_enabled(self, pack_id: str, enabled: bool) -> None:
        """Update one non-locked pack override in the shared state."""

        if not isinstance(enabled, bool):
            raise TypeError("Plugin pack enabled value must be a boolean")
        normalized_id = str(pack_id)
        if self.pack_locked(normalized_id):
            raise PluginRegistryError(
                f"Plugin pack activation is locked: {normalized_id}"
            )
        snapshot = self._activation.snapshot()
        snapshot.packs[normalized_id] = enabled
        self._activation.replace(plugins=snapshot.plugins, packs=snapshot.packs)

    def resolve(self, name: str, *, agent_id: str = "main") -> Plugin:
        with self._lock:
            registered = self._plugins.get(str(name))
        if registered is None:
            log_operation(
                logger,
                "plugin.registry",
                "resolve",
                phase="failed",
                level=logging.WARNING,
                plugin=name,
                error="not_registered",
            )
            raise PluginNotFoundError(f"Plugin is not registered: {name}")
        if not self._registered_enabled(registered):
            log_operation(
                logger,
                "plugin.registry",
                "resolve",
                phase="failed",
                level=logging.WARNING,
                plugin=name,
                error="disabled",
            )
            raise PluginUnavailableError(f"Plugin is disabled: {name}")
        if registered.plugin.main_only and not self._main_agent(agent_id):
            log_operation(
                logger,
                "plugin.registry",
                "resolve",
                phase="failed",
                level=logging.WARNING,
                plugin=name,
                agent_id=agent_id,
                error="main_only",
            )
            raise PluginUnavailableError(
                f"Plugin is only available to the main Agent: {name}"
            )
        if registered.plugin.subagent_only and self._main_agent(agent_id):
            log_operation(
                logger,
                "plugin.registry",
                "resolve",
                phase="failed",
                level=logging.WARNING,
                plugin=name,
                agent_id=agent_id,
                error="subagent_only",
            )
            raise PluginUnavailableError(
                f"Plugin is only available to subagents: {name}"
            )
        log_operation(
            logger,
            "plugin.registry",
            "resolve",
            phase="completed",
            level=logging.DEBUG,
            plugin=registered.plugin.name,
            plugin_kind=registered.plugin.kind,
            pack_id=registered.pack_id,
            source=registered.source,
            agent_id=agent_id,
        )
        return registered.plugin

    def registered(self, name: str) -> RegisteredPlugin:
        with self._lock:
            result = self._plugins.get(str(name))
        if result is None:
            log_operation(
                logger,
                "plugin.registry",
                "registered",
                phase="failed",
                level=logging.DEBUG,
                plugin=name,
                error="not_registered",
            )
            raise PluginNotFoundError(f"Plugin is not registered: {name}")
        log_operation(
            logger,
            "plugin.registry",
            "registered",
            phase="completed",
            level=logging.DEBUG,
            plugin=result.plugin.name,
            plugin_kind=result.plugin.kind,
            pack_id=result.pack_id,
            source=result.source,
        )
        return result

    def list_plugins(self) -> tuple[RegisteredPlugin, ...]:
        with self._lock:
            result = tuple(self._plugins[name] for name in sorted(self._plugins))
        log_operation(
            logger,
            "plugin.registry",
            "list_plugins",
            phase="completed",
            level=logging.DEBUG,
            count=len(result),
            plugins=[item.plugin.name for item in result],
        )
        return result

    def list_packs(self) -> tuple[PluginPack, ...]:
        with self._lock:
            result = tuple(self._packs[pack_id] for pack_id in sorted(self._packs))
        log_operation(
            logger,
            "plugin.registry",
            "list_packs",
            phase="completed",
            level=logging.DEBUG,
            count=len(result),
            packs=[pack.id for pack in result],
        )
        return result

    def registered_by_canonical(self, canonical_name: str) -> RegisteredPlugin:
        normalized = str(canonical_name)
        with self._lock:
            result = next(
                (
                    item
                    for item in self._plugins.values()
                    if item.plugin.canonical_name == normalized
                ),
                None,
            )
        if result is None:
            raise PluginNotFoundError(
                f"Plugin is not registered: {canonical_name}"
            )
        return result

    def customize_tool(
        self,
        canonical_name: str,
        values: Mapping[str, Any],
    ) -> RegisteredPlugin | None:
        """Apply a persisted tool override immediately to this registry."""

        registered = self.registered_by_canonical(canonical_name)
        if self._plugin_locked(registered):
            raise PluginRegistryError(
                f"Plugin customization is locked: {canonical_name}"
            )
        if registered.plugin.kind not in {"tool", "model"}:
            raise PluginRegistryError(
                f"Plugin cannot be customized: {canonical_name}"
            )
        if (
            registered.plugin.kind != "tool"
            and "agent_exposure" in values
        ):
            raise PluginRegistryError(
                f"Only tool Plugins have agent exposure: {canonical_name}"
            )
        current = self._customizations.get(canonical_name)
        current.update(dict(values))
        previous = self._customizations.get(canonical_name)
        self._customizations.set(canonical_name, current)
        if current.get("deleted") is True:
            with self._lock:
                self._plugins.pop(registered.plugin.name, None)
                if registered.pack_id is not None:
                    pack = self._packs[registered.pack_id]
                    self._packs[registered.pack_id] = dataclass_replace(
                        pack,
                        plugins=tuple(
                            plugin
                            for plugin in pack.plugins
                            if plugin.canonical_name != canonical_name
                        ),
                    )
                self._bump_revision_locked()
            return None
        metadata = dict(registered.plugin.metadata)
        source_name = str(metadata.get("source_name") or canonical_name)
        source_description = str(
            metadata.get("source_description") or registered.plugin.description
        )
        source_plugin = dataclass_replace(
            registered.plugin,
            name=source_name,
            description=source_description,
            metadata=metadata,
        )
        try:
            customized = self._customized_plugin(source_plugin, registered.source)
            assert customized is not None
        except Exception:
            self._customizations.set(canonical_name, previous)
            raise
        with self._lock:
            collision = self._plugins.get(customized.name)
            if collision is not None and collision is not registered:
                self._customizations.set(canonical_name, previous)
                raise PluginRegistryError(
                    f"Plugin name already exists: {customized.name}"
                )
            self._plugins.pop(registered.plugin.name, None)
            next_registered = RegisteredPlugin(
                plugin=customized,
                pack_id=registered.pack_id,
                source=registered.source,
            )
            self._plugins[customized.name] = next_registered
            if registered.pack_id is not None:
                pack = self._packs[registered.pack_id]
                self._packs[registered.pack_id] = dataclass_replace(
                    pack,
                    plugins=tuple(
                        customized
                        if plugin.canonical_name == canonical_name
                        else plugin
                        for plugin in pack.plugins
                    ),
                )
            self._bump_revision_locked()
        return next_registered

    def refresh_customizations(self) -> None:
        """Project the shared customization state onto already-loaded tools."""

        with self._lock:
            packs = dict(self._packs)
            rebuilt: dict[str, RegisteredPlugin] = {}
            rebuilt_pack_plugins: dict[str, list[Plugin]] = {
                pack_id: [] for pack_id in packs
            }
            for registered in self._plugins.values():
                plugin = registered.plugin
                if registered.source != "core" and plugin.kind == "tool":
                    metadata = dict(plugin.metadata)
                    plugin = dataclass_replace(
                        plugin,
                        name=str(metadata.get("source_name") or plugin.canonical_name),
                        description=str(
                            metadata.get("source_description") or plugin.description
                        ),
                        metadata=metadata,
                    )
                    plugin = self._customized_plugin(plugin, registered.source)
                    if plugin is None:
                        continue
                if plugin.name in rebuilt:
                    raise PluginRegistryError(
                        f"Plugin name already exists: {plugin.name}"
                    )
                next_registered = RegisteredPlugin(
                    plugin=plugin,
                    pack_id=registered.pack_id,
                    source=registered.source,
                )
                rebuilt[plugin.name] = next_registered
                if registered.pack_id is not None:
                    rebuilt_pack_plugins[registered.pack_id].append(plugin)
            self._plugins = rebuilt
            self._packs = {
                pack_id: dataclass_replace(
                    pack,
                    plugins=tuple(rebuilt_pack_plugins[pack_id]),
                )
                for pack_id, pack in packs.items()
            }

    def pack_source(self, pack_id: str) -> str:
        with self._lock:
            source = self._pack_sources.get(str(pack_id))
        if source is None:
            log_operation(
                logger,
                "plugin.registry",
                "pack_source",
                phase="failed",
                level=logging.DEBUG,
                pack_id=pack_id,
                error="not_registered",
            )
            raise PluginNotFoundError(f"Plugin pack is not registered: {pack_id}")
        log_operation(
            logger,
            "plugin.registry",
            "pack_source",
            phase="completed",
            level=logging.DEBUG,
            pack_id=pack_id,
            source=source,
        )
        return source

    def tool_definitions(self, *, agent_id: str = "main") -> tuple[dict, ...]:
        return tuple(
            item.plugin.tool_definition(
                allow_resource_reveal=str(agent_id or "main") == "main"
            )
            for item in self.list_plugins()
            if item.plugin.kind == "tool"
            and item.plugin.model_visible
            and self.plugin_accessible(item.plugin.name, agent_id=agent_id)
        )

    def direct_tool_definitions(
        self,
        *,
        agent_id: str = "main",
        read_only: bool = False,
    ) -> tuple[dict, ...]:
        """Return core and user-selected tools exposed directly to the model."""

        return tuple(
            item.plugin.tool_definition(
                allow_resource_reveal=str(agent_id or "main") == "main"
            )
            for item in self.list_plugins()
            if item.plugin.kind == "tool"
            and item.plugin.model_visible
            and (
                item.source == "core"
                or item.plugin.agent_exposure == "direct"
            )
            and self.plugin_accessible(item.plugin.name, agent_id=agent_id)
            and (not read_only or item.plugin.permits_read_only())
        )

    @staticmethod
    def _load_module(entry: Path) -> tuple[ModuleType, str]:
        started = time.perf_counter()
        log_operation(
            logger,
            "plugin.registry",
            "load_module",
            phase="started",
            path=entry,
        )
        is_package = entry.is_dir()
        initializer = entry / "__init__.py" if is_package else entry
        if is_package and not initializer.is_file():
            raise PluginRegistryError("Plugin pack must contain __init__.py")
        if not is_package and (not entry.is_file() or entry.suffix != ".py"):
            raise PluginRegistryError("standalone Plugin must be a Python file")
        module_name = f"_cyrene_plugin_{uuid4().hex}"
        if is_package:
            spec = importlib.util.spec_from_file_location(
                module_name,
                initializer,
                submodule_search_locations=[str(entry)],
            )
        else:
            spec = importlib.util.spec_from_file_location(module_name, initializer)
        if spec is None or spec.loader is None:
            raise PluginRegistryError("unable to create a module loader")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException as exc:
            PluginRegistry._unload_module_tree(module_name)
            log_operation(
                logger,
                "plugin.registry",
                "load_module",
                phase="failed",
                level=logging.ERROR,
                exc_info=True,
                path=entry,
                module=module_name,
                duration_ms=round((time.perf_counter() - started) * 1_000, 3),
                error=exc,
            )
            raise
        log_operation(
            logger,
            "plugin.registry",
            "load_module",
            phase="completed",
            path=entry,
            module=module_name,
            duration_ms=round((time.perf_counter() - started) * 1_000, 3),
        )
        return module, module_name

    @staticmethod
    def _unload_module_tree(module_name: str) -> None:
        if not module_name:
            return
        for loaded_name in tuple(sys.modules):
            if loaded_name == module_name or loaded_name.startswith(f"{module_name}."):
                sys.modules.pop(loaded_name, None)

    def load_directory(
        self,
        directory: str | Path | None = None,
        *,
        replace: bool = False,
    ) -> tuple[PluginLoadFailure, ...]:
        """Load top-level Plugin files and PluginPack directories."""

        with self._reload_lock:
            return self._load_directory(directory, replace=replace)

    def _load_directory(
        self,
        directory: str | Path | None = None,
        *,
        replace: bool = False,
    ) -> tuple[PluginLoadFailure, ...]:
        """Implementation shared by initial loading and locked refreshes."""

        root = Path(directory or default_plugin_impl_directory()).expanduser().resolve()
        started = time.perf_counter()
        log_operation(
            logger,
            "plugin.registry",
            "load_directory",
            phase="started",
            directory=root,
            replace=replace,
        )
        with self._lock:
            self._user_directories.add(root)
            self._directory_revision += 1
        failures: list[PluginLoadFailure] = []
        catalog_failure = self._reload_i18n_catalog(root)
        if catalog_failure is not None:
            failures.append(catalog_failure)
        if not root.exists():
            log_operation(
                logger,
                "plugin.registry",
                "load_directory",
                phase="completed",
                directory=root,
                replace=replace,
                loaded=0,
                failures=len(failures),
                missing=True,
                duration_ms=round((time.perf_counter() - started) * 1_000, 3),
            )
            return tuple(failures)
        loaded: list[dict[str, str]] = []
        reused: list[dict[str, str]] = []
        try:
            entries = sorted(root.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            with self._lock:
                tracked = tuple(
                    path for path in self._user_contributions if path.parent == root
                )
            for entry in tracked:
                self._retire_loaded_entry(entry, reason="directory_unreadable")
            failures.append(PluginLoadFailure(root, str(exc)))
            return tuple(failures)
        for entry in entries:
            if entry.name.startswith((".", "_")) or not (
                entry.is_dir() or (entry.is_file() and entry.suffix == ".py")
            ):
                continue
            if entry.is_dir() and not _contains_python_source(entry):
                continue
            module_name = ""
            contribution_kind: Literal["pack", "plugin"]
            contribution_identity = ""
            source_signature = ""
            try:
                source_signature = _contribution_signature(
                    entry,
                    root / _I18N_FILE_NAME,
                )
                with self._lock:
                    existing = self._user_contributions.get(entry)
                if (
                    replace
                    and existing is not None
                    and existing.source_signature == source_signature
                    and existing.module_name in sys.modules
                ):
                    revision = _set_user_source_available(str(entry), True)
                    if revision is not None:
                        with self._lock:
                            self._user_source_revisions[str(entry)] = revision
                    reused.append(
                        {
                            "path": str(entry),
                            "kind": existing.kind,
                            "id": existing.identity,
                        }
                    )
                    continue
                module, module_name = self._load_module(entry)
                if entry.is_dir():
                    pack = getattr(module, "plugin_pack", None)
                    if not isinstance(pack, PluginPack):
                        raise PluginRegistryError(
                            "Plugin pack __init__.py must export PluginPack as plugin_pack"
                        )
                    if pack.id != entry.name:
                        raise PluginRegistryError(
                            f"PluginPack id must match directory name: {entry.name}"
                        )
                    contribution_kind = "pack"
                    contribution_identity = pack.id
                    contribution: Plugin | PluginPack = pack
                else:
                    plugin = getattr(module, "plugin", None)
                    if not isinstance(plugin, Plugin):
                        raise PluginRegistryError(
                            "standalone Plugin module must export Plugin as plugin"
                        )
                    contribution_kind = "plugin"
                    contribution_identity = plugin.name
                    contribution = plugin
                self._install_loaded_contribution(
                    entry,
                    contribution,
                    kind=contribution_kind,
                    replace=replace,
                )
            except Exception as exc:
                if module_name:
                    self._unload_module_tree(module_name)
                if replace:
                    self._retire_loaded_entry(
                        entry,
                        reason="source_load_failed",
                    )
                _set_user_source_available(str(entry), False)
                failures.append(PluginLoadFailure(entry, str(exc)))
                log_operation(
                    logger,
                    "plugin.registry",
                    "load_contribution",
                    phase="failed",
                    level=logging.ERROR,
                    exc_info=True,
                    path=entry,
                    error=exc,
                )
            else:
                revision = _set_user_source_available(str(entry), True)
                if revision is not None:
                    with self._lock:
                        self._user_source_revisions[str(entry)] = revision
                with self._lock:
                    previous = self._user_contributions.get(entry)
                    self._user_contributions[entry] = _LoadedContribution(
                        module_name,
                        contribution_kind,
                        contribution_identity,
                        source_signature,
                    )
                if previous and previous.module_name != module_name:
                    self._retain_module_tree(previous.module_name)
                loaded.append(
                    {"path": str(entry), "kind": contribution_kind, "id": contribution_identity}
                )
                log_operation(
                    logger,
                    "plugin.registry",
                    "load_contribution",
                    phase="completed",
                    path=entry,
                    kind=contribution_kind,
                    identity=contribution_identity,
                    replaced=previous is not None,
                )
        log_operation(
            logger,
            "plugin.registry",
            "load_directory",
            phase="completed",
            directory=root,
            replace=replace,
            loaded=loaded,
            reused=reused,
            failures=[{"path": item.path, "error": item.error} for item in failures],
            duration_ms=round((time.perf_counter() - started) * 1_000, 3),
        )
        return tuple(failures)

    def refresh_directory(
        self,
        directory: str | Path | None = None,
    ) -> tuple[PluginLoadFailure, ...]:
        """Synchronize one editable directory, including deleted packages."""

        with self._reload_lock:
            return self._refresh_directory(directory)

    def _refresh_directory(
        self,
        directory: str | Path | None = None,
    ) -> tuple[PluginLoadFailure, ...]:
        """Refresh one directory while the reload lock is held."""

        root = Path(directory or default_plugin_impl_directory()).expanduser().resolve()
        with self._lock:
            self._user_directories.add(root)
            tracked = {
                path for path in self._user_contributions if path.parent == root
            }
        try:
            present = (
                {
                    path
                    for path in root.iterdir()
                    if not path.name.startswith((".", "_"))
                    and (path.is_dir() or (path.is_file() and path.suffix == ".py"))
                }
                if root.exists()
                else set()
            )
        except OSError as exc:
            for entry in tracked:
                self._retire_loaded_entry(entry, reason="directory_unreadable")
            return (PluginLoadFailure(root, str(exc)),)
        for entry in sorted(tracked - present, key=lambda path: path.name):
            self._retire_loaded_entry(entry, reason="source_deleted")
        return self._load_directory(root, replace=True)

    def refresh(self) -> tuple[PluginLoadFailure, ...]:
        """Refresh every user Plugin directory already attached to this Registry."""

        with self._reload_lock:
            with self._lock:
                roots = tuple(sorted(self._user_directories, key=str))
            failures: list[PluginLoadFailure] = []
            for root in roots:
                failures.extend(self._refresh_directory(root))
            return tuple(failures)

    @staticmethod
    def ensure_user_directory(directory: str | Path | None = None) -> Path:
        root = Path(directory or default_plugin_impl_directory()).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        log_operation(
            logger,
            "plugin.registry",
            "ensure_user_directory",
            phase="completed",
            directory=root,
        )
        return root


__all__ = [
    "PluginLoadFailure",
    "PluginNotFoundError",
    "PluginRegistry",
    "PluginRegistryError",
    "PluginUnavailableError",
    "RegisteredPlugin",
    "default_plugin_impl_directory",
]
