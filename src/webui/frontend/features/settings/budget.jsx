import {
  workbenchServices,
  useStateSt,
  useEffectSt,
  useRefSt,
  readSettingsResponse,
  settingsFetch,
  showSettingsToast,
  SectionTitle,
  SectionBlock,
  FieldRow,
  Toggle,
} from "./shared.jsx"

function UsageTrendChart(p) {
  var { t } = p;
  var items = Array.isArray(p.items) ? p.items : [];
  var currencySymbol = String(p.currencySymbol || "");
  var chartRef = useRefSt(null);
  var signature = JSON.stringify(items.map(function (item) {
    return [item.day, item.total_tokens, item.requests, item.cost];
  })) + currencySymbol;

  function compactAxisValue(value) {
    var number = Number(value) || 0;
    if (Math.abs(number) >= 1e6) return (number / 1e6).toFixed(number >= 1e7 ? 0 : 1) + "M";
    if (Math.abs(number) >= 1e3) return (number / 1e3).toFixed(number >= 1e4 ? 0 : 1) + "K";
    return String(Math.round(number));
  }

  function compactCostAxisValue(value) {
    var number = Number(value) || 0;
    if (Math.abs(number) >= 1e3) return currencySymbol + compactAxisValue(number);
    if (Math.abs(number) >= 10) return currencySymbol + number.toFixed(0);
    if (Math.abs(number) >= 1) return currencySymbol + number.toFixed(1);
    return currencySymbol + number.toFixed(2);
  }

  useEffectSt(function () {
    var node = chartRef.current;
    if (!node || items.length < 2 || !window.echarts || typeof window.echarts.init !== "function") return undefined;
    var byDay = {};
    items.forEach(function (item) { byDay[String(item.day || "")] = item; });
    var now = new Date();
    var year = now.getFullYear();
    var month = now.getMonth() + 1;
    var prefix = String(year) + "-" + String(month).padStart(2, "0") + "-";
    var days = [];
    var tokenValues = [];
    var requestValues = [];
    var costValues = [];
    for (var day = 1; day <= now.getDate(); day += 1) {
      var key = prefix + String(day).padStart(2, "0");
      var row = byDay[key] || {};
      days.push(String(month).padStart(2, "0") + "/" + String(day).padStart(2, "0"));
      tokenValues.push(Number(row.total_tokens || 0));
      requestValues.push(Number(row.requests || 0));
      costValues.push(Number(row.cost || 0));
    }

    var chart = window.echarts.init(node);
    function renderChart() {
      var style = getComputedStyle(node);
      var textColor = style.getPropertyValue("--wb-chart-legend").trim() || "#3f4a57";
      var mutedColor = style.getPropertyValue("--wb-chart-axis").trim() || "#687584";
      var gridColor = style.getPropertyValue("--wb-chart-grid").trim() || "rgba(23, 28, 34, 0.12)";
      var axisLineColor = style.getPropertyValue("--wb-chart-axis-line").trim() || "rgba(23, 28, 34, 0.22)";
      var tokenColor = style.getPropertyValue("--wb-chart-token").trim() || "#2f6fec";
      var requestColor = style.getPropertyValue("--wb-chart-request").trim() || "#9d6100";
      var costColor = style.getPropertyValue("--wb-chart-cost").trim() || "#a3448f";
      var tooltipBackground = style.getPropertyValue("--wb-chart-tooltip-bg").trim() || "#ffffff";
      var tooltipBorder = style.getPropertyValue("--wb-chart-tooltip-border").trim() || "#d2dae2";
      var tooltipText = style.getPropertyValue("--wb-chart-tooltip-text").trim() || "#171c22";
      function combinedYAxis(position, offset, formatter, color, showSplitLine) {
        return {
          type: "value",
          position: position,
          offset: offset || 0,
          min: 0,
          axisLine: { show: true, lineStyle: { color: axisLineColor } },
          axisTick: { show: false },
          axisLabel: { color: color, fontSize: 10, formatter: formatter },
          splitLine: showSplitLine
            ? { show: true, lineStyle: { color: gridColor, type: "dashed" } }
            : { show: false },
        };
      }
      chart.setOption({
        animation: false,
        backgroundColor: "transparent",
        color: [tokenColor, requestColor, costColor],
        grid: { left: 58, right: 112, top: 44, bottom: 32 },
        legend: {
          type: "scroll",
          top: 0,
          left: "center",
          itemWidth: 24,
          itemHeight: 8,
          textStyle: { color: textColor, fontSize: 11, fontWeight: 500 },
          inactiveColor: mutedColor,
        },
        tooltip: {
          trigger: "axis",
          confine: true,
          backgroundColor: tooltipBackground,
          borderColor: tooltipBorder,
          borderWidth: 1,
          textStyle: { color: tooltipText },
          axisPointer: { lineStyle: { color: axisLineColor, type: "dashed" } },
          extraCssText: "box-shadow: 0 10px 30px rgba(0, 0, 0, 0.24); border-radius: 8px;",
        },
        xAxis: {
          type: "category",
          boundaryGap: false,
          data: days,
          axisLine: { lineStyle: { color: axisLineColor } },
          axisTick: { show: false },
          axisLabel: { color: mutedColor, fontSize: 10, hideOverlap: true },
          splitLine: { show: false },
        },
        yAxis: [
          combinedYAxis("left", 0, compactAxisValue, tokenColor, true),
          combinedYAxis("right", 0, compactAxisValue, requestColor, false),
          combinedYAxis("right", 56, compactCostAxisValue, costColor, false),
        ],
        series: [{
          name: t("settings.usageTrendTokens"),
          type: "line",
          yAxisIndex: 0,
          data: tokenValues,
          symbol: "circle",
          showSymbol: days.length <= 16,
          symbolSize: 6,
          lineStyle: { width: 2, type: "solid" },
          itemStyle: { color: tokenColor },
          emphasis: { focus: "series" },
          tooltip: { valueFormatter: function (value) { return Number(value || 0).toLocaleString(); } },
        }, {
          name: t("settings.usageTrendRequests"),
          type: "line",
          yAxisIndex: 1,
          data: requestValues,
          symbol: "diamond",
          showSymbol: days.length <= 16,
          symbolSize: 7,
          lineStyle: { width: 2, type: "dashed" },
          itemStyle: { color: requestColor },
          emphasis: { focus: "series" },
          tooltip: { valueFormatter: function (value) { return Number(value || 0).toLocaleString(); } },
        }, {
          name: t("settings.usageTrendCost"),
          type: "line",
          yAxisIndex: 2,
          data: costValues,
          symbol: "triangle",
          showSymbol: days.length <= 16,
          symbolSize: 7,
          lineStyle: { width: 2, type: "dotted" },
          itemStyle: { color: costColor },
          emphasis: { focus: "series" },
          tooltip: { valueFormatter: function (value) { return currencySymbol + Number(value || 0).toFixed(2); } },
        }],
      }, true);
    }
    renderChart();

    function resizeChart() { chart.resize(); }
    var resizeObserver = typeof ResizeObserver === "function" ? new ResizeObserver(resizeChart) : null;
    if (resizeObserver) resizeObserver.observe(node);
    var themeObserver = typeof MutationObserver === "function" ? new MutationObserver(renderChart) : null;
    if (themeObserver) themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return function () {
      if (resizeObserver) resizeObserver.disconnect();
      if (themeObserver) themeObserver.disconnect();
      chart.dispose();
    };
  }, [signature]);

  return React.createElement("div", { className: "wb-usage-trend" },
    React.createElement("div", { className: "wb-usage-trend-head" },
      React.createElement("strong", null, t("settings.usageTrendTitle")),
      React.createElement("small", null, t("settings.usageTrendHint")),
    ),
    items.length >= 2
      ? React.createElement("div", {
          ref: chartRef,
          className: "wb-usage-trend-canvas",
          role: "img",
          "aria-label": t("settings.usageTrendHint"),
        })
      : React.createElement("div", { className: "wb-usage-trend-empty" }, t("settings.usageTrendEmpty")),
  );
}

// ── Budget Panel ──
function BudgetPanel(p) {
  var { t, config } = p;
  var mode = p.mode === "usage" ? "usage" : "budget";
  var dataStore = workbenchServices.data();
  dataStore.useVersion();
  var dashboard = dataStore.state.dashboard || {};
  var profileUsage = dashboard.usage || {};

  // ── Init from config (unified config API) ──
  var [budgetEnabled, setBudgetEnabled] = useStateSt(!!config.budget_enabled);
  var [budgetMonthly, setBudgetMonthly] = useStateSt(String(config.budget_monthly != null ? config.budget_monthly : 50));
  var [budgetCurrency, setBudgetCurrency] = useStateSt(config.budget_currency || "CNY");
  var [budgetAction, setBudgetAction] = useStateSt(config.budget_action || "warn");
  var [budgetStartDay, setBudgetStartDay] = useStateSt(String(config.budget_start_day != null ? config.budget_start_day : 1));
  var [budgetSaved, setBudgetSaved] = useStateSt("");
  var codexQuotaModel = workbenchServices.model();
  var [codexQuota, setCodexQuota] = useStateSt({ connected: false, limits: {} });
  var [providerUsage, setProviderUsage] = useStateSt([]);
  var [providerUsageLoading, setProviderUsageLoading] = useStateSt(true);
  var providerRefreshTimer = useRefSt(null);

  var BUDGET_KEY = "cyrene-budget";

  // Sync to localStorage (cache for ProjectRail / backward compat)
  var budgetSaveTimer = useRefSt(null);

  function syncLocalStorage(values) {
    try {
      localStorage.setItem(BUDGET_KEY, JSON.stringify(values || {
        enabled: budgetEnabled,
        monthly: budgetMonthly,
        currency: budgetCurrency,
        action: budgetAction,
        startDay: budgetStartDay,
      }));
    } catch (e) {}
  }

  function scheduleClearSaved() {
    if (budgetSaveTimer.current) clearTimeout(budgetSaveTimer.current);
    budgetSaveTimer.current = setTimeout(function () { setBudgetSaved(""); }, 1200);
  }

  function saveBudgetConfig(body) {
    return settingsFetch("/api/settings/config", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (r) {
      if (r.ok) {
        setBudgetSaved("");
        showSettingsToast(t("settings.saved"), "success");
        syncLocalStorage(body);
        try { window.dispatchEvent(new CustomEvent("budget-saved")); } catch (e) {}
      } else {
        setBudgetSaved("");
        showSettingsToast(t("settings.error"), "error");
      }
      scheduleClearSaved();
    }).catch(function (error) {
      setBudgetSaved("");
      showSettingsToast(t("settings.error") + ": " + (error.message || ""), "error");
      scheduleClearSaved();
    });
  }

  function toggleEnabled() {
    var next = !budgetEnabled;
    setBudgetEnabled(next);
    saveBudgetConfig({ budget_enabled: next });
  }

  function fetchCodexQuota() {
    settingsFetch("/api/settings/openai-oauth/limits")
      .then(readSettingsResponse)
      .then(function (data) {
        setCodexQuota(data);
        codexQuotaModel.writeCodexQuotaCache(data);
      })
      .catch(function () {});
  }

  function fetchProviderUsage(forceRefresh, quiet) {
    if (!quiet) setProviderUsageLoading(true);
    settingsFetch("/api/settings/model-config/provider-usage" + (forceRefresh ? "?refresh=true" : ""))
      .then(readSettingsResponse)
      .then(function (data) {
        var items = data && Array.isArray(data.items) ? data.items : [];
        setProviderUsage(items);
        if (items.some(function (item) { return item.refreshing === true; })) {
          if (providerRefreshTimer.current) clearTimeout(providerRefreshTimer.current);
          providerRefreshTimer.current = setTimeout(function () {
            fetchProviderUsage(false, true);
          }, 750);
        }
      })
      .catch(function () {})
      .finally(function () { if (!quiet) setProviderUsageLoading(false); });
  }

  function saveBudget() {
    saveBudgetConfig({
      budget_monthly: Number(budgetMonthly) || 0,
      budget_currency: budgetCurrency,
      budget_action: budgetAction,
      budget_start_day: Number(budgetStartDay) || 1,
    }).then(fetchStats);
  }

  // ── Stats from API ──
  var [budgetModels, setBudgetModels] = useStateSt([]);
  var [budgetDaily, setBudgetDaily] = useStateSt([]);
  var [totalCost, setTotalCost] = useStateSt(0);
  var [totalRequests, setTotalRequests] = useStateSt(0);
  var [maxRequestTokens, setMaxRequestTokens] = useStateSt(0);
  var [maxRequestCost, setMaxRequestCost] = useStateSt(0);
  var [budgetLoading, setBudgetLoading] = useStateSt(true);

  function fetchStats() {
    settingsFetch("/api/settings/budget/stats")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        setBudgetModels(d.models || []);
        setBudgetDaily(d.by_day || []);
        setTotalCost(d.total_cost || 0);
        setTotalRequests(d.total_requests || 0);
        setMaxRequestTokens(d.max_request_tokens || 0);
        setMaxRequestCost(d.max_request_cost || 0);
        setBudgetLoading(false);
      })
      .catch(function () { setBudgetLoading(false); });
  }

  useEffectSt(function () {
    fetchStats();
    fetchCodexQuota();
    fetchProviderUsage(false, false);
    return function () {
      if (budgetSaveTimer.current) clearTimeout(budgetSaveTimer.current);
      if (providerRefreshTimer.current) clearTimeout(providerRefreshTimer.current);
    };
  }, []);

  var budgetNum = Number(budgetMonthly) || 0;
  var budgetRatio = budgetNum > 0 ? Math.min(totalCost / budgetNum, 1) : 0;
  var currencySymbol = budgetCurrency === "CNY" ? "¥" : "$";
  var periodPromptTokens = budgetModels.reduce(function (sum, item) { return sum + (Number(item.prompt_tokens) || 0); }, 0);
  var periodCompletionTokens = budgetModels.reduce(function (sum, item) { return sum + (Number(item.completion_tokens) || 0); }, 0);
  var periodTotalTokens = periodPromptTokens + periodCompletionTokens;
  var averageRequestTokens = totalRequests > 0 ? periodTotalTokens / totalRequests : 0;
  var averageRequestCost = totalRequests > 0 ? totalCost / totalRequests : 0;
  var peakUsageDay = budgetDaily.reduce(function (peak, item) {
    return !peak || Number(item.total_tokens || 0) >= Number(peak.total_tokens || 0) ? item : peak;
  }, null);
  var peakCallsDay = budgetDaily.reduce(function (peak, item) {
    return !peak || Number(item.requests || 0) >= Number(peak.requests || 0) ? item : peak;
  }, null);
  var profileSpend = budgetCurrency === "CNY"
    ? Number(profileUsage.spend_cny || 0)
    : Number(profileUsage.spend_usd || 0);
  var profilePromptTokens = Number(profileUsage.prompt_tokens || 0);
  var profileCompletionTokens = Number(profileUsage.completion_tokens || 0);
  var profileTotalTokens = Number(profileUsage.total_tokens || (profilePromptTokens + profileCompletionTokens));
  var codexWindows = codexQuotaModel.codexQuotaWindows(codexQuota.limits);
  var codexPlan = codexQuotaModel.codexPlanLabel(
    codexQuota.account,
    codexQuota.limits
  );
  var codexUsageItem = {
    connection_id: "codex_oauth",
    provider: "codex_oauth",
    label: "Codex",
    kind: "codex_quota",
    status: codexQuota.connected ? "ok" : codexQuota.error ? "error" : "unconfigured",
    available: codexQuota.connected === true,
    error: codexQuota.error || "",
    plan: codexPlan || "",
    windows: codexWindows.map(function (windowData) {
      return {
        model: "codex",
        kind: windowData.kind,
        label: windowData.label,
        remaining_percent: windowData.remainingPercent,
        used_percent: windowData.usedPercent,
        reset_at: windowData.resetsAt ? new Date(windowData.resetsAt * 1000).toISOString() : null,
      };
    }),
  };
  var providerUsageItems = providerUsage.slice();
  if (codexQuota.connected === true) providerUsageItems.push(codexUsageItem);
  var minimaxUsageItems = providerUsageItems.filter(function (item) {
    return item.provider === "minimax";
  });
  var compactProviderUsageItems = providerUsageItems.filter(function (item) {
    return item.provider !== "minimax";
  });

  function formatCost(val) {
    return currencySymbol + val.toFixed(2);
  }

  function formatTokens(n) {
    n = Number(n) || 0;
    if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
    return String(n);
  }

  function formatPeakDate(item) {
    if (!item || !item.day) return "";
    var value = new Date(String(item.day) + "T00:00:00");
    return t("settings.usagePeakDate", {
      date: isNaN(value.getTime()) ? String(item.day) : value.toLocaleDateString(),
    });
  }

  function usageMetric(value, label, detail) {
    return React.createElement("div", { className: "wb-usage-metric" },
      React.createElement("strong", null, value),
      React.createElement("span", null, label),
      detail && React.createElement("small", null, detail),
    );
  }

  function providerAmount(value, currency) {
    var number = Number(value);
    if (!isFinite(number)) return String(value || "0") + " " + currency;
    try {
      return new Intl.NumberFormat(undefined, {
        style: "currency", currency: currency || "CNY", minimumFractionDigits: 2,
      }).format(number);
    } catch (error) {
      return number.toFixed(2) + " " + currency;
    }
  }

  function providerWindowLabel(windowData) {
    if (windowData.label) return windowData.label;
    var windowLabel = windowData.kind === "weekly"
      ? t("settings.providerUsageWeekly")
      : t("settings.providerUsageInterval");
    return (windowData.model && windowData.model !== "general" ? windowData.model + " · " : "") + windowLabel;
  }

  function providerUsageCard(item) {
    var visibleWindows = (item.windows || []).filter(function (windowData) {
      return String(windowData.model || "").trim().toLowerCase() !== "video";
    });
    var stateClass = item.status === "ok" && item.available === false ? "empty" : item.status;
    var stateLabel = item.status === "ok"
      ? item.available === false ? t("settings.providerUsageDepleted") : t("settings.providerUsageConnected")
      : item.status === "unconfigured"
        ? t("settings.providerUsageUnconfigured")
        : t("settings.providerUsageUnavailable");
    return React.createElement("article", {
      className: "wb-provider-usage-card",
      id: item.kind === "codex_quota" ? "setting-codex-quota" : undefined,
      key: item.connection_id || item.provider,
    },
      React.createElement("header", { className: "wb-provider-usage-card-head" },
        React.createElement("div", null,
          React.createElement("strong", null, item.label || item.provider),
          React.createElement("small", null, item.kind === "balance"
            ? t("settings.providerUsageBalance")
            : item.kind === "codex_quota"
              ? t("settings.codexQuotaPlan", { plan: item.plan || "—" })
              : t("settings.providerUsageQuota")),
        ),
        React.createElement("span", { className: "wb-provider-usage-state is-" + stateClass }, stateLabel),
      ),
      item.status === "unconfigured" && React.createElement("p", { className: "wb-hint" },
        t(item.kind === "codex_quota" ? "settings.codexQuotaLoginHint" : "settings.providerUsageConfigureKey")
      ),
      item.status === "error" && React.createElement("p", { className: "wb-provider-usage-error" }, item.error || t("settings.providerUsageUnavailable")),
      item.status === "ok" && item.kind === "balance" && React.createElement("div", { className: "wb-provider-balance-list" },
        (item.balances || []).map(function (balance) {
          return React.createElement("div", { className: "wb-provider-balance", key: balance.currency },
            React.createElement("strong", null, providerAmount(balance.total, balance.currency)),
            React.createElement("span", null, t("settings.providerUsageTotalBalance")),
            React.createElement("small", null,
              t("settings.providerUsageBalanceBreakdown", {
                toppedUp: providerAmount(balance.topped_up, balance.currency),
                granted: providerAmount(balance.granted, balance.currency),
              })
            ),
          );
        }),
        !(item.balances || []).length && React.createElement("p", { className: "wb-hint" }, t("settings.providerUsageNoBalance")),
      ),
      item.status === "ok" && (item.kind === "quota" || item.kind === "codex_quota") && React.createElement("div", { className: "wb-provider-quota-list" },
        visibleWindows.map(function (windowData, index) {
          var remaining = windowData.remaining_percent == null ? null : Number(windowData.remaining_percent);
          var used = windowData.used_percent == null ? 0 : Number(windowData.used_percent);
          var valueLabel = windowData.unlimited
            ? t("settings.providerUsageUnlimited")
            : windowData.ambiguous
              ? t("settings.providerUsageStatusUnknown")
            : remaining == null
              ? "—"
              : t("settings.providerUsageRemaining", { pct: Math.round(remaining) });
          return React.createElement("div", { className: "wb-provider-quota-window", key: windowData.model + "-" + windowData.kind + "-" + index },
            React.createElement("div", { className: "wb-provider-quota-label" },
              React.createElement("span", null, providerWindowLabel(windowData)),
              React.createElement("strong", null, valueLabel),
            ),
            !windowData.unlimited && !windowData.ambiguous && React.createElement("div", { className: "wb-budget-progress-bar" },
              React.createElement("div", {
                className: "wb-budget-progress-fill" + (used >= 100 ? " over" : used >= 80 ? " high" : ""),
                style: { width: Math.max(0, Math.min(100, used)) + "%" },
              }),
            ),
            React.createElement("small", null, windowData.reset_at
              ? t("settings.providerUsageResets", { time: new Date(windowData.reset_at).toLocaleString() })
              : t("settings.providerUsageResetUnknown")),
            windowData.ambiguous && React.createElement("small", { className: "wb-provider-quota-warning" },
              t("settings.providerUsageStatusUnknownHint")
            ),
          );
        }),
        !visibleWindows.length && React.createElement("p", { className: "wb-hint" }, t("settings.providerUsageNoQuota")),
      ),
      item.refreshed_at && React.createElement("footer", null, t("settings.providerUsageUpdated", { time: new Date(item.refreshed_at).toLocaleString() })),
    );
  }

  return React.createElement("div", {
    className: "settings-panel" + (mode === "usage" ? " wb-usage-settings" : ""),
  },
    SectionTitle(
      t(mode === "usage" ? "settings.usage" : "settings.budget"),
      t(mode === "usage" ? "settings.usageSubtitle" : "settings.budgetSubtitle")
    ),

    mode === "usage" && SectionBlock(t("settings.profileUsageSnapshot"), t("settings.profileUsageSnapshotHint"),
      React.createElement("div", { className: "wb-usage-metrics is-profile" },
        usageMetric(formatCost(profileSpend), t("profile.spend")),
        usageMetric(Number(profileUsage.requests || 0).toLocaleString(), t("profile.requests")),
        usageMetric(formatTokens(profileTotalTokens), t("profile.tokens")),
        usageMetric(formatTokens(profilePromptTokens), t("settings.usageInputTokens")),
        usageMetric(formatTokens(profileCompletionTokens), t("settings.usageOutputTokens")),
      ),
    ),

    // ── Overview section ──
    mode === "usage" && SectionBlock(t("settings.usageBillingPeriod"), t("settings.usageBillingPeriodHint"),
      React.createElement("div", { className: "wb-budget-summary" },
        React.createElement("div", { className: "wb-usage-metrics is-period" },
          usageMetric(formatCost(totalCost), t("settings.budgetSpend")),
          usageMetric(totalRequests.toLocaleString(), t("settings.budgetRequests")),
          usageMetric(formatTokens(periodTotalTokens), t("settings.budgetTokens")),
          usageMetric(formatTokens(periodPromptTokens), t("settings.usageInputTokens")),
          usageMetric(
            totalRequests > 0 ? formatTokens(Math.round(averageRequestTokens)) : "—",
            t("settings.usageAverageTokens"),
            totalRequests > 0 ? t("settings.usageMaxRequestTokens", { tokens: formatTokens(maxRequestTokens) }) : ""
          ),
          usageMetric(
            formatCost(averageRequestCost),
            t("settings.usageAverageCost"),
            totalRequests > 0 ? t("settings.usageMaxRequestCost", { cost: formatCost(maxRequestCost) }) : ""
          ),
          usageMetric(peakUsageDay ? formatTokens(peakUsageDay.total_tokens) : "—", t("settings.usagePeakUsage"), formatPeakDate(peakUsageDay)),
          usageMetric(peakCallsDay ? Number(peakCallsDay.requests || 0).toLocaleString() : "—", t("settings.usagePeakCalls"), formatPeakDate(peakCallsDay)),
        ),
        React.createElement(UsageTrendChart, { t: t, items: budgetDaily, currencySymbol: currencySymbol }),
      ),
    ),

    // ── Budget configuration ──
    mode === "budget" && React.cloneElement(SectionBlock(t("settings.budgetConfig"), null,
      FieldRow(t("settings.budgetEnable"), t("settings.budgetEnableHint"),
        Toggle(budgetEnabled, toggleEnabled),
      ),
      budgetEnabled && React.createElement(React.Fragment, null,
        FieldRow(t("settings.budgetMonthly"), t("settings.budgetMonthlyHint"),
          React.createElement("div", { className: "wb-inline-row" },
            React.createElement("input", {
              className: "wb-input mono",
              type: "text", inputMode: "decimal",
              value: budgetMonthly,
              onChange: function (e) { setBudgetMonthly(e.target.value); },
              placeholder: "0",
              style: { maxWidth: 120 },
              key: "budget-input",
            }),
            React.createElement("select", {
              className: "wb-select",
              value: budgetCurrency,
              onChange: function (e) { setBudgetCurrency(e.target.value); },
              style: { maxWidth: 90 },
            },
              React.createElement("option", { value: "CNY" }, "CNY (¥)"),
              React.createElement("option", { value: "USD" }, "USD ($)"),
            ),
          ),
        ),

        // Billing cycle start day
        FieldRow(t("settings.budgetStartDay"), t("settings.budgetStartDayHint"),
          React.createElement("input", {
            className: "wb-input mono",
            type: "text", inputMode: "numeric",
            value: budgetStartDay,
            onChange: function (e) { setBudgetStartDay(e.target.value); },
            placeholder: "1",
            style: { maxWidth: 80 },
          }),
        ),

        FieldRow(t("settings.budgetAction"), t("settings.budgetActionHint"),
          React.createElement("select", {
            className: "wb-select",
            value: budgetAction,
            onChange: function (e) { setBudgetAction(e.target.value); },
            style: { maxWidth: 240 },
          },
            React.createElement("option", { value: "warn" }, t("settings.budgetActionWarn")),
            React.createElement("option", { value: "block" }, t("settings.budgetActionBlock")),
          ),
        ),
        React.createElement("div", { className: "wb-save-actions" },
          React.createElement("button", { className: "wb-btn primary", onClick: saveBudget },
            t("settings.saveApply")
          ),
          budgetSaved && React.createElement("span", { className: "wb-hint saved" }, budgetSaved),
        ),
      ),
    ), { id: "setting-budget" }),

    mode === "budget" && SectionBlock(t("settings.budgetOverview"), null,
      React.createElement("div", { className: "wb-budget-summary" },
        React.createElement("div", { className: "wb-usage-metrics is-period" },
          usageMetric(budgetEnabled ? formatCost(budgetNum) : "—", t("settings.budgetLimit")),
          usageMetric(budgetEnabled && budgetNum > 0 ? Math.round(budgetRatio * 100) + "%" : "—", t("settings.usageBudgetRate")),
        ),
        React.createElement("div", { className: "wb-budget-progress-wrap" },
          React.createElement("div", { className: "wb-budget-progress-bar" },
            React.createElement("div", {
              className: "wb-budget-progress-fill" + (budgetRatio >= 1 ? " over" : budgetRatio >= 0.8 ? " high" : ""),
              style: { width: Math.round(budgetRatio * 100) + "%" },
            }),
          ),
          React.createElement("span", { className: "wb-budget-progress-label" },
            t("settings.budgetUsed", { pct: Math.round(budgetRatio * 100) })
          ),
        ),
        !budgetEnabled && React.createElement("p", { className: "wb-hint", style: { textAlign: "center", marginTop: 8 } },
          t("settings.budgetDisabledHint")
        ),
      ),
    ),

    // ── Cost by model ──
    mode === "usage" && SectionBlock(t("settings.usageByModel"), t("settings.usageByModelHint"),
      React.createElement("div", { className: "wb-budget-model-grid" },
        budgetModels.map(function (item) {
          var modelPct = totalCost > 0 ? (item.cost / totalCost * 100) : 0;
          return React.createElement("article", { className: "wb-budget-model-card", key: item.model },
            React.createElement("header", { className: "wb-budget-model-card-head" },
              React.createElement("strong", { className: "wb-budget-model-name mono", title: item.model }, item.model),
              React.createElement("div", { className: "wb-budget-model-cost" },
                React.createElement("small", null, t("settings.budgetCost")),
                React.createElement("strong", null, formatCost(item.cost)),
              ),
            ),
            React.createElement("dl", { className: "wb-budget-model-stats" },
              React.createElement("div", null,
                React.createElement("dt", null, t("settings.budgetRequests")),
                React.createElement("dd", null, Number(item.requests || 0).toLocaleString()),
              ),
              React.createElement("div", null,
                React.createElement("dt", null, t("settings.usageInputTokens")),
                React.createElement("dd", null, formatTokens(item.prompt_tokens)),
              ),
              React.createElement("div", null,
                React.createElement("dt", null, t("settings.usageOutputTokens")),
                React.createElement("dd", null, formatTokens(item.completion_tokens)),
              ),
              React.createElement("div", null,
                React.createElement("dt", null, t("settings.budgetTokens")),
                React.createElement("dd", null, formatTokens(item.prompt_tokens + item.completion_tokens)),
              ),
            ),
            modelPct > 0 && React.createElement("div", { className: "wb-budget-model-bar-wrap" },
              React.createElement("div", { className: "wb-budget-model-bar", style: { width: modelPct + "%" }, "aria-hidden": "true" }),
            ),
          );
        }),
        !budgetLoading && !budgetModels.length && React.createElement("div", { className: "wb-budget-model-empty" },
          t("settings.usageNoModelData")
        ),
      ),
    ),

    mode === "usage" && SectionBlock(t("settings.providerUsage"), t("settings.providerUsageHint"),
      providerUsageItems.length > 0 && React.createElement("div", { className: "wb-provider-usage-grid" },
        React.createElement("div", { className: "wb-provider-usage-column is-compact" },
          compactProviderUsageItems.map(providerUsageCard),
        ),
        React.createElement("div", { className: "wb-provider-usage-column is-minimax" },
          minimaxUsageItems.map(providerUsageCard),
        ),
      ),
      !providerUsageLoading && !providerUsageItems.length && React.createElement("div", { className: "wb-budget-model-empty" },
        t("settings.providerUsageEmpty")
      ),
    ),

  );
}

export { BudgetPanel };
