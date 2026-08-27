import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from conftest import workbench_shell_source

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from route.registry import register_routes


def _patch_paths(monkeypatch, tmp_path, soul_content, default_content):
    from cyrene.runtime import onboarding, setup
    from agent.plugin.plugin_impl.cyrene_memory import archive as conversations

    soul_path = tmp_path / "workspace" / "SOUL.md"
    soul_path.parent.mkdir(parents=True, exist_ok=True)
    soul_path.write_text(soul_content, encoding="utf-8")

    monkeypatch.setattr(onboarding, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        onboarding,
        "DB_PATH",
        tmp_path / "store" / "cyrene.runtime.database",
    )
    monkeypatch.setattr(onboarding, "get_soul_path", lambda: soul_path)
    monkeypatch.setattr(onboarding, "read_soul", lambda: soul_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(onboarding, "get_default_soul_content", lambda name=None: default_content)
    monkeypatch.setattr(
        onboarding,
        "_memory_service",
        lambda: types.SimpleNamespace(
            has_existing_data=lambda: False,
            write_soul=lambda content: soul_path.write_text(
                str(content or ""), encoding="utf-8"
            )
        ),
    )
    monkeypatch.setattr(setup, "DATA_DIR", tmp_path)
    monkeypatch.setattr(setup, "_SETUP_FLAG", None)
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
    monkeypatch.setattr(onboarding, "get_model_configuration", lambda: {
        "version": 10,
        "connections": [],
        "profiles": [],
        "routes": {name: [] for name in ("primary", "secondary", "vision", "embedding")},
    })
    monkeypatch.setattr(
        onboarding,
        "save_model_configuration",
        lambda graph: saved.setdefault("graph", graph),
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


async def test_save_codex_oauth_setup_persists_model_and_effort(monkeypatch, tmp_path):
    from cyrene.model_runtime import codex_provider
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
    monkeypatch.setattr(codex_provider, "get_codex_provider", lambda: FakeProvider())
    monkeypatch.setattr(onboarding, "get_model_configuration", lambda: {
        "version": 10,
        "connections": [],
        "profiles": [],
        "routes": {name: [] for name in ("primary", "secondary", "vision", "embedding")},
    })
    monkeypatch.setattr(
        onboarding,
        "save_model_configuration",
        lambda graph: saved.setdefault("graph", graph),
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
    from cyrene.runtime import model_probe_service

    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "Image received"}}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, endpoint, headers=None, json=None):
            calls.append({"endpoint": endpoint, "headers": headers, "json": json})
            return FakeResponse()

    monkeypatch.setattr(model_probe_service.httpx, "AsyncClient", lambda *args, **kwargs: FakeClient())

    result = await onboarding.test_llm_vision_capability("sk-test", "https://example.test/v1", "vision-model")

    assert result["vision_capable"] is True
    content = calls[0]["json"]["messages"][0]["content"]
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
    from cyrene.runtime import model_probe_service

    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "OK"}}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, endpoint, headers=None, json=None):
            calls.append(endpoint)
            return FakeResponse()

    monkeypatch.setattr(model_probe_service.httpx, "AsyncClient", lambda *args, **kwargs: FakeClient())

    assert await onboarding.test_llm_connection("sk-test", base_url, "model") == "OK"
    assert calls == [expected]










def test_completed_onboarding_creates_and_opens_the_first_chat():
    root = Path(__file__).resolve().parent.parent
    welcome_source = (
        root / "src" / "webui" / "frontend" / "workbench-welcome.jsx"
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
