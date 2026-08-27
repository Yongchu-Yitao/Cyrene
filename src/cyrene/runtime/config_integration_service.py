"""Application service for configuration and integration settings."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx

from agent.plugin import active_plugin_service
from cyrene.model_runtime import opencv_runtime
from cyrene.runtime import config_store, integration_settings
from cyrene.runtime.host_bridge import HostBridgeError, call_host
from cyrene.runtime.settings_service import (
    SettingsServiceError,
    read_public,
    update,
    validate_changes,
)
from cyrene.runtime.storage import scan_storage

logger = logging.getLogger(__name__)
SettingsChangedPublisher = Callable[[str, int | None, list[str]], Awaitable[None]]


def _knowledge_service() -> Any:
    service = active_plugin_service("knowledge")
    if service is None:
        raise ConfigIntegrationError("knowledge Plugin is not available", 503)
    return service


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
    """Own settings projections and integration/local-model operations."""

    def __init__(
        self,
        queries: ConfigQueryPort,
        publish_settings_changed: SettingsChangedPublisher,
    ) -> None:
        self.queries = queries
        self.publish_settings_changed = publish_settings_changed

    def config(self) -> dict[str, Any]:
        payload = dict(self.queries.config())
        payload.update({
            "external_agent_proxy_url": str(config_store.get_setting(
                "external_agent_proxy_url", ""
            ) or ""),
            "proxy_search_enabled": config_store.get_setting(
                "proxy_search_enabled", False
            ) is True,
            "proxy_browser_enabled": config_store.get_setting(
                "proxy_browser_enabled", False
            ) is True,
            "proxy_extensions_enabled": config_store.get_setting(
                "proxy_extensions_enabled", False
            ) is True,
        })
        return payload

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

    def integration_settings(self) -> dict[str, Any]:
        return integration_settings.public_settings()

    def local_model_status(self) -> dict[str, Any]:
        return _knowledge_service().local_model_status()

    def download_ocr_runtime(self) -> dict[str, Any]:
        try:
            return {"ok": True, **opencv_runtime.start_download()}
        except Exception as exc:
            raise ConfigIntegrationError(str(exc), 503) from exc

    def download_local_model(self, model_id: str) -> dict[str, Any]:
        try:
            return {"ok": True, **_knowledge_service().start_local_model_download(model_id)}
        except ValueError as exc:
            raise ConfigIntegrationError(str(exc), 404) from exc

    async def delete_local_model(self, model_id: str) -> dict[str, Any]:
        try:
            return {"ok": True, **(await _knowledge_service().delete_local_model(model_id))}
        except ValueError as exc:
            raise ConfigIntegrationError(str(exc), 404) from exc

    def update_integration(self, body: dict[str, Any]) -> dict[str, Any]:
        if "zotero" not in body:
            raise ConfigIntegrationError(
                "zotero settings are required",
                400,
            )
        try:
            return {"ok": True, **integration_settings.update_settings(body)}
        except (TypeError, ValueError) as exc:
            raise ConfigIntegrationError(str(exc), 400) from exc

    async def test_integration(self, body: dict[str, Any]) -> dict[str, Any]:
        service = str(body.get("service") or "").strip().lower()
        draft = body.get("config", {})
        try:
            config = integration_settings.merged_test_config(service, draft)
            if service == "zotero":
                return await integration_settings.test_zotero(config)
            raise ValueError("unknown integration service")
        except (TypeError, ValueError) as exc:
            raise ConfigIntegrationError(str(exc), 400) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            raise ConfigIntegrationError(
                f"remote service returned HTTP {status}",
                502,
            ) from exc
        except httpx.RequestError as exc:
            raise ConfigIntegrationError(
                "could not reach the configured service",
                503,
            ) from exc
        except Exception as exc:
            logger.info("Integration connectivity test failed", exc_info=True)
            message = "connection test failed"
            raise ConfigIntegrationError(message, 502) from exc
