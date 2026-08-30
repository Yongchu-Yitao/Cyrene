"""Application service for core configuration settings."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from cyrene.platform import config_store
from cyrene.platform.host_bridge import HostBridgeError, call_host
from cyrene.platform.settings_service import (
    SettingsServiceError,
    read_public,
    update,
    validate_changes,
)
from cyrene.platform.storage import scan_storage

SettingsChangedPublisher = Callable[[str, int | None, list[str]], Awaitable[None]]


class ConfigQueryPort(Protocol):
    def config(self) -> dict[str, Any]: ...


class ConfigIntegrationError(RuntimeError):
    def __init__(
        self,
        message: str,
        status_code: int,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {"error": message}


class ConfigIntegrationApplicationService:
    """Own core settings projections and updates."""

    def __init__(
        self,
        queries: ConfigQueryPort,
        publish_settings_changed: SettingsChangedPublisher,
    ) -> None:
        self.queries = queries
        self.publish_settings_changed = publish_settings_changed

    def config(self) -> dict[str, Any]:
        # Runtime settings are projected by the active application packs and
        # the presentation query. Core only owns the generic configuration
        # envelope; it must not special-case an optional proxy provider.
        return dict(self.queries.config())

    async def storage(self) -> dict[str, Any]:
        return {"ok": True, **(await asyncio.to_thread(scan_storage))}

    async def read_namespace(self, namespace: str) -> dict[str, Any]:
        try:
            if namespace != "desktop":
                return read_public(namespace)
            result = await call_host("desktop.settings.get", {})
            if result.get("ok") is False:
                raise ConfigIntegrationError("revision conflict", 409, result)
            settings = dict(result.get("settings") or {})
            revision = settings.pop("settingsRevision", None)
            return {"revision": revision, "values": settings}
        except HostBridgeError as exc:
            raise ConfigIntegrationError(exc.code, 503) from exc
        except SettingsServiceError as exc:
            raise ConfigIntegrationError(str(exc), 400) from exc

    async def update_namespace(
        self,
        namespace: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        changes = body.get("changes")
        try:
            if namespace == "desktop":
                return await self._update_desktop(changes, body.get("expected_revision"))
            result = update(
                namespace,
                changes,
                actor="ui",
                expected_revision=body.get("expected_revision"),
            )
            await self.publish_settings_changed(
                namespace,
                result["revision"],
                result["changed"],
            )
            return {"ok": True, **result}
        except config_store.SettingsRevisionConflict as exc:
            payload = {"error": str(exc), "revision": exc.actual}
            raise ConfigIntegrationError(str(exc), 409, payload) from exc
        except HostBridgeError as exc:
            raise ConfigIntegrationError(exc.code, 503) from exc
        except SettingsServiceError as exc:
            raise ConfigIntegrationError(str(exc), 400) from exc

    async def update_config(self, body: dict[str, Any]) -> dict[str, Any]:
        values = dict(body)
        expected_revision = values.pop("expected_revision", None)
        try:
            result = update(
                "runtime",
                values,
                actor="ui",
                expected_revision=expected_revision,
            )
        except config_store.SettingsRevisionConflict as exc:
            payload = {"error": str(exc), "revision": exc.actual}
            raise ConfigIntegrationError(str(exc), 409, payload) from exc
        except SettingsServiceError as exc:
            raise ConfigIntegrationError(str(exc), 400) from exc
        await self.publish_settings_changed(
            "runtime",
            result["revision"],
            result["changed"],
        )
        return {"ok": True, **result}

    async def _update_desktop(
        self,
        changes: Any,
        expected_revision: Any,
    ) -> dict[str, Any]:
        normalized, _specs = validate_changes("desktop", changes, actor="ui")
        result = await call_host(
            "desktop.settings.update",
            {"changes": normalized, "expectedRevision": expected_revision},
        )
        if result.get("ok") is False:
            status = 409 if result.get("error") == "revision_conflict" else 400
            raise ConfigIntegrationError(str(result.get("error") or "error"), status, result)
        settings = result.get("settings") or {}
        await self.publish_settings_changed(
            "desktop",
            settings.get("settingsRevision"),
            list(normalized),
        )
        return result
