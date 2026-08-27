"""Thread-safe registration and discovery of Plugin packs and standalone Plugins."""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, replace as dataclass_replace
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, Mapping
from uuid import uuid4

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


def default_plugin_impl_directory() -> Path:
    """Return Cyrene's editable, user-owned Plugin implementation directory."""

    override = str(os.environ.get("CYRENE_PLUGIN_IMPL_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    user_data = str(os.environ.get("CYRENE_USER_DATA_DIR") or "").strip()
    if user_data:
        return (Path(user_data).expanduser() / "plugin_impl").resolve()
    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support" / "Cyrene"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / "Cyrene"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share") / "Cyrene"
    return (base / "plugin_impl").resolve()


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
        self._user_directories: set[Path] = set()
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

    def _customized_plugin(self, plugin: Plugin, source: str) -> Plugin | None:
        metadata = dict(plugin.metadata)
        canonical = str(metadata.get("canonical_name") or plugin.name)
        metadata.setdefault("canonical_name", canonical)
        metadata.setdefault("source_name", plugin.name)
        metadata.setdefault("source_description", plugin.description)
        if source == "core" or plugin.kind != "tool":
            if source == "core" and plugin.kind == "tool":
                metadata["agent_exposure"] = "direct"
            return dataclass_replace(plugin, metadata=metadata)
        override = self._customizations.get(canonical)
        if override.get("deleted") is True:
            return None
        if "agent_exposure" in override:
            metadata["agent_exposure"] = override["agent_exposure"]
            metadata["model_visible"] = override["agent_exposure"] != "hidden"
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
        return registered.source == "core" or registered.plugin.kind == "model"

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
        if self._plugin_locked(registered):
            return True
        if (
            registered.pack_id is not None
            and not self._activation.pack_enabled(registered.pack_id)
        ):
            return False
        return self._activation.plugin_enabled(registered.plugin.canonical_name)

    def plugin_locked(self, name: str) -> bool:
        return self._plugin_locked(self._registered_value(name))

    def pack_locked(self, pack_id: str) -> bool:
        normalized_id = str(pack_id)
        with self._lock:
            pack = self._packs.get(normalized_id)
            source = self._pack_sources.get(normalized_id)
        if pack is None:
            raise PluginNotFoundError(f"Plugin pack is not registered: {pack_id}")
        return source == "core" or any(plugin.kind == "model" for plugin in pack.plugins)

    def plugin_configured_enabled(self, name: str) -> bool:
        registered = self._registered_value(name)
        if self._plugin_locked(registered):
            return True
        return self._activation.plugin_enabled(registered.plugin.canonical_name)

    def pack_configured_enabled(self, pack_id: str) -> bool:
        if self.pack_locked(pack_id):
            return True
        return self._activation.pack_enabled(str(pack_id))

    def pack_enabled(self, pack_id: str) -> bool:
        """Return the effective pack switch, including locked core packs."""

        return self.pack_configured_enabled(pack_id)

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
        return not (
            registered.plugin.main_only
            and not self._main_agent(agent_id)
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
        log_operation(
            logger,
            "plugin.registry",
            "resolve",
            phase="completed",
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
                level=logging.WARNING,
                plugin=name,
                error="not_registered",
            )
            raise PluginNotFoundError(f"Plugin is not registered: {name}")
        log_operation(
            logger,
            "plugin.registry",
            "registered",
            phase="completed",
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
        if registered.plugin.kind != "tool":
            raise PluginRegistryError(f"Plugin is not a tool: {canonical_name}")
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
                level=logging.WARNING,
                pack_id=pack_id,
                error="not_registered",
            )
            raise PluginNotFoundError(f"Plugin pack is not registered: {pack_id}")
        log_operation(
            logger,
            "plugin.registry",
            "pack_source",
            phase="completed",
            pack_id=pack_id,
            source=source,
        )
        return source

    def tool_definitions(self, *, agent_id: str = "main") -> tuple[dict, ...]:
        return tuple(
            item.plugin.tool_definition()
            for item in self.list_plugins()
            if item.plugin.kind == "tool"
            and item.plugin.model_visible
            and self.plugin_accessible(item.plugin.name, agent_id=agent_id)
        )

    def direct_tool_definitions(self, *, agent_id: str = "main") -> tuple[dict, ...]:
        """Return core and user-selected tools exposed directly to the model."""

        return tuple(
            item.plugin.tool_definition()
            for item in self.list_plugins()
            if item.plugin.kind == "tool"
            and item.plugin.model_visible
            and (
                item.source == "core"
                or item.plugin.agent_exposure == "direct"
            )
            and self.plugin_accessible(item.plugin.name, agent_id=agent_id)
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
        if not root.exists():
            log_operation(
                logger,
                "plugin.registry",
                "load_directory",
                phase="completed",
                directory=root,
                replace=replace,
                loaded=0,
                failures=0,
                missing=True,
                duration_ms=round((time.perf_counter() - started) * 1_000, 3),
            )
            return ()
        failures: list[PluginLoadFailure] = []
        loaded: list[dict[str, str]] = []
        for entry in sorted(root.iterdir(), key=lambda item: item.name):
            if entry.name.startswith((".", "_")) or not (
                entry.is_dir() or (entry.is_file() and entry.suffix == ".py")
            ):
                continue
            if entry.is_dir() and not _contains_python_source(entry):
                continue
            module_name = ""
            contribution_kind: Literal["pack", "plugin"]
            contribution_identity = ""
            try:
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
                with self._lock:
                    previous = self._user_contributions.get(entry)
                    self._user_contributions[entry] = _LoadedContribution(
                        module_name,
                        contribution_kind,
                        contribution_identity,
                    )
                if previous and previous.module_name != module_name:
                    self._unload_module_tree(previous.module_name)
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
        for entry in sorted(tracked - present, key=lambda path: path.name):
            source = str(entry)
            with self._lock:
                contribution = self._user_contributions.pop(entry)
                self._remove_loaded_locked(contribution, source)
            self._unload_module_tree(contribution.module_name)
            log_operation(
                logger,
                "plugin.registry",
                "remove_contribution",
                phase="completed",
                path=entry,
                kind=contribution.kind,
                identity=contribution.identity,
                reason="source_deleted",
            )
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
