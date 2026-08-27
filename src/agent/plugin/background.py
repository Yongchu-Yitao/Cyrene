"""Run user-editable background Plugins from one host-owned clock."""

from __future__ import annotations

import asyncio
import logging
import weakref
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .native_tools import seed_builtin_plugin_directory
from .plugin import Plugin, PluginContext
from .registry import PluginRegistry, default_plugin_impl_directory
from .runtime import PluginRuntime

logger = logging.getLogger(__name__)

BACKGROUND_JOB_METADATA = "background_job"
BACKGROUND_SYNC_JOB_ID = "plugin_registry_sync"


@dataclass(frozen=True, slots=True)
class BackgroundJobSpec:
    plugin_name: str
    job_id: str
    interval_seconds: int
    arguments: dict[str, Any]
    coalesce: bool
    max_instances: int
    run_on_start: bool

    @property
    def signature(self) -> tuple[Any, ...]:
        return (
            self.plugin_name,
            self.interval_seconds,
            repr(sorted(self.arguments.items())),
            self.coalesce,
            self.max_instances,
            self.run_on_start,
        )


@dataclass(frozen=True, slots=True)
class _BackgroundJobBinding:
    spec: BackgroundJobSpec
    pack_id: str | None
    source: str
    handler_identity: int

    @property
    def signature(self) -> tuple[Any, ...]:
        return (
            *self.spec.signature,
            self.pack_id,
            self.source,
            self.handler_identity,
        )


_BACKGROUND_HOSTS: weakref.WeakSet["BackgroundPluginHost"] = weakref.WeakSet()
_BACKGROUND_SCHEDULER: AsyncIOScheduler | None = None
_MAINTENANCE_LOCK: asyncio.Lock | None = None
_MAINTENANCE_LOCK_LOOP: asyncio.AbstractEventLoop | None = None


def maintenance_lock() -> asyncio.Lock:
    """Serialize heavyweight maintenance contributed by background Plugins."""

    global _MAINTENANCE_LOCK, _MAINTENANCE_LOCK_LOOP
    loop = asyncio.get_running_loop()
    if _MAINTENANCE_LOCK is None or _MAINTENANCE_LOCK_LOOP is not loop:
        _MAINTENANCE_LOCK = asyncio.Lock()
        _MAINTENANCE_LOCK_LOOP = loop
    return _MAINTENANCE_LOCK


def setup_background_plugin_scheduler(db_path: str) -> AsyncIOScheduler:
    """Create the single host clock used by editable background Plugins."""

    global _BACKGROUND_SCHEDULER
    try:
        from cyrene.workbench.context import configure_store as configure_context
        from cyrene.workbench.notifications import (
            configure_store as configure_notifications,
        )

        configure_context(str(db_path))
        configure_notifications(str(db_path))
    except Exception:
        logger.debug(
            "Could not configure Workbench SQLite stores for background Plugins",
            exc_info=True,
        )
    scheduler = AsyncIOScheduler()
    host = BackgroundPluginHost(
        scheduler,
        data={"source": "background", "db_path": str(db_path)},
    )
    host.attach()
    # Keep the clock reachable for lifecycle operations such as atomic restore;
    # the host is retained by the scheduler's registered bound callbacks.
    _BACKGROUND_SCHEDULER = scheduler
    return scheduler


def background_plugin_scheduler() -> AsyncIOScheduler | None:
    return _BACKGROUND_SCHEDULER


async def reconcile_background_plugin_hosts(application_host: Any) -> None:
    """Immediately project authoritative application state into every clock."""

    for host in tuple(_BACKGROUND_HOSTS):
        if host.owns_application_host(application_host):
            await host.reconcile(application_host=application_host)


def background_job_spec(plugin: Plugin) -> BackgroundJobSpec | None:
    raw = plugin.metadata.get(BACKGROUND_JOB_METADATA)
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise TypeError(f"{plugin.name} background_job metadata must be an object")
    if plugin.kind != "tool":
        raise ValueError(f"background Plugin must be a tool: {plugin.name}")
    if plugin.model_visible:
        raise ValueError(
            f"background Plugin must set metadata.model_visible=false: {plugin.name}"
        )
    seconds = raw.get("interval_seconds")
    if callable(seconds):
        seconds = seconds()
    if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0:
        raise ValueError(
            f"{plugin.name} background_job.interval_seconds must be a positive integer"
        )
    max_instances = raw.get("max_instances", 1)
    if (
        isinstance(max_instances, bool)
        or not isinstance(max_instances, int)
        or max_instances <= 0
    ):
        raise ValueError(
            f"{plugin.name} background_job.max_instances must be a positive integer"
        )
    arguments = raw.get("arguments", {})
    if not isinstance(arguments, Mapping):
        raise TypeError(f"{plugin.name} background_job.arguments must be an object")
    return BackgroundJobSpec(
        plugin_name=plugin.name,
        job_id=str(raw.get("id") or f"plugin:{plugin.name}").strip(),
        interval_seconds=seconds,
        arguments=dict(arguments),
        coalesce=bool(raw.get("coalesce", True)),
        max_instances=max_instances,
        run_on_start=bool(raw.get("run_on_start", False)),
    )


class BackgroundPluginHost:
    """Discover background metadata and invoke current Plugin code each tick."""

    def __init__(
        self,
        scheduler: AsyncIOScheduler,
        *,
        plugin_directory: str | Path | None = None,
        services: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        workspace: str | Path | None = None,
        refresh_seconds: int = 30,
    ) -> None:
        self.scheduler = scheduler
        self.plugin_directory = Path(
            plugin_directory or default_plugin_impl_directory()
        ).expanduser().resolve()
        self.services = dict(services or {})
        self.data = dict(data or {})
        self.workspace = (
            Path(workspace).expanduser().resolve() if workspace else None
        )
        self.refresh_seconds = max(5, int(refresh_seconds))
        self._authoritative_host_ref: weakref.ReferenceType[Any] | None = None
        application_host = self._application_host()
        if application_host is not None:
            self._authoritative_host_ref = weakref.ref(application_host)
            self.registry = application_host.registry
            failures = application_host.load_failures
        else:
            seed_builtin_plugin_directory(self.plugin_directory)
            self.registry = PluginRegistry()
            failures = self.registry.load_directory(self.plugin_directory)
        if failures:
            logger.warning(
                "Background Plugin load failures: %s",
                "; ".join(f"{item.path.name}: {item.error}" for item in failures),
            )
        self.runtime = PluginRuntime(self.registry)
        self._installed: dict[str, _BackgroundJobBinding] = {}
        self._running_tasks: dict[
            asyncio.Task[Any], tuple[str, str | None, str, int]
        ] = {}
        self._attached = False
        self._closed = False

    def attach(self) -> None:
        if self._attached:
            return
        self._attached = True
        _BACKGROUND_HOSTS.add(self)
        application_host = self._application_host()
        if application_host is not None and self._authoritative_host_ref is None:
            self._authoritative_host_ref = weakref.ref(application_host)
        self._synchronize_now(refresh=False, application_host=application_host)
        self.scheduler.add_job(
            self.synchronize,
            "interval",
            seconds=self.refresh_seconds,
            id=BACKGROUND_SYNC_JOB_ID,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )

    async def synchronize(self) -> None:
        await self.reconcile(refresh=True)

    def _application_host(self, application_host: Any | None = None) -> Any | None:
        if application_host is not None:
            return application_host
        if self._authoritative_host_ref is not None:
            bound = self._authoritative_host_ref()
            if bound is not None:
                return bound
        from .application import active_plugin_application_host

        return active_plugin_application_host()

    def owns_application_host(self, application_host: Any) -> bool:
        if self._authoritative_host_ref is None:
            return True
        return self._authoritative_host_ref() is application_host

    def _desired_bindings(
        self,
        application_host: Any | None,
        *,
        refresh: bool,
    ) -> dict[str, _BackgroundJobBinding]:
        if application_host is not None:
            if not application_host.started:
                return {}
            registry = application_host.registry
            failures = application_host.load_failures
        else:
            registry = self.registry
            failures = registry.refresh() if refresh else ()
        failed_sources = {str(item.path.resolve()) for item in failures}
        desired: dict[str, _BackgroundJobBinding] = {}
        for registered in registry.list_plugins():
            try:
                source = str(Path(registered.source).resolve())
            except (OSError, ValueError):
                source = registered.source
            if source in failed_sources:
                continue
            if not registry.plugin_enabled(registered.plugin.name):
                continue
            if (
                application_host is not None
                and registered.pack_id is not None
                and not application_host.pack_operational(registered.pack_id)
            ):
                continue
            try:
                spec = background_job_spec(registered.plugin)
            except Exception:
                logger.exception(
                    "Invalid background metadata for Plugin %s",
                    registered.plugin.name,
                )
                continue
            if spec is None:
                continue
            if not spec.job_id:
                logger.error("Background Plugin %s has an empty job id", spec.plugin_name)
                continue
            if spec.job_id in desired:
                logger.error("Duplicate background job id: %s", spec.job_id)
                continue
            desired[spec.job_id] = _BackgroundJobBinding(
                spec=spec,
                pack_id=registered.pack_id,
                source=registered.source,
                handler_identity=id(registered.plugin.handler),
            )
        return desired

    def _synchronize_now(
        self,
        *,
        refresh: bool,
        application_host: Any | None = None,
    ) -> dict[str, _BackgroundJobBinding]:
        application_host = self._application_host(application_host)
        desired = self._desired_bindings(application_host, refresh=refresh)

        for job_id in tuple(self._installed):
            if job_id not in desired:
                try:
                    self.scheduler.remove_job(job_id)
                except Exception:
                    logger.debug("Background job was already absent: %s", job_id)
                self._installed.pop(job_id, None)

        for job_id, binding in desired.items():
            spec = binding.spec
            installed = self._installed.get(job_id)
            if installed is not None and installed.signature == binding.signature:
                continue
            options: dict[str, Any] = {}
            if spec.run_on_start:
                options["next_run_time"] = datetime.now(timezone.utc)
            self.scheduler.add_job(
                self._invoke,
                "interval",
                seconds=spec.interval_seconds,
                args=[
                    spec.plugin_name,
                    dict(spec.arguments),
                    job_id,
                    binding.pack_id,
                    binding.source,
                    binding.handler_identity,
                ],
                id=job_id,
                replace_existing=True,
                coalesce=spec.coalesce,
                max_instances=spec.max_instances,
                **options,
            )
            self._installed[job_id] = binding
        return desired

    async def reconcile(
        self,
        *,
        application_host: Any | None = None,
        refresh: bool = False,
    ) -> None:
        if self._closed:
            return
        if application_host is not None and self._authoritative_host_ref is None:
            self._authoritative_host_ref = weakref.ref(application_host)
        desired = self._synchronize_now(
            refresh=refresh,
            application_host=application_host,
        )
        to_cancel: list[asyncio.Task[Any]] = []
        for task, (job_id, pack_id, source, handler_identity) in tuple(
            self._running_tasks.items()
        ):
            binding = desired.get(job_id)
            if (
                binding is None
                or binding.pack_id != pack_id
                or binding.source != source
                or binding.handler_identity != handler_identity
            ):
                task.cancel()
                to_cancel.append(task)
        if to_cancel:
            await asyncio.gather(*to_cancel, return_exceptions=True)

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        _BACKGROUND_HOSTS.discard(self)
        for job_id in tuple(self._installed):
            try:
                self.scheduler.remove_job(job_id)
            except Exception:
                logger.debug("Background job was already absent: %s", job_id)
        self._installed.clear()
        try:
            self.scheduler.remove_job(BACKGROUND_SYNC_JOB_ID)
        except Exception:
            logger.debug("Background registry sync job was already absent")
        tasks = tuple(self._running_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._running_tasks.clear()

    async def _invoke(
        self,
        plugin_name: str,
        arguments: dict[str, Any],
        job_id: str,
        pack_id: str | None,
        source: str,
        handler_identity: int,
    ) -> None:
        application_host = self._application_host()
        binding = self._installed.get(job_id)
        if (
            self._closed
            or binding is None
            or binding.pack_id != pack_id
            or binding.source != source
            or binding.handler_identity != handler_identity
        ):
            return
        registry = application_host.registry if application_host is not None else self.registry
        registered = next(
            (
                item
                for item in registry.list_plugins()
                if item.plugin.name == plugin_name
            ),
            None,
        )
        if (
            registered is None
            or registered.pack_id != pack_id
            or registered.source != source
            or id(registered.plugin.handler) != handler_identity
            or not registry.plugin_enabled(plugin_name)
            or (
                application_host is not None
                and pack_id is not None
                and not application_host.pack_operational(pack_id)
            )
        ):
            logger.debug("Background Plugin %s is unavailable; skipping", plugin_name)
            return
        current = asyncio.current_task()
        if current is not None:
            self._running_tasks[current] = (
                job_id,
                pack_id,
                source,
                handler_identity,
            )
        services = dict(self.services)
        if application_host is not None:
            services.update(
                {
                    name: value
                    for name in application_host.services
                    if (value := application_host.service(name)) is not None
                }
            )
        try:
            runtime = PluginRuntime(registry)
            result = await runtime.call(
                plugin_name,
                arguments,
                PluginContext(
                    workspace=self.workspace,
                    data={**self.data, "background_job_id": job_id},
                    services=services,
                ),
                call_id=f"background:{job_id}:{datetime.now(timezone.utc).isoformat()}",
            )
            if not result.success:
                logger.error(
                    "Background Plugin %s failed: %s",
                    plugin_name,
                    result.error,
                )
        finally:
            if current is not None:
                self._running_tasks.pop(current, None)


__all__ = [
    "BACKGROUND_JOB_METADATA",
    "BACKGROUND_SYNC_JOB_ID",
    "BackgroundJobSpec",
    "BackgroundPluginHost",
    "background_plugin_scheduler",
    "background_job_spec",
    "maintenance_lock",
    "reconcile_background_plugin_hosts",
    "setup_background_plugin_scheduler",
]
