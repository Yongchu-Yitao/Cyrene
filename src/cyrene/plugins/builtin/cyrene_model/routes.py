"""HTTP adapter for plugin-oriented model configuration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene.core.plugin import PluginContext, PluginRegistry, PluginRuntime
from cyrene.plugins.model_catalog import (
    application_model_runtime,
    registered_model_plugin_catalog,
    resolve_registered_model_plugin,
)
from cyrene.localization import app_language, localized
from cyrene.runtime import config_store
from .configuration import (
    get_model_configuration,
    normalize_model_configuration,
    public_model_configuration,
    save_model_configuration,
    selectable_model_candidates,
)
from cyrene.workbench.http.errors import localized_error_response


_BUILTIN_MODEL_PLUGIN_PACK = "cyrene_model"
logger = logging.getLogger(__name__)

_MODEL_VALIDATION_ZH = {
    (
        "connection draft must contain a canonical connection object"
    ): "连接草稿必须包含规范的 connection 对象。",
    "connection draft must be an object": "连接草稿必须是对象。",
    "model connection not found": "未找到模型连接。",
    (
        "connection draft id must match the requested connection"
    ): "连接草稿 ID 必须与请求的连接一致。",
    "model profile is required": "必须提供模型配置项。",
    "model id is required": "必须提供模型 ID。",
}


class ModelConfigurationApplicationService:
    """Model settings boundary bound to one application Plugin registry."""

    def __init__(
        self,
        registry: PluginRegistry,
        runtime: PluginRuntime | None = None,
    ) -> None:
        self.registry = registry
        self.runtime = runtime or application_model_runtime(registry)

    def catalog(self) -> list[dict[str, Any]]:
        return registered_model_plugin_catalog(self.registry)

    def resolve(self, provider_id: str, adapter_id: str):
        return resolve_registered_model_plugin(
            self.registry,
            provider_id,
            adapter_id,
        )


def _error(
    en: str,
    zh: str,
    code: str,
    status: int = 400,
    **details: Any,
) -> JSONResponse:
    return localized_error_response(en, zh, status, code, **details)


def _validation_error(
    exc: TypeError | ValueError,
    *,
    en: str,
    zh: str,
    code: str,
) -> JSONResponse:
    message = str(exc)
    translated = _MODEL_VALIDATION_ZH.get(message)
    if translated is not None:
        return _error(message, translated, code)
    logger.info("Invalid model configuration request [%s]", code, exc_info=True)
    return _error(en, zh, code)


def _append_unconfigured_user_plugin_connections(
    payload: dict[str, Any],
    plugin_catalog: list[dict[str, Any]],
) -> None:
    """Expose enabled user model Plugins as editable provider connections.

    Built-in Provider Plugins are seeded when the model configuration is first
    created.  User Plugins can be installed later, so they need a lightweight
    connection projection in the public payload before the user has had a
    chance to configure and save them.  The projection deliberately remains
    non-persistent until edited, and built-in Providers are excluded so an
    intentionally deleted built-in connection stays deleted.
    """

    connections = payload.get("connections")
    if not isinstance(connections, list):
        return
    configured_provider_ids: set[str] = set()
    connection_ids: set[str] = set()
    for connection in connections:
        if not isinstance(connection, dict):
            continue
        connection_ids.add(str(connection.get("id") or "").strip())
        options = connection.get("options")
        provider_id = str(
            options.get("provider_preset") if isinstance(options, dict) else ""
        ).strip().lower()
        if provider_id:
            configured_provider_ids.add(provider_id)

    for provider in plugin_catalog:
        if not isinstance(provider, dict):
            continue
        if str(provider.get("pack_id") or "").strip() == _BUILTIN_MODEL_PLUGIN_PACK:
            continue
        provider_id = str(provider.get("id") or "").strip().lower()
        adapter = str(provider.get("adapter") or "").strip().lower()
        if not provider_id or not adapter or provider_id in configured_provider_ids:
            continue
        connection_id = provider_id
        suffix = 2
        while connection_id in connection_ids:
            connection_id = f"{provider_id}:plugin{suffix}"
            suffix += 1
        connections.append(
            {
                "id": connection_id,
                "name": (
                    str(provider.get("name") or provider_id).strip() or provider_id
                ),
                "adapter": adapter,
                "enabled": True,
                "use_proxy": False,
                "base_url": str(provider.get("default_base_url") or "").strip(),
                "api_key": "",
                "api_key_configured": False,
                "secret_configured": False,
                "options": {"provider_preset": provider_id},
                "_plugin_unconfigured": True,
            }
        )
        connection_ids.add(connection_id)
        configured_provider_ids.add(provider_id)


def _public_configuration_with_plugins(
    configuration: dict[str, Any] | None = None,
    *,
    service: ModelConfigurationApplicationService | None = None,
) -> dict[str, Any]:
    resolved = configuration or get_model_configuration()
    payload = public_model_configuration(resolved)
    try:
        if service is not None:
            plugin_catalog = service.catalog()
        else:
            from cyrene.plugins.model_catalog import model_plugin_catalog

            plugin_catalog = model_plugin_catalog()
        payload["model_plugins"] = plugin_catalog
    except Exception:
        logger.exception("Unable to build model Plugin catalog")
        plugin_catalog = []
        payload["model_plugins"] = []
        payload["model_plugin_error"] = localized(
            "Model provider catalog is unavailable.",
            "模型服务商目录不可用。",
        )
        payload["model_plugin_error_code"] = "model_plugin_catalog_unavailable"
    _append_unconfigured_user_plugin_connections(payload, plugin_catalog)
    providers = {
        str(item.get("id") or "").strip().lower(): item
        for item in plugin_catalog
        if isinstance(item, dict)
    }
    connections = {
        str(connection.get("id") or ""): connection
        for connection in resolved.get("connections") or []
        if isinstance(connection, dict)
    }
    selectable: list[dict[str, Any]] = []
    for candidate in selectable_model_candidates(resolved):
        item = {
            key: value
            for key, value in candidate.items()
            if key not in {"api_key", "options", "endpoints", "preferred_endpoint"}
            and not str(key).startswith("_")
        }
        connection = connections.get(str(candidate.get("connection_id") or ""), {})
        options = connection.get("options")
        provider_id = str(
            options.get("provider_preset") if isinstance(options, dict) else ""
        ).strip().lower()
        if provider_id and provider_id not in providers:
            continue
        provider = providers.get(provider_id, {})
        efforts = provider.get("supported_reasoning_efforts")
        if isinstance(efforts, list) and efforts:
            item["supported_reasoning_efforts"] = list(efforts)
        default_effort = str(provider.get("default_reasoning_effort") or "").strip()
        if default_effort:
            item["default_reasoning_effort"] = default_effort
        selectable.append(item)
    payload["selectable_models"] = selectable
    primary = list((resolved.get("routes") or {}).get("primary") or ())
    payload["active"] = str(primary[0] if primary else "")
    return payload


def _connection_draft(connection_id: str, body: Any) -> dict[str, Any]:
    configuration = get_model_configuration()
    existing = next(
        (item for item in configuration["connections"] if item["id"] == connection_id),
        None,
    )
    if not isinstance(body, dict) or not isinstance(body.get("connection"), dict):
        raise ValueError("connection draft must contain a canonical connection object")
    source = body["connection"]
    if not isinstance(source, dict):
        raise ValueError("connection draft must be an object")
    if existing is None and not source:
        raise ValueError("model connection not found")
    submitted_id = str(source.get("id") or "").strip()
    if submitted_id != connection_id:
        raise ValueError("connection draft id must match the requested connection")
    merged = {**(existing or {}), **source, "id": connection_id}
    connections = list(configuration["connections"])
    if existing is None:
        connections.append(merged)
    else:
        connections = [
            merged if item["id"] == connection_id else item
            for item in connections
        ]
    candidate = {
        **configuration,
        "connections": connections,
    }
    normalized = normalize_model_configuration(candidate, previous=configuration)
    return next(item for item in normalized["connections"] if item["id"] == connection_id)


async def _discover(
    connection: dict[str, Any],
    *,
    service: ModelConfigurationApplicationService | None = None,
) -> list[dict[str, Any]]:
    adapter = str(connection.get("adapter") or "")
    options = connection.get("options")
    provider_preset = str(
        options.get("provider_preset") if isinstance(options, dict) else ""
    ).strip().lower()
    if service is None:
        from cyrene.plugins.model_catalog import resolve_model_plugin

        registry, plugin = resolve_model_plugin(provider_preset, adapter)
        runtime = application_model_runtime(registry)
    else:
        plugin = service.resolve(provider_preset, adapter)
        runtime = service.runtime
    if plugin is None:
        raise RuntimeError(
            f"no model Provider Plugin is registered for {provider_preset or adapter}"
        )
    outcome = await runtime.call(
        plugin.name,
        {"operation": "list_models"},
        PluginContext(
            data={
                "caller": "settings_model_discovery",
                "language": app_language(),
            },
            services={"model_connection": connection},
        ),
    )
    if not outcome.success:
        raise RuntimeError(outcome.error or "model Plugin discovery failed")
    result = outcome.value
    if not isinstance(result, dict) or not isinstance(result.get("models"), list):
        raise RuntimeError(
            f"model Plugin {plugin.name!r} returned an invalid model catalog"
        )
    return [dict(item) for item in result["models"] if isinstance(item, dict)]


async def _test_connection(
    connection: dict[str, Any],
    *,
    service: ModelConfigurationApplicationService | None = None,
) -> dict[str, Any]:
    adapter = str(connection.get("adapter") or "")
    models = await _discover(connection, service=service)
    return {"connected": True, "adapter": adapter, "model_count": len(models)}


async def _test_model(
    connection: dict[str, Any],
    profile: Any,
    *,
    service: ModelConfigurationApplicationService | None = None,
) -> dict[str, Any]:
    if not isinstance(profile, dict):
        raise ValueError("model profile is required")
    normalized = normalize_model_configuration({
        "connections": [connection],
        "profiles": [profile],
        "routes": {
            "primary": [],
            "secondary": [],
            "vision": [],
            "embedding": [],
        },
    })
    profile = normalized["profiles"][0]
    model = str(profile.get("model") or "").strip()
    if not model:
        raise ValueError("model id is required")
    capabilities = {
        str(item or "").strip().lower()
        for item in (profile.get("capabilities") or [])
        if str(item or "").strip()
    }
    adapter = str(connection.get("adapter") or "").strip().lower()
    connection_options = (
        connection.get("options")
        if isinstance(connection.get("options"), dict)
        else {}
    )
    provider_preset = str(
        connection_options.get("provider_preset") or ""
    ).strip().lower()
    if service is None:
        from cyrene.plugins.model_catalog import resolve_model_plugin

        registry, plugin = resolve_model_plugin(provider_preset, adapter)
        runtime = application_model_runtime(registry)
    else:
        plugin = service.resolve(provider_preset, adapter)
        runtime = service.runtime
    if plugin is None:
        raise RuntimeError(
            f"no model Provider Plugin is registered for {provider_preset or adapter}"
        )

    # Embedding-only profiles are probed through the same Provider Plugin
    # operation used by knowledge indexing.
    if "embedding" in capabilities and not ({"chat", "vision"} & capabilities):
        arguments: dict[str, Any] = {
            "operation": "embed",
            "inputs": ["connection test"],
            "input_type": "query",
            "model": model,
        }
        dimensions = int(profile.get("dimensions") or 0)
        if dimensions:
            arguments["dimensions"] = dimensions
        outcome = await asyncio.wait_for(
            runtime.call(
                plugin.name,
                arguments,
                PluginContext(
                    data={
                        "caller": "settings_model_test",
                        "language": app_language(),
                    },
                    services={
                        "model_connection": connection,
                        "model_profile": profile,
                    },
                ),
            ),
            timeout=20.0,
        )
        if not outcome.success or not isinstance(outcome.value, dict):
            raise RuntimeError(
                outcome.error or "model Provider Plugin embedding test failed"
            )
        return {"connected": True, "adapter": adapter, "model": model}
    outcome = await asyncio.wait_for(
        runtime.call(
            plugin.name,
            {
                "operation": "complete",
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "model": model,
                "max_tokens": 8,
                "reasoning_effort": str(profile.get("reasoning_effort") or ""),
            },
            PluginContext(
                data={
                    "caller": "settings_model_test",
                    "language": app_language(),
                },
                services={
                    "model_connection": connection,
                    "model_profile": profile,
                },
            ),
        ),
        timeout=20.0,
    )
    if not outcome.success:
        raise RuntimeError(outcome.error or "model Provider Plugin test failed")
    response = outcome.value
    if not isinstance(response, dict):
        raise RuntimeError("model returned an invalid response")
    return {"connected": True, "adapter": adapter, "model": model}


def register_model_configuration_routes(
    router: APIRouter,
    service: ModelConfigurationApplicationService | None = None,
) -> None:
    @router.get("/api/settings/model-config")
    async def api_get_model_configuration():
        return _public_configuration_with_plugins(service=service)

    @router.put("/api/settings/model-config")
    async def api_put_model_configuration(request: Request):
        try:
            body = await request.json()
        except ValueError:
            return _error(
                "request body must be valid JSON",
                "请求体必须是有效的 JSON。",
                "invalid_json",
            )
        if not isinstance(body, dict):
            return _error(
                "model configuration must be an object",
                "模型配置必须是对象。",
                "invalid_model_configuration",
            )
        expected_revision: int | None = None
        if "revision" in body:
            value = body.get("revision")
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                return _error(
                    "revision must be a non-negative integer",
                    "revision 必须是非负整数。",
                    "invalid_settings_revision",
                )
            expected_revision = value
        source = {key: value for key, value in body.items() if key != "revision"}
        try:
            saved, revision = save_model_configuration(
                source,
                expected_revision=expected_revision,
            )
        except config_store.SettingsRevisionConflict as exc:
            return _error(
                "model configuration was changed by another client",
                "模型配置已被其他客户端更改。",
                "settings_revision_conflict",
                409,
                expected_revision=exc.expected,
                actual_revision=exc.actual,
            )
        except (TypeError, ValueError) as exc:
            return _validation_error(
                exc,
                en="Invalid model configuration.",
                zh="模型配置无效。",
                code="invalid_model_configuration",
            )
        payload = _public_configuration_with_plugins(saved, service=service)
        payload["revision"] = revision
        return {"ok": True, **payload}

    @router.get("/api/settings/model-config/provider-usage")
    async def api_get_provider_usage(request: Request):
        from .telemetry import (
            configured_provider_telemetry,
        )

        force_refresh = str(request.query_params.get("refresh") or "").lower() in {
            "1", "true", "yes",
        }
        items = await configured_provider_telemetry(force_refresh=force_refresh)
        return {"items": items}

    @router.post("/api/settings/model-config/connections/{connection_id}/test")
    async def api_test_model_connection(connection_id: str, request: Request):
        try:
            body = await request.json()
        except ValueError:
            return _error(
                "request body must be valid JSON",
                "请求体必须是有效的 JSON。",
                "invalid_json",
            )
        if not isinstance(body, dict):
            return _error(
                "connection test request must be an object",
                "连接测试请求必须是对象。",
                "invalid_model_connection",
            )
        try:
            connection = _connection_draft(connection_id, body)
            profile = body.get("profile")
            result = (
                await _test_model(connection, profile, service=service)
                if profile is not None
                else await _test_connection(connection, service=service)
            )
        except (TimeoutError, httpx.TimeoutException):
            logger.info("Model connection test timed out", exc_info=True)
            return _error(
                "model connection timed out",
                "模型连接超时。",
                "model_connection_timeout",
                504,
            )
        except httpx.HTTPStatusError as exc:
            logger.info("Model connection test was rejected", exc_info=True)
            return _error(
                "model connection was rejected",
                "模型服务拒绝了连接。",
                "model_connection_rejected",
                502,
                upstream_status=exc.response.status_code,
            )
        except httpx.HTTPError:
            logger.info("Model connection request failed", exc_info=True)
            return _error(
                "model connection failed",
                "模型连接失败。",
                "model_connection_failed",
                502,
            )
        except (RuntimeError, OSError):
            logger.info("Model connection is unavailable", exc_info=True)
            return _error(
                "model connection is unavailable",
                "模型连接当前不可用。",
                "model_connection_unavailable",
                503,
            )
        except (TypeError, ValueError) as exc:
            return _validation_error(
                exc,
                en="Invalid model connection settings.",
                zh="模型连接设置无效。",
                code="invalid_model_connection",
            )
        return {"ok": True, **result}

    @router.post("/api/settings/model-config/connections/{connection_id}/discover")
    async def api_discover_connection_models(connection_id: str, request: Request):
        try:
            body = await request.json()
        except ValueError:
            return _error(
                "request body must be valid JSON",
                "请求体必须是有效的 JSON。",
                "invalid_json",
            )
        if not isinstance(body, dict):
            return _error(
                "model discovery request must be an object",
                "模型发现请求必须是对象。",
                "invalid_model_connection",
            )
        try:
            connection = _connection_draft(connection_id, body)
            models = await _discover(connection, service=service)
        except (TimeoutError, httpx.TimeoutException):
            logger.info("Model discovery timed out", exc_info=True)
            return _error(
                "model discovery timed out",
                "模型发现超时。",
                "model_discovery_timeout",
                504,
            )
        except httpx.HTTPStatusError as exc:
            logger.info("Model discovery was rejected", exc_info=True)
            return _error(
                "model discovery was rejected",
                "模型服务拒绝了模型发现请求。",
                "model_discovery_rejected",
                502,
                upstream_status=exc.response.status_code,
            )
        except httpx.HTTPError:
            logger.info("Model discovery request failed", exc_info=True)
            return _error(
                "model discovery failed",
                "模型发现失败。",
                "model_discovery_failed",
                502,
            )
        except (RuntimeError, OSError):
            logger.info("Model discovery is unavailable", exc_info=True)
            return _error(
                "model discovery is unavailable",
                "模型发现当前不可用。",
                "model_discovery_unavailable",
                503,
            )
        except (TypeError, ValueError) as exc:
            return _validation_error(
                exc,
                en="Invalid model connection settings.",
                zh="模型连接设置无效。",
                code="invalid_model_connection",
            )
        return {"ok": True, "models": models}


__all__ = [
    "ModelConfigurationApplicationService",
    "register_model_configuration_routes",
]
