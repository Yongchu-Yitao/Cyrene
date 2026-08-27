import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


def test_deepseek_balance_normalization_preserves_provider_currencies():
    from agent.plugin.plugin_impl.cyrene_model.telemetry import _normalize_deepseek

    result = _normalize_deepseek({
        "is_available": True,
        "balance_infos": [
            {
                "currency": "CNY",
                "total_balance": "110.00",
                "granted_balance": "10.00",
                "topped_up_balance": "100.00",
            },
            {
                "currency": "USD",
                "total_balance": "2.50",
                "granted_balance": "0.50",
                "topped_up_balance": "2.00",
            },
        ],
    })

    assert result["kind"] == "balance"
    assert result["available"] is True
    assert result["balances"][0] == {
        "currency": "CNY",
        "total": "110.00",
        "granted": "10.00",
        "topped_up": "100.00",
    }
    assert result["balances"][1]["currency"] == "USD"


def test_minimax_quota_does_not_treat_undocumented_status_as_unlimited():
    from agent.plugin.plugin_impl.cyrene_model.telemetry import _normalize_minimax

    result = _normalize_minimax({
        "model_remains": [{
            "model_name": "general",
            "current_interval_total_count": 0,
            "current_interval_usage_count": 0,
            "current_interval_remaining_percent": 63,
            "current_interval_status": 1,
            "remains_time": 3_600_000,
            "current_weekly_total_count": 0,
            "current_weekly_usage_count": 0,
            "current_weekly_remaining_percent": 100,
            "current_weekly_status": 3,
            "weekly_remains_time": 86_400_000,
        }],
        "base_resp": {"status_code": 0, "status_msg": "success"},
    })

    current, weekly = result["windows"]
    assert current["remaining_percent"] == 63
    assert current["used_percent"] == 37
    assert current["reset_at"]
    assert weekly["unlimited"] is False
    assert weekly["ambiguous"] is True
    assert weekly["remaining_percent"] == 100


def test_minimax_count_plan_treats_usage_count_as_remaining():
    from agent.plugin.plugin_impl.cyrene_model.telemetry import _normalize_minimax

    result = _normalize_minimax({
        "model_remains": [{
            "model_name": "image-01",
            "current_interval_total_count": 50,
            "current_interval_usage_count": 49,
            "current_interval_status": 1,
            "current_weekly_total_count": 350,
            "current_weekly_usage_count": 349,
            "current_weekly_status": 1,
        }],
        "base_resp": {"status_code": 0, "status_msg": "success"},
    })

    assert result["windows"][0]["remaining_percent"] == 98
    assert result["windows"][1]["remaining_percent"] == pytest.approx(99.7142857)


def test_minimax_http_200_business_error_is_rejected():
    from agent.plugin.plugin_impl.cyrene_model.telemetry import (
        ProviderTelemetryError,
        _normalize_minimax,
    )

    with pytest.raises(ProviderTelemetryError, match="invalid api key"):
        _normalize_minimax({
            "model_remains": [],
            "base_resp": {"status_code": 1004, "status_msg": "invalid api key"},
        })


@pytest.mark.asyncio
async def test_provider_requests_use_official_account_endpoints(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_model import telemetry

    requested = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, endpoint, **kwargs):
            requested.append((endpoint, kwargs))
            if "deepseek" in endpoint:
                return FakeResponse({"is_available": True, "balance_infos": []})
            return FakeResponse({
                "model_remains": [],
                "base_resp": {"status_code": 0, "status_msg": "success"},
            })

    monkeypatch.setattr(telemetry.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    telemetry._CACHE.clear()

    deepseek = await telemetry.provider_telemetry({
        "id": "deepseek",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "deepseek-secret",
        "options": {"provider_preset": "deepseek"},
    })
    minimax = await telemetry.provider_telemetry({
        "id": "minimax",
        "base_url": "https://api.minimaxi.com/v1",
        "api_key": "minimax-secret",
        "options": {"provider_preset": "minimax"},
    })

    assert deepseek["kind"] == "balance"
    assert minimax["kind"] == "quota"
    assert requested[0][0] == "https://api.deepseek.com/user/balance"
    assert requested[1][0] == "https://api.minimaxi.com/v1/token_plan/remains"
    assert requested[0][1]["headers"]["Authorization"] == "Bearer deepseek-secret"


@pytest.mark.asyncio
async def test_custom_provider_host_cannot_receive_account_credentials():
    from agent.plugin.plugin_impl.cyrene_model.telemetry import (
        ProviderTelemetryError,
        provider_telemetry,
    )

    with pytest.raises(ProviderTelemetryError, match="official provider endpoint"):
        await provider_telemetry({
            "id": "deepseek-proxy",
            "base_url": "https://proxy.example/v1",
            "api_key": "must-not-be-sent",
            "options": {"provider_preset": "deepseek"},
        }, force_refresh=True)


@pytest.mark.asyncio
async def test_cached_provider_usage_returns_immediately_and_refreshes_in_background(
    monkeypatch,
):
    from agent.plugin.plugin_impl.cyrene_model import telemetry

    connection = {
        "id": "deepseek",
        "base_url": "https://api.deepseek.com",
        "api_key": "secret",
        "options": {"provider_preset": "deepseek"},
    }
    key = telemetry._cache_key(connection, "deepseek")
    stale = {
        "connection_id": "deepseek",
        "provider": "deepseek",
        "label": "DeepSeek",
        "kind": "balance",
        "status": "ok",
        "available": True,
        "balances": [{"currency": "CNY", "total": "1.00"}],
        "windows": [],
    }
    fresh = {
        **stale,
        "balances": [{"currency": "CNY", "total": "2.00"}],
    }
    telemetry._CACHE.clear()
    telemetry._REFRESH_TASKS.clear()
    telemetry._CACHE[key] = (0.0, stale)
    request = AsyncMock(return_value=fresh)
    monkeypatch.setattr(telemetry, "_request_provider", request)

    immediate = await telemetry.provider_telemetry(connection, force_refresh=True)

    assert immediate["balances"][0]["total"] == "1.00"
    assert immediate["refreshing"] is True
    await asyncio.gather(*list(telemetry._REFRESH_TASKS.values()))
    updated = await telemetry.provider_telemetry(connection)
    assert updated["balances"][0]["total"] == "2.00"
    assert updated["refreshing"] is False


@pytest.mark.asyncio
async def test_configured_provider_usage_skips_connections_without_api_keys(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_model import telemetry

    model_service = type("ModelService", (), {
        "get_model_configuration": lambda self: {
        "connections": [
            {
                "id": "deepseek",
                "api_key": "",
                "options": {"provider_preset": "deepseek"},
            },
            {
                "id": "minimax",
                "api_key": "minimax-secret",
                "options": {"provider_preset": "minimax"},
            },
        ],
        }
    })()
    import agent.plugin as plugin_api
    monkeypatch.setattr(
        plugin_api,
        "active_plugin_service",
        lambda name: model_service if name == "model_configuration" else None,
    )
    fetch = AsyncMock(return_value={
        "connection_id": "minimax",
        "provider": "minimax",
        "status": "ok",
    })
    monkeypatch.setattr(telemetry, "provider_telemetry", fetch)

    result = await telemetry.configured_provider_telemetry()

    assert [item["provider"] for item in result] == ["minimax"]
    fetch.assert_awaited_once()
    assert fetch.await_args.args[0]["id"] == "minimax"


def test_provider_usage_route_forwards_explicit_refresh(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_model import telemetry
    from agent.plugin.plugin_impl.cyrene_model.routes import register_model_configuration_routes

    fetch = AsyncMock(return_value=[{
        "connection_id": "deepseek",
        "provider": "deepseek",
        "status": "ok",
    }])
    monkeypatch.setattr(telemetry, "configured_provider_telemetry", fetch)
    app = FastAPI()
    router = APIRouter()
    register_model_configuration_routes(router)
    app.include_router(router)

    response = TestClient(app).get(
        "/api/settings/model-config/provider-usage?refresh=true"
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["provider"] == "deepseek"
    fetch.assert_awaited_once_with(force_refresh=True)
