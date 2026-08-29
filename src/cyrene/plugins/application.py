"""Application host for process-level contributions from loaded Plugin packs."""

from __future__ import annotations

import hashlib
import inspect
import logging
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, WebSocketException
from starlette.requests import HTTPConnection

from cyrene.core.plugin.activation import (
    PluginActivationState,
    set_active_plugin_activation_state,
)
from cyrene.core.plugin.customization import (
    PluginCustomizationState,
    set_active_plugin_customization_state,
)
from cyrene.plugins.model_gateway import PluginModelGateway, ensure_model_router
from cyrene.plugins.model_catalog import configured_context_limit
from .native_tools import BuiltinPluginSeedResult, seed_builtin_plugin_directory
from .contributions import (
    frontend_views,
    project_tools as _plugin_project_tools,
    serialize_workbench_surface,
    serialize_workspace_action,
    serialize_workspace_file_type,
    validate_workbench_contributions,
    workbench_surfaces,
    workspace_actions,
    workspace_file_types,
)
from .context import (
    PluginApplicationContext,
    PluginFrontendHandler,
    PluginLifecycleHandler,
    PluginSearchHandler,
)
from cyrene.core.plugin.registry import (
    PluginLoadFailure,
    PluginRegistry,
    PluginRegistryError,
    default_plugin_impl_directory,
)
from cyrene.core.plugin.runtime import PluginRuntime
from cyrene.core.plugin.scopes import (
    application_plugin_scope as _core_application_plugin_scope,
    application_plugin_service as _core_application_plugin_service,
    set_application_plugin_scope as _set_core_application_plugin_scope,
)

logger = logging.getLogger(__name__)


def _lifecycle_handler_name(handler: PluginLifecycleHandler) -> str:
    """Return a stable, log-friendly identity for a lifecycle contribution."""

    module = str(getattr(handler, "__module__", "") or "").strip()
    name = str(
        getattr(handler, "__qualname__", "")
        or getattr(handler, "__name__", "")
        or type(handler).__qualname__
    ).strip()
    return f"{module}.{name}" if module else name


class PluginApplicationHost:
    """Attach application contributions from the same registry used by Agents."""

    def __init__(
        self,
        *,
        app: FastAPI,
        registry: PluginRegistry,
        bot: Any,
        db_path: str,
        data_directory: str | Path,
        plugin_directory: str | Path,
        load_failures: tuple[PluginLoadFailure, ...] = (),
    ) -> None:
        self.app = app
        self.registry = registry
        self.bot = bot
        self.db_path = str(db_path or "")
        self.data_directory = Path(data_directory).expanduser().resolve()
        self.plugin_directory = Path(plugin_directory).expanduser().resolve()
        self.load_failures = tuple(load_failures)
        ensure_model_router(self.registry)
        self.runtime = PluginRuntime(self.registry)
        self.model_gateway = PluginModelGateway(self.registry, self.runtime)
        self.services: dict[str, Any] = {
            "model": self.model_gateway,
            "model_context_limit": configured_context_limit,
            "plugin_seed": seed_builtin_plugin_directory,
        }
        self._service_owners: dict[str, str] = {}
        self._search_providers: dict[str, PluginSearchHandler] = {}
        self._search_provider_owners: dict[str, str] = {}
        self._frontend_modules: list[str] = []
        self._frontend_module_owners: dict[str, str] = {}
        self._frontend_methods: dict[str, dict[str, PluginFrontendHandler]] = {}
        self._startup_handlers: dict[str, list[PluginLifecycleHandler]] = {}
        self._shutdown_handlers: dict[str, list[PluginLifecycleHandler]] = {}
        self._running_packs: set[str] = set()
        self._attached_packs: list[str] = []
        self._attached_pack_sources: dict[str, str] = {}
        self._attached_pack_generations: dict[str, tuple[Any, ...]] = {}
        self._restart_required_packs: set[str] = set()
        self._setup_failures: dict[str, str] = {}
        self._startup_failures: dict[str, str] = {}
        self._attached = False
        self._started = False

    @classmethod
    def load_user_plugins(
        cls,
        *,
        app: FastAPI,
        bot: Any,
        db_path: str,
        data_directory: str | Path,
        plugin_directory: str | Path | None = None,
    ) -> "PluginApplicationHost":
        root = Path(plugin_directory or default_plugin_impl_directory()).expanduser().resolve()
        seed_builtin_plugin_directory(root)
        from cyrene.runtime import settings_store

        customization_state = PluginCustomizationState(
            settings_store.get("plugin_tool_customizations", {}) or {}
        )
        set_active_plugin_customization_state(customization_state)
        registry = PluginRegistry(
            activation=PluginActivationState(),
            customizations=customization_state,
        )
        ensure_model_router(registry)
        failures = registry.load_directory(root)
        registry.configure_activation(
            plugins=settings_store.get_enabled_plugins(),
            packs=settings_store.get_enabled_plugin_packs(),
        )
        if failures:
            logger.warning(
                "Some Plugin application contributions failed to load: %s",
                "; ".join(f"{item.path}: {item.error}" for item in failures),
            )
        return cls(
            app=app,
            registry=registry,
            bot=bot,
            db_path=db_path,
            data_directory=data_directory,
            plugin_directory=root,
            load_failures=failures,
        )

    async def reload_user_plugins(
        self,
    ) -> tuple[BuiltinPluginSeedResult, tuple[PluginLoadFailure, ...]]:
        """Refresh contributions and immediately reconcile process lifecycle."""

        seed_error: Exception | None = None
        seeded: BuiltinPluginSeedResult | None = None
        try:
            seeded = seed_builtin_plugin_directory(self.plugin_directory)
        except Exception as exc:
            seed_error = exc
        try:
            failures = self.registry.refresh_directory(self.plugin_directory)
            self.load_failures = tuple(failures)
            self._reconcile_attachment_generations()
        finally:
            await self.reconcile_activation()
        if seed_error is not None:
            raise seed_error
        assert seeded is not None
        return seeded, self.load_failures

    @property
    def attached_packs(self) -> tuple[str, ...]:
        return tuple(self._attached_packs)

    @property
    def setup_failures(self) -> dict[str, str]:
        return dict(self._setup_failures)

    @property
    def startup_failures(self) -> dict[str, str]:
        return dict(self._startup_failures)

    @property
    def restart_required_packs(self) -> tuple[str, ...]:
        return tuple(sorted(self._restart_required_packs))

    @property
    def started(self) -> bool:
        return self._started

    @staticmethod
    def _source_generation(source: str) -> tuple[Any, ...]:
        """Return a stable content generation for one application source."""

        if source == "core" or source.startswith("mcp:"):
            return ("logical", source)
        path = Path(source)
        try:
            files = (
                tuple(sorted(path.rglob("*.py")))
                if path.is_dir()
                else (path,)
                if path.is_file()
                else ()
            )
            digest = hashlib.sha256()
            for item in files:
                relative = item.relative_to(path) if path.is_dir() else Path(item.name)
                digest.update(str(relative).encode("utf-8"))
                digest.update(b"\0")
                digest.update(item.read_bytes())
                digest.update(b"\0")
            return ("files", len(files), digest.hexdigest())
        except OSError as exc:
            return ("unreadable", source, type(exc).__name__)

    def _reconcile_attachment_generations(self) -> None:
        """Quarantine application closures that no longer match registry code."""

        packs = {pack.id: pack for pack in self.registry.list_packs()}
        for pack_id in self._attached_packs:
            pack = packs.get(pack_id)
            if pack is None:
                self._restart_required_packs.add(pack_id)
                continue
            try:
                source = self.registry.pack_source(pack_id)
            except PluginRegistryError:
                self._restart_required_packs.add(pack_id)
                continue
            if (
                source != self._attached_pack_sources.get(pack_id)
                or self._source_generation(source)
                != self._attached_pack_generations.get(pack_id)
            ):
                self._restart_required_packs.add(pack_id)
        for pack in packs.values():
            if (
                pack.has_application_contributions
                and pack.id not in self._attached_packs
                and self._pack_enabled(pack.id)
            ):
                self._restart_required_packs.add(pack.id)

    def _pack_enabled(self, pack_id: str) -> bool:
        """Treat removed or failed-to-reload packs as unavailable."""

        try:
            return self.registry.pack_enabled(pack_id)
        except (KeyError, PluginRegistryError):
            return False

    def _pack_available(self, pack_id: str) -> bool:
        if not self._pack_enabled(pack_id):
            return False
        return not self._started or pack_id in self._running_packs

    def pack_running(self, pack_id: str) -> bool:
        return str(pack_id) in self._running_packs

    def pack_restart_required(self, pack_id: str) -> bool:
        return str(pack_id) in self._restart_required_packs

    def pack_operational(self, pack_id: str) -> bool:
        """Return whether a pack may currently serve process/background work."""

        normalized = str(pack_id)
        if not self._pack_enabled(normalized):
            return False
        if not self._started:
            return False
        if normalized in self._attached_packs:
            return normalized in self._running_packs
        pack = next(
            (item for item in self.registry.list_packs() if item.id == normalized),
            None,
        )
        return pack is not None and not pack.has_application_contributions

    @property
    def frontend_modules(self) -> list[str]:
        """Return only Workbench surfaces whose owning pack is enabled."""

        return [
            module
            for module in self._frontend_modules
            if self._pack_available(self._frontend_module_owners[module])
        ]

    def workbench_contributions(self) -> dict[str, list[dict[str, Any]]]:
        """Return capabilities owned by currently operational Plugin packs."""

        views: list[dict[str, Any]] = []
        project_tools: list[dict[str, Any]] = []
        surfaces: list[dict[str, Any]] = []
        file_types: list[dict[str, Any]] = []
        actions: list[dict[str, Any]] = []
        for pack in self.registry.list_packs():
            if not self.pack_operational(pack.id):
                continue
            pack_views = frontend_views(pack)
            view_ids = {str(item.get("id") or "") for item in pack_views}
            for raw in pack_views:
                item = dict(raw)
                item["pack_id"] = pack.id
                views.append(item)
            for raw in _plugin_project_tools(pack):
                item = dict(raw)
                if str(item.get("view") or "") not in view_ids:
                    continue
                item["pack_id"] = pack.id
                project_tools.append(item)
            surfaces.extend(
                serialize_workbench_surface(pack, value)
                for value in workbench_surfaces(pack)
            )
            file_types.extend(
                serialize_workspace_file_type(pack, value)
                for value in workspace_file_types(pack)
            )
            actions.extend(
                serialize_workspace_action(pack, value)
                for value in workspace_actions(pack)
            )
        return {
            "views": views,
            "project_tools": project_tools,
            "surfaces": surfaces,
            "file_types": file_types,
            "actions": actions,
        }

    def frontend_contributions(self) -> dict[str, list[dict[str, Any]]]:
        """Compatibility alias for callers using the original UI-only name."""

        return self.workbench_contributions()

    def frontend_asset_path(self, pack_id: str, asset_path: str) -> Path:
        """Resolve one enabled view asset without exposing the pack's Python source."""

        normalized_pack = str(pack_id or "").strip()
        if not self.pack_operational(normalized_pack):
            raise PluginRegistryError(
                f"Plugin pack is unavailable: {normalized_pack}"
            )
        pack = next(
            (item for item in self.registry.list_packs() if item.id == normalized_pack),
            None,
        )
        if pack is None:
            raise PluginRegistryError(f"Plugin pack is not registered: {normalized_pack}")
        source = Path(self.registry.pack_source(normalized_pack)).resolve()
        if not source.is_dir():
            raise PluginRegistryError("Plugin frontend views require a pack directory")
        relative = Path(str(asset_path or "").replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise PluginRegistryError("Plugin frontend asset path is invalid")
        target = (source / relative).resolve()
        allowed = False
        for view in frontend_views(pack):
            view_root = (source / Path(str(view.get("entry") or "")).parent).resolve()
            if target == view_root or view_root in target.parents:
                allowed = True
                break
        if not allowed or not target.is_file():
            raise PluginRegistryError("Plugin frontend asset was not found")
        return target

    async def call_frontend_method(
        self,
        pack_id: str,
        method: str,
        arguments: Any,
        *,
        project_id: str = "",
    ) -> Any:
        """Invoke a JSON RPC method registered by one operational Plugin pack."""

        normalized_pack = str(pack_id or "").strip()
        normalized_method = str(method or "").strip()
        if not self.pack_operational(normalized_pack):
            raise PluginRegistryError(
                f"Plugin pack is unavailable: {normalized_pack}"
            )
        handler = self._frontend_methods.get(normalized_pack, {}).get(normalized_method)
        if handler is None:
            raise PluginRegistryError(
                f"Plugin frontend method is not registered: {normalized_method}"
            )
        result = handler(
            arguments,
            {"pack_id": normalized_pack, "project_id": str(project_id or "")},
        )
        return await result if inspect.isawaitable(result) else result

    @property
    def search_providers(self) -> dict[str, PluginSearchHandler]:
        """Return only search providers whose owning pack is enabled."""

        return {
            name: provider
            for name, provider in self._search_providers.items()
            if self._pack_available(self._search_provider_owners[name])
        }

    @property
    def active_services(self) -> dict[str, Any]:
        """Return core services plus services whose owning pack is enabled."""

        return {
            name: service
            for name, service in self.services.items()
            if (
                (owner := self._service_owners.get(name)) is None
                or self._pack_available(owner)
            )
        }

    def _pack_guard(self, pack_id: str):
        async def unavailable(connection: HTTPConnection, status_code: int, detail: str) -> None:
            if connection.scope.get("type") == "websocket":
                raise WebSocketException(code=4404, reason=detail)
            raise HTTPException(status_code=status_code, detail=detail)

        async def require_enabled(connection: HTTPConnection) -> None:
            if not self._pack_enabled(pack_id):
                await unavailable(
                    connection,
                    404,
                    f"Plugin pack is unavailable: {pack_id}",
                )
            if self._started and pack_id not in self._running_packs:
                await unavailable(
                    connection,
                    503,
                    (
                        self._startup_failures.get(pack_id)
                        or f"Plugin pack did not start: {pack_id}"
                    ),
                )

        return require_enabled

    def attach(self, parent_router: APIRouter) -> None:
        """Run each pack setup against an isolated router and commit atomically."""

        if self._attached:
            raise RuntimeError("Plugin application host is already attached")
        self._attached = True
        for pack in self.registry.list_packs():
            validate_workbench_contributions(pack)
            if not pack.has_application_contributions:
                continue
            # Optional packs that are disabled at process composition time must
            # remain completely inert.  Running application_setup here would
            # still construct stores, managers, route closures, and other
            # process state even though guards later hide the contribution.
            # Enabling one of these packs is therefore an explicit restart
            # boundary; the next composition attaches its application surface.
            if not self._pack_enabled(pack.id):
                continue
            child_router = APIRouter()
            inherited_services = dict(self.services)
            services: dict[str, Any] = dict(inherited_services)
            frontend_modules: list[str] = []
            search_providers: dict[str, PluginSearchHandler] = {}
            startup_handlers: list[PluginLifecycleHandler] = []
            shutdown_handlers: list[PluginLifecycleHandler] = []
            frontend_methods: dict[str, PluginFrontendHandler] = {}
            context = PluginApplicationContext(
                app=self.app,
                router=child_router,
                bot=self.bot,
                db_path=self.db_path,
                data_directory=self.data_directory,
                plugin_directory=self.plugin_directory,
                services=services,
                frontend_modules=frontend_modules,
                search_providers=search_providers,
                startup_handlers=startup_handlers,
                shutdown_handlers=shutdown_handlers,
                frontend_methods=frontend_methods,
                registry=self.registry,
            )
            try:
                for setup in pack.application_setups:
                    setup(context)
                service_collisions = {name for name, value in services.items() if name in inherited_services and value is not inherited_services[name]}
                search_collisions = set(search_providers) & set(self._search_providers)
                if service_collisions:
                    raise ValueError("application service collision: " + ", ".join(sorted(service_collisions)))
                if search_collisions:
                    raise ValueError("search provider collision: " + ", ".join(sorted(search_collisions)))
            except Exception as exc:
                self._setup_failures[pack.id] = str(exc)
                logger.exception("Failed to attach Plugin application pack %s", pack.id)
                if bool(pack.metadata.get("required")):
                    raise RuntimeError(
                        f"Required Plugin application pack failed to attach: {pack.id}"
                    ) from exc
                continue
            parent_router.include_router(
                child_router,
                dependencies=[Depends(self._pack_guard(pack.id))],
            )
            provided_services = {
                name: value
                for name, value in services.items()
                if name not in inherited_services
            }
            self.services.update(provided_services)
            self._service_owners.update(
                {name: pack.id for name in provided_services}
            )
            self._search_providers.update(search_providers)
            self._search_provider_owners.update(
                {name: pack.id for name in search_providers}
            )
            for module in frontend_modules:
                if module not in self._frontend_modules:
                    self._frontend_modules.append(module)
                    self._frontend_module_owners[module] = pack.id
            self._startup_handlers[pack.id] = list(startup_handlers)
            self._shutdown_handlers[pack.id] = list(shutdown_handlers)
            self._frontend_methods[pack.id] = dict(frontend_methods)
            self._attached_packs.append(pack.id)
            source = self.registry.pack_source(pack.id)
            self._attached_pack_sources[pack.id] = source
            self._attached_pack_generations[pack.id] = self._source_generation(source)

    @staticmethod
    async def _call_lifecycle(handler: PluginLifecycleHandler) -> None:
        result = handler()
        if inspect.isawaitable(result):
            await result

    async def _start_pack(self, pack_id: str) -> None:
        if pack_id in self._running_packs:
            return
        self._startup_failures.pop(pack_id, None)
        handlers = tuple(self._startup_handlers.get(pack_id, ()))
        pack_started = time.perf_counter()
        current_stage = "prepare"
        stage_started = pack_started
        logger.info(
            "Plugin startup pack begin pack=%s handlers=%d",
            pack_id,
            len(handlers),
        )
        try:
            for index, handler in enumerate(handlers, start=1):
                handler_name = _lifecycle_handler_name(handler)
                current_stage = f"handler[{index}/{len(handlers)}] {handler_name}"
                stage_started = time.perf_counter()
                logger.info(
                    "Plugin startup handler begin pack=%s handler=%s index=%d/%d",
                    pack_id,
                    handler_name,
                    index,
                    len(handlers),
                )
                await self._call_lifecycle(handler)
                logger.info(
                    "Plugin startup handler complete pack=%s handler=%s "
                    "index=%d/%d elapsed_ms=%.1f",
                    pack_id,
                    handler_name,
                    index,
                    len(handlers),
                    (time.perf_counter() - stage_started) * 1000,
                )
        except Exception as exc:
            self._startup_failures[pack_id] = str(exc)
            logger.exception(
                "Plugin startup pack failed pack=%s stage=%s "
                "stage_elapsed_ms=%.1f total_elapsed_ms=%.1f",
                pack_id,
                current_stage,
                (time.perf_counter() - stage_started) * 1000,
                (time.perf_counter() - pack_started) * 1000,
            )
            for handler in reversed(self._shutdown_handlers.get(pack_id, ())):
                try:
                    await self._call_lifecycle(handler)
                except Exception:
                    logger.exception(
                        "Plugin application rollback failed for pack %s",
                        pack_id,
                    )
            return
        self._running_packs.add(pack_id)
        logger.info(
            "Plugin startup pack complete pack=%s handlers=%d elapsed_ms=%.1f",
            pack_id,
            len(handlers),
            (time.perf_counter() - pack_started) * 1000,
        )

    async def _stop_pack(self, pack_id: str) -> None:
        if pack_id not in self._running_packs:
            return
        # Make the pack non-operational before touching its services so clocks
        # cannot start or continue work while shutdown handlers are running.
        self._running_packs.discard(pack_id)
        from cyrene.plugins.background import reconcile_background_plugin_hosts

        await reconcile_background_plugin_hosts(self)
        try:
            for handler in reversed(self._shutdown_handlers.get(pack_id, ())):
                try:
                    await self._call_lifecycle(handler)
                except Exception:
                    logger.exception(
                        "Plugin application shutdown failed for pack %s",
                        pack_id,
                    )
        finally:
            self._running_packs.discard(pack_id)

    async def reconcile_activation(self) -> None:
        """Apply pack switches to process lifecycle contributions immediately."""

        for pack in self.registry.list_packs():
            if (
                pack.has_application_contributions
                and pack.id not in self._attached_packs
                and self._pack_enabled(pack.id)
            ):
                self._restart_required_packs.add(pack.id)

        if self._started:
            for pack_id in reversed(self._attached_packs):
                if not self._pack_enabled(pack_id):
                    await self._stop_pack(pack_id)
            for pack_id in self._attached_packs:
                if (
                    self._pack_enabled(pack_id)
                    and (
                        not self.pack_restart_required(pack_id)
                        or pack_id in self._running_packs
                    )
                ):
                    await self._start_pack(pack_id)
        from cyrene.plugins.background import reconcile_background_plugin_hosts

        await reconcile_background_plugin_hosts(self)

    async def startup(self) -> None:
        if self._started:
            return
        started = time.perf_counter()
        logger.info(
            "Plugin startup host begin attached_packs=%d enabled_packs=%d",
            len(self._attached_packs),
            sum(1 for pack_id in self._attached_packs if self._pack_enabled(pack_id)),
        )
        self._started = True
        try:
            await self.reconcile_activation()
        except BaseException:
            logger.exception(
                "Plugin startup host failed running_packs=%d "
                "startup_failures=%d elapsed_ms=%.1f",
                len(self._running_packs),
                len(self._startup_failures),
                (time.perf_counter() - started) * 1000,
            )
            raise
        logger.info(
            "Plugin startup host complete running_packs=%d "
            "startup_failures=%d elapsed_ms=%.1f",
            len(self._running_packs),
            len(self._startup_failures),
            (time.perf_counter() - started) * 1000,
        )

    async def shutdown(self) -> None:
        if not self._started:
            return
        for pack_id in reversed(self._attached_packs):
            await self._stop_pack(pack_id)
        self._started = False
        from cyrene.plugins.background import reconcile_background_plugin_hosts

        await reconcile_background_plugin_hosts(self)

    def service(self, name: str) -> Any | None:
        normalized = str(name or "").strip()
        owner = self._service_owners.get(normalized)
        if owner is not None and not self._pack_available(owner):
            return None
        return self.services.get(normalized)


_ACTIVE_LOCK = threading.RLock()
_ACTIVE_HOST: PluginApplicationHost | None = None


def set_application_plugin_scope(
    host: PluginApplicationHost | None,
) -> None:
    global _ACTIVE_HOST
    with _ACTIVE_LOCK:
        _ACTIVE_HOST = host
        _set_core_application_plugin_scope(host)
        set_active_plugin_activation_state(host.registry.activation if host is not None else None)
        set_active_plugin_customization_state(
            host.registry.customizations if host is not None else None
        )


def application_plugin_scope() -> PluginApplicationHost | None:
    scope = _core_application_plugin_scope()
    return scope if isinstance(scope, PluginApplicationHost) else None


def application_plugin_service(name: str) -> Any | None:
    return _core_application_plugin_service(name)


def resolve_plugin_registry(
    plugin_directory: str | Path,
) -> tuple[PluginRegistry, bool]:
    """Return the authoritative Registry and whether a session must load it.

    The application host owns the editable Plugin directory for the lifetime
    of the process. Agent sessions using that same directory share its loaded
    contribution definitions and keep only their Hooks/services session-local.
    Standalone embedders and alternate directories retain the isolated loader.
    """

    root = Path(plugin_directory).expanduser().resolve()
    host = application_plugin_scope()
    if host is not None and host.plugin_directory == root:
        return host.registry, False
    seed_builtin_plugin_directory(root)
    registry = PluginRegistry()
    ensure_model_router(registry)
    return registry, True


__all__ = [
    "PluginApplicationHost",
    "application_plugin_scope",
    "application_plugin_service",
    "resolve_plugin_registry",
    "set_application_plugin_scope",
]
