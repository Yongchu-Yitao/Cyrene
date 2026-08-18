"""Focused coverage for plugin-oriented model configuration."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_model_store(tmp_path, monkeypatch):
    from cyrene.runtime import config_store

    monkeypatch.setattr(config_store, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config_store, "_ENCRYPTED_PATH", tmp_path / "data" / "config.enc")
    monkeypatch.setattr(config_store, "_KEY_PATH", tmp_path / "data" / ".config_key")
    monkeypatch.setattr(config_store, "_LEGACY_ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(
        config_store,
        "_LEGACY_SETTINGS_PATH",
        tmp_path / "data" / "web_settings.json",
    )
    monkeypatch.setattr(config_store, "_cache", None)
    monkeypatch.setattr(config_store, "_migrated", False)
    monkeypatch.setattr(config_store, "_fernet", None)
    monkeypatch.setattr(config_store, "_SETTINGS_MIGRATIONS_DONE", False)
    return config_store


def _configuration(api_key: str = "sk-private") -> dict:
    return {
        "connections": [
            {
                "id": "fastllm",
                "name": "FastLLM",
                "adapter": "openai_compatible",
                "base_url": "http://127.0.0.1:1256/v1",
                "api_key": api_key,
            },
            {
                "id": "local",
                "name": "Local embedding",
                "adapter": "local_onnx",
            },
        ],
        "profiles": [
            {
                "id": "qwen-next",
                "connection_id": "fastllm",
                "model": "Qwen3-Next-80B-A3B",
                "capabilities": ["chat", "vision", "tools"],
                "context_limit": 1_000_000,
            },
            {
                "id": "qwen-embed",
                "connection_id": "local",
                "model": "qwen3-embedding-0.6b",
                "capabilities": ["embedding"],
                "dimensions": 1024,
            },
        ],
        "routes": {
            "primary": ["qwen-next"],
            "secondary": ["qwen-next"],
            "vision": ["qwen-next"],
            "embedding": ["qwen-embed"],
        },
    }


def test_save_redacts_secrets_and_mirrors_independent_routes(isolated_model_store):
    from cyrene.runtime.model_configuration import (
        public_model_configuration,
        save_model_configuration,
    )

    saved, revision = save_model_configuration(_configuration())
    public = public_model_configuration(saved)

    assert revision > 0
    assert public["connections"][0]["api_key"] == ""
    assert public["connections"][0]["api_key_configured"] is True
    assert public["connections"][0]["secret_configured"] is True
    assert public["routes"] == _configuration()["routes"]
    adapters = {item["id"]: item for item in public["adapters"]}
    assert {"openai_compatible", "codex_oauth", "ollama", "local_onnx"} <= set(adapters)
    assert adapters["openai_compatible"]["config_schema"] == [
        {"name": "base_url", "type": "url", "required": True},
        {"name": "api_key", "type": "secret", "required": False},
    ]
    for adapter_id in ("anthropic", "openai", "openai_responses", "gemini"):
        assert adapters[adapter_id]["user_selectable"] is True
        assert adapters[adapter_id]["category"] == "remote"
    for adapter_id in ("openai_compatible", "codex_oauth", "ollama", "local_onnx"):
        assert adapters[adapter_id]["user_selectable"] is False
    assert adapters["anthropic"]["wire_protocol"] == "anthropic_messages"
    assert adapters["openai"]["wire_protocol"] == "openai_chat_completions"
    assert adapters["openai_responses"]["wire_protocol"] == "openai_responses"
    assert adapters["gemini"]["wire_protocol"] == "gemini_generate_content"

    primary = isolated_model_store.get_models()
    assert primary[0]["profile_id"] == "qwen-next"
    assert primary[0]["context_limit"] == 1_000_000
    assert isolated_model_store.get_vision_models()[0]["profile_id"] == "qwen-next"
    assert isolated_model_store.get_secondary_model()["profile_id"] == "qwen-next"
    assert isolated_model_store.get_setting("embedding")["model"] == "qwen3-embedding-0.6b"
    assert isolated_model_store.get_env("OPENAI_MODEL") == "Qwen3-Next-80B-A3B"
    assert isolated_model_store.get_env("EMBEDDING_MODEL") == "qwen3-embedding-0.6b"


def test_blank_secret_is_retained_and_clear_is_explicit(isolated_model_store):
    from cyrene.runtime.model_configuration import (
        get_model_configuration,
        save_model_configuration,
    )

    save_model_configuration(_configuration())
    blank = _configuration(api_key="")
    blank["connections"][0]["name"] = "Renamed"
    save_model_configuration(blank)

    retained = get_model_configuration()
    assert retained["connections"][0]["name"] == "Renamed"
    assert retained["connections"][0]["api_key"] == "sk-private"

    blank["connections"][0]["clear_api_key"] = True
    save_model_configuration(blank)
    assert get_model_configuration()["connections"][0]["api_key"] == ""
    assert isolated_model_store.get_env("OPENAI_API_KEY") == ""


def test_legacy_models_migrate_to_profiles_and_routes(isolated_model_store):
    from cyrene.runtime.model_configuration import get_model_configuration

    isolated_model_store.update_settings_and_env_atomic(
        {
            "models": [
                {
                    "id": "legacy-main",
                    "name": "Legacy main",
                    "model": "legacy/model",
                    "base_url": "https://example.test/v1",
                    "api_key": "sk-legacy",
                    "ctx": "128K",
                }
            ],
            "vision_models": [],
            "custom_models": [],
            "model_source": "custom",
        },
        {
            "OPENAI_MODEL": "legacy/model",
            "OPENAI_BASE_URL": "https://example.test/v1",
            "OPENAI_API_KEY": "sk-legacy",
        },
    )

    migrated = get_model_configuration(persist_migration=False)

    assert migrated["routes"]["primary"] == ["legacy-main"]
    assert migrated["profiles"][0]["context_limit"] == 128_000
    assert migrated["profiles"][0]["connection_id"] == migrated["connections"][0]["id"]


def test_default_provider_connections_are_added_once_and_can_be_deleted(
    isolated_model_store,
):
    from cyrene.runtime.model_configuration import (
        CONFIG_VERSION,
        get_model_configuration,
        save_model_configuration,
    )

    configured = get_model_configuration()
    providers = {item["id"]: item for item in configured["connections"]}

    assert configured["version"] == CONFIG_VERSION
    assert providers["minimax"] == {
        "id": "minimax",
        "name": "MiniMax",
        "adapter": "openai",
        "enabled": True,
        "base_url": "https://api.minimax.io/v1",
        "api_key": "",
        "options": {"provider_preset": "minimax"},
    }
    assert providers["deepseek"] == {
        "id": "deepseek",
        "name": "DeepSeek",
        "adapter": "openai",
        "enabled": True,
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "options": {"provider_preset": "deepseek"},
    }
    assert configured["profiles"] == []
    assert all(not route for route in configured["routes"].values())

    without_minimax = {
        **configured,
        "connections": [
            item for item in configured["connections"] if item["id"] != "minimax"
        ],
    }
    save_model_configuration(without_minimax)
    reloaded = get_model_configuration()

    assert reloaded["version"] == CONFIG_VERSION
    assert [item["id"] for item in reloaded["connections"]] == ["deepseek"]


def test_provider_upgrade_recognizes_existing_custom_connections(
    isolated_model_store,
):
    from cyrene.runtime.model_configuration import get_model_configuration

    existing = _configuration()
    existing["version"] = 1
    existing["connections"].append({
        "id": "minimax-cn",
        "name": "My MiniMax",
        "adapter": "openai",
        "enabled": True,
        "base_url": "https://api.minimaxi.com/v1",
        "api_key": "sk-minimax",
        "options": {},
    })
    isolated_model_store.update_settings_atomic({"model_configuration": existing})

    upgraded = get_model_configuration(persist_migration=False)
    minimax = [
        item
        for item in upgraded["connections"]
        if "minimax" in item["name"].lower()
    ]

    assert len(minimax) == 1
    assert minimax[0]["id"] == "minimax-cn"
    assert minimax[0]["api_key"] == "sk-minimax"
    assert sum(item["id"] == "deepseek" for item in upgraded["connections"]) == 1
    assert upgraded["routes"] == existing["routes"]


def test_provider_upgrade_rebrands_legacy_deepseek_connection_in_place(
    isolated_model_store,
):
    from cyrene.runtime.model_configuration import get_model_configuration

    existing = _configuration()
    existing["version"] = 2
    existing["connections"][0].update({
        "name": "OpenAI Compatible",
        "adapter": "openai_compatible",
        "base_url": "https://api.deepseek.com",
        "api_key": "sk-deepseek",
    })
    existing["profiles"][0].update({
        "model": "deepseek-v4-flash",
        "name": "deepseek-v4-flash",
        "capabilities": ["chat"],
    })
    isolated_model_store.update_settings_atomic({"model_configuration": existing})

    upgraded = get_model_configuration(persist_migration=False)
    deepseek = next(
        item for item in upgraded["connections"] if item["id"] == "fastllm"
    )

    assert deepseek["name"] == "DeepSeek"
    assert deepseek["adapter"] == "openai"
    assert deepseek["api_key"] == "sk-deepseek"
    assert deepseek["options"]["provider_preset"] == "deepseek"
    assert sum(item["name"] == "DeepSeek" for item in upgraded["connections"]) == 1
    assert upgraded["profiles"][0]["connection_id"] == "fastllm"
    assert upgraded["routes"] == existing["routes"]


def test_profile_route_validation_rejects_dangling_references():
    from cyrene.runtime.model_configuration import normalize_model_configuration

    raw = _configuration()
    raw["routes"]["primary"] = ["missing-profile"]
    with pytest.raises(ValueError, match="unknown profile"):
        normalize_model_configuration(raw)


def test_runtime_candidates_keep_wire_protocols_distinct(monkeypatch):
    from cyrene.model_runtime import client

    configured = [
        {
            "id": "chat",
            "profile_id": "chat",
            "adapter": "openai",
            "provider": "openai",
            "model": "gpt-test",
            "base_url": "https://api.example.test/v1",
            "api_key": "sk-test",
        },
        {
            "id": "responses",
            "profile_id": "responses",
            "adapter": "openai_responses",
            "provider": "openai_responses",
            "model": "gpt-test",
            "base_url": "https://api.example.test/v1",
            "api_key": "sk-test",
        },
    ]
    monkeypatch.setattr(client, "get_models", lambda: configured)

    candidates = client.resolve_llm_candidates()

    assert candidates[0]["endpoints"] == ["https://api.example.test/v1/chat/completions"]
    assert candidates[1]["endpoints"] == ["https://api.example.test/v1/responses"]
    assert client._candidate_key(candidates[0], "chat-1") != client._candidate_key(
        candidates[1], "chat-1"
    )
    selected = client.resolve_exact_model_candidate({
        "candidateId": "responses",
        "adapter": "openai_responses",
        "provider": "openai_responses",
        "model": "gpt-test",
        "baseUrl": "https://api.example.test",
    })
    assert selected is not None
    assert selected["adapter"] == "openai_responses"


def test_selectable_models_include_non_default_chat_profiles_only():
    from cyrene.runtime.model_configuration import (
        normalize_model_configuration,
        selectable_model_candidates,
    )

    raw = _configuration()
    raw["profiles"].extend([
        {
            "id": "manual-vision",
            "connection_id": "fastllm",
            "model": "vision/manual",
            "capabilities": ["vision"],
        },
        {
            "id": "disabled-chat",
            "connection_id": "fastllm",
            "model": "chat/disabled",
            "capabilities": ["chat"],
            "enabled": False,
        },
    ])

    candidates = selectable_model_candidates(normalize_model_configuration(raw))

    assert [item["id"] for item in candidates] == ["qwen-next", "manual-vision"]
    assert candidates[1]["model"] == "vision/manual"


def test_deleting_connection_persists_graph_and_clears_legacy_mirrors(
    isolated_model_store,
):
    from cyrene.runtime.model_configuration import (
        get_model_configuration,
        save_model_configuration,
    )

    save_model_configuration(_configuration())
    deleted = _configuration()
    deleted["connections"] = [
        item for item in deleted["connections"] if item["id"] != "fastllm"
    ]
    deleted["profiles"] = [
        item for item in deleted["profiles"] if item["connection_id"] != "fastllm"
    ]
    deleted["routes"] = {
        route: [profile_id for profile_id in profile_ids if profile_id != "qwen-next"]
        for route, profile_ids in deleted["routes"].items()
    }

    save_model_configuration(deleted)
    isolated_model_store._cache = None
    reloaded = get_model_configuration()

    assert all(item["id"] != "fastllm" for item in reloaded["connections"])
    assert all(item["id"] != "qwen-next" for item in reloaded["profiles"])
    assert all(
        "qwen-next" not in profile_ids for profile_ids in reloaded["routes"].values()
    )
    assert isolated_model_store.get_models() == []
    assert all(
        item.get("profile_id") != "qwen-next"
        for item in isolated_model_store.get_custom_models()
    )


def test_frontend_registers_split_pages_and_live_context_contract():
    root = Path(__file__).resolve().parents[1]
    settings = (root / "src/webui/frontend/settings-model-configuration.jsx").read_text()
    overlay = (root / "src/webui/frontend/settings-overlay.jsx").read_text()
    chat = (root / "src/webui/frontend/workbench-chat.jsx").read_text()
    i18n = (root / "src/webui/frontend/workbench-i18n.jsx").read_text()

    assert 'register("model-settings"' in settings
    assert "ServicesPage: ServicesPage" in settings
    assert "UsagePage: UsagePage" in settings
    assert 'id: "setting-model-" + route + "-route"' in settings
    assert 'CustomEvent("cyrene:model-configuration-changed"' in settings
    assert 'require("model-settings").ServicesPage' in overlay
    assert 'require("model-settings").UsagePage' in overlay
    assert '"settings.modelUsage": "模型配置"' in i18n
    assert 'label(props, "settings.adapter", "协议")' in settings
    assert '"settings.adapter": "Adapter"' in i18n
    assert '"settings.adapter": "协议"' in i18n
    assert 'h("h4", { id: "wb-mcfg-profiles-heading" }, "模型列表")' in settings
    assert "档案描述一个可被多个用途引用的远端模型。" not in settings
    assert 'label(props, "settings.inputPrice", "输入价格")' in settings
    assert 'label(props, "settings.outputPrice", "输出价格")' in settings
    assert 'label(props, "settings.cachePrice", "缓存价格")' in settings
    assert 'props.onChange("price", updateProfilePriceField' in settings
    assert 'h("span", null, "能力")' not in settings
    assert 'h("button", {' in settings
    assert 'className: "wb-mcfg-profile-summary"' in settings
    assert '"aria-expanded": expanded' in settings
    assert 'h("span", { className: "wb-btn wb-mcfg-profile-details-button"' in settings
    assert 'label: "Adapter"' not in settings
    assert 'return "本地模型"' in settings
    assert 'className: "wb-model-card wb-local-model wb-mcfg-local-row"' in settings
    assert 'label(props, "settings.localModelActive"' in settings
    assert '!isLocalConnection(selected) ? h("section", { className: "wb-mcfg-form-section"' in settings
    assert 'hideHeader: true' in settings
    assert 'WBC_CHAT_MODEL_CHANGED_EVENT = "cyrene:wbc-chat-model-changed"' in chat
    assert 'window.addEventListener("cyrene:model-configuration-changed"' in chat
    assert "payload.selectable_models" in chat
    assert "payload.selectable_models" in (
        root / "src/webui/frontend/workbench.jsx"
    ).read_text()
    settings_route = (root / "src/route/settings/general.py").read_text()
    assert "for candidate in selectable_model_candidates():" in settings_route
    assert '"selectable_models": selectable_models,' in settings_route
    assert "selectable_models or normalized" not in settings_route
    assert 'setSelectedModelId("");' in chat
    assert "store.save(nextConfig, true).then" in settings
    assert "并立即保存。" in settings
    assert chat.index("var liveModel = String(liveData") < chat.index(
        "var activeModel = String(runtime"
    )
    assert "segTotal <= 0 && used <= 0 && limit <= 0" in chat
