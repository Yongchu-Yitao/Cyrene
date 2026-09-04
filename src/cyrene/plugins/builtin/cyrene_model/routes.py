"""HTTP adapter for plugin-oriented model configuration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene.core.plugin import PluginContext, PluginRegistry, PluginRuntime
from cyrene.model.error_details import (
    ModelCallError,
    classify_model_error,
    details_from_mapping,
)
from cyrene.plugins.model_catalog import (
    application_model_runtime,
    registered_model_plugin_catalog,
    resolve_registered_model_plugin,
)
from cyrene.localization import app_language, localized
from .configuration import (
    get_model_configuration,
    model_configuration_hash,
    normalize_model_configuration,
    patch_model_configuration,
    provider_preset_for_connection,
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


def _raise_model_plugin_failure(outcome: Any, fallback: str) -> None:
    details = details_from_mapping(getattr(outcome, "error_details", None))
    if details is not None:
        raise ModelCallError(details)
    raise RuntimeError(str(getattr(outcome, "error", "") or fallback))


def _model_error_response(exc: ModelCallError) -> JSONResponse:
    details = exc.details
    status = {
        "model_authentication_failed": 401,
        "model_quota_exhausted": 402,
        "model_rate_limited": 429,
        "model_request_invalid": 400,
        "model_request_too_large": 413,
        "model_timeout": 504,
        "model_service_unavailable": 503,
    }.get(details.code, 502)
    return _error(
        details.message_en,
        details.message_zh,
        details.code,
        status,
        retryable=details.retryable,
        **({"upstream_status": details.status_code} if details.status_code else {}),
    )


def _model_discovery_error_response(exc: ModelCallError) -> JSONResponse:
    """Translate provider failures at the discovery boundary without guessing in UI."""

    details = exc.details
    if details.code == "model_credentials_missing":
        code, status = "model_discovery_credentials_missing", 400
        en, zh = (
            "No API key is configured for this model service.",
            "尚未配置 API 密钥。",
        )
    elif details.code == "model_authentication_failed":
        code, status = "model_discovery_authentication_failed", 401
        en, zh = (
            "The API key is invalid or is not authorized to list models.",
            "API 密钥无效或没有获取模型列表的权限。",
        )
    elif details.status_code == 404 or details.code == "model_unavailable":
        code, status = "model_discovery_catalog_not_found", 502
        en, zh = (
            "The model-list endpoint does not exist.",
            "模型列表地址不存在。",
        )
    elif details.code == "model_timeout":
        code, status = "model_discovery_timeout", 504
        en, zh = "The connection timed out.", "连接超时。"
    elif details.code == "model_response_invalid":
        code, status = "model_discovery_response_invalid", 502
        en, zh = (
            "The model service returned data that could not be parsed.",
            "模型服务返回了无法解析的数据。",
        )
    elif details.code == "model_connection_failed":
        code, status = "model_discovery_connection_failed", 502
        en, zh = (
            "Could not connect to the model service.",
            "无法连接到模型服务。",
        )
    elif details.code == "model_service_unavailable":
        code, status = "model_discovery_service_unavailable", 503
        en, zh = (
            "The model service is temporarily unavailable.",
            "模型服务暂时不可用。",
        )
    else:
        code, status = "model_discovery_failed", 502
        en, zh = "Model discovery failed.", "获取模型列表失败。"
    diagnostic = {
        "operation": "model_discovery",
        "code": code,
        "cause": details.code,
        "retryable": details.retryable,
    }
    if details.status_code:
        diagnostic["upstream_status"] = details.status_code
    return _error(
        en,
        zh,
        code,
        status,
        retryable=details.retryable,
        diagnostic=diagnostic,
    )


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
        provider_id = provider_preset_for_connection(connection)
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
    payload["content_hash"] = model_configuration_hash(resolved)
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
        provider_id = provider_preset_for_connection(connection)
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
        (options.get("provider_preset") or "")
        if isinstance(options, dict)
        else ""
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
        _raise_model_plugin_failure(outcome, "model Plugin discovery failed")
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


def _test_profile_context(
    connection: dict[str, Any], profile: Any
) -> tuple[dict[str, Any], str, set[str], str, str]:
    if not isinstance(profile, dict):
        raise ValueError("model profile is required")
    normalized = normalize_model_configuration({
        "connections": [connection],
        "profiles": [profile],
        "routes": {"primary": [], "secondary": [], "vision": [], "embedding": []},
    })
    normalized_profile = normalized["profiles"][0]
    model = str(normalized_profile.get("model") or "").strip()
    if not model:
        raise ValueError("model id is required")
    capabilities = {
        str(item or "").strip().lower()
        for item in (normalized_profile.get("capabilities") or [])
        if str(item or "").strip()
    }
    adapter = str(connection.get("adapter") or "").strip().lower()
    options = connection.get("options") if isinstance(connection.get("options"), dict) else {}
    provider_preset = str(options.get("provider_preset") or "").strip().lower()
    return normalized_profile, model, capabilities, adapter, provider_preset


async def _test_model(
    connection: dict[str, Any],
    profile: Any,
    *,
    service: ModelConfigurationApplicationService | None = None,
) -> dict[str, Any]:
    profile, model, capabilities, adapter, provider_preset = _test_profile_context(
        connection, profile
    )
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
            if not outcome.success:
                _raise_model_plugin_failure(
                    outcome,
                    "model Provider Plugin embedding test failed",
                )
            raise RuntimeError("model Provider Plugin embedding test returned invalid data")
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
        _raise_model_plugin_failure(outcome, "model Provider Plugin test failed")
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
        # Compatibility fallback for older clients that still submit a full
        # graph.  Global settings revisions are intentionally ignored: model
        # writes must not be rejected because an unrelated setting changed.
        source = {
            key: value
            for key, value in body.items()
            if key not in {"revision", "content_hash"}
        }
        try:
            saved, revision = save_model_configuration(source)
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

    @router.patch("/api/settings/model-config")
    async def api_patch_model_configuration(request: Request):
        try:
            body = await request.json()
        except ValueError:
            return _error(
                "request body must be valid JSON",
                "请求体必须是有效的 JSON。",
                "invalid_json",
            )
        try:
            saved, revision, content_hash, rebased = patch_model_configuration(body)
        except (TypeError, ValueError) as exc:
            return _validation_error(
                exc,
                en="Invalid model configuration patch.",
                zh="模型配置补丁无效。",
                code="invalid_model_configuration_patch",
            )
        payload = _public_configuration_with_plugins(saved, service=service)
        payload["revision"] = revision
        payload["content_hash"] = content_hash
        return {"ok": True, "rebased": rebased, **payload}

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
        except ModelCallError as exc:
            logger.info(
                "Model connection test failed [%s]",
                exc.details.code,
                exc_info=True,
            )
            return _model_error_response(exc)
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

    _register_model_discovery_route(router, service)


__all__ = [
    "ModelConfigurationApplicationService",
    "register_model_configuration_routes",
]
def _register_model_discovery_route(
    router: APIRouter,
    service: ModelConfigurationApplicationService | None,
) -> None:
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
        except ModelCallError as exc:
            logger.info(
                "Model discovery failed [%s]",
                exc.details.code,
                exc_info=True,
            )
            return _model_discovery_error_response(exc)
        except (TimeoutError, httpx.TimeoutException):
            logger.info("Model discovery timed out", exc_info=True)
            return _model_discovery_error_response(
                ModelCallError(classify_model_error("model discovery timeout"))
            )
        except httpx.HTTPStatusError as exc:
            logger.info("Model discovery was rejected", exc_info=True)
            return _model_discovery_error_response(
                ModelCallError(classify_model_error(exc))
            )
        except httpx.HTTPError as exc:
            logger.info("Model discovery request failed", exc_info=True)
            return _model_discovery_error_response(
                ModelCallError(classify_model_error(exc))
            )
        except (RuntimeError, OSError) as exc:
            logger.info("Model discovery is unavailable", exc_info=True)
            classified = classify_model_error(exc)
            if "invalid model catalog" in str(exc).lower():
                classified = classify_model_error("invalid provider plugin result")
            return _model_discovery_error_response(
                ModelCallError(classified)
            )
        except (TypeError, ValueError) as exc:
            return _validation_error(
                exc,
                en="Invalid model connection settings.",
                zh="模型连接设置无效。",
                code="invalid_model_connection",
            )
        return {"ok": True, "models": models}
