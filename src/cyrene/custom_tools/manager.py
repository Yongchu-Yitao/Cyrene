"""Discover and hot-reload trusted native-style Python tool modules.

Custom tools intentionally run in Cyrene's process.  A package is either a
top-level ``.py`` file or a top-level directory below :data:`CUSTOM_TOOLS_ROOT`.
Every Python module in a directory is imported; modules exporting neither
``TOOL_DEF`` nor ``handler`` are treated as support modules.  A tool module
exports both, and may additionally export ``TOOL_METADATA``.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.abc
import importlib.util
import inspect
import json
import logging
import re
import sys
import types
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from cyrene.custom_tools.models import (
    CustomToolDefinition,
    CustomToolLoadError,
    CustomToolPackage,
)
from cyrene.runtime.paths import USER_DATA_DIR
from cyrene.tooling.cache_invalidation import invalidate_tool_caches

logger = logging.getLogger(__name__)

CUSTOM_TOOLS_ROOT = USER_DATA_DIR / "custom-tools"

_CUSTOM_ID_RE = re.compile(r"^custom:([^/]+)/([^/@]+)(?:@([0-9a-f]{16}))?$")
_PACKAGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_IGNORED_DIRECTORIES = frozenset({
    "__pycache__",
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
})
_WATCH_INTERVAL_SECONDS = 0.75
_IMPORT_NAMESPACE = "_cyrene_user_tools"
_PACKAGE_SETTING_PREFIX = "custom_tools:"
_DISPLAY_CACHE_FILENAME = ".cyrene-tool-index.json"
_DISPLAY_CACHE_VERSION = 1


def _safe_module_segment(value: str) -> str:
    if value.isidentifier():
        return value
    digest = hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()
    return f"_file_{digest[:16]}"


class _SourceOnlyLoader(importlib.abc.Loader):
    """Load source bytes directly so rapid same-size saves cannot reuse stale pyc."""

    def __init__(self, path: Path, *, is_package: bool) -> None:
        self.path = path
        self.is_package = is_package

    def create_module(self, _spec):
        return None

    def exec_module(self, module: types.ModuleType) -> None:
        source = self.path.read_bytes()
        code = compile(source, str(self.path), "exec", dont_inherit=True)
        module.__file__ = str(self.path)
        module.__cached__ = None
        if self.is_package:
            module.__path__ = [str(self.path.parent)]
        exec(code, module.__dict__)


@dataclass(frozen=True, slots=True)
class _ImportEntry:
    path: Path | None
    package_dir: Path
    is_package: bool


class _CustomToolFinder(importlib.abc.MetaPathFinder):
    """Resolve generation-scoped custom package modules from source only."""

    def __init__(self) -> None:
        self._entries: dict[str, _ImportEntry] = {}

    def register(self, entries: dict[str, _ImportEntry]) -> None:
        self._entries.update(entries)

    def unregister_namespace(self, namespace: str) -> None:
        prefix = namespace + "."
        for name in tuple(self._entries):
            if name == namespace or name.startswith(prefix):
                self._entries.pop(name, None)

    def find_spec(self, fullname: str, _path=None, _target=None):
        entry = self._entries.get(fullname)
        if entry is None:
            return None
        if entry.path is None:
            spec = importlib.util.spec_from_loader(fullname, loader=None, is_package=True)
            if spec is not None:
                spec.submodule_search_locations = [str(entry.package_dir)]
            return spec
        loader = _SourceOnlyLoader(entry.path, is_package=entry.is_package)
        return importlib.util.spec_from_loader(
            fullname,
            loader,
            origin=str(entry.path),
            is_package=entry.is_package,
        )


_FINDER = _CustomToolFinder()
if not any(item is _FINDER for item in sys.meta_path):
    sys.meta_path.insert(0, _FINDER)
if _IMPORT_NAMESPACE not in sys.modules:
    base = types.ModuleType(_IMPORT_NAMESPACE)
    base.__package__ = _IMPORT_NAMESPACE
    base.__path__ = []
    sys.modules[_IMPORT_NAMESPACE] = base


def _pack_enabled() -> bool:
    try:
        from cyrene.runtime.settings_store import is_tool_pack_enabled

        return bool(is_tool_pack_enabled("custom_tools"))
    except Exception:
        # Startup can reach the loader before the settings store is available.
        # Match the rest of the tool-pack system's enabled-by-default behavior.
        return True


def _package_switches() -> dict[str, bool]:
    """Return explicit per-package switches; missing packages default on."""

    try:
        from cyrene.runtime.settings_store import get_enabled_tool_packs

        saved = get_enabled_tool_packs()
    except Exception:
        # Match the enabled-by-default behavior used for the global pack gate.
        return {}
    if not isinstance(saved, dict):
        return {}
    result: dict[str, bool] = {}
    for raw_key, raw_enabled in saved.items():
        key = str(raw_key or "")
        if not key.startswith(_PACKAGE_SETTING_PREFIX):
            continue
        package_id = key[len(_PACKAGE_SETTING_PREFIX):]
        if package_id and type(raw_enabled) is bool:
            result[package_id] = raw_enabled
    return result


def _package_switch_fingerprint(
    switches: dict[str, bool] | None = None,
) -> tuple[tuple[str, bool], ...]:
    selected = switches if switches is not None else _package_switches()
    return tuple(sorted(selected.items()))


def _cached_display_tool(
    raw: Any,
    *,
    package_id: str,
    revision: str,
) -> dict[str, Any] | None:
    """Validate one untrusted, presentation-only cache record."""

    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not _TOOL_NAME_RE.fullmatch(name):
        return None
    input_schema = raw.get("input_schema")
    metadata = raw.get("metadata")
    return {
        "package_id": package_id,
        "name": name,
        "description": str(raw.get("description") or ""),
        "capability_id": f"custom.{package_id}.{name}",
        "concrete_name": f"custom:{package_id}/{name}@{revision}",
        "stable_name": f"custom:{package_id}/{name}",
        "input_schema": deepcopy(input_schema) if isinstance(input_schema, dict) else {"type": "object"},
        "metadata": deepcopy(metadata) if isinstance(metadata, dict) else {},
        "path": str(raw.get("path") or ""),
        "module": str(raw.get("module") or ""),
        "revision": revision,
    }


def _python_files(package_root: Path) -> tuple[Path, ...]:
    if package_root.is_file():
        return (package_root,) if package_root.suffix == ".py" else ()
    result: list[Path] = []
    for path in package_root.rglob("*.py"):
        try:
            relative = path.relative_to(package_root)
        except ValueError:
            continue
        if any(
            part.startswith(".") or part in _IGNORED_DIRECTORIES
            for part in relative.parts[:-1]
        ):
            continue
        if path.is_file():
            result.append(path)
    return tuple(sorted(result, key=lambda item: item.relative_to(package_root).as_posix()))


def _module_parts(package_root: Path, source_path: Path) -> tuple[str, ...]:
    if package_root.is_file():
        return ()
    relative = source_path.relative_to(package_root)
    parts = list(relative.parts)
    filename = parts.pop()
    if filename != "__init__.py":
        parts.append(Path(filename).stem)
    return tuple(_safe_module_segment(part) for part in parts)


def _import_entries(
    namespace: str,
    package_root: Path,
    source_files: tuple[Path, ...],
) -> tuple[dict[str, _ImportEntry], dict[Path, str]]:
    if package_root.is_file():
        return (
            {
                namespace: _ImportEntry(
                    path=package_root,
                    package_dir=package_root.parent,
                    is_package=False,
                ),
            },
            {package_root: namespace},
        )

    entries: dict[str, _ImportEntry] = {}
    module_names: dict[Path, str] = {}
    root_init = package_root / "__init__.py"
    entries[namespace] = _ImportEntry(
        path=root_init if root_init in source_files else None,
        package_dir=package_root,
        is_package=True,
    )
    for source_path in source_files:
        parts = _module_parts(package_root, source_path)
        module_name = ".".join((namespace, *parts)) if parts else namespace
        module_names[source_path] = module_name
        relative_parent = source_path.parent.relative_to(package_root)
        parent_parts: list[str] = []
        parent_dir = package_root
        for raw_part in relative_parent.parts:
            parent_parts.append(_safe_module_segment(raw_part))
            parent_dir = parent_dir / raw_part
            parent_name = ".".join((namespace, *parent_parts))
            parent_init = parent_dir / "__init__.py"
            entries.setdefault(
                parent_name,
                _ImportEntry(
                    path=parent_init if parent_init in source_files else None,
                    package_dir=parent_dir,
                    is_package=True,
                ),
            )
        entries[module_name] = _ImportEntry(
            path=source_path,
            package_dir=source_path.parent,
            is_package=source_path.name == "__init__.py",
        )
    return entries, module_names


def _validate_tool_module(
    module: types.ModuleType,
    *,
    package_id: str,
    source_path: Path,
    module_name: str,
    generation: int,
    revision: str,
) -> CustomToolDefinition | None:
    has_definition = hasattr(module, "TOOL_DEF")
    has_handler = hasattr(module, "handler")
    has_metadata = hasattr(module, "TOOL_METADATA")
    # Package initializers and underscore-prefixed files are support modules.
    # They are imported so relative imports work, but are never discovered as
    # tools even when they happen to expose similarly named helpers.
    if source_path.name == "__init__.py" or source_path.name.startswith("_"):
        return None
    if not has_definition and not has_handler and not has_metadata:
        return None
    if not has_definition or not has_handler:
        missing = "TOOL_DEF" if not has_definition else "handler"
        raise ValueError(f"tool module is missing required export {missing}")

    raw_definition = module.TOOL_DEF
    if not isinstance(raw_definition, dict):
        raise TypeError("TOOL_DEF must be a dictionary")
    definition = deepcopy(raw_definition)
    if definition.get("type") != "function":
        raise ValueError("TOOL_DEF.type must be 'function'")
    function = definition.get("function")
    if not isinstance(function, dict):
        raise ValueError("TOOL_DEF.function must be an object")
    name = str(function.get("name") or "").strip()
    if not _TOOL_NAME_RE.fullmatch(name):
        raise ValueError(
            "TOOL_DEF.function.name must contain only letters, digits, '_' or '-' "
            "and be 1-128 characters"
        )
    description = function.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("TOOL_DEF.function.description must be a non-empty string")
    parameters = function.get("parameters")
    if not isinstance(parameters, dict):
        raise ValueError("TOOL_DEF.function.parameters must be an object")
    _validate_input_schema(parameters)
    try:
        json.dumps(definition, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"TOOL_DEF must be JSON serializable: {exc}") from exc

    handler = module.handler
    is_async = inspect.iscoroutinefunction(handler) or (
        callable(handler)
        and inspect.iscoroutinefunction(getattr(handler, "__call__", None))
    )
    if not callable(handler) or not is_async:
        raise TypeError("handler must be an async callable")
    try:
        inspect.signature(handler).bind({}, None, 0, "", None)
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "handler must accept the native five-argument call: "
            "(arguments, bot, chat_id, db_path, notify_state)"
        ) from exc

    raw_metadata = getattr(module, "TOOL_METADATA", {})
    if raw_metadata is None:
        raw_metadata = {}
    if not isinstance(raw_metadata, dict):
        raise TypeError("TOOL_METADATA must be a dictionary when provided")
    metadata = {
        "read_only": False,
        "resource_keys": (f"custom-tool:{package_id}:{name}",),
        "requires_order": True,
        **deepcopy(raw_metadata),
    }
    if type(metadata.get("read_only")) is not bool:
        raise TypeError("TOOL_METADATA.read_only must be a boolean")
    if type(metadata.get("requires_order")) is not bool:
        raise TypeError("TOOL_METADATA.requires_order must be a boolean")
    resource_keys = metadata.get("resource_keys")
    if not isinstance(resource_keys, (list, tuple)) or not all(
        isinstance(item, str) and item.strip() for item in resource_keys
    ):
        raise TypeError("TOOL_METADATA.resource_keys must be a list or tuple of strings")
    metadata["resource_keys"] = tuple(resource_keys)
    try:
        json.dumps(metadata, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"TOOL_METADATA must be JSON serializable: {exc}") from exc

    return CustomToolDefinition(
        package_id=package_id,
        source_path=source_path,
        module_name=module_name,
        tool_def=definition,
        handler=handler,
        metadata=metadata,
        generation=generation,
        revision=revision,
    )


def _validate_input_schema(schema: dict[str, Any]) -> None:
    """Require the minimum function-calling input-schema shape.

    Do not attempt to implement a JSON Schema validator here. Providers accept
    different JSON Schema dialects, but all Cyrene function tools require an
    object at the root.
    """

    if schema.get("type") != "object":
        raise ValueError(
            "TOOL_DEF.function.parameters.type must be 'object'"
        )


class CustomToolManager:
    """Own the effective registry of trusted custom Python modules."""

    def __init__(self, root: str | Path | None = None) -> None:
        selected_root = Path(root) if root is not None else CUSTOM_TOOLS_ROOT
        self.root = selected_root.expanduser().resolve()
        self._instance_token = uuid4().hex[:12]
        self._packages: dict[str, CustomToolPackage] = {}
        self._fingerprint: tuple[tuple[str, str], ...] = ()
        self._namespaces: set[str] = set()
        self._generation = 0
        self._loaded_pack_enabled = False
        self._loaded_package_switches: tuple[tuple[str, bool], ...] = ()
        self._last_reload_reason = "not_loaded"
        self._running = False
        self._watch_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def generation(self) -> int:
        return self._generation

    async def start(self) -> None:
        async with self._lock:
            if self._running:
                return
            self.root.mkdir(parents=True, exist_ok=True)
            try:
                await self._reload_locked(reason="startup")
            except BaseException:
                # A failed initial scan must remain retryable. In particular,
                # do not report a running manager when no watcher was created.
                self._running = False
                raise
            self._running = True
            self._watch_task = asyncio.create_task(
                self._watch_loop(),
                name="custom-tool-source-watcher",
            )
        await self._after_reload(reason="startup")

    async def stop(self) -> None:
        async with self._lock:
            if not self._running and not self._packages and not self._namespaces:
                return
            self._running = False
            watch_task, self._watch_task = self._watch_task, None
            if watch_task is not None:
                watch_task.cancel()
            self._packages = {}
            self._fingerprint = ()
            self._loaded_pack_enabled = False
            self._loaded_package_switches = ()
            self._discard_namespaces(self._namespaces)
            self._namespaces = set()
            self._generation += 1
        if watch_task is not None:
            await asyncio.gather(watch_task, return_exceptions=True)
        self._invalidate_catalog()

    def stop_sync(self) -> None:
        """Best-effort teardown for legacy synchronous shutdown paths."""
        self._running = False
        if self._watch_task is not None:
            self._watch_task.cancel()
            self._watch_task = None
        self._packages = {}
        self._fingerprint = ()
        self._loaded_pack_enabled = False
        self._loaded_package_switches = ()
        self._discard_namespaces(self._namespaces)
        self._namespaces = set()
        self._generation += 1
        self._invalidate_catalog()

    async def reload(self, *, reason: str = "manual") -> dict[str, Any]:
        async with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            await self._reload_locked(reason=reason)
        await self._after_reload(reason=reason)
        return self.status()

    async def rescan(self) -> dict[str, Any]:
        """Compatibility name for an explicit full source reload."""
        return await self.reload(reason="rescan")

    async def _reload_locked(self, *, reason: str) -> None:
        fingerprint = self._root_fingerprint()
        self._generation += 1
        generation = self._generation
        enabled = _pack_enabled()
        package_switches = _package_switches()
        previous_packages = self._packages
        display_cache = self._read_display_cache()
        old_namespaces = set(self._namespaces)
        new_namespaces: set[str] = set()
        packages: dict[str, CustomToolPackage] = {}

        for package_id, package_root, source_files in self._discover_packages():
            namespace = self._namespace(package_id, generation)
            revision = self._package_revision(package_id, package_root, source_files)
            errors: list[CustomToolLoadError] = []
            tools: list[CustomToolDefinition] = []
            display_tools: tuple[dict[str, Any], ...] = ()
            configured_enabled = package_switches.get(package_id, True)
            effective_enabled = enabled and configured_enabled
            if not _PACKAGE_ID_RE.fullmatch(package_id):
                errors.append(CustomToolLoadError(
                    package_id=package_id,
                    source_path=package_root,
                    error_type="InvalidPackageId",
                    message=(
                        "package name must start with a letter or digit and contain "
                        "only letters, digits, '.', '_' or '-' (maximum 128 characters)"
                    ),
                ))
            elif effective_enabled:
                module_paths: dict[tuple[str, ...], list[Path]] = defaultdict(list)
                for source_path in source_files:
                    module_paths[_module_parts(package_root, source_path)].append(
                        source_path
                    )
                colliding_sources = {
                    source_path
                    for paths in module_paths.values()
                    if len(paths) > 1
                    for source_path in paths
                }
                for module_parts, paths in sorted(
                    module_paths.items(),
                    key=lambda item: item[0],
                ):
                    if len(paths) < 2:
                        continue
                    choices = ", ".join(
                        path.relative_to(self.root).as_posix() for path in paths
                    )
                    for source_path in paths:
                        errors.append(CustomToolLoadError(
                            package_id=package_id,
                            source_path=source_path,
                            error_type="DuplicateModulePath",
                            message=(
                                "multiple source files resolve to Python module "
                                f"{'.'.join(module_parts) or '<package>'!r}: {choices}"
                            ),
                        ))
                loadable_sources = tuple(
                    path for path in source_files if path not in colliding_sources
                )
                entries, module_names = _import_entries(
                    namespace,
                    package_root,
                    loadable_sources,
                )
                _FINDER.register(entries)
                new_namespaces.add(namespace)
                ordered_sources = sorted(
                    loadable_sources,
                    key=lambda path: (
                        path.name != "__init__.py",
                        len(path.parts),
                        str(path),
                    ),
                )
                for source_path in ordered_sources:
                    module_name = module_names[source_path]
                    try:
                        module = importlib.import_module(module_name)
                        tool = _validate_tool_module(
                            module,
                            package_id=package_id,
                            source_path=source_path,
                            module_name=module_name,
                            generation=generation,
                            revision=revision,
                        )
                        if tool is not None:
                            tools.append(tool)
                    except (Exception, SystemExit) as exc:
                        # A broken user file must not hide healthy files in the same
                        # package or prevent the rest of Cyrene from starting.
                        errors.append(CustomToolLoadError(
                            package_id=package_id,
                            source_path=source_path,
                            error_type=type(exc).__name__,
                            message=str(exc) or repr(exc),
                        ))
                        logger.warning(
                            "Unable to load custom tool source %s: %s",
                            source_path,
                            exc,
                        )

                by_name: dict[str, list[CustomToolDefinition]] = defaultdict(list)
                for tool in tools:
                    by_name[tool.name].append(tool)
                duplicates = {
                    name: definitions
                    for name, definitions in by_name.items()
                    if len(definitions) > 1
                }
                if duplicates:
                    duplicate_ids = {
                        id(tool)
                        for definitions in duplicates.values()
                        for tool in definitions
                    }
                    tools = [tool for tool in tools if id(tool) not in duplicate_ids]
                    for name, definitions in sorted(duplicates.items()):
                        choices = ", ".join(
                            tool.source_path.relative_to(self.root).as_posix()
                            for tool in definitions
                        )
                        for tool in definitions:
                            errors.append(CustomToolLoadError(
                                package_id=package_id,
                                source_path=tool.source_path,
                                error_type="DuplicateToolName",
                                message=(
                                    f"tool name {name!r} is exported more than once "
                                    f"inside package {package_id!r}: {choices}"
                                ),
                            ))
                display_tools = tuple(
                    tool.public(root=self.root)
                    for tool in sorted(
                        tools,
                        key=lambda item: (item.name, str(item.source_path)),
                    )
                )
            else:
                previous = previous_packages.get(package_id)
                previous_revision_matches = bool(
                    previous
                    and previous.source_files == source_files
                    and previous.revision == revision
                )
                if previous_revision_matches and previous is not None:
                    # Keep already-known display metadata while the package is
                    # disabled. The package gate below prevents these retained
                    # handlers from entering discovery or execution. A cold
                    # disabled package remains source-only and is never imported.
                    tools.extend(previous.tools)
                    errors.extend(previous.errors)
                    display_tools = previous.display_tools or tuple(
                        tool.public(root=self.root) for tool in previous.tools
                    )
                else:
                    cached = display_cache.get(package_id)
                    if cached and cached[0] == revision:
                        display_tools = cached[1]

            packages[package_id] = CustomToolPackage(
                package_id=package_id,
                root=package_root,
                tools=tuple(sorted(tools, key=lambda item: (item.name, str(item.source_path)))),
                display_tools=display_tools,
                errors=tuple(sorted(errors, key=lambda item: str(item.source_path))),
                source_files=source_files,
                generation=generation,
                revision=revision,
                configured_enabled=configured_enabled,
                enabled=effective_enabled,
                module_namespace=(
                    namespace if namespace in new_namespaces else ""
                ),
            )

        self._packages = packages
        self._write_display_cache(packages)
        self._fingerprint = fingerprint
        self._loaded_pack_enabled = enabled
        self._loaded_package_switches = _package_switch_fingerprint(
            package_switches,
        )
        self._last_reload_reason = reason
        self._namespaces = new_namespaces
        self._discard_namespaces(old_namespaces)
        self._invalidate_catalog()

    async def _watch_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(_WATCH_INTERVAL_SECONDS)
                fingerprint = self._root_fingerprint()
                if (
                    fingerprint != self._fingerprint
                    or _pack_enabled() != self._loaded_pack_enabled
                    or _package_switch_fingerprint()
                    != self._loaded_package_switches
                ):
                    await self.reload(reason="source_or_settings_changed")
            except asyncio.CancelledError:
                raise
            except Exception:
                # One transient read/import/event failure must not permanently
                # disable hot reload for all later edits.
                logger.exception("Custom tool source watcher iteration failed")

    def _discover_packages(self) -> list[tuple[str, Path, tuple[Path, ...]]]:
        if not self.root.is_dir():
            return []
        result: list[tuple[str, Path, tuple[Path, ...]]] = []
        for child in sorted(self.root.iterdir(), key=lambda item: item.name):
            if child.name.startswith(".") or child.name in _IGNORED_DIRECTORIES:
                continue
            if (
                child.is_file()
                and child.suffix == ".py"
                and not child.name.startswith("_")
            ):
                result.append((child.stem, child, (child,)))
            elif child.is_dir():
                result.append((child.name, child, _python_files(child)))
        return result

    def _root_fingerprint(self) -> tuple[tuple[str, str], ...]:
        result: list[tuple[str, str]] = []
        for package_id, package_root, source_files in self._discover_packages():
            result.append((f"package:{package_id}", "file" if package_root.is_file() else "dir"))
            for source_path in source_files:
                try:
                    data = source_path.read_bytes()
                    relative = source_path.relative_to(self.root).as_posix()
                    result.append((relative, hashlib.sha256(data).hexdigest()))
                except OSError as exc:
                    result.append((str(source_path), f"error:{type(exc).__name__}:{exc}"))
        return tuple(result)

    def _read_display_cache(
        self,
    ) -> dict[str, tuple[str, tuple[dict[str, Any], ...]]]:
        cache_path = self.root / _DISPLAY_CACHE_FILENAME
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        if (
            not isinstance(payload, dict)
            or payload.get("version") != _DISPLAY_CACHE_VERSION
            or not isinstance(payload.get("packages"), dict)
        ):
            return {}
        result: dict[str, tuple[str, tuple[dict[str, Any], ...]]] = {}
        for raw_package_id, raw_package in payload["packages"].items():
            package_id = str(raw_package_id or "")
            if (
                not _PACKAGE_ID_RE.fullmatch(package_id)
                or not isinstance(raw_package, dict)
            ):
                continue
            revision = str(raw_package.get("revision") or "")
            raw_tools = raw_package.get("tools")
            if not re.fullmatch(r"[0-9a-f]{16}", revision) or not isinstance(raw_tools, list):
                continue
            tools = tuple(
                cached
                for raw_tool in raw_tools
                if (
                    cached := _cached_display_tool(
                        raw_tool,
                        package_id=package_id,
                        revision=revision,
                    )
                ) is not None
            )
            result[package_id] = (revision, tools)
        return result

    def _write_display_cache(
        self,
        packages: dict[str, CustomToolPackage],
    ) -> None:
        cache_path = self.root / _DISPLAY_CACHE_FILENAME
        temporary = self.root / f".{_DISPLAY_CACHE_FILENAME.lstrip('.')}.{uuid4().hex}.tmp"
        payload = {
            "version": _DISPLAY_CACHE_VERSION,
            "packages": {
                package_id: {
                    "revision": package.revision,
                    "tools": deepcopy(list(package.display_tools)),
                }
                for package_id, package in sorted(packages.items())
                if package.display_tools
            },
        }
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            temporary.replace(cache_path)
        except (OSError, TypeError, ValueError):
            logger.warning("Unable to persist custom-tool display index", exc_info=True)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _package_revision(
        self,
        package_id: str,
        package_root: Path,
        source_files: tuple[Path, ...],
    ) -> str:
        digest = hashlib.sha256(package_id.encode("utf-8"))
        for source_path in source_files:
            try:
                relative = (
                    source_path.name
                    if package_root.is_file()
                    else source_path.relative_to(package_root).as_posix()
                )
                digest.update(relative.encode("utf-8", errors="surrogatepass"))
                digest.update(b"\0")
                digest.update(source_path.read_bytes())
                digest.update(b"\0")
            except OSError as exc:
                digest.update(f"error:{source_path}:{exc}".encode("utf-8"))
        return digest.hexdigest()[:16]

    def _namespace(self, package_id: str, generation: int) -> str:
        digest = hashlib.sha256(package_id.encode("utf-8")).hexdigest()[:12]
        return f"{_IMPORT_NAMESPACE}.m_{self._instance_token}_{digest}_g{generation}"

    @staticmethod
    def _discard_namespaces(namespaces: set[str]) -> None:
        for namespace in namespaces:
            _FINDER.unregister_namespace(namespace)
            prefix = namespace + "."
            for module_name in tuple(sys.modules):
                if module_name == namespace or module_name.startswith(prefix):
                    sys.modules.pop(module_name, None)
            parent_name, _separator, child_name = namespace.rpartition(".")
            parent = sys.modules.get(parent_name)
            if parent is not None and hasattr(parent, child_name):
                try:
                    delattr(parent, child_name)
                except (AttributeError, TypeError):
                    pass

    def list_packages(self) -> list[dict[str, Any]]:
        global_enabled = _pack_enabled()
        switches = _package_switches()
        result: list[dict[str, Any]] = []
        for package_id, package in sorted(self._packages.items()):
            public = package.public(custom_tools_root=self.root)
            configured_enabled = switches.get(package_id, True)
            effective_enabled = bool(
                global_enabled and configured_enabled and package.enabled
            )
            public["configured_enabled"] = configured_enabled
            public["effective_enabled"] = effective_enabled
            public["enabled"] = effective_enabled
            if not effective_enabled:
                public["status"] = "disabled"
            result.append(public)
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        return [tool.public(root=self.root) for tool in self.get_tool_definitions()]

    def list_errors(self) -> list[dict[str, str]]:
        return [
            error.public(root=self.root)
            for package in self._packages.values()
            for error in package.errors
        ]

    def list_files(self) -> list[dict[str, Any]]:
        global_enabled = _pack_enabled()
        switches = _package_switches()
        result: list[dict[str, Any]] = []
        for package in self._packages.values():
            effective_enabled = bool(
                package.enabled
                and global_enabled
                and switches.get(package.package_id, True)
            )
            tools_by_path = {tool.source_path: tool for tool in package.tools}
            errors_by_path: dict[Path, list[CustomToolLoadError]] = defaultdict(list)
            for error in package.errors:
                errors_by_path[error.source_path].append(error)
            for source_path in package.source_files:
                try:
                    relative = source_path.relative_to(self.root).as_posix()
                except ValueError:
                    relative = str(source_path)
                tool = tools_by_path.get(source_path)
                file_errors = errors_by_path.get(source_path, [])
                result.append({
                    "package_id": package.package_id,
                    "path": relative,
                    "status": (
                        "disabled"
                        if not effective_enabled
                        else "error"
                        if file_errors
                        else "tool"
                        if tool is not None
                        else "support"
                    ),
                    "tool": tool.name if tool is not None else "",
                    "errors": [error.public(root=self.root) for error in file_errors],
                })
        return sorted(result, key=lambda item: (item["package_id"], item["path"]))

    def status(self) -> dict[str, Any]:
        tools = self.get_tool_definitions()
        errors = self.list_errors()
        enabled = _pack_enabled()
        return {
            "root": str(self.root),
            "running": self._running,
            "enabled": enabled,
            "generation": self._generation,
            "last_reload_reason": self._last_reload_reason,
            "package_count": len(self._packages),
            "file_count": sum(len(package.source_files) for package in self._packages.values()),
            "tool_count": len(tools),
            "error_count": len(errors),
            "packages": self.list_packages(),
            "files": self.list_files(),
            "tools": self.list_tools(),
            "errors": errors,
        }

    def get_package(self, package_id: str) -> CustomToolPackage | None:
        return self._packages.get(str(package_id or "").strip())

    async def set_package_enabled(
        self,
        package_id: str,
        enabled: bool,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Persist one package switch and apply it before returning."""

        selected_id = str(package_id or "").strip()
        if not _PACKAGE_ID_RE.fullmatch(selected_id):
            raise ValueError("invalid custom tool package id")
        if type(enabled) is not bool:
            raise TypeError("enabled must be a boolean")

        async with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            discovered_ids = {
                discovered_id
                for discovered_id, _package_root, _source_files
                in self._discover_packages()
            }
            if selected_id not in discovered_ids:
                raise KeyError(f"custom tool package {selected_id!r} was not found")

            # Import lazily to keep the custom-tool loader out of the runtime
            # settings module graph. boolean_map updates merge atomically.
            from cyrene.runtime import settings_service

            update_result = settings_service.update(
                "runtime",
                {
                    "enabled_tool_packs": {
                        _PACKAGE_SETTING_PREFIX + selected_id: enabled,
                    },
                },
                actor="ui",
                expected_revision=expected_revision,
            )
            # The persisted switch is authoritative immediately. Invalidate
            # frozen discovery before reload so a failed import cannot leave a
            # newly-disabled package advertised from an old cache.
            self._invalidate_catalog()
            try:
                await self._reload_locked(reason="package_toggle")
            except Exception:
                self._invalidate_catalog()
                raise
            status = self.status()
            status["settings_revision"] = update_result["revision"]

        await self._after_reload(reason="package_toggle")
        return status

    def get_tool_definitions(self) -> list[CustomToolDefinition]:
        global_enabled = _pack_enabled()
        switches = _package_switches()
        return [
            tool
            for _package_id, package in sorted(self._packages.items())
            if (
                package.enabled
                and global_enabled
                and switches.get(package.package_id, True)
            )
            for tool in package.tools
        ]

    def get_public_tool_defs(self) -> list[dict[str, Any]]:
        grouped: dict[str, list[CustomToolDefinition]] = defaultdict(list)
        for tool in self.get_tool_definitions():
            grouped[tool.name].append(tool)
        return [
            definitions[0].function_definition(public_name=True)
            for _name, definitions in sorted(grouped.items())
            if len(definitions) == 1
        ]

    def has_tool(self, name: str) -> bool:
        try:
            self.resolve_tool(name)
        except (KeyError, ValueError):
            return False
        return True

    def resolve_tool(self, name: str) -> tuple[CustomToolPackage, CustomToolDefinition]:
        target = str(name or "").strip()
        qualified = _CUSTOM_ID_RE.fullmatch(target)
        global_enabled = _pack_enabled()
        switches = _package_switches()
        candidates: list[tuple[CustomToolPackage, CustomToolDefinition]] = []
        for package in self._packages.values():
            if (
                not package.enabled
                or not global_enabled
                or not switches.get(package.package_id, True)
            ):
                continue
            for tool in package.tools:
                if qualified:
                    requested_revision = qualified.group(3)
                    if (
                        package.package_id == qualified.group(1)
                        and tool.name == qualified.group(2)
                        and (
                            requested_revision is None
                            or requested_revision == tool.revision
                        )
                    ):
                        return package, tool
                elif tool.name == target or tool.capability_id == target:
                    candidates.append((package, tool))
        if qualified or not candidates:
            raise KeyError(f"custom tool {target!r} was not found")
        if len(candidates) > 1:
            choices = ", ".join(tool.concrete_name for _package, tool in candidates)
            raise ValueError(
                f"custom tool name {target!r} is ambiguous; use one of: {choices}"
            )
        return candidates[0]

    def resolve_declared_tool(
        self,
        name: str,
    ) -> tuple[CustomToolPackage, CustomToolDefinition]:
        identity = str(name or "").strip()
        qualified = _CUSTOM_ID_RE.fullmatch(identity)
        if qualified is None or qualified.group(3) is None:
            raise KeyError(
                "custom tool execution requires a frozen @<source-revision> identity"
            )
        package, tool = self.resolve_tool(identity)
        try:
            current_sources = _python_files(package.root)
            current_revision = self._package_revision(
                package.package_id,
                package.root,
                current_sources,
            )
        except OSError as exc:
            raise KeyError(
                f"custom tool source could not be verified: {exc}"
            ) from exc
        if (
            current_sources != package.source_files
            or current_revision != tool.revision
        ):
            raise KeyError(
                "custom tool source changed after this execution identity was frozen"
            )
        return package, tool

    def get_tool_metadata(self, name: str) -> dict[str, Any]:
        _package, tool = self.resolve_tool(name)
        return deepcopy(tool.metadata)

    def get_tool_timeout(self, name: str) -> float | None:
        return 180.0 if self.has_tool(name) else None

    async def _after_reload(self, *, reason: str) -> None:
        try:
            from cyrene.observability.debug import publish_event

            await publish_event({
                "type": "custom_tools_changed",
                "reason": reason,
                "generation": self._generation,
                "status": self.status(),
            })
        except Exception:
            logger.debug("Unable to publish custom-tool change event", exc_info=True)

    @staticmethod
    def _invalidate_catalog() -> None:
        try:
            invalidate_tool_caches()
        except Exception:
            logger.debug("Unable to invalidate custom-tool wire cache", exc_info=True)


_manager: CustomToolManager | None = None


def get_custom_tool_manager() -> CustomToolManager:
    global _manager
    if _manager is None:
        _manager = CustomToolManager()
    return _manager


async def start_custom_tools() -> None:
    await get_custom_tool_manager().start()


async def stop_custom_tools() -> None:
    await get_custom_tool_manager().stop()


def stop_custom_tools_sync() -> None:
    get_custom_tool_manager().stop_sync()


__all__ = [
    "CUSTOM_TOOLS_ROOT",
    "CustomToolManager",
    "get_custom_tool_manager",
    "start_custom_tools",
    "stop_custom_tools",
    "stop_custom_tools_sync",
]
