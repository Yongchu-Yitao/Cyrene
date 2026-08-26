"""Thread-safe registration and discovery of Plugin packs and standalone Plugins."""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Literal
from uuid import uuid4

from .plugin import Plugin, PluginPack


class PluginRegistryError(RuntimeError):
    """Raised when Plugin identities or packages are invalid."""


class PluginNotFoundError(PluginRegistryError):
    """Raised when no registered Plugin has the requested name."""


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


class PluginRegistry:
    """Keep a short lock around snapshots; Plugin code never runs under it."""

    def __init__(self, *, include_core: bool = True) -> None:
        self._lock = threading.RLock()
        self._reload_lock = threading.RLock()
        self._packs: dict[str, PluginPack] = {}
        self._plugins: dict[str, RegisteredPlugin] = {}
        self._pack_sources: dict[str, str] = {}
        self._user_contributions: dict[Path, _LoadedContribution] = {}
        self._user_directories: set[Path] = set()
        if include_core:
            from .core_impl import create_core_plugin_pack

            self.register_pack(create_core_plugin_pack(self), source="core")

    def register_pack(
        self,
        pack: PluginPack,
        *,
        source: str,
        replace: bool = False,
    ) -> None:
        if not isinstance(pack, PluginPack):
            raise TypeError("pack must be a PluginPack")
        normalized_source = str(source).strip()
        if not normalized_source:
            raise ValueError("Plugin pack source cannot be empty")
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

    def register_plugin(
        self,
        plugin: Plugin,
        *,
        source: str,
        replace: bool = False,
    ) -> None:
        """Register one standalone Plugin without assigning it to a pack."""

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

    def unregister_pack(self, pack_id: str) -> bool:
        normalized_id = str(pack_id)
        with self._lock:
            if self._pack_sources.get(normalized_id) == "core":
                raise PluginRegistryError(
                    f"Core Plugin pack cannot be unregistered: {normalized_id}"
                )
            pack = self._packs.pop(normalized_id, None)
            self._pack_sources.pop(normalized_id, None)
            if pack is None:
                return False
            for plugin in pack.plugins:
                self._plugins.pop(plugin.name, None)
            return True

    def unregister_plugin(self, name: str) -> bool:
        """Remove one standalone Plugin while protecting pack-owned and core entries."""

        normalized_name = str(name)
        with self._lock:
            registered = self._plugins.get(normalized_name)
            if registered is None:
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
        if (
            registered is not None
            and registered.pack_id is None
            and registered.source == source
        ):
            self._plugins.pop(contribution.identity, None)

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

    def resolve(self, name: str) -> Plugin:
        with self._lock:
            registered = self._plugins.get(str(name))
        if registered is None:
            raise PluginNotFoundError(f"Plugin is not registered: {name}")
        return registered.plugin

    def registered(self, name: str) -> RegisteredPlugin:
        with self._lock:
            result = self._plugins.get(str(name))
        if result is None:
            raise PluginNotFoundError(f"Plugin is not registered: {name}")
        return result

    def list_plugins(self) -> tuple[RegisteredPlugin, ...]:
        with self._lock:
            return tuple(self._plugins[name] for name in sorted(self._plugins))

    def list_packs(self) -> tuple[PluginPack, ...]:
        with self._lock:
            return tuple(self._packs[pack_id] for pack_id in sorted(self._packs))

    def pack_source(self, pack_id: str) -> str:
        with self._lock:
            source = self._pack_sources.get(str(pack_id))
        if source is None:
            raise PluginNotFoundError(f"Plugin pack is not registered: {pack_id}")
        return source

    def tool_definitions(self) -> tuple[dict, ...]:
        return tuple(
            item.plugin.tool_definition()
            for item in self.list_plugins()
            if item.plugin.kind == "tool"
        )

    def direct_tool_definitions(self) -> tuple[dict, ...]:
        """Return the fixed core protocol exposed directly to the model."""

        return tuple(
            item.plugin.tool_definition()
            for item in self.list_plugins()
            if item.plugin.kind == "tool" and item.source == "core"
        )

    @staticmethod
    def _load_module(entry: Path) -> tuple[ModuleType, str]:
        is_package = entry.is_dir()
        initializer = entry / "__init__.py" if is_package else entry
        if is_package and not initializer.is_file():
            raise PluginRegistryError("tool pack must contain __init__.py")
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
        except BaseException:
            PluginRegistry._unload_module_tree(module_name)
            raise
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
        with self._lock:
            self._user_directories.add(root)
        if not root.exists():
            return ()
        failures: list[PluginLoadFailure] = []
        for entry in sorted(root.iterdir(), key=lambda item: item.name):
            if entry.name.startswith((".", "_")) or not (
                entry.is_dir() or (entry.is_file() and entry.suffix == ".py")
            ):
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
                            "tool pack __init__.py must export PluginPack as plugin_pack"
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
        return root


__all__ = [
    "PluginLoadFailure",
    "PluginNotFoundError",
    "PluginRegistry",
    "PluginRegistryError",
    "RegisteredPlugin",
    "default_plugin_impl_directory",
]
