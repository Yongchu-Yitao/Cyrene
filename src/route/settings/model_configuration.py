"""HTTP adapter for plugin-oriented model configuration."""

from __future__ import annotations

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
)


def _error(message: str, status: int = 400, *, detail: str = "") -> JSONResponse:
    payload = {"error": message}
    if detail:
        payload["detail"] = detail
    return JSONResponse(payload, status_code=status)


def _connection_draft(connection_id: str, body: Any) -> dict[str, Any]:
    configuration = get_model_configuration()
    existing = next(
        (item for item in configuration["connections"] if item["id"] == connection_id),
        None,
    )
    source = body.get("connection", body) if isinstance(body, dict) else {}
    if not isinstance(source, dict):
        raise ValueError("connection draft must be an object")
    if existing is None and not source:
        raise ValueError("model connection not found")
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
    if adapter == "codex_oauth":
        from cyrene.model_runtime.codex_provider import get_codex_provider

        raw_models = await get_codex_provider().models()
        return [
            {
                "id": str(item.get("model") or item.get("id") or "").strip(),
                "model": str(item.get("model") or item.get("id") or "").strip(),
                "name": str(item.get("name") or item.get("model") or item.get("id") or "").strip(),
                "capabilities": ["chat", "vision", "tools", "reasoning"],
            }
            for item in raw_models
            if str(item.get("model") or item.get("id") or "").strip()
        ]
    if adapter == "local_onnx":
        return [{
            "id": "qwen3-embedding-0.6b",
            "model": "qwen3-embedding-0.6b",
            "name": "Qwen3 Embedding 0.6B",
            "capabilities": ["embedding"],
            "dimensions": 1024,
        }]

    from cyrene.model_runtime.protocol_adapters import (
        discovery_request,
        parse_discovery_response,
    )

    base_url = str(connection.get("base_url") or "").rstrip("/")
    api_key = str(connection.get("api_key") or "")
    endpoint, headers = discovery_request(adapter, base_url, api_key)
    timeout = httpx.Timeout(20.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(endpoint, headers=headers)
        response.raise_for_status()
        return parse_discovery_response(adapter, response.json())


async def _test_connection(connection: dict[str, Any]) -> dict[str, Any]:
    adapter = str(connection.get("adapter") or "")
    if adapter == "codex_oauth":
        from cyrene.model_runtime.codex_provider import get_codex_provider

        account = await get_codex_provider().account()
        connected = (
            isinstance(account.get("account"), dict)
            and account["account"].get("type") == "chatgpt"
        )
        if not connected:
            raise ValueError("Codex OAuth login is required")
        return {"connected": True, "adapter": adapter, "account": account.get("account")}
    if adapter == "local_onnx":
        from cyrene.knowledge.local_models import status

        snapshot = status()
        return {"connected": True, "adapter": adapter, "local_models": snapshot}
    models = await _discover(connection)
    return {"connected": True, "adapter": adapter, "model_count": len(models)}


async def _test_model(connection: dict[str, Any], profile: Any) -> dict[str, Any]:
    if not isinstance(profile, dict):
        raise ValueError("model profile is required")
    model = str(profile.get("model") or profile.get("model_id") or "").strip()
    if not model:
        raise ValueError("model id is required")
    capabilities = {
        str(item or "").strip().lower()
        for item in (profile.get("capabilities") or [])
        if str(item or "").strip()
    }
    adapter = str(connection.get("adapter") or "openai_compatible").strip().lower()

    # Embedding-only profiles do not accept a chat probe. Confirm that the
    # exact configured model is currently advertised by the provider instead.
    if "embedding" in capabilities and not ({"chat", "vision"} & capabilities):
        discovered = await _discover(connection)
        available = {
            str(item.get("model") or item.get("id") or "").strip()
            for item in discovered
            if isinstance(item, dict)
        }
        if model not in available:
            raise ValueError("configured model is not available")
        return {"connected": True, "adapter": adapter, "model": model}

    runtime_provider = (
        "codex_oauth"
        if adapter == "codex_oauth"
        else adapter
        if adapter in {"anthropic", "openai", "openai_responses", "gemini"}
        else "openai_compatible"
    )
    base_url = str(connection.get("base_url") or "").strip().rstrip("/")
    if adapter == "ollama" and base_url and not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"
    candidate = {
        "id": str(profile.get("id") or model),
        "profile_id": str(profile.get("id") or model),
        "connection_id": str(connection.get("id") or ""),
        "model": model,
        "name": str(profile.get("name") or model),
        "provider": runtime_provider,
        "adapter": adapter,
        "base_url": base_url,
        "api_key": str(connection.get("api_key") or ""),
        "capabilities": sorted(capabilities),
        "reasoning_effort": str(profile.get("reasoning_effort") or ""),
    }
    from cyrene.model_runtime.client import call_llm, _normalized_llm_endpoints
    from cyrene.model_runtime.protocol_adapters import protocol_endpoints

    candidate["endpoints"] = (
        _normalized_llm_endpoints(base_url)
        if adapter in {"openai", "openai_compatible"}
        else protocol_endpoints(adapter, base_url, model)
    )

    response = await call_llm(
        [{"role": "user", "content": "Reply with OK."}],
        candidates=[candidate],
        max_tokens=8,
        timeout=20.0,
        caller="settings_model_test",
        phase="connectivity",
        publish_events=False,
        record_usage=False,
        record_latency=False,
    )
    if not isinstance(response, dict):
        raise RuntimeError("model returned an invalid response")
    return {"connected": True, "adapter": adapter, "model": model}


def register_model_configuration_routes(router: APIRouter) -> None:
    @router.get("/api/settings/model-config")
    async def api_get_model_configuration():
        return public_model_configuration()

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
        source = body.get("model_configuration", body)
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
        payload = public_model_configuration(saved)
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
