import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from conftest import workbench_shell_source

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))



def _patch_paths(monkeypatch, tmp_path, soul_content, default_content):
    from cyrene.runtime import onboarding
    from cyrene.plugins.builtin.cyrene_memory import archive as conversations

    soul_path = tmp_path / "workspace" / "SOUL.md"
    soul_path.parent.mkdir(parents=True, exist_ok=True)
    soul_path.write_text(soul_content, encoding="utf-8")

    monkeypatch.setattr(onboarding, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        onboarding,
        "DB_PATH",
        tmp_path / "store" / "cyrene.runtime.database",
    )
    def personality_status():
        current = soul_path.read_text(encoding="utf-8")
        configured = current.strip() != default_content.strip()
        return {
            "available": True,
            "configured": configured,
            "completedAt": "",
            "mode": "custom" if configured else "",
            "label": "",
            "isDefaultSoul": not configured,
            "path": str(soul_path),
            "currentContent": current,
            "source": "soul" if configured else "",
            "pristine": not configured,
        }

    monkeypatch.setattr(onboarding, "_personality_status", personality_status)
    monkeypatch.setattr(
        onboarding,
        "_memory_service",
        lambda: types.SimpleNamespace(has_existing_data=lambda: False),
    )
    monkeypatch.setattr(conversations, "CONVERSATIONS_DIR", tmp_path / "conversations")
    return soul_path


def test_get_onboarding_status_detects_absolute_fresh_start(monkeypatch, tmp_path):
    from cyrene.runtime import onboarding

    default_soul = "# Cyrene's Soul\n\n## SELF:IDENTITY\n- default\n"
    _patch_paths(monkeypatch, tmp_path, default_soul, default_soul)
    monkeypatch.setattr(onboarding, "_primary_model", lambda: {})

    status = onboarding.get_onboarding_status()
    assert status["hasExistingData"] is False

    assert status["needsOnboarding"] is True
    assert status["isAbsoluteFreshStart"] is True
    assert status["activeStep"] == "llm"


def test_get_onboarding_status_infers_existing_setup(monkeypatch, tmp_path):
    from cyrene.runtime import onboarding

    default_soul = "# Cyrene's Soul\n\n## SELF:IDENTITY\n- default\n"
    custom_soul = "# Sherlock's Soul\n\n## CORE IDENTITY\n- sharp and theatrical\n"
    _patch_paths(monkeypatch, tmp_path, custom_soul, default_soul)
    monkeypatch.setattr(onboarding, "_primary_model", lambda: {
        "api_key": "secret-key",
        "base_url": "https://example.test/v1",
        "model": "example-model",
        "adapter": "openai",
    })

    status = onboarding.get_onboarding_status()

    assert status["needsOnboarding"] is False
    assert status["activeStep"] == "done"
    assert status["personality"]["configured"] is True
    assert (tmp_path / "onboarding_state.json").exists()


def test_get_onboarding_status_skips_personality_when_soul_is_unavailable(
    monkeypatch,
    tmp_path,
):
    from cyrene.runtime import onboarding

    monkeypatch.setattr(onboarding, "DATA_DIR", tmp_path)
    monkeypatch.setattr(onboarding, "_has_existing_data", lambda: False)
    monkeypatch.setattr(
        onboarding,
        "_primary_model",
        lambda: {"model": "ready", "adapter": "test"},
    )
    monkeypatch.setattr(
        onboarding,
        "_personality_status",
        lambda: {
            "available": False,
            "configured": False,
            "completedAt": "",
            "mode": "",
            "label": "",
            "isDefaultSoul": False,
            "path": "",
            "currentContent": "",
            "source": "",
            "pristine": True,
        },
    )

    status = onboarding.get_onboarding_status()

    assert status["needsOnboarding"] is False
    assert status["activeStep"] == "done"
    assert status["personality"]["available"] is False


def test_core_onboarding_router_does_not_own_personality_endpoint():
    from fastapi import APIRouter

    from cyrene.workbench.http.settings.onboarding_context import register_onboarding_routes

    router = APIRouter()
    register_onboarding_routes(router)

    paths = {route.path for route in router.routes}
    assert "/api/onboarding" in paths
    assert "/api/onboarding/llm" in paths
    assert "/api/onboarding/openai-oauth" in paths
    assert "/api/onboarding/personality" not in paths


async def test_save_and_test_llm_setup_persists_completion(monkeypatch, tmp_path):
    from cyrene.runtime import onboarding

    default_soul = "# Cyrene's Soul\n\n## SELF:IDENTITY\n- default\n"
    _patch_paths(monkeypatch, tmp_path, default_soul, default_soul)
    monkeypatch.setattr(onboarding, "test_llm_connection", AsyncMock(return_value="OK"))
    monkeypatch.setattr(onboarding, "test_llm_vision_capability", AsyncMock(return_value={
        "vision_capable": True,
        "vision_checked_at": "2026-07-12T00:00:00+00:00",
        "vision_check_error": "",
    }))

    saved = {}
    graph = {
        "version": 10,
        "connections": [],
        "profiles": [],
        "routes": {name: [] for name in ("primary", "secondary", "vision", "embedding")},
    }
    model_service = type("ModelService", (), {
        "get_model_configuration": lambda self: graph,
        "save_model_configuration": lambda self, value: saved.setdefault("graph", value),
    })()
    memory_service = type("MemoryService", (), {"has_existing_data": lambda self: False})()
    import cyrene.core.plugin as plugin_api
    monkeypatch.setattr(
        plugin_api,
        "application_plugin_service",
        lambda name: model_service if name == "model_configuration" else memory_service if name == "memory" else None,
    )
    monkeypatch.setattr(
        onboarding,
        "application_plugin_service",
        lambda name: model_service if name in {"model_configuration", "model_probe"} else memory_service if name == "memory" else None,
    )
    monkeypatch.setattr(onboarding, "_primary_model", lambda: {
        "api_key": "sk-test",
        "base_url": "http://localhost:11434/v1",
        "model": "qwen3",
        "options": {"provider_preset": "openai_compatible"},
    })

    payload = await onboarding.save_and_test_llm_setup("sk-test", "http://localhost:11434/v1", "qwen3")

    assert payload["ok"] is True
    assert payload["preview"] == "OK"
    assert payload["onboarding"]["llm"]["configured"] is True
    assert payload["onboarding"]["activeStep"] == "personality"

    graph = saved["graph"]
    assert graph["routes"]["primary"] == ["onboarding-primary"]
    assert graph["connections"][0]["base_url"] == "http://localhost:11434/v1"
    assert graph["connections"][0]["api_key"] == "sk-test"
    assert graph["profiles"][0]["model"] == "qwen3"
    assert graph["profiles"][0]["capabilities"] == ["chat", "vision"]


async def test_onboarding_uses_enabled_provider_endpoints_and_adapter(monkeypatch, tmp_path):
    from cyrene.runtime import onboarding

    default_soul = "# Cyrene's Soul\n\n## SELF:IDENTITY\n- default\n"
    _patch_paths(monkeypatch, tmp_path, default_soul, default_soul)
    text_probe = AsyncMock(return_value="OK")
    vision_probe = AsyncMock(return_value={"vision_capable": False})
    monkeypatch.setattr(onboarding, "test_llm_connection", text_probe)
    monkeypatch.setattr(onboarding, "test_llm_vision_capability", vision_probe)

    catalog = [
        {"id": "codex_oauth", "name": "Codex OAuth", "adapter": "codex_oauth", "auth_type": "oauth", "default_base_url": "codex://oauth"},
        {"id": "anthropic", "name": "Anthropic", "adapter": "anthropic", "default_base_url": "https://api.anthropic.com/v1"},
        {"id": "deepseek", "name": "DeepSeek", "adapter": "openai", "default_base_url": "https://api.deepseek.com/v1", "default_model": "deepseek-chat"},
        {"id": "openai", "name": "OpenAI", "adapter": "openai", "default_base_url": "https://api.openai.com/v1"},
        {"id": "openai_compatible", "name": "OpenAI Compatible", "adapter": "openai_compatible", "default_base_url": "https://api.openai.com/v1"},
    ]
    saved = {}
    graph = {
        "version": 10,
        "connections": [],
        "profiles": [],
        "routes": {name: [] for name in ("primary", "secondary", "vision", "embedding")},
    }
    model_service = type("ModelService", (), {
        "catalog": lambda self: catalog,
        "get_model_configuration": lambda self: graph,
        "save_model_configuration": lambda self, value: saved.setdefault("graph", value),
    })()
    memory_service = type("MemoryService", (), {"has_existing_data": lambda self: False})()
    monkeypatch.setattr(
        onboarding,
        "application_plugin_service",
        lambda name: model_service if name in {"model_configuration", "model_probe"} else memory_service if name == "memory" else None,
    )
    monkeypatch.setattr(onboarding, "_primary_model", lambda: {
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-test",
        "options": {"provider_preset": "anthropic"},
    })

    options = onboarding._supported_model_endpoints()
    assert [item["providerId"] for item in options] == ["deepseek", "openai", "anthropic"]
    assert options[0]["defaultModel"] == "deepseek-chat"

    payload = await onboarding.save_and_test_llm_setup(
        "sk-test",
        "https://api.anthropic.com/v1",
        "claude-test",
        "anthropic",
    )

    assert payload["ok"] is True
    text_probe.assert_awaited_once_with(
        "sk-test", "https://api.anthropic.com/v1", "claude-test", "anthropic", "anthropic"
    )
    assert saved["graph"]["connections"][0]["adapter"] == "anthropic"
    assert saved["graph"]["connections"][0]["options"] == {"provider_preset": "anthropic"}


async def test_save_codex_oauth_setup_persists_model_and_effort(monkeypatch, tmp_path):
    from cyrene.runtime import onboarding

    default_soul = "# Cyrene's Soul\n\n## SELF:IDENTITY\n- default\n"
    _patch_paths(monkeypatch, tmp_path, default_soul, default_soul)

    class FakeProvider:
        async def account(self):
            return {
                "account": {
                    "type": "chatgpt",
                    "email": "user@example.com",
                }
            }

        async def models(self):
            return [{
                "id": "gpt-5.6-terra",
                "supportedReasoningEfforts": [
                    {"reasoningEffort": "low"},
                    {"reasoningEffort": "high"},
                ],
            }]

        async def close(self):
            return None

    saved = {}
    graph = {
        "version": 10,
        "connections": [],
        "profiles": [],
        "routes": {name: [] for name in ("primary", "secondary", "vision", "embedding")},
    }
    model_service = type("ModelService", (), {
        "get_model_configuration": lambda self: graph,
        "save_model_configuration": lambda self, value: saved.setdefault("graph", value),
        "oauth_provider": lambda self: FakeProvider(),
        "oauth_base_url": lambda self: "codex://oauth",
    })()
    memory_service = type("MemoryService", (), {"has_existing_data": lambda self: False})()
    import cyrene.core.plugin as plugin_api
    monkeypatch.setattr(
        plugin_api,
        "application_plugin_service",
        lambda name: model_service if name == "model_configuration" else memory_service if name == "memory" else None,
    )
    monkeypatch.setattr(
        onboarding,
        "application_plugin_service",
        lambda name: model_service if name == "model_configuration" else memory_service if name == "memory" else None,
    )
    monkeypatch.setattr(onboarding, "_primary_model", lambda: {
        "model": "gpt-5.6-terra",
        "reasoning_effort": "high",
        "options": {"provider_preset": "codex_oauth"},
    })

    payload = await onboarding.save_codex_oauth_setup(
        "gpt-5.6-terra",
        "high",
    )

    assert payload["ok"] is True
    assert payload["onboarding"]["llm"]["provider"] == "codex_oauth"
    assert payload["onboarding"]["llm"]["model"] == "gpt-5.6-terra"
    assert payload["onboarding"]["llm"]["reasoningEffort"] == "high"
    assert payload["onboarding"]["activeStep"] == "personality"
    graph = saved["graph"]
    assert graph["routes"]["primary"] == ["onboarding-codex-primary"]
    assert graph["connections"][0]["adapter"] == "codex_oauth"
    assert graph["profiles"][0]["model"] == "gpt-5.6-terra"
    assert graph["profiles"][0]["reasoning_effort"] == "high"
    assert graph["profiles"][0]["capabilities"] == [
        "chat", "vision", "tools", "reasoning"
    ]


async def test_vision_capability_probe_sends_an_image(monkeypatch):
    from cyrene.runtime import onboarding
    from cyrene.plugins.builtin.cyrene_model import probe as model_probe_service

    calls = []

    async def fake_complete(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return {"content": "Image received"}

    monkeypatch.setattr(model_probe_service, "_complete", fake_complete)
    probe_service = model_probe_service.ModelProbeService()
    monkeypatch.setattr(
        onboarding,
        "application_plugin_service",
        lambda name: probe_service if name == "model_probe" else None,
    )

    result = await onboarding.test_llm_vision_capability("sk-test", "https://example.test/v1", "vision-model")

    assert result["vision_capable"] is True
    content = calls[0]["kwargs"]["messages"][0]["content"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://api.deepseek.com", "https://api.deepseek.com/v1/chat/completions"),
        ("https://api.minimaxi.com", "https://api.minimaxi.com/v1/chat/completions"),
    ],
)
async def test_text_connection_probe_normalizes_official_provider_endpoint(
    monkeypatch,
    base_url,
    expected,
):
    from cyrene.runtime import onboarding
    from cyrene.plugins.builtin.cyrene_model import probe as model_probe_service

    calls = []

    async def fake_complete(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return {"content": "OK"}

    monkeypatch.setattr(model_probe_service, "_complete", fake_complete)
    probe_service = model_probe_service.ModelProbeService()
    monkeypatch.setattr(
        onboarding,
        "application_plugin_service",
        lambda name: probe_service if name == "model_probe" else None,
    )

    assert await onboarding.test_llm_connection("sk-test", base_url, "model") == "OK"
    assert calls[0]["args"][1] == base_url.rstrip("/")










def test_completed_onboarding_creates_and_opens_the_first_chat():
    root = Path(__file__).resolve().parent.parent
    welcome_source = (
        root / "src" / "cyrene" / "workbench" / "webui" / "frontend" / "workbench-welcome.jsx"
    ).read_text(encoding="utf-8")
    workbench_source = workbench_shell_source()

    assert (
        "p.onboarding && !p.onboarding.needsOnboarding && props.onComplete"
        in welcome_source
    )
    assert "props.onComplete();" in welcome_source
    assert "Page: OnboardingFlow" in welcome_source

    completion_handler = workbench_source.split(
        "function handleOnboardingComplete() {", 1
    )[1].split("}", 1)[0]
    assert "createChat();" in completion_handler
    create_chat_handler = workbench_source.split("function createChat() {", 1)[1].split(
        "}", 1
    )[0]
    assert 'setFullPage("chat");' in create_chat_handler
    assert "setNewChatRequestId" in create_chat_handler
    assert "onComplete={handleOnboardingComplete}" in workbench_source
