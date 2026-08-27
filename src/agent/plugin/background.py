"""Run user-editable background Plugins from one host-owned clock."""

from __future__ import annotations

import logging
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
        seed_builtin_plugin_directory(self.plugin_directory)
        self.registry = PluginRegistry()
        failures = self.registry.load_directory(self.plugin_directory)
        if failures:
            logger.warning(
                "Background Plugin load failures: %s",
                "; ".join(f"{item.path.name}: {item.error}" for item in failures),
            )
        self.runtime = PluginRuntime(self.registry)
        self._installed: dict[str, tuple[Any, ...]] = {}

    def attach(self) -> None:
        self._synchronize(refresh=False)
        self.scheduler.add_job(
            self.synchronize,
            "interval",
            seconds=self.refresh_seconds,
            id=BACKGROUND_SYNC_JOB_ID,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )

    def synchronize(self) -> None:
        self._synchronize(refresh=True)

    def _synchronize(self, *, refresh: bool) -> None:
        failures = self.registry.refresh() if refresh else ()
        failed_sources = {str(item.path) for item in failures}
        desired: dict[str, BackgroundJobSpec] = {}
        for registered in self.registry.list_plugins():
            if registered.source in failed_sources:
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
            desired[spec.job_id] = spec

        for job_id in tuple(self._installed):
            if job_id not in desired:
                try:
                    self.scheduler.remove_job(job_id)
                except Exception:
                    logger.debug("Background job was already absent: %s", job_id)
                self._installed.pop(job_id, None)

        for job_id, spec in desired.items():
            if self._installed.get(job_id) == spec.signature:
                continue
            options: dict[str, Any] = {}
            if spec.run_on_start:
                options["next_run_time"] = datetime.now(timezone.utc)
            self.scheduler.add_job(
                self._invoke,
                "interval",
                seconds=spec.interval_seconds,
                args=[spec.plugin_name, dict(spec.arguments), job_id],
                id=job_id,
                replace_existing=True,
                coalesce=spec.coalesce,
                max_instances=spec.max_instances,
                **options,
            )
            self._installed[job_id] = spec.signature

    async def _invoke(
        self,
        plugin_name: str,
        arguments: dict[str, Any],
        job_id: str,
    ) -> None:
        result = await self.runtime.call(
            plugin_name,
            arguments,
            PluginContext(
                workspace=self.workspace,
                data={**self.data, "background_job_id": job_id},
                services=self.services,
            ),
            call_id=f"background:{job_id}:{datetime.now(timezone.utc).isoformat()}",
        )
        if not result.success:
            logger.error(
                "Background Plugin %s failed: %s",
                plugin_name,
                result.error,
            )


__all__ = [
    "BACKGROUND_JOB_METADATA",
    "BACKGROUND_SYNC_JOB_ID",
    "BackgroundJobSpec",
    "BackgroundPluginHost",
    "background_job_spec",
]
