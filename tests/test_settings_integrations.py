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

from cyrene.workbench.http.registry import register_routes


def test_zotero_local_api_rejects_non_loopback_urls():
    from cyrene.plugins.builtin.cyrene_knowledge.zotero_settings import (
        normalize_zotero,
    )

    with pytest.raises(ValueError, match="localhost:23119"):
        normalize_zotero({"base_url": "https://example.com/api"})

    with pytest.raises(ValueError, match="localhost:23119"):
        normalize_zotero({"base_url": "http://127.0.0.1:9999/api"})


def test_budget_stats_exposes_daily_usage_for_peak_metrics(monkeypatch, tmp_path):
    from cyrene.platform import database, settings_store

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
    model_source = (
        root / "src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx"
    ).read_text(encoding="utf-8")
    styles = workbench_style_source()
    translations = workbench_i18n_source()

    assert 'settingsFetch("/api/settings/integrations", { signal:' in source
    assert 'settingsFetch("/api/settings/integrations/test"' in source
    assert 'requestJson("/api/settings/model-config")' in model_source
    assert 'function configPayload(config)' in model_source
    assert 'method: "PATCH"' in model_source
    assert 'modelConfigurationPatchOperations(' in model_source
    assert 'embedding: route("embedding")' in model_source
    assert 'var capabilityOptions = ["chat", "vision", "embedding"]' in model_source
    assert 'qwen3-embedding-0.6b' in model_source
    assert 'type: "password"' in model_source
    assert 'settings.zoteroCopyAttachments' in source
    assert 'function importFromZotero()' in source
    assert '"/api/workbench/library/zotero/sync?workspace="' in source
    assert 'disabled: !!integrationBusy || !(p.project && p.project.id)' in source
    general_panel = (
        root / "src/cyrene/workbench/webui/frontend/features/settings/general.jsx"
    ).read_text(encoding="utf-8")
    assert 'settings.zoteroIntegration' in general_panel
    assert 'p.integrationsOnly && hasKnowledge && React.cloneElement(SectionBlock' in general_panel
    assert 'tab === "integrations" && React.createElement(GeneralPanel, { integrationsOnly: true' in source
    assert 'settings.embeddingIntegration' not in general_panel
    assert 'settings.localModels' in model_source
    assert 'settings.localModelOptional' in model_source
    assert 'className: "wb-local-model-icon is-" + kind' in model_source
    for kind in ("embedding", "ocr", "asr", "tts"):
        assert f".wb-local-model-icon.is-{kind}" in styles
    assert "var(--wb-local-model-icon-color)" in styles
    assert 'title: "Embedding model", titleKey: "settings.embeddingRouteTitle"' in model_source
    assert 'capability: "embedding"' in model_source
    assert translations.count('"settings.zoteroIntegration"') == 2
    assert translations.count('"settings.zoteroImportAction"') == 2

    library = (root / "src/cyrene/workbench/webui/frontend/workbench-library.jsx").read_text(encoding="utf-8")
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
    assert 'ids: ["model-usage", "models", "media", "agents", "voice"]' in settings
    assert '{ id: "plugins", labelKey: "settings.pluginsTab", icon: "tools" }' not in settings
    assert 'tab === "plugins"' not in settings
    assert 'mode === "plugins"' not in settings
    icon_names = re.findall(r'\{ id: "[^"]+", labelKey: "[^"]+", icon: "([^"]+)"[^}]*\}', settings)
    assert len(icon_names) == 19
    assert len(set(icon_names)) == len(icon_names)
    assert 'className: "settings-overlay-tab-glyph"' in settings
    build_source = (root / "src/cyrene/workbench/webui/build-jsx.mjs").read_text(encoding="utf-8")
    assert '@tabler/icons/icons/outline' in build_source
    assert "'code.svg'," in build_source
    assert (root / "src/cyrene/workbench/webui/static/app/settings-icons/code.svg").is_file()
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
        "语音交互", "消息渠道", "远程连接", "插件与集成", "插件中心",
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
    assert 'providerUsageItems.map(providerUsageCard)' in panel
    assert 'wb-provider-usage-column' not in panel
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
    assert ".settings-overlay .settings-panel.wb-usage-settings" in styles
    assert "width: min(100%, 1120px)" in styles
    model_grid_rule = styles.split(".wb-budget-model-grid {", 1)[1].split("}", 1)[0]
    assert "repeat(auto-fit, minmax(min(100%, 19rem), 1fr))" in model_grid_rule
    provider_grid_rule = styles.split(".wb-provider-usage-grid {", 1)[1].split("}", 1)[0]
    assert "repeat(auto-fit, minmax(min(100%, 15rem), 1fr))" in provider_grid_rule
    assert "align-items: start" in provider_grid_rule
    assert "align-items: stretch" not in provider_grid_rule
    assert ".wb-provider-usage-column" not in styles
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
    assert 'external_agent_proxy_url: address' in general_panel
    assert 't("settings.agentProxyAddress")' in general_panel
    assert 'placeholder: "http://proxy.example.com:7897"' in general_panel
    assert 'proxy_search_enabled: !!scopes.search' in general_panel
    assert 'proxy_browser_enabled: !!scopes.browser' in general_panel
    assert 'proxy_extensions_enabled: !!scopes.extensions' in general_panel
    assert 't("settings.proxySearch")' in general_panel
    assert 't("settings.proxyBrowser")' in general_panel
    assert 't("settings.proxyExtensions")' in general_panel
    assert '{ search: !proxySearchEnabled }); }, !agentProxyEnabled' in general_panel
    assert i18n.count('"settings.agentProxyEnabled"') == 2
    assert i18n.count('"settings.agentProxyAddress"') == 2
    assert i18n.count('"settings.proxySearch"') == 2
    assert i18n.count('"settings.proxyBrowser"') == 2
    assert i18n.count('"settings.proxyExtensions"') == 2
