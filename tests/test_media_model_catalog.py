from __future__ import annotations

import json

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


def _install_transport(monkeypatch, catalog, handler) -> None:
    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def client_factory(**kwargs):
        return real_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(catalog.httpx, "AsyncClient", client_factory)


def _by_id(response: dict) -> dict[str, dict]:
    return {item["id"]: item for item in response["models"]}


@pytest.mark.asyncio
async def test_catalog_only_provider_keeps_recommendations_and_unknown_configured_model():
    from cyrene.media.model_catalog import provider_model_catalog

    response = await provider_model_catalog(
        "seedance",
        {
            "providers": {
                "seedance": {
                    "video_model": "doubao-seedance-2-0-pro-260128",
                    "api_key": "stored-but-unused-key",
                }
            }
        },
    )

    models = _by_id(response)
    assert response["status"] == "catalog_only"
    assert models["doubao-seedance-2-0-260128"]["recommended"] is True
    assert "doubao-seedance-2-0-fast-260128" in models
    assert models["doubao-seedance-2-0-pro-260128"] == {
        "id": "doubao-seedance-2-0-pro-260128",
        "label": "doubao-seedance-2-0-pro-260128",
        "name": "doubao-seedance-2-0-pro-260128",
        "kinds": ["video"],
        "recommended": False,
        "configured": True,
        "source": "configured",
        "verified": False,
        "available": None,
    }


@pytest.mark.asyncio
async def test_openai_discovery_is_paginated_filtered_and_never_reflects_key(
    monkeypatch,
):
    import cyrene.media.model_catalog as catalog

    secret = "sk-private-model-discovery-123456"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == f"Bearer {secret}"
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "gpt-image-2"},
                        {"id": f"gpt-image-{secret}"},
                        {"id": "text-only-model"},
                    ],
                    "has_more": True,
                },
            )
        return httpx.Response(
            200,
            json={"data": [{"id": "gpt-image-3"}], "has_more": False},
        )

    _install_transport(monkeypatch, catalog, handler)
    response = await catalog.provider_model_catalog(
        "openai",
        {
            "providers": {
                "openai": {
                    "api_key": secret,
                    "base_url": "https://gateway.example/v1",
                    "image_model": "gpt-image-private-deployment",
                }
            }
        },
    )

    models = _by_id(response)
    assert response["status"] == "verified"
    assert len(requests) == 2
    assert requests[0].url.path == "/v1/models"
    assert requests[1].url.params["after"] == "text-only-model"
    assert models["gpt-image-2"]["verified"] is True
    assert models["gpt-image-3"]["source"] == "live"
    assert models["gpt-image-private-deployment"]["configured"] is True
    assert "text-only-model" not in models
    assert secret not in json.dumps(response)


@pytest.mark.asyncio
async def test_minimax_discovery_normalizes_versioned_base_and_filters_models(
    monkeypatch,
):
    import cyrene.media.model_catalog as catalog

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "models": [
                    {"id": "minimax-h3"},
                    {"id": "MiniMax-Hailuo-2.3-Fast"},
                    {"id": "music-2.6"},
                    {"id": "music-cover"},
                    {"id": "MiniMax-Text-01"},
                ]
            },
        )

    _install_transport(monkeypatch, catalog, handler)
    response = await catalog.provider_model_catalog(
        "minimax",
        {
            "providers": {
                "minimax": {
                    "api_key": "mini-private-key",
                    "base_url": "https://gateway.example/v2",
                    "video_model": "MiniMax-H3",
                    "music_model": "music-3.0",
                }
            }
        },
    )

    models = _by_id(response)
    assert response["status"] == "verified"
    assert seen[0].url.path == "/v1/models"
    assert models["MiniMax-H3"]["verified"] is True
    assert models["music-2.6"]["verified"] is True
    assert "minimax-h3" not in models
    assert "music-cover" not in models
    assert "MiniMax-Text-01" not in models


@pytest.mark.asyncio
async def test_google_discovery_uses_key_header_and_page_tokens(monkeypatch):
    import cyrene.media.model_catalog as catalog

    secret = "google-private-key"
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["x-goog-api-key"] == secret
        if len(seen) == 1:
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "models/gemini-3.1-flash-image"},
                        {"name": "models/gemini-text-model"},
                    ],
                    "nextPageToken": "page-two",
                },
            )
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "models/gemini-omni-flash"},
                    {"name": "models/veo-3.1-generate-preview"},
                ]
            },
        )

    _install_transport(monkeypatch, catalog, handler)
    response = await catalog.provider_model_catalog(
        "google",
        {
            "providers": {
                "google": {
                    "api_key": secret,
                    "image_model": "gemini-3.1-flash-image",
                    "video_model": "gemini-omni-flash-preview",
                }
            }
        },
    )

    models = _by_id(response)
    assert response["status"] == "verified"
    assert len(seen) == 2
    assert seen[0].url.path == "/v1beta/models"
    assert seen[1].url.params["pageToken"] == "page-two"
    assert models["gemini-3.1-flash-image"]["verified"] is True
    assert models["gemini-omni-flash"]["verified"] is True
    assert models["gemini-omni-flash-preview"]["configured"] is True
    assert "gemini-text-model" not in models
    assert secret not in json.dumps(response)


@pytest.mark.asyncio
async def test_missing_key_and_discovery_failure_both_keep_safe_static_catalog(
    monkeypatch,
):
    import cyrene.media.model_catalog as catalog

    missing = await catalog.provider_model_catalog(
        "openai",
        {"providers": {"openai": {"image_model": "gpt-image-2"}}},
    )
    assert missing["status"] == "missing_key"
    assert _by_id(missing)["gpt-image-2"]["configured"] is True

    secret = "must-not-escape-upstream-error"

    async def fail_discovery(_provider, _settings):
        raise RuntimeError(f"upstream body contained {secret}")

    monkeypatch.setattr(catalog, "_discover_live_models", fail_discovery)
    failed = await catalog.provider_model_catalog(
        "openai",
        {"providers": {"openai": {"api_key": secret}}},
    )
    assert failed["status"] == "failed"
    assert "gpt-image-2" in _by_id(failed)
    assert secret not in json.dumps(failed)
    assert "error" not in failed


def test_model_catalog_route_degrades_to_200_and_rejects_unknown_provider(
    monkeypatch,
):
    import cyrene.media.model_catalog as catalog
    import route.settings.media as media_routes

    secret = "route-private-key"

    async def fail_discovery(_provider, _settings):
        raise httpx.ConnectError(f"private upstream failure: {secret}")

    monkeypatch.setattr(catalog, "_discover_live_models", fail_discovery)
    monkeypatch.setattr(
        media_routes,
        "get_media_settings",
        lambda: {"providers": {"openai": {"api_key": secret}}},
    )
    router = APIRouter()
    media_routes.register_media_settings_routes(router)
    app = FastAPI()
    app.include_router(router)

    with TestClient(app) as client:
        failed = client.get("/api/settings/media/providers/openai/models")
        unknown = client.get("/api/settings/media/providers/unknown/models")

    assert failed.status_code == 200
    assert failed.json()["status"] == "failed"
    assert secret not in failed.text
    assert unknown.status_code == 404
    assert unknown.json() == {"error": "unknown media provider"}
