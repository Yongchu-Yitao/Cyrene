"""Focused coverage for plugin-oriented model configuration."""

from __future__ import annotations
from conftest import (
    workbench_chat_source,
    workbench_i18n_source,
    workbench_settings_source,
)

from pathlib import Path

import pytest


@pytest.fixture
def isolated_model_store(tmp_path, monkeypatch):
    from cyrene.platform import config_store

    monkeypatch.setattr(config_store, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config_store, "_ENCRYPTED_PATH", tmp_path / "data" / "config.enc")
    monkeypatch.setattr(config_store, "_KEY_PATH", tmp_path / "data" / ".config_key")
    monkeypatch.setattr(config_store, "_cache", None)
    monkeypatch.setattr(config_store, "_fernet", None)
    monkeypatch.setattr(config_store, "_initialized", False)
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


def test_runtime_candidate_merges_connection_and_profile_transport_options():
    from cyrene.plugins.builtin.cyrene_model.configuration import (
        candidate_for_profile,
        normalize_model_configuration,
    )

    raw = _configuration()
    raw["connections"][0]["options"] = {
        "provider_preset": "custom",
        "prompt_cache_key_supported": False,
    }
    raw["profiles"][0]["options"] = {
        "prompt_cache_key_supported": True,
    }

    candidate = candidate_for_profile(
        "qwen-next",
        normalize_model_configuration(raw),
    )

    assert candidate is not None
    assert candidate["options"] == {
        "provider_preset": "custom",
        "prompt_cache_key_supported": True,
    }


def test_model_connection_proxy_opt_in_survives_normalization_and_runtime_projection():
    from cyrene.plugins.builtin.cyrene_model.configuration import (
        candidate_for_profile,
        normalize_model_configuration,
        public_model_configuration,
    )

    raw = _configuration()
    raw["connections"][0]["use_proxy"] = True
    normalized = normalize_model_configuration(raw)

    assert normalized["connections"][0]["use_proxy"] is True
    assert public_model_configuration(normalized)["connections"][0]["use_proxy"] is True
    candidate = candidate_for_profile("qwen-next", normalized)
    assert candidate is not None
    assert candidate["use_proxy"] is True


def test_save_redacts_secrets_and_persists_only_the_canonical_graph(
    isolated_model_store,
):
    from cyrene.plugins.builtin.cyrene_model.configuration import (
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

    settings = isolated_model_store.export_snapshot()["settings"]
    assert settings["model_configuration"]["routes"] == _configuration()["routes"]
    assert {
        "models",
        "custom_models",
        "model_source",
        "vision_models",
        "secondary_model",
        "embedding",
    }.isdisjoint(settings)




def test_blank_secret_is_retained_and_clear_is_explicit(isolated_model_store):
    from cyrene.plugins.builtin.cyrene_model.configuration import (
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


def test_user_model_plugin_is_projected_as_an_editable_provider_connection():
    from cyrene.plugins.builtin.cyrene_model.configuration import normalize_model_configuration
    from cyrene.plugins.builtin.cyrene_model.routes import _public_configuration_with_plugins

    class CatalogService:
        @staticmethod
        def catalog():
            return [
                {
                    "id": "deleted_builtin",
                    "name": "Deleted built-in",
                    "adapter": "openai",
                    "default_base_url": "https://builtin.example/v1",
                    "plugin_name": "DeletedBuiltin",
                    "pack_id": "cyrene_model",
                },
                {
                    "id": "user_cloud",
                    "name": "User Cloud",
                    "adapter": "openai_compatible",
                    "default_base_url": "https://user-cloud.example/v1",
                    "plugin_name": "UserCloud",
                    "pack_id": "my_model_pack",
                },
            ]

    configuration = normalize_model_configuration(_configuration(api_key=""))
    original_connection_ids = [
        connection["id"] for connection in configuration["connections"]
    ]

    payload = _public_configuration_with_plugins(
        configuration,
        service=CatalogService(),
    )
    connections = {connection["id"]: connection for connection in payload["connections"]}

    assert "deleted_builtin" not in connections
    assert connections["user_cloud"] == {
        "id": "user_cloud",
        "name": "User Cloud",
        "adapter": "openai_compatible",
        "enabled": True,
        "use_proxy": False,
        "base_url": "https://user-cloud.example/v1",
        "api_key": "",
        "api_key_configured": False,
        "secret_configured": False,
        "options": {"provider_preset": "user_cloud"},
        "_plugin_unconfigured": True,
    }
    assert [connection["id"] for connection in configuration["connections"]] == (
        original_connection_ids
    )


def test_configured_user_model_plugin_connection_is_not_duplicated():
    from cyrene.plugins.builtin.cyrene_model.configuration import normalize_model_configuration
    from cyrene.plugins.builtin.cyrene_model.routes import _public_configuration_with_plugins

    class CatalogService:
        @staticmethod
        def catalog():
            return [
                {
                    "id": "user_cloud",
                    "name": "User Cloud",
                    "adapter": "openai_compatible",
                    "default_base_url": "https://user-cloud.example/v1",
                    "plugin_name": "UserCloud",
                    "pack_id": "",
                }
            ]

    raw = _configuration(api_key="")
    raw["connections"][0]["options"] = {"provider_preset": "user_cloud"}

    payload = _public_configuration_with_plugins(
        normalize_model_configuration(raw),
        service=CatalogService(),
    )

    assert sum(
        connection.get("options", {}).get("provider_preset") == "user_cloud"
        for connection in payload["connections"]
    ) == 1
    assert all(
        connection.get("_plugin_unconfigured") is not True
        for connection in payload["connections"]
    )


@pytest.mark.parametrize(
    "options",
    [{}, {"provider_preset": None}, {"provider_preset": ""}],
)
def test_selectable_models_keep_connections_without_a_provider_preset(options):
    from cyrene.plugins.builtin.cyrene_model.configuration import normalize_model_configuration
    from cyrene.plugins.builtin.cyrene_model.routes import _public_configuration_with_plugins

    class CatalogService:
        @staticmethod
        def catalog():
            return [
                {
                    "id": "openai_compatible",
                    "name": "OpenAI Compatible",
                    "adapter": "openai_compatible",
                    "plugin_name": "OpenAICompatible",
                    "pack_id": "cyrene_model",
                }
            ]

    raw = _configuration(api_key="")
    raw["connections"][0]["options"] = options

    payload = _public_configuration_with_plugins(
        normalize_model_configuration(raw),
        service=CatalogService(),
    )

    assert [model["id"] for model in payload["selectable_models"]] == [
        "qwen-next"
    ]


@pytest.mark.asyncio
async def test_embedding_profile_test_checks_the_exact_discovered_model(monkeypatch):
    from cyrene.core.plugin import Plugin, PluginRegistry
    import cyrene.plugins.model_catalog as model_catalog
    from cyrene.plugins.builtin.cyrene_model.routes import _test_model

    captured = {}

    async def provider(arguments, _context):
        captured.update(arguments)
        return {"vectors": [[1.0, 0.0]], "dimensions": 2}

    plugin = Plugin(
        name="FakeEmbeddingProvider",
        description="Test embedding Provider Plugin.",
        input_schema={"type": "object", "additionalProperties": True},
        handler=provider,
        kind="model",
    )
    registry = PluginRegistry(include_core=False)
    registry.register_plugin(plugin, source="test")
    monkeypatch.setattr(
        model_catalog,
        "resolve_model_plugin",
        lambda _provider_id, _adapter_id: (registry, plugin),
    )
    result = await _test_model(
        {
            "id": "provider",
            "adapter": "openai_compatible",
            "base_url": "https://example.test/v1",
            "options": {"provider_preset": "fake"},
        },
        {
            "id": "embedding",
            "connection_id": "provider",
            "model": "embed-b",
            "capabilities": ["embedding"],
        },
    )

    assert result == {"connected": True, "adapter": "openai_compatible", "model": "embed-b"}
    assert captured["operation"] == "embed"
    assert captured["model"] == "embed-b"


@pytest.mark.asyncio
async def test_model_discovery_dispatches_through_provider_plugin(monkeypatch):
    from cyrene.core.plugin import Plugin, PluginContext, PluginRegistry
    import cyrene.plugins.model_catalog as model_catalog
    from cyrene.plugins.builtin.cyrene_model.routes import _discover

    captured = {}

    async def discover(arguments, context: PluginContext):
        captured["arguments"] = arguments
        captured["connection"] = dict(context.services["model_connection"])
        return {
            "provider": "minimax",
            "models": [{"id": "MiniMax-M2.7", "model": "MiniMax-M2.7"}],
        }

    plugin = Plugin(
        name="FakeMiniMax",
        description="Test editable provider plugin.",
        input_schema={
            "type": "object",
            "properties": {
                "operation": {"type": "string", "const": "list_models"},
            },
            "required": ["operation"],
            "additionalProperties": False,
        },
        handler=discover,
        kind="model",
    )
    registry = PluginRegistry(include_core=False)
    registry.register_plugin(plugin, source="test")
    monkeypatch.setattr(
        model_catalog,
        "resolve_model_plugin",
        lambda provider_id, adapter_id: (registry, plugin),
    )
    connection = {
        "id": "minimax",
        "adapter": "openai",
        "base_url": "https://api.minimaxi.com/v1",
        "api_key": "sk-private",
        "options": {"provider_preset": "minimax"},
    }

    models = await _discover(connection)

    assert models == [{"id": "MiniMax-M2.7", "model": "MiniMax-M2.7"}]
    assert captured == {
        "arguments": {"operation": "list_models"},
        "connection": connection,
    }


@pytest.mark.asyncio
async def test_model_discovery_uses_adapter_fallback_when_provider_preset_is_missing(
    monkeypatch,
):
    from cyrene.core.plugin import Plugin, PluginRegistry
    import cyrene.plugins.model_catalog as model_catalog
    from cyrene.plugins.builtin.cyrene_model.routes import _discover

    captured = {}

    async def discover(_arguments, _context):
        return {"models": [{"id": "custom-model", "model": "custom-model"}]}

    plugin = Plugin(
        name="FakeOpenAICompatible",
        description="Test generic provider fallback.",
        input_schema={"type": "object", "additionalProperties": True},
        handler=discover,
        kind="model",
    )
    registry = PluginRegistry(include_core=False)
    registry.register_plugin(plugin, source="test")

    def resolve(provider_id, adapter_id):
        captured.update(provider_id=provider_id, adapter_id=adapter_id)
        return registry, plugin

    monkeypatch.setattr(model_catalog, "resolve_model_plugin", resolve)

    models = await _discover({
        "id": "custom",
        "adapter": "openai",
        "base_url": "https://custom.test/v1",
        "api_key": "",
        "options": {},
    })

    assert models == [{"id": "custom-model", "model": "custom-model"}]
    assert captured == {"provider_id": "", "adapter_id": "openai"}




def test_default_provider_connections_include_managed_local_provider(
    isolated_model_store,
):
    from cyrene.plugins.builtin.cyrene_model.configuration import (
        CONFIG_VERSION,
        get_model_configuration,
        save_model_configuration,
    )

    configured = get_model_configuration()
    providers = {item["id"]: item for item in configured["connections"]}

    assert configured["version"] == CONFIG_VERSION
    assert providers["openai"] == {
        "id": "openai",
        "name": "OpenAI",
        "adapter": "openai",
        "enabled": True,
        "use_proxy": False,
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "options": {"provider_preset": "openai"},
    }
    assert providers["anthropic"] == {
        "id": "anthropic",
        "name": "Anthropic",
        "adapter": "anthropic",
        "enabled": True,
        "use_proxy": False,
        "base_url": "https://api.anthropic.com/v1",
        "api_key": "",
        "options": {"provider_preset": "anthropic"},
    }
    assert providers["minimax"] == {
        "id": "minimax",
        "name": "MiniMax",
        "adapter": "openai",
        "enabled": True,
        "use_proxy": False,
        "base_url": "https://api.minimaxi.com/v1",
        "api_key": "",
        "options": {"provider_preset": "minimax"},
    }
    assert providers["deepseek"] == {
        "id": "deepseek",
        "name": "DeepSeek",
        "adapter": "openai",
        "enabled": True,
        "use_proxy": False,
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "options": {"provider_preset": "deepseek"},
    }
    assert providers["codex_oauth"] == {
        "id": "codex_oauth",
        "name": "OpenAI Codex OAuth",
        "adapter": "codex_oauth",
        "enabled": True,
        "use_proxy": False,
        "base_url": "codex://oauth",
        "api_key": "",
        "options": {"provider_preset": "codex_oauth"},
    }
    assert providers["local_onnx"] == {
        "id": "local_onnx",
        "name": "Local ONNX",
        "adapter": "local_onnx",
        "enabled": True,
        "use_proxy": False,
        "base_url": "",
        "api_key": "",
        "options": {"provider_preset": "local_onnx"},
    }
    assert providers["ollama"] == {
        "id": "ollama",
        "name": "Ollama",
        "adapter": "ollama",
        "enabled": True,
        "use_proxy": False,
        "base_url": "http://127.0.0.1:11434",
        "api_key": "",
        "options": {"provider_preset": "ollama"},
    }
    assert providers["kimi"] == {
        "id": "kimi",
        "name": "Kimi",
        "adapter": "openai",
        "enabled": True,
        "use_proxy": False,
        "base_url": "https://api.moonshot.cn/v1",
        "api_key": "",
        "options": {"provider_preset": "kimi"},
    }
    assert providers["glm"] == {
        "id": "glm",
        "name": "GLM",
        "adapter": "openai",
        "enabled": True,
        "use_proxy": False,
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key": "",
        "options": {"provider_preset": "glm"},
    }
    assert providers["opencode_go"] == {
        "id": "opencode_go",
        "name": "OpenCode Go",
        "adapter": "openai",
        "enabled": True,
        "use_proxy": False,
        "base_url": "https://opencode.ai/zen/go/v1",
        "api_key": "",
        "options": {"provider_preset": "opencode_go"},
    }
    assert providers["gemini"] == {
        "id": "gemini",
        "name": "Gemini",
        "adapter": "gemini",
        "enabled": True,
        "use_proxy": False,
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_key": "",
        "options": {"provider_preset": "gemini"},
    }
    assert providers["openrouter"] == {
        "id": "openrouter",
        "name": "OpenRouter",
        "adapter": "openai",
        "enabled": True,
        "use_proxy": False,
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "",
        "options": {"provider_preset": "openrouter"},
    }
    assert providers["amd_gpu_cloud"] == {
        "id": "amd_gpu_cloud",
        "name": "AMD GPU Cloud",
        "adapter": "openai",
        "enabled": True,
        "use_proxy": False,
        "base_url": "https://developer.amd.com.cn/radeon/api/v1",
        "api_key": "",
        "options": {"provider_preset": "amd_gpu_cloud"},
    }
    assert configured["profiles"] == [{
        "id": "local_onnx:qwen3-embedding-0.6b",
        "connection_id": "local_onnx",
        "model": "qwen3-embedding-0.6b",
        "name": "Qwen3 Embedding 0.6B",
        "enabled": True,
        "capabilities": ["embedding"],
        "context_limit": 0,
        "ctx": "",
        "dimensions": 1024,
        "reasoning_effort": "",
        "description": "",
        "price": "",
        "max_concurrency": 0,
        "options": {},
    }]
    assert configured["routes"] == {
        "primary": [],
        "secondary": [],
        "vision": [],
        "embedding": ["local_onnx:qwen3-embedding-0.6b"],
    }

    without_minimax = {
        **configured,
        "connections": [
            item for item in configured["connections"] if item["id"] != "minimax"
        ],
    }
    save_model_configuration(without_minimax)
    reloaded = get_model_configuration()

    assert reloaded["version"] == CONFIG_VERSION
    assert [item["id"] for item in reloaded["connections"]] == [
        item["id"]
        for item in configured["connections"]
        if item["id"] != "minimax"
    ]




def test_managed_connections_can_be_deleted_and_readded(isolated_model_store):
    from cyrene.plugins.builtin.cyrene_model.configuration import (
        get_model_configuration,
        save_model_configuration,
    )

    configured = get_model_configuration()
    removed_profile_ids = {
        profile["id"]
        for profile in configured["profiles"]
        if profile["connection_id"] in {"codex_oauth", "local_onnx"}
    }
    without_managed = {
        **configured,
        "connections": [
            connection
            for connection in configured["connections"]
            if connection["adapter"] not in {"codex_oauth", "local_onnx"}
        ],
        "profiles": [
            profile
            for profile in configured["profiles"]
            if profile["id"] not in removed_profile_ids
        ],
        "routes": {
            route: [
                profile_id
                for profile_id in profile_ids
                if profile_id not in removed_profile_ids
            ]
            for route, profile_ids in configured["routes"].items()
        },
    }
    save_model_configuration(without_managed)

    deleted = get_model_configuration()
    assert not any(
        connection["adapter"] in {"codex_oauth", "local_onnx"}
        for connection in deleted["connections"]
    )

    restored = {
        **deleted,
        "connections": deleted["connections"] + [
            {
                "id": "restored-codex",
                "name": "OpenAI Codex OAuth",
                "adapter": "codex_oauth",
            },
            {
                "id": "restored-local",
                "name": "Local ONNX",
                "adapter": "local_onnx",
            },
        ],
    }
    save_model_configuration(restored)

    reloaded = get_model_configuration()
    assert [
        connection["id"]
        for connection in reloaded["connections"]
        if connection["adapter"] in {"codex_oauth", "local_onnx"}
    ] == ["restored-codex", "restored-local"]














def test_profile_route_validation_rejects_dangling_references():
    from cyrene.plugins.builtin.cyrene_model.configuration import normalize_model_configuration

    raw = _configuration()
    raw["routes"]["primary"] = ["missing-profile"]
    with pytest.raises(ValueError, match="unknown profile"):
        normalize_model_configuration(raw)


def test_selectable_models_include_non_default_chat_profiles_only():
    from cyrene.plugins.builtin.cyrene_model.configuration import (
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




def test_deleting_connection_persists_the_canonical_graph(
    isolated_model_store,
):
    from cyrene.plugins.builtin.cyrene_model.configuration import (
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
    stored = isolated_model_store.get_setting("model_configuration")
    assert all(item["id"] != "fastllm" for item in stored["connections"])
    assert all(item["id"] != "qwen-next" for item in stored["profiles"])


def test_frontend_registers_split_pages_and_live_context_contract():
    root = Path(__file__).resolve().parents[1]
    settings = (root / "src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx").read_text()
    overlay = workbench_settings_source()
    chat = workbench_chat_source()
    i18n = workbench_i18n_source()
    styles = (root / "src/cyrene/workbench/webui/frontend/settings-model-configuration.css").read_text()

    assert 'register("model-settings"' in settings
    assert "ServicesPage: ServicesPage" in settings
    assert "UsagePage: UsagePage" in settings
    assert 'id: "setting-model-" + route + "-route"' in settings
    assert 'CustomEvent("cyrene:model-configuration-changed"' in settings
    assert 'workbenchServices.modelSettings().ServicesPage' in overlay
    assert 'workbenchServices.modelSettings().UsagePage' in overlay
    assert '"settings.modelUsage": "模型配置"' in i18n
    assert 'v.label(v.props, "settings.adapter", "Adapter")' in settings
    assert '"settings.adapter": "Adapter"' in i18n
    assert '"settings.adapter": "协议"' in i18n
    assert '"settings.modelConnectionFailed": "Model connection failed."' in i18n
    assert '"settings.modelConnectionFailed": "模型连接失败。"' in i18n
    assert '"settings.modelDiscoveryFailed": "Model discovery failed."' in i18n
    assert '"settings.modelDiscoveryFailed": "获取模型列表失败。"' in i18n
    assert "localizedModelConfigurationError(error, props)" in settings
    assert 'h("h4", { id: "wb-mcfg-profiles-heading" }, v.label(v.props, "settings.modelList", "Model list"))' in settings
    assert "档案描述一个可被多个用途引用的远端模型。" not in settings
    assert "连接配置与模型档案" not in settings
    assert 'v.selectedDescription ? h("p", null, v.selectedDescription) : null' in settings
    assert 'className: "wb-mcfg-capability-picker"' in settings
    assert 'var capabilityOptions = ["chat", "vision", "embedding"];' in settings
    assert 'capabilityLabel(capability, props)' in settings
    assert 'props.onChange("capabilities", next);' in settings
    assert 'body: JSON.stringify({ connection: connectionDraftPayload(selected), profile: profile })' in settings
    assert 'onTest: function () { v.testProfile(profile); }' in settings
    assert 'onClick: testConnection' not in settings
    assert 'function discoverConnection(options)' in settings
    assert '"/discover"' in settings
    assert 'function ModelIdCombobox(props)' in settings
    assert 'modelPluginForConnection(config, selected)' in settings
    assert "function configPayload(config)" in settings
    assert 'body: JSON.stringify(configPayload(draft))' in settings
    assert 'role: "combobox"' in settings
    assert 'aria-autocomplete": "list"' in settings
    assert '"settings.refreshModels"' in settings
    assert 'label(props, "settings.inputPrice", "Input price")' in settings
    assert 'label(props, "settings.outputPrice", "Output price")' in settings
    assert 'label(props, "settings.cachePrice", "Cache price")' in settings
    assert 'props.onChange("price", updateProfilePriceField' in settings
    assert 'label(props, "settings.modelCapabilities", "Model capabilities")' in settings
    assert 'h("button", {' in settings
    assert 'className: "wb-mcfg-profile-summary"' in settings
    assert '"aria-expanded": expanded' in settings
    assert 'h("span", { className: "wb-btn wb-mcfg-profile-details-button"' in settings
    assert 'className: "wb-workbench-searchbox wb-mcfg-searchbox"' in settings
    assert 'placeholder: v.label(v.props, "settings.searchModelServicesPlaceholder", "Search model services…")' in settings
    assert "config.connections.filter(matchesConnectionQuery)" in settings
    assert 'className: "wb-mcfg-filter"' not in settings
    assert 'return presetIcons[preset] || (isLocalConnection(connection) ? "onnx" : "")' in settings
    assert 'settingsGlyph("server", 17)' in settings
    assert ".wb-mcfg-toggle.is-on span {\n  transform: translateX(18px);\n  background: #fff;\n}" in styles
    assert 'label: "Adapter"' not in settings
    assert 'label(props, "settings.localModels", "Local models")' in settings
    assert 'var serviceLabel = local ? "Local"' in settings
    assert 'localModels.filter(function (model) { return model.ready === true; }).length' in settings
    assert "localConnectionSignature" not in settings
    assert 'useEffect(function () { refreshLocalModels(); }, []);' in settings
    assert "function selectableConnectionAdapters()" in settings
    assert 'adapterId === "codex_oauth"' in settings
    assert 'adapterId === "local_onnx"' in settings
    assert "!connections.some(isCodexConnection)" in settings
    assert "!connections.some(isLocalConnection)" in settings
    assert 'next.name === label(props, "settings.newProvider", "New provider")' in settings
    assert 'value === "local_onnx" ? "Local ONNX"' in settings
    assert 'className: "wb-model-card wb-local-model wb-mcfg-local-row"' in settings
    assert 'label(props, "settings.localModelActive"' in settings
    assert '!v.isLocalConnection(selected) ? h("section", { className: "wb-mcfg-form-section"' in settings
    assert 'hideHeader: true' in settings
    assert 'title: "Embedding model", titleKey: "settings.embeddingRouteTitle"' in settings
    assert 'label(props, "settings.selectModelProfile", "Select model profile…")' in settings
    assert 'capabilityText(profile, props)' in settings
    for key in (
        "settings.modelCapability.chat",
        "settings.modelCapability.vision",
        "settings.modelCapability.embedding",
        "settings.selectModelProfile",
        "settings.localProvider",
        "settings.embeddingRouteTitle",
    ):
        assert i18n.count(f'"{key}"') == 2
    assert 'WBC_CHAT_MODEL_CHANGED_EVENT = "cyrene:wbc-chat-model-changed"' in chat
    assert 'window.addEventListener("cyrene:model-configuration-changed"' in chat
    assert "payload.selectable_models" in chat
    assert 'setSelectedId("")' in chat
    assert "persistQueuedConfig();" in settings
    assert "store.save(snapshot, true, {" in settings
    assert "saveQueueInFlight.current" in settings
    assert '"保存配置"' not in settings
    assert "saved immediately." in settings
    assert chat.index("var activeModel = String(runtime") < chat.index(
        "var liveModel = String(liveData"
    )
    assert "segTotal <= 0 && used <= 0 && limit <= 0" in chat


def test_codex_oauth_service_exposes_cli_download_and_progress():
    root = Path(__file__).resolve().parents[1]
    settings = (
        root / "src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx"
    ).read_text()
    styles = (
        root / "src/cyrene/workbench/webui/frontend/settings-model-configuration.css"
    ).read_text()
    i18n = workbench_i18n_source()

    oauth_section = settings.split("function OAuthSection(props) {", 1)[1].split(
        "function LocalModelsSection(props) {", 1
    )[0]
    services_page = settings.split("function ServicesPage(props) {", 1)[1].split(
        "function UsagePage(props) {", 1
    )[0]

    assert 'className: "wb-mcfg-cli-runtime"' in oauth_section
    assert 'role: "status", "aria-live": "polite"' in oauth_section
    assert 'h("progress", { max: 100' in oauth_section
    assert "props.onDownloadCli(!!cli.broken)" in oauth_section
    assert 'label(props, "settings.codexCliDownload"' in oauth_section
    assert 'label(props, "settings.codexCliRedownload"' in oauth_section
    assert 'requestJson("/api/settings/openai-oauth/cli")' in services_page
    assert (
        'requestJson("/api/settings/openai-oauth/cli/download", init)'
        in services_page
    )
    assert "JSON.stringify({ force: true })" in services_page
    assert "function startOauthCliPolling()" in services_page
    assert "onDownloadCli: v.downloadOauthCli" in settings
    assert "downloadOauthCli: downloadOauthCli" in services_page
    assert ".wb-mcfg-cli-runtime {" in styles
    assert ".wb-mcfg-cli-progress progress {" in styles
    for key in (
        "settings.codexCliRuntime",
        "settings.codexCliDownload",
        "settings.codexCliDownloading",
        "settings.codexCliRedownload",
        "settings.codexCliDownloadTimeout",
    ):
        assert i18n.count(f'"{key}"') == 2


def test_settings_and_provider_icons_are_inlined_before_first_render():
    root = Path(__file__).resolve().parents[1]
    build = (root / "src/cyrene/workbench/webui/build-jsx.mjs").read_text()
    index = (root / "src/cyrene/workbench/webui/frontend/index.html").read_text()
    overlay = workbench_settings_source()
    settings = (root / "src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx").read_text()

    assert "<!-- CYRENE_ICON_ASSETS -->" in index
    assert "function inlineIconAssets(" in build
    assert "function svgMarkup(" in build
    assert "inlineIconAssets(settingsIconFiles, PROVIDER_ICON_FILES, EXTENSION_ICON_FILES)" in build
    assert "window.CyreneIconAssets" in overlay
    assert "settingsIconMarkup(item.icon)" in overlay
    assert 'className: "settings-overlay-tab-glyph is-inline"' in overlay
    assert "window.CyreneIconAssets" in settings
    assert 'iconMarkup("providers", name)' in settings
    assert 'className: "wb-mcfg-provider-logo is-inline is-" + name' in settings


def test_model_service_credentials_are_agent_write_only_and_r3():
    root = Path(__file__).resolve().parents[1]
    settings = (root / "src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx").read_text()
    surface = (root / "src/cyrene/workbench/webui/frontend/platform/ui-surface.jsx").read_text()

    assert '"data-cyrene-agent-secret-input": "true"' in settings
    assert '"data-cyrene-risk": "R3"' in settings
    assert '"aria-label": v.label(v.props, "settings.apiKeyWriteOnly", "API key (write only)")' in settings
    assert "if (modelPanel) return;" not in surface
    assert 'action_id: "set_secret", kind: "set_value", risk: risk' in surface
    assert 'input_schema: { secret_value: "text<=4000" }' in surface
    assert 'String(input.secret_value || "")' in surface
    assert 'element.classList.contains("is-danger")' in surface
    assert '"aria-label": v.label(v.props, "settings.connectionName", "Connection name")' in settings
    assert 'label(props, "settings.modelId", "Model ID")' in settings
    assert '"aria-label": v.label(v.props, "settings.modelServiceApiEndpoint", "Model service API endpoint")' in settings


def test_model_service_api_row_has_per_connection_proxy_switch():
    root = Path(__file__).resolve().parents[1]
    settings = (root / "src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx").read_text()
    styles = (root / "src/cyrene/workbench/webui/frontend/settings-model-configuration.css").read_text()

    assert 'className: "wb-mcfg-api-proxy-row"' in settings
    assert 'checked: selected.use_proxy === true' in settings
    assert 'v.updateConnection("use_proxy", value)' in settings
    assert 'payload.external_agent_proxy_enabled === true' in settings
    assert ".wb-mcfg-api-proxy-row" in styles
    assert "grid-template-columns: minmax(0, 1fr) auto" in styles


def test_services_autosave_is_single_flight_retryable_and_current_only():
    root = Path(__file__).resolve().parents[1]
    settings = (root / "src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx").read_text()
    styles = (root / "src/cyrene/workbench/webui/frontend/settings-model-configuration.css").read_text()

    hook = settings.split("function useModelConfiguration(props) {", 1)[1].split(
        "function LoadingState(props) {", 1
    )[0]
    services = settings.split("function ServicesPage(props) {", 1)[1].split(
        "var ROUTE_META =", 1
    )[0]

    assert "var queuedSnapshot = useRef(null);" in services
    assert "var queuedVersion = useRef(0);" in services
    assert "var saveQueueInFlight = useRef(false);" in services
    assert "if (!saveQueueMounted.current || saveQueueInFlight.current" in services
    assert services.count("store.save(snapshot, true, {") == 1
    assert services.count("store.setConfig(snapshot);") == 1
    assert "queuedSnapshot.current = Object.assign({}, queuedSnapshot.current || {}," in services
    assert "scheduleQueuedSave(0);" in services

    assert "updateConfig(nextConfig, { immediate: true });" in services
    assert "persistConfig(" not in services
    assert "saveQueueBlockedVersion.current = failedVersion;" in services
    assert "saveQueueBlockedVersion.current = -1;" in services
    assert "function retryQueuedSave()" in services
    retry = services.split("function retryQueuedSave()", 1)[1].split(
        "function updateConnection", 1
    )[0]
    assert "saveQueueBlockedVersion.current = -1;" in retry
    assert "editVersion.current" not in retry
    assert '"立即保存"' not in services
    assert 'onClick: retryQueuedSave }, label(props, "settings.retrySave", "Retry save")' in services
    assert 'className: "wb-mcfg-status is-error wb-mcfg-save-error"' in services
    assert ".wb-mcfg-save-error" in styles

    conflict_reload = services.split("store.load({", 1)[1].split(
        ").catch(function (reloadError)", 1
    )[0]
    assert ").then(function (reloaded)" in conflict_reload
    assert "setQueueDirty(false);" in conflict_reload
    assert "setQueueDirty(false);" not in services.split("store.load({", 1)[0].split(
        "function handleQueuedSaveFailure(error) {", 1
    )[1]
    reload_failure = services.split(").catch(function (reloadError)", 1)[1].split(
        "function persistQueuedConfig()", 1
    )[0]
    assert "setQueueDirty(true);" in reload_failure

    current_completion = hook.split("if (isCurrent) {", 1)[1].split(
        "return saved;", 1
    )[0]
    assert "setConfig(saved);" in current_completion
    assert "props.onConfigChange(saved)" in current_completion
    assert 'CustomEvent("cyrene:model-configuration-changed"' in current_completion
    assert "return function () { mounted.current = false; };" in hook
    assert "saveQueueMounted.current = false;" in services
    assert "clearTimeout(saveQueueTimer.current)" in services


def test_new_model_profile_stays_local_until_explicitly_committed():
    root = Path(__file__).resolve().parents[1]
    settings = (
        root
        / "src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx"
    ).read_text()
    services = settings.split("function ServicesPage(props) {", 1)[1].split(
        "var ROUTE_META =", 1
    )[0]

    add_profile = services.split("function addProfile(raw)", 1)[1].split(
        "function updateProfileDraft", 1
    )[0]
    assert "setProfileDrafts" in add_profile
    assert "updateConfig(" not in add_profile

    commit_profile = services.split("function commitProfileDraft()", 1)[1].split(
        "function updateProfile", 1
    )[0]
    assert 'if (!draft || !String(draft.model || "").trim()) return;' in commit_profile
    assert "profiles: config.profiles.concat([committed])" in commit_profile
    assert "{ immediate: true }" in commit_profile

    assert "disabled: !!v.profileDraft" in settings
    assert "draft: isDraft" in settings
    assert 'label(props, "common.save", "Save")' in settings
    assert 'label(props, "common.cancel", "Cancel")' in settings
