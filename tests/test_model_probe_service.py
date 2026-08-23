from __future__ import annotations

import pytest

from route.settings import model_service


@pytest.mark.asyncio
async def test_model_settings_orders_validation_capability_and_persistence(monkeypatch):
    calls: list[object] = []
    candidate = {
        "id": "primary",
        "model": "vision-model",
        "provider": "openai_compatible",
        "api_key": "sk-test",
        "base_url": "https://example.test/v1",
    }
    prepared = model_service.PreparedModels(
        primary_source="custom",
        custom=[candidate],
        codex=None,
        active=[candidate],
        vision=[dict(candidate)],
        raw_vision=[candidate],
        raw_secondary=None,
    )

    class Probe:
        async def test_connection(self, api_key, base_url, model):
            calls.append(("text", api_key, base_url, model))
            return "OK"

        async def probe_vision(self, api_key, base_url, model):
            calls.append(("vision", api_key, base_url, model))
            return {
                "vision_capable": True,
                "vision_checked_at": "2026-08-23T00:00:00+00:00",
                "vision_check_error": "",
            }

    monkeypatch.setattr(model_service, "_prepare_models", lambda _body: prepared)
    monkeypatch.setattr(
        model_service,
        "_persist_models",
        lambda models: calls.append(("persist", models)),
    )
    monkeypatch.setattr(
        model_service,
        "_update_response",
        lambda models: calls.append(("response", models)) or {"ok": True},
    )

    result = await model_service.ModelSettingsApplicationService(Probe()).update_settings({})

    assert result == {"ok": True}
    assert [call[0] for call in calls] == ["text", "vision", "persist", "response"]
    assert prepared.custom[0]["vision_capable"] is True
    assert prepared.vision[0]["vision_capable"] is True
