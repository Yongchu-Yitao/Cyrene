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

    @router.post("/api/settings/model-config/connections/{connection_id}/test")
    async def api_test_model_connection(connection_id: str, request: Request):
        try:
            body = await request.json()
        except ValueError:
            body = {}
        try:
            connection = _connection_draft(connection_id, body)
            result = await _test_connection(connection)
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
