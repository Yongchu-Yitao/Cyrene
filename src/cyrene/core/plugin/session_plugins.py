"""Session contribution ownership: reconciliation, setup rollback and disposal."""
from __future__ import annotations
import logging
import threading
from pathlib import Path
from typing import Any
from .plugin import PluginPack
from .session_attachment import SetupHookTracker as _SetupHookTracker, SessionPackAttachment as _SessionPackAttachment
from ..plugin_boundary import PLUGIN_BOUNDARY_ERRORS

logger = logging.getLogger("cyrene.core.session")

class SessionPlugins:
    def __init__(self, *, registry, plugin_directory, services, application_scope,
                 failures, setup_context, get_hooks, tree_id, is_closed, worker_alive):
        self.registry = registry
        self.plugin_directory = plugin_directory
        self.services = services
        self.application_scope = application_scope
        self.setup_context = setup_context
        self.get_hooks = get_hooks
        self.tree_id = tree_id
        self.is_closed = is_closed
        self.worker_alive = worker_alive
        self.lock = threading.RLock()
        self.attachments: dict[str, _SessionPackAttachment] = {}
        self.setup_failures: dict[str, str] = {}
        self.load_failures = tuple(failures)
        self.required_ids = {pack.id for pack in registry.list_packs()
            if pack.has_session_contributions and bool(pack.metadata.get("required"))}
        self.sync_token = None
        self.directory_revision = None
        self.customization_revision = None
        self.host_service_names: set[str] = set()
        self.driver: Any = None
        self.owns_driver = False
        self._capture_application_host_services()

    def _application_host(self) -> Any | None:
        host = self.application_scope
        if host is None:
            return None
        if Path(host.plugin_directory).resolve() != self.plugin_directory:
            return None
        return host


    def _capture_application_host_services(self) -> None:
        host = self._application_host()
        if host is None:
            return
        for name, value in host.services.items():
            if self.services.get(name) is value:
                self.host_service_names.add(name)
        self.directory_revision = host.registry.directory_revision
        self.customization_revision = host.registry.customizations.revision


    def _sync_application_host_services(self, host: Any | None) -> None:
        if host is None:
            return
        active = host.active_services
        owned = {
            name
            for attachment in self.attachments.values()
            for name in attachment.provided_services
        }
        names = self.host_service_names | set(active)
        self.host_service_names.update(active)
        for name in names:
            if name in owned:
                continue
            if name in active:
                self.services[name] = active[name]
            else:
                self.services.pop(name, None)


    def _failed_pack_sources(self, host: Any | None) -> set[str]:
        failures = list(self.load_failures)
        if host is not None:
            failures.extend(host.load_failures)
        return {str(item.path.resolve()) for item in failures}


    def _application_pack_state_token(self, host: Any | None) -> tuple[Any, ...]:
        """Include process lifecycle state in lazy session reconciliation."""

        if host is None:
            return ("application_host", "unavailable")
        values = []
        for pack in self.registry.list_packs():
            if not pack.has_application_contributions:
                continue
            values.append(
                (
                    pack.id,
                    host.pack_operational(pack.id),
                    host.pack_restart_required(pack.id),
                    host.startup_failures.get(pack.id, ""),
                )
            )
        return tuple(values)


    @staticmethod
    def _application_pack_error(pack: PluginPack, host: Any | None) -> str:
        if not pack.has_application_contributions:
            return ""
        if host is None:
            # A session can be embedded without Cyrene's process-level
            # application host (for example in a worker, test host, or a
            # standalone Agent integration).  ``application_setup`` is an
            # additional surface; it must not prevent the pack's
            # session-scoped ``setup`` from wiring Hooks and services there.
            # When a host exists its lifecycle state is authoritative and is
            # still enforced below.
            return ""
        if host.pack_restart_required(pack.id):
            return "application contribution changed and requires restart"
        startup_error = host.startup_failures.get(pack.id, "")
        if startup_error:
            return f"application startup failed: {startup_error}"
        if not host.pack_operational(pack.id):
            return "application contribution is not operational"
        return ""


    def _remember_required_session_packs(self, host: Any | None) -> None:
        for pack in self.registry.list_packs():
            if pack.has_session_contributions and bool(pack.metadata.get("required")):
                self.required_ids.add(pack.id)
        failures = list(self.load_failures)
        if host is not None:
            failures.extend(host.load_failures)


    def _required_session_pack_error(self, host: Any | None = None) -> str:
        missing = sorted(
            pack_id
            for pack_id in self.required_ids
            if pack_id not in self.attachments
        )
        if not missing:
            return ""
        failures = list(self.load_failures)
        if host is not None:
            failures.extend(host.load_failures)
        load_errors = {
            failure.path.name: str(failure.error or "load failed")
            for failure in failures
        }
        details = []
        for pack_id in missing:
            reason = self.setup_failures.get(pack_id)
            if not reason:
                reason = load_errors.get(pack_id, "setup is not attached")
            details.append(f"{pack_id} ({reason})")
        return ", ".join(details)


    def _ensure_required_session_packs(self) -> None:
        error = self._required_session_pack_error(self._application_host())
        if error:
            raise RuntimeError(
                "Required Plugin session setup unavailable: " + error
            )


    @staticmethod
    def _pack_setup_fingerprint(pack: PluginPack, source: str) -> tuple[Any, ...]:
        """Keep no-op directory refreshes from restarting session services."""

        path = Path(source)
        try:
            if path.is_dir():
                files = tuple(sorted(path.rglob("*.py")))
            elif path.is_file():
                files = (path,)
            else:
                files = ()
            if files:
                return (
                    "files",
                    tuple(
                        (
                            str(item.relative_to(path) if path.is_dir() else item.name),
                            item.stat().st_mtime_ns,
                            item.stat().st_size,
                        )
                        for item in files
                    ),
                )
        except OSError:
            pass
        return ("callable", tuple(id(setup) for setup in pack.session_setups))


    def _detach_session_pack(self, pack_id: str, *, reason: str) -> None:
        attachment = self.attachments.pop(pack_id, None)
        if attachment is None:
            return
        for hook_id in attachment.hooks:
            self.get_hooks().unregister(hook_id)
        for name, provided in attachment.provided_services.items():
            if self.services.get(name) is not provided:
                continue
            existed, previous = attachment.previous_services[name]
            if existed:
                self.services[name] = previous
            else:
                self.services.pop(name, None)
        driver = attachment.driver
        if driver is not None:
            request_cancel = getattr(driver, "request_cancel_all", None)
            if callable(request_cancel):
                try:
                    request_cancel(reason)
                except PLUGIN_BOUNDARY_ERRORS:
                    logger.exception("Failed to cancel session driver for %s", pack_id)
            close = getattr(driver, "close", None)
            if callable(close):
                try:
                    close()
                except PLUGIN_BOUNDARY_ERRORS:
                    logger.exception("Failed to close session driver for %s", pack_id)
            if self.driver is driver:
                self.driver = None
                self.owns_driver = False


    def _attach_session_pack(self, pack: PluginPack, source: str) -> None:
        if "agent_session" in self.services:
            raise ValueError("Plugin service name is reserved: agent_session")
        before = dict(self.services)
        tracker = _SetupHookTracker(self.get_hooks())
        driver: Any = None
        context = self.setup_context(tracker)
        try:
            for setup in pack.session_setups:
                setup(context)
            driver = self.services.pop("session_driver", None)
            changed = {
                name: value
                for name, value in self.services.items()
                if name != "agent_session" and before.get(name) is not value
            }
            previous = {
                name: (name in before, before.get(name)) for name in changed
            }
            attachment = _SessionPackAttachment(
                pack=pack,
                source=source,
                setup_fingerprint=self._pack_setup_fingerprint(pack, source),
                hooks=set(tracker.touched),
                previous_services=previous,
                provided_services=changed,
                driver=driver,
            )
            if driver is not None:
                if self.driver is not None:
                    raise ValueError("Plugin session_driver service already exists")
                self.driver = driver
                self.owns_driver = True
                attach = getattr(driver, "attach", None)
                if callable(attach) and self.worker_alive():
                    attach()
            self.attachments[pack.id] = attachment
        except PLUGIN_BOUNDARY_ERRORS as exc:
            self.setup_failures[pack.id] = str(exc)
            self.services.pop("agent_session", None)
            attachment = self.attachments.get(pack.id)
            if attachment is not None:
                self._detach_session_pack(pack.id, reason="plugin_setup_failed")
            else:
                if driver is not None and self.driver is driver:
                    self.driver = None
                    self.owns_driver = False
                    close = getattr(driver, "close", None)
                    if callable(close):
                        try:
                            close()
                        except PLUGIN_BOUNDARY_ERRORS:
                            logger.exception(
                                "Failed to close setup driver for %s", pack.id
                            )
                tracker.rollback()
                for name in tuple(self.services):
                    if name not in before:
                        self.services.pop(name, None)
                self.services.update(before)
            logger.exception(
                "Failed to attach Plugin pack %s to Agent session %s",
                pack.id,
                self.tree_id(),
            )
        else:
            self.setup_failures.pop(pack.id, None)
            self.services.pop("agent_session", None)


    def reconcile_plugins(self, *, force: bool = False) -> None:
        """Synchronize live setup Hooks/services with shared Plugin state."""

        with self.lock:
            if self.is_closed():
                return
            host = self._application_host()
            host_token = host.registry.sync_token if host is not None else None
            failure_token = tuple(
                sorted(self._failed_pack_sources(host))
            )
            application_token = self._application_pack_state_token(host)
            token = (
                self.registry.sync_token,
                host_token,
                failure_token,
                application_token,
            )
            if not force and token == self.sync_token:
                return

            if host is not None:
                authoritative_directory = host.registry.directory_revision
                if (
                    host.registry is not self.registry
                    and self.directory_revision is not None
                    and authoritative_directory != self.directory_revision
                ):
                    self.load_failures = tuple(
                        self.registry.refresh_directory(self.plugin_directory)
                    )
                self.directory_revision = authoritative_directory
                customization_revision = host.registry.customizations.revision
            else:
                customization_revision = self.registry.customizations.revision
            if customization_revision != self.customization_revision:
                self.registry.refresh_customizations()
                self.customization_revision = customization_revision

            self._remember_required_session_packs(host)
            failed_sources = self._failed_pack_sources(host)
            desired: dict[str, tuple[PluginPack, str]] = {}
            for pack in self.registry.list_packs():
                if not pack.has_session_contributions:
                    continue
                try:
                    source = self.registry.pack_source(pack.id)
                    enabled = self.registry.pack_enabled(pack.id)
                except Exception:
                    continue
                application_error = self._application_pack_error(pack, host)
                if application_error:
                    self.setup_failures[pack.id] = application_error
                if (
                    enabled
                    and not application_error
                    and str(Path(source).resolve()) not in failed_sources
                ):
                    desired[pack.id] = (pack, source)

            for pack_id, attachment in tuple(self.attachments.items()):
                next_value = desired.get(pack_id)
                if next_value is None or (
                    attachment.source != next_value[1]
                    or attachment.setup_fingerprint
                    != self._pack_setup_fingerprint(*next_value)
                ):
                    self._detach_session_pack(
                        pack_id,
                        reason="plugin_disabled_or_reloaded",
                    )

            self._sync_application_host_services(host)
            for pack_id, (pack, source) in desired.items():
                if pack_id not in self.attachments:
                    self._attach_session_pack(pack, source)

            self.sync_token = (
                self.registry.sync_token,
                host.registry.sync_token if host is not None else None,
                tuple(sorted(self._failed_pack_sources(host))),
                self._application_pack_state_token(host),
            )

