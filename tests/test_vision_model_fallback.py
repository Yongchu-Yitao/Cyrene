from types import SimpleNamespace

import pytest

from cyrene.core.plugin import Plugin, PluginContext, PluginRegistry
from cyrene.plugins import model_catalog, model_router
from cyrene.plugins.model_gateway import PluginModelGateway


def candidate(name, capabilities=("chat", "vision"), **kwargs):
    return {"id": name, "model": name, "adapter": "openai",
            "provider": "openai", "capabilities": list(capabilities),
            "context_limit": 200_000, **kwargs}


def configure(monkeypatch, vision, primary, selected=None):
    from cyrene.platform import settings_store

    service = SimpleNamespace(
        candidates_for_route=lambda route: {"vision": vision, "primary": primary}.get(route, []),
        candidate_for_profile=lambda profile_id: selected,
    )
    preferences = {"chat": {
        "candidate_id": selected["id"], "model": selected["model"], "adapter": "openai",
    }} if selected else {}
    monkeypatch.setattr(model_catalog, "_model_configuration_port", lambda: service)
    monkeypatch.setattr(settings_store, "get", lambda key, default=None: (
        preferences if key == "llm_session_model_preferences" else default
    ))


def test_vision_chain_prioritizes_selected_primary_filters_and_deduplicates(monkeypatch):
    vision = candidate("vision")
    primary = candidate("primary")
    selected = candidate("selected")
    configure(monkeypatch, [vision], [
        candidate("text", ("chat",)), primary, vision,
        candidate("foreign", adapter="codex_oauth", provider="codex_oauth"),
    ], selected)
    chain = model_catalog.configured_model_candidates("chat", route="vision")
    assert [item["id"] for item in chain] == ["vision", "selected", "primary"]
    assert [item["_model_route"] for item in chain] == ["vision", "primary", "primary"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["offline", "context", "empty", "none", "all_failed"])
async def test_vision_gateway_automatically_uses_primary(monkeypatch, failure):
    vision = candidate("vision", context_limit=1 if failure == "context" else 200_000)
    configure(monkeypatch, [] if failure == "empty" else [vision], [candidate("primary")])
    calls = []

    async def provider(arguments, _context):
        calls.append(arguments["model"])
        if failure == "all_failed" or (arguments["model"] == "vision" and failure == "offline"):
            raise RuntimeError("service unavailable")
        return {"content": "Image analyzed", "model": arguments["model"], "usage": {}}

    async def ignore(*args, **kwargs):
        pass

    monkeypatch.setattr(model_router, "remember_model_success", lambda *a, **k: None)
    for name in ("_publish_llm_event", "_publish_fallback", "_persist_fallback_result"):
        monkeypatch.setattr(model_router, name, ignore)
    registry = PluginRegistry(include_core=False)
    registry.register_plugin(Plugin(
        name="VisionTestProvider", description="test", kind="model",
        input_schema={"type": "object"}, handler=provider,
        metadata={"provider": {"id": "openai", "name": "test"}},
    ), source="test")
    gateway = PluginModelGateway(registry)
    request = gateway.complete(
        [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "https://example.test/image.png"}},
        ]}], route="vision", session_id="chat", context=PluginContext(),
    )
    if failure == "all_failed":
        with pytest.raises(RuntimeError):
            await request
        assert calls == ["vision", "primary"]
    else:
        result = await request
        assert result["model"] == ("vision" if failure == "none" else "primary")
        assert calls == {
            "offline": ["vision", "primary"], "context": ["primary"],
            "empty": ["primary"], "none": ["vision"],
        }[failure]
