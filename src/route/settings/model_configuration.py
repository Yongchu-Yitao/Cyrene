"""HTTP adapter for plugin-oriented model configuration."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene.runtime import config_store
from cyrene.runtime.model_configuration import (
    get_model_configuration,
    normalize_model_configuration,
    public_model_configuration,
    save_model_configuration,
    selectable_model_candidates,
)


def _error(message: str, status: int = 400, *, detail: str = "") -> JSONResponse:
    payload = {"error": message}
    if detail:
        payload["detail"] = detail
    return JSONResponse(payload, status_code=status)


def _public_configuration_with_plugins(
    configuration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved = configuration or get_model_configuration()
    payload = public_model_configuration(resolved)
    try:
        from agent.plugin.model_catalog import model_plugin_catalog

        plugin_catalog = model_plugin_catalog()
        payload["model_plugins"] = plugin_catalog
    except Exception as exc:
        plugin_catalog = []
        payload["model_plugins"] = []
        payload["model_plugin_error"] = str(exc)
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


async def _discover(connection: dict[str, Any]) -> list[dict[str, Any]]:
    adapter = str(connection.get("adapter") or "")
    options = connection.get("options")
    provider_preset = str(
        options.get("provider_preset") if isinstance(options, dict) else ""
    ).strip().lower()
    from agent.plugin import PluginContext, PluginRuntime
    from agent.plugin.model_catalog import resolve_model_plugin

    registry, plugin = resolve_model_plugin(provider_preset, adapter)
    if plugin is None:
        raise RuntimeError(
            f"no model Provider Plugin is registered for {provider_preset or adapter}"
        )
    outcome = await PluginRuntime(registry).call(
        plugin.name,
        {"operation": "list_models"},
        PluginContext(
            data={"caller": "settings_model_discovery"},
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


async def _test_connection(connection: dict[str, Any]) -> dict[str, Any]:
    adapter = str(connection.get("adapter") or "")
    models = await _discover(connection)
    return {"connected": True, "adapter": adapter, "model_count": len(models)}


async def _test_model(connection: dict[str, Any], profile: Any) -> dict[str, Any]:
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
    from agent.plugin import PluginContext, PluginRuntime
    from agent.plugin.model_catalog import resolve_model_plugin

    registry, plugin = resolve_model_plugin(provider_preset, adapter)
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
            PluginRuntime(registry).call(
                plugin.name,
                arguments,
                PluginContext(
                    data={"caller": "settings_model_test"},
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
        PluginRuntime(registry).call(
            plugin.name,
            {
                "operation": "complete",
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "model": model,
                "max_tokens": 8,
                "reasoning_effort": str(profile.get("reasoning_effort") or ""),
            },
            PluginContext(
                data={"caller": "settings_model_test"},
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


def register_model_configuration_routes(router: APIRouter) -> None:
    @router.get("/api/settings/model-config")
    async def api_get_model_configuration():
        return _public_configuration_with_plugins()

    @router.put("/api/settings/model-config")
    async def api_put_model_configuration(request: Request):
        try:
            body = await request.json()
        except ValueError:
            return _error("request body must be valid JSON")
        if not isinstance(body, dict):
            return _error("model configuration must be an object")
        expected_revision: int | None = None
        if "revision" in body:
            value = body.get("revision")
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                return _error("revision must be a non-negative integer")
            expected_revision = value
        source = {key: value for key, value in body.items() if key != "revision"}
        try:
            saved, revision = save_model_configuration(
                source,
                expected_revision=expected_revision,
            )
        except config_store.SettingsRevisionConflict as exc:
            return JSONResponse(
                {
                    "error": "model configuration was changed by another client",
                    "code": "settings_revision_conflict",
                    "expected_revision": exc.expected,
                    "actual_revision": exc.actual,
                },
                status_code=409,
            )
        except (TypeError, ValueError) as exc:
            return _error(str(exc))
        payload = _public_configuration_with_plugins(saved)
        payload["revision"] = revision
        return {"ok": True, **payload}

    @router.get("/api/settings/model-config/provider-usage")
    async def api_get_provider_usage(request: Request):
        from cyrene.model_runtime.provider_telemetry import (
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
            body = {}
        try:
            connection = _connection_draft(connection_id, body)
            profile = body.get("profile") if isinstance(body, dict) else None
            result = (
                await _test_model(connection, profile)
                if profile is not None
                else await _test_connection(connection)
            )
        except httpx.TimeoutException as exc:
            return _error("model connection timed out", 504, detail=str(exc))
        except httpx.HTTPStatusError as exc:
            return _error(
                "model connection was rejected",
                502,
                detail=f"HTTP {exc.response.status_code}",
            )
        except httpx.HTTPError as exc:
            return _error("model connection failed", 502, detail=str(exc))
        except (RuntimeError, OSError) as exc:
            return _error("model connection is unavailable", 503, detail=str(exc))
        except (TypeError, ValueError) as exc:
            return _error(str(exc))
        return {"ok": True, **result}

    @router.post("/api/settings/model-config/connections/{connection_id}/discover")
    async def api_discover_connection_models(connection_id: str, request: Request):
        try:
            body = await request.json()
        except ValueError:
            body = {}
        try:
            connection = _connection_draft(connection_id, body)
            models = await _discover(connection)
        except httpx.TimeoutException as exc:
            return _error("model discovery timed out", 504, detail=str(exc))
        except httpx.HTTPStatusError as exc:
            return _error(
                "model discovery was rejected",
                502,
                detail=f"HTTP {exc.response.status_code}",
            )
        except httpx.HTTPError as exc:
            return _error("model discovery failed", 502, detail=str(exc))
        except (RuntimeError, OSError) as exc:
            return _error("model discovery is unavailable", 503, detail=str(exc))
        except (TypeError, ValueError) as exc:
            return _error(str(exc))
        return {"ok": True, "models": models}


__all__ = ["register_model_configuration_routes"]
