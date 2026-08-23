from pathlib import Path
import re
import sys
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import (
    workbench_i18n_source,
    workbench_settings_source,
    workbench_shell_source,
    workbench_style_source,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from route.registry import register_routes


@pytest.fixture
def integration_store(monkeypatch):
    from cyrene.runtime import integration_settings

    settings = {
        "zotero": {
            "base_url": "http://127.0.0.1:23119/api",
            "auto_sync": False,
            "copy_attachments": True,
        },
        "embedding": {
            "provider": "openai_compatible",
            "base_url": "https://embeddings.example/v1",
            "api_key": "stored-secret",
            "model": "embed-small",
            "dimensions": 768,
        },
    }
    env = {}

    monkeypatch.setattr(
        integration_settings.config_store,
        "get_setting",
        lambda key, default=None: settings.get(key, default),
    )
    monkeypatch.setattr(
        integration_settings.config_store,
        "set_setting",
        lambda key, value: settings.__setitem__(key, value),
    )
    monkeypatch.setattr(
        integration_settings.config_store,
        "get_env",
        lambda key, default="": env.get(key, default),
    )
    monkeypatch.setattr(
        integration_settings.config_store,
        "set_env_many",
        lambda values: env.update(values),
    )
    return integration_settings, settings, env


def test_public_integration_settings_never_return_embedding_secret(integration_store):
    integration_settings, _, _ = integration_store

    payload = integration_settings.public_settings()

    assert payload["embedding"]["api_key_configured"] is True
    assert "api_key" not in payload["embedding"]
    assert "stored-secret" not in str(payload)


def test_update_embedding_keeps_blank_secret_and_syncs_legacy_slots(integration_store):
    integration_settings, settings, env = integration_store

    payload = integration_settings.update_settings({
        "embedding": {
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434",
            "api_key": "",
            "model": "nomic-embed-text",
            "dimensions": 0,
        }
    })

    assert settings["embedding"]["api_key"] == "stored-secret"
    assert env["EMBEDDING_API_KEY"] == "stored-secret"
    assert payload["embedding"]["api_key_configured"] is True
    assert "api_key" not in payload["embedding"]


def test_zotero_local_api_rejects_non_loopback_urls():
    from cyrene.runtime.integration_settings import normalize_zotero

    with pytest.raises(ValueError, match="localhost:23119"):
        normalize_zotero({"base_url": "https://example.com/api"})

    with pytest.raises(ValueError, match="localhost:23119"):
        normalize_zotero({"base_url": "http://127.0.0.1:9999/api"})


def test_embedding_runtime_reads_persisted_settings_without_api_key(monkeypatch):
    from cyrene.knowledge import embeddings

    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.setattr(embeddings, "_persisted", lambda: {
        "provider": "ollama",
        "base_url": "http://127.0.0.1:11434",
        "api_key": "",
        "model": "nomic-embed-text",
        "dimensions": 0,
    })

    assert embeddings.is_configured() is True
    assert embeddings._base_url() == "http://127.0.0.1:11434"
    assert embeddings._model() == "nomic-embed-text"


def test_embedding_probe_requires_endpoint_and_model(integration_store):
    integration_settings, settings, _ = integration_store
    settings["embedding"].update({"base_url": "", "model": "", "api_key": ""})

    with pytest.raises(ValueError, match="base URL and model"):
        integration_settings.merged_test_config("embedding", {})


def test_local_qwen_embedding_does_not_require_endpoint(integration_store):
    integration_settings, settings, _ = integration_store
    settings["embedding"] = {
        "provider": "local_onnx",
        "base_url": "",
        "api_key": "",
        "model": "qwen3-embedding-0.6b",
        "dimensions": 1024,
    }

    config = integration_settings.merged_test_config("embedding", {})

    assert config["provider"] == "local_onnx"
    assert config["base_url"] == ""
    assert config["dimensions"] == 1024


def test_missing_local_qwen_falls_back_to_keyword_retrieval(monkeypatch):
    from cyrene.knowledge import embeddings, local_models

    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.setattr(embeddings, "_persisted", lambda: {
        "provider": "local_onnx",
        "base_url": "",
        "api_key": "",
        "model": "qwen3-embedding-0.6b",
        "dimensions": 1024,
    })
    monkeypatch.setattr(local_models, "is_ready", lambda _model_id: False)

    assert embeddings.is_configured() is False

    monkeypatch.setattr(local_models, "is_ready", lambda _model_id: True)
    assert embeddings.is_configured() is True


@pytest.mark.asyncio
async def test_local_embedding_probe_reports_keyword_fallback_when_pack_is_missing(monkeypatch):
    from cyrene.knowledge import local_models
    from cyrene.runtime import integration_settings

    monkeypatch.setattr(local_models, "is_ready", lambda _model_id: False)

    result = await integration_settings.test_embedding({
        "provider": "local_onnx",
        "model": "qwen3-embedding-0.6b",
        "dimensions": 1024,
    })

    assert result["ok"] is True
    assert result["fallback"] == "keyword"
    assert result["dimensions"] == 0


@pytest.mark.asyncio
async def test_embedding_transport_normalizes_vectors(monkeypatch):
    from cyrene.knowledge import embedding_client

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"index": 0, "embedding": [3.0, 4.0]}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(embedding_client.httpx, "AsyncClient", lambda: FakeClient())
    vectors = await embedding_client.embed_texts_with_config(["hello"], {
        "provider": "openai_compatible", "base_url": "https://example.test/v1",
        "model": "embed", "dimensions": 0,
    })

    assert vectors[0] == pytest.approx([0.6, 0.8])


@pytest.mark.asyncio
async def test_ollama_embedding_request_and_response(monkeypatch):
    from cyrene.knowledge import embeddings
    from cyrene.knowledge import embedding_client

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"embeddings": [[0.25, 0.75]]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, endpoint, **kwargs):
            captured.update({"endpoint": endpoint, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(
        embedding_client.httpx,
        "AsyncClient",
        lambda: FakeClient(),
    )

    result = await embeddings.embed_texts_with_config(["hello"], {
        "provider": "ollama",
        "base_url": "http://127.0.0.1:11434",
        "api_key": "",
        "model": "nomic-embed-text",
        "dimensions": 2,
    })

    assert result[0] == pytest.approx([0.316227766, 0.948683298])
    assert captured["endpoint"] == "http://127.0.0.1:11434/api/embed"
    assert captured["json"]["dimensions"] == 2
    assert "Authorization" not in captured["headers"]


def test_integration_settings_routes_hide_secrets_and_probe_drafts(monkeypatch, integration_store):
    integration_settings, _, _ = integration_store
    monkeypatch.setattr(
        integration_settings,
        "test_embedding",
        AsyncMock(return_value={
            "ok": True,
            "service": "embedding",
            "model": "draft-model",
            "dimensions": 384,
        }),
    )

    app = FastAPI()
    register_routes(app, bot=None, db_path="test.db")
    client = TestClient(app)

    get_response = client.get("/api/settings/integrations")
    assert get_response.status_code == 200
    assert "stored-secret" not in get_response.text
    assert "api_key" not in get_response.json()["embedding"]

    test_response = client.post("/api/settings/integrations/test", json={
        "service": "embedding",
        "config": {
            "provider": "openai_compatible",
            "base_url": "https://draft.example/v1",
            "api_key": "one-use-secret",
            "model": "draft-model",
            "dimensions": 384,
        },
    })
    assert test_response.status_code == 200
    assert test_response.json()["dimensions"] == 384
    assert "one-use-secret" not in test_response.text

    called_config = integration_settings.test_embedding.await_args.args[0]
    assert called_config["api_key"] == "one-use-secret"

    integration_settings.test_embedding.side_effect = RuntimeError(
        "MLX inference dependencies are unavailable"
    )
    local_response = client.post("/api/settings/integrations/test", json={
        "service": "embedding",
        "config": {
            "provider": "local_onnx",
            "model": "qwen3-embedding-0.6b",
            "dimensions": 1024,
        },
    })
    assert local_response.status_code == 502
    assert local_response.json()["error"] == (
        "local embedding test failed: MLX inference dependencies are unavailable"
    )


def test_budget_stats_exposes_daily_usage_for_peak_metrics(monkeypatch, tmp_path):
    from cyrene.runtime import database, settings_store

    usage_stats = AsyncMock(return_value={
        "by_model": [],
        "by_day": [
            {"day": "2026-08-18", "requests": 7, "total_tokens": 1200, "cost": 0.75},
            {"day": "2026-08-19", "requests": 11, "total_tokens": 3400, "cost": 1.25},
        ],
        "total": {
            "requests": 18,
            "max_total_tokens": 2400,
            "max_cost": 1.75,
        },
    })
    monkeypatch.setattr(database, "get_token_usage_stats", usage_stats)
    monkeypatch.setattr(settings_store, "get_all", lambda: {
        "budget_currency": "CNY",
        "budget_start_day": 19,
    })

    app = FastAPI()
    register_routes(app, bot=None, db_path=str(tmp_path / "usage.db"))

    response = TestClient(app).get("/api/settings/budget/stats")

    assert response.status_code == 200
    assert response.json()["by_day"] == [
        {"day": "2026-08-18", "requests": 7, "total_tokens": 1200, "cost": 0.75},
        {"day": "2026-08-19", "requests": 11, "total_tokens": 3400, "cost": 1.25},
    ]
    assert response.json()["max_request_tokens"] == 2400
    assert response.json()["max_request_cost"] == pytest.approx(1.75)
    month_start = usage_stats.await_args.kwargs["since"]
    assert month_start.day == 1
    assert month_start.hour == 0
    assert month_start.minute == 0


def test_settings_ui_moves_zotero_to_integrations_and_keeps_embedding_in_models():
    root = Path(__file__).resolve().parent.parent
    source = workbench_settings_source()
    styles = workbench_style_source()
    translations = workbench_i18n_source()

    assert 'settingsFetch("/api/settings/integrations")' in source
    assert 'settingsFetch("/api/settings/integrations/test"' in source
    assert 'value: "openai_compatible"' in source
    assert 'value: "ollama"' in source
    assert 'value: "local_onnx"' in source
    assert 'qwen3-embedding-0.6b' in source
    assert 'type: "password"' in source
    assert 'settings.zoteroCopyAttachments' in source
    assert 'function importFromZotero()' in source
    assert '"/api/workbench/library/zotero/sync?workspace="' in source
    assert 'disabled: !!integrationBusy || !(p.project && p.project.id)' in source
    general_panel = source.split("function GeneralPanel(p) {", 1)[1].split("// ── Models Panel ──", 1)[0]
    models_panel = source.split("// ── Models Panel ──", 1)[1].split("// ── Channels Panel ──", 1)[0]
    assert 'settings.zoteroIntegration' in general_panel
    assert 'p.integrationsOnly && React.cloneElement(SectionBlock(t("settings.zoteroIntegration")' in general_panel
    assert 'tab === "integrations" && React.createElement(GeneralPanel, { integrationsOnly: true' in source
    assert 'settings.embeddingIntegration' not in general_panel
    assert 'function EmbeddingSettingsSection(p)' in models_panel
    assert 'React.createElement(EmbeddingSettingsSection, {' in models_panel
    assert 'settings.localModels' in models_panel
    assert 'settings.localModelOptional' in models_panel
    assert 'className: "wb-local-model-icon is-" + kind' in models_panel
    for kind in ("embedding", "ocr", "asr", "tts"):
        assert f".wb-local-model-icon.is-{kind}" in styles
    assert "var(--wb-local-model-icon-color)" in styles
    assert '!coverage.configured' in models_panel
    assert 'saveAllModels' in models_panel
    assert 'settings.reembedPromptTitle' in models_panel
    assert 'coverage.pending_vectors' in models_panel
    assert '"/api/workbench/library/reembed?workspace="' in models_panel
    embedding_section = models_panel.split("function EmbeddingSettingsSection(p) {", 1)[1].split("function modelCredentialFields", 1)[0]
    assert 'onClick: save' not in embedding_section
    assert translations.count('"settings.embeddingIntegration"') == 2
    assert translations.count('"settings.reembedPromptTitle"') == 2
    assert translations.count('"settings.zoteroIntegration"') == 2
    assert translations.count('"settings.zoteroImportAction"') == 2

    library = (root / "src/webui/frontend/workbench-library.jsx").read_text(encoding="utf-8")
    assert '"/api/workbench/library/embedding/status?workspace="' in library
    assert '"/api/workbench/library/reembed?workspace="' in library
    assert 'L("library.vectorizeAll", "Vectorize all")' in library
    assert translations.count('"library.vectorizeAll"') == 2


def test_profile_is_a_settings_item_without_a_collapsed_settings_icon_stack():
    root = Path(__file__).resolve().parent.parent
    settings = workbench_settings_source()
    workbench = workbench_shell_source()
    styles = workbench_style_source()

    assert '{ id: "profile", labelKey: "rail.profile", icon: "user" }' in settings
    assert 'ids: ["profile", "general", "search", "appearance", "shortcuts"]' in settings
    assert 'ids: ["model-usage", "models", "agents", "voice", "tools"]' in settings
    icon_names = re.findall(r'\{ id: "[^"]+", labelKey: "[^"]+", icon: "([^"]+)" \}', settings)
    assert len(icon_names) == 20
    assert len(set(icon_names)) == len(icon_names)
    assert 'className: "settings-overlay-tab-glyph"' in settings
    build_source = (root / "src/webui/build-jsx.mjs").read_text(encoding="utf-8")
    assert '@tabler/icons/icons/outline' in build_source
    assert "'code.svg'," in build_source
    assert (root / "src/webui/static/app/settings-icons/code.svg").is_file()
    assert 'tab === "profile" && React.createElement("div", { className: "settings-profile-panel" }' in settings
    assert 'if (page === "profile") {' in workbench
    assert 'setSettingsTab("profile")' in workbench
    collapsed_rule = styles.split(
        ".settings-overlay .settings-overlay-nav.is-collapsed .settings-overlay-nav-scroll {",
        1,
    )[1].split("}", 1)[0]
    assert "display: none" in collapsed_rule

    translations = workbench_i18n_source()
    assert '"rail.profile": "个人信息"' in translations
    assert '"profile.basicInfo": "个人信息"' in translations
    for label in (
        "通用设置", "界面外观", "键盘快捷键", "模型配置", "智能代理",
        "语音交互", "工具管理", "消息渠道", "远程连接", "扩展与系统", "扩展中心", "自定义工具",
        "服务集成", "预算管理", "用量统计", "数据管理", "关于 Cyrene",
    ):
        assert f'": "{label}"' in translations


def test_usage_settings_reuses_profile_metrics_and_expands_model_breakdown():
    settings = workbench_settings_source()
    translations = workbench_i18n_source()
    styles = workbench_style_source()

    panel = settings.split("function BudgetPanel(p) {", 1)[1].split("// ── Shared UI helpers", 1)[0]
    assert 'var profileUsage = dashboard.usage || {};' in panel
    assert 'profileUsage.spend_cny' in panel
    assert 'profileUsage.requests' in panel
    assert 'profileUsage.total_tokens' in panel
    assert 'profileUsage.prompt_tokens' in panel
    assert 'profileUsage.completion_tokens' in panel
    assert 't("settings.profileUsageSnapshot")' in panel
    assert 't("settings.usageBillingPeriod")' in panel
    assert 't("settings.usageByModel")' in panel
    assert '"settings.usageBillingPeriod": "本月统计"' in translations
    assert '"settings.usageBillingPeriodHint": "统计本自然月的用量与费用。"' in translations
    assert "从设置的计费起始日开始统计" not in translations
    assert "当前计费周期" not in translations
    usage_overview = panel.split('// ── Overview section ──', 1)[1].split('// ── Budget configuration ──', 1)[0]
    budget_controls = panel.split('// ── Budget configuration ──', 1)[1].split('// ── Cost by model ──', 1)[0]
    assert 't("settings.budgetLimit")' not in usage_overview
    assert 'className: "wb-budget-progress-wrap"' not in usage_overview
    assert 't("settings.usagePeakUsage")' in usage_overview
    assert 't("settings.usagePeakCalls")' in usage_overview
    assert 't("settings.usageAverageTokens")' in usage_overview
    assert 't("settings.usageMaxRequestTokens"' in usage_overview
    assert 't("settings.usageMaxRequestCost"' in usage_overview
    assert 't("settings.usageOutputTokens")' not in usage_overview
    assert 'formatPeakDate(peakUsageDay)' in usage_overview
    assert 'formatPeakDate(peakCallsDay)' in usage_overview
    assert 'React.createElement(UsageTrendChart, { t: t, items: budgetDaily, currencySymbol: currencySymbol })' in usage_overview
    assert 'window.echarts.init(node)' in settings
    assert 'var style = getComputedStyle(node)' in settings
    assert 'getComputedStyle(document.documentElement)' not in settings
    assert 'style.getPropertyValue("--wb-chart-token")' in settings
    assert 'style.getPropertyValue("--wb-chart-request")' in settings
    assert 'style.getPropertyValue("--wb-chart-cost")' in settings
    assert 'backgroundColor: tooltipBackground' in settings
    assert 'yAxisIndex: 0' in settings
    assert 'yAxisIndex: 1' in settings
    assert 'yAxisIndex: 2' in settings
    assert 'type: "dashed"' in settings
    assert 'type: "dotted"' in settings
    assert 'grid: { left: 58, right: 112, top: 44, bottom: 32 }' in settings
    assert 'combinedYAxis("right", 56, compactCostAxisValue' in settings
    assert 'mode === "budget" && SectionBlock(t("settings.budgetOverview")' in budget_controls
    assert 't("settings.budgetLimit")' in budget_controls
    assert 't("settings.usageBudgetRate")' in budget_controls
    assert 'className: "wb-budget-progress-wrap"' in budget_controls
    assert 't("settings.budgetDisabledHint")' in budget_controls
    assert 'formatTokens(item.prompt_tokens)' in panel
    assert 'formatTokens(item.completion_tokens)' in panel
    assert 't("settings.usageNoModelData")' in panel
    assert 'mode === "usage" ? " wb-usage-settings" : ""' in panel
    assert 'React.createElement("article", { className: "wb-budget-model-card"' in panel
    assert 'React.createElement("dl", { className: "wb-budget-model-stats" }' in panel
    assert 'className: "wb-budget-model-head"' not in panel
    assert 'settingsFetch("/api/settings/model-config/provider-usage"' in panel
    assert 't("settings.providerUsage")' in panel
    assert '"settings.providerUsageHint": "显示已配置服务商的实时账户余额与额度"' in translations
    assert 'className: "wb-provider-usage-grid"' in panel
    assert 'item.kind === "balance"' in panel
    assert 'item.kind === "quota"' in panel
    assert 'kind: "codex_quota"' in panel
    assert 'if (codexQuota.connected === true) providerUsageItems.push(codexUsageItem)' in panel
    assert 'var [codexQuota, setCodexQuota] = useStateSt({ connected: false, limits: {} })' in panel
    assert 't("settings.providerUsageStatusUnknown")' in panel
    assert 'Toggle(codexQuotaEnabled, toggleCodexQuota)' not in panel
    assert 'String(windowData.model || "").trim().toLowerCase() !== "video"' in panel
    assert 'visibleWindows.map' in panel
    assert '!visibleWindows.length' in panel
    assert 'cyrene-provider-usage-v1' not in panel
    assert 'localStorage.setItem(PROVIDER_USAGE_CACHE_KEY' not in panel
    assert 'providerUsageItems.length > 0 && React.createElement("div", { className: "wb-provider-usage-grid" }' in panel
    assert '!providerUsageLoading && !providerUsageItems.length' in panel
    assert 'item.refreshing === true' in panel
    assert 'fetchProviderUsage(false, true)' in panel
    assert 'className: "wb-provider-usage-column is-compact"' in panel
    assert 'className: "wb-provider-usage-column is-minimax"' in panel
    assert 'onClick: function () { fetchProviderUsage(true' not in panel

    for key in (
        "settings.usageSubtitle",
        "settings.profileUsageSnapshot",
        "settings.usageBillingPeriod",
        "settings.usageInputTokens",
        "settings.usageOutputTokens",
        "settings.usageAverageTokens",
        "settings.usageMaxRequestTokens",
        "settings.usageAverageCost",
        "settings.usageMaxRequestCost",
        "settings.usageBudgetRate",
        "settings.usagePeakUsage",
        "settings.usagePeakCalls",
        "settings.usagePeakDate",
        "settings.usageTrendTitle",
        "settings.usageTrendHint",
        "settings.usageTrendTokens",
        "settings.usageTrendRequests",
        "settings.usageTrendCost",
        "settings.usageTrendEmpty",
        "settings.usageByModel",
        "settings.usageNoModelData",
        "settings.providerUsage",
        "settings.providerUsageRemaining",
        "settings.providerUsageBalanceBreakdown",
    ):
        assert translations.count(f'"{key}"') == 2

    assert ".wb-usage-metrics" in styles
    assert ".wb-usage-metric" in styles
    assert ".wb-usage-trend-canvas" in styles
    assert '--wb-chart-token: #72a7ff' in styles
    assert '--wb-chart-request: #f4bd50' in styles
    assert '--wb-chart-cost: #e77bd5' in styles
    assert '--wb-chart-grid: rgba(194, 195, 202, 0.16)' in styles
    provider_grid_rule = styles.split(".wb-provider-usage-grid {", 1)[1].split("}", 1)[0]
    provider_card_rule = styles.split(".wb-provider-usage-column > .wb-provider-usage-card {", 1)[1].split("}", 1)[0]
    assert "align-items: start" in provider_grid_rule
    assert "align-items: stretch" not in provider_grid_rule
    assert "flex: 0 0 auto" in provider_card_rule
    assert "min-height: 72px" in styles
    assert ".settings-overlay .wb-usage-settings > .wb-section-block" in styles
    assert ".settings-overlay .wb-usage-settings .wb-budget-summary" in styles
    assert ".wb-budget-model-grid" in styles
    assert ".wb-budget-model-card" in styles
    assert ".wb-budget-model-stats" in styles
    assert ".wb-provider-usage-grid" in styles
    assert ".wb-provider-usage-card" in styles
    assert ".wb-provider-quota-window" in styles


def test_about_settings_matches_the_shared_settings_page_hierarchy():
    settings = workbench_settings_source()
    translations = workbench_i18n_source()
    styles = workbench_style_source()

    panel = settings.split("function AboutPanel(p) {", 1)[1].split("// ── Skills Panel", 1)[0]
    assert 'className: "settings-panel wb-about-settings"' in panel
    assert 'SectionTitle(t("settings.about"), t("settings.aboutSubtitle"))' in panel
    assert 'className: "wb-about-product-card"' in panel
    assert panel.index('className: "wb-about-product-card"') < panel.index('className: "wb-btn primary wb-about-check-btn"')
    assert 'className: "wb-about-hero-progress"' in panel
    assert '"--wb-about-download-progress": heroProgress + "%"' in panel
    assert "var heroProgress = downloaded\n    ? 100" in panel
    assert 'className: "wb-about-update-footer"' not in panel
    assert 'className: "wb-about-card-head"' in panel
    assert 'className: "wb-about-related-card"' in panel
    assert 't("settings.relatedLinksHint"' in panel
    assert 'className: "wb-about-related-list"' in panel
    assert 'className: "wb-about-related-row"' in panel
    assert 'className: "wb-changelog-modal"' in panel
    assert 'settingsFetch("/api/logs/export"' in panel

    assert translations.count('"settings.aboutSubtitle"') == 2
    assert translations.count('"settings.relatedLinksHint"') == 2
    update_rule = styles.split(".wb-about-update-card {", 1)[1].split("}", 1)[0]
    assert "padding: 4px 0 0" in update_rule
    assert "border: 0" in update_rule
    assert "background: transparent" in update_rule
    hero_rule = styles.split(".wb-about-hero-progress {", 1)[1].split("}", 1)[0]
    assert "width: var(--wb-about-download-progress)" in hero_rule
    assert "background: color-mix(in srgb, var(--wb-blue) 15%, var(--wb-card-bg))" in hero_rule
    assert ".wb-about-related-list" in styles
    assert ".wb-about-related-row" in styles
    assert ".wb-about-update-footer" not in styles


def test_general_settings_has_opt_in_external_agent_proxy():
    source = workbench_settings_source()
    i18n = workbench_i18n_source()
    general_panel = source.split("function GeneralPanel(p) {", 1)[1].split("// ── Models Panel ──", 1)[0]

    assert 't("settings.agentProxyEnabled")' in general_panel
    assert 'external_agent_proxy_enabled: !!nextEnabled' in general_panel
    assert 'external_agent_proxy_port: port' in general_panel
    assert 'disabled: !agentProxyEnabled' in general_panel
    assert i18n.count('"settings.agentProxyEnabled"') == 2
    assert i18n.count('"settings.agentProxyPort"') == 2
def test_performance_mode_is_an_appearance_runtime_setting():
    from cyrene.runtime.settings_service import SETTING_SPECS
    from cyrene.workbench.runtime import _build_config

    spec = next(item for item in SETTING_SPECS if item.key == "performance_mode")
    assert spec.namespace == "runtime"
    assert spec.tab == "appearance"
    assert spec.value_type == "boolean"
    assert spec.default is False
    assert "performance_mode" in _build_config()


def test_agents_settings_control_background_skill_learning():
    from cyrene.runtime.settings_service import SETTING_SPECS
    from cyrene.workbench.runtime import _build_config

    source = workbench_settings_source()
    i18n = workbench_i18n_source()
    agents_panel = source.split("function AgentsPanel(p) {", 1)[1].split(
        "// ── Appearance Panel ──", 1
    )[0]
    spec = next(item for item in SETTING_SPECS if item.key == "background_skill_learning")

    assert spec.namespace == "runtime"
    assert spec.tab == "agents"
    assert spec.value_type == "boolean"
    assert spec.default is True
    assert "background_skill_learning" in _build_config()
    assert 't("settings.backgroundSkillLearning")' in agents_panel
    assert "background_skill_learning: config.background_skill_learning !== false" in source
    assert i18n.count('"settings.backgroundSkillLearning"') == 2
