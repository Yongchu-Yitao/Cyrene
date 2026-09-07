import { SectionBlock, FieldRow, Toggle, workbenchServices, useStateSt, useEffectSt, settingsFetch, readSettingsResponse, showSettingsToast } from "./shared.jsx"

  function normalizeProxyAddress(value) {
    var raw = String(value || "").trim();
    if (!raw) return "";
    var candidate = raw.indexOf("://") >= 0 ? raw : "http://" + raw;
    try {
      var parsed = new URL(candidate);
      if ((parsed.protocol !== "http:" && parsed.protocol !== "https:")
          || !parsed.hostname || parsed.username || parsed.password
          || (parsed.pathname && parsed.pathname !== "/")
          || parsed.search || parsed.hash) return "";
      return parsed.protocol + "//" + parsed.host;
    } catch (error) {
      return "";
    }
  }

  function saveProxySettings(nextEnabled, nextAddress, scopeChanges, { proxySearchEnabled, proxyBrowserEnabled, proxyExtensionsEnabled, proxyUpdatesEnabled, setAgentProxyEnabled, setAgentProxyAddress, setProxySearchEnabled, setProxyBrowserEnabled, setProxyExtensionsEnabled, setProxyUpdatesEnabled, setAgentProxyStatus, t }) {
    var address = normalizeProxyAddress(nextAddress);
    if (!address) {
      setAgentProxyStatus(t("settings.agentProxyAddressInvalid"));
      return;
    }
    var scopes = Object.assign({
      search: proxySearchEnabled,
      browser: proxyBrowserEnabled,
      extensions: proxyExtensionsEnabled,
      updates: proxyUpdatesEnabled,
    }, scopeChanges || {});
    setAgentProxyStatus(t("settings.saving"));
    settingsFetch("/api/settings/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        external_agent_proxy_enabled: !!nextEnabled,
        external_agent_proxy_url: address,
        proxy_search_enabled: !!scopes.search,
        proxy_browser_enabled: !!scopes.browser,
        proxy_extensions_enabled: !!scopes.extensions,
        proxy_updates_enabled: !!scopes.updates,
      }),
    }).then(readSettingsResponse).then(function () {
      setAgentProxyEnabled(!!nextEnabled);
      setAgentProxyAddress(address);
      setProxySearchEnabled(!!scopes.search);
      setProxyBrowserEnabled(!!scopes.browser);
      setProxyExtensionsEnabled(!!scopes.extensions);
      setProxyUpdatesEnabled(!!scopes.updates);
      setAgentProxyStatus("");
      if (window.cyrene && window.cyrene.browser && typeof window.cyrene.browser.syncProxy === "function") {
        window.cyrene.browser.syncProxy().catch(function () {});
      }
      showSettingsToast(t("settings.agentProxySaved"), "success");
    }).catch(function (error) {
      setAgentProxyStatus("");
      showSettingsToast(t("settings.error") + ": " + (error.message || ""), "error");
    });
  }

export function useAgentProxySettings(hasKnowledge, hasExtensions, timezoneOptions, setSelectedTimezone, t) {
  var [agentProxyEnabled, setAgentProxyEnabled] = useStateSt(false);
  var [agentProxyAddress, setAgentProxyAddress] = useStateSt("http://127.0.0.1:7897");
  var [proxySearchEnabled, setProxySearchEnabled] = useStateSt(false);
  var [proxyBrowserEnabled, setProxyBrowserEnabled] = useStateSt(false);
  var [proxyExtensionsEnabled, setProxyExtensionsEnabled] = useStateSt(false);
  var [proxyUpdatesEnabled, setProxyUpdatesEnabled] = useStateSt(false);
  var [agentProxyStatus, setAgentProxyStatus] = useStateSt("");
  useEffectSt(function () {
    if (!hasKnowledge && !hasExtensions) return undefined;
    var cancelled = false;
    var controller = new AbortController();
    settingsFetch("/api/settings/config", { signal: controller.signal }).then(readSettingsResponse).then(function (payload) {
      if (cancelled) return;
      var savedTimezone = String(payload.timezone || "");
      setAgentProxyEnabled(payload.external_agent_proxy_enabled === true);
      setAgentProxyAddress(String(
        payload.external_agent_proxy_url
        || ("http://127.0.0.1:" + (payload.external_agent_proxy_port || 7897))
      ));
      setProxySearchEnabled(payload.proxy_search_enabled === true);
      setProxyBrowserEnabled(payload.proxy_browser_enabled === true);
      setProxyExtensionsEnabled(payload.proxy_extensions_enabled === true);
      setProxyUpdatesEnabled(payload.proxy_updates_enabled === true);
      if (timezoneOptions.indexOf(savedTimezone) < 0) return;
      var previousTimezone = "";
      try { previousTimezone = localStorage.getItem("cyrene-timezone") || ""; } catch (e) {}
      setSelectedTimezone(savedTimezone);
      try { localStorage.setItem("cyrene-timezone", savedTimezone); } catch (e) {}
      if (previousTimezone && previousTimezone !== savedTimezone) {
        try { workbenchServices.data().reload(); } catch (e) {}
      }
    }).catch(function () {});
    return function () { cancelled = true; controller.abort(); };
  }, [hasKnowledge, hasExtensions]);
  function saveAgentProxy(nextEnabled, nextAddress, scopeChanges) {
    return saveProxySettings(nextEnabled, nextAddress, scopeChanges, { proxySearchEnabled, proxyBrowserEnabled, proxyExtensionsEnabled, proxyUpdatesEnabled, setAgentProxyEnabled, setAgentProxyAddress, setProxySearchEnabled, setProxyBrowserEnabled, setProxyExtensionsEnabled, setProxyUpdatesEnabled, setAgentProxyStatus, t });
  }
  return { agentProxyEnabled, agentProxyAddress, proxySearchEnabled, proxyBrowserEnabled,
    proxyExtensionsEnabled, proxyUpdatesEnabled, agentProxyStatus, setAgentProxyAddress,
    setAgentProxyStatus, saveAgentProxy };
}

export function renderAgentProxySettings({ agentProxyEnabled, agentProxyAddress,
  proxySearchEnabled, proxyBrowserEnabled, proxyExtensionsEnabled, proxyUpdatesEnabled,
  agentProxyStatus, setAgentProxyAddress, setAgentProxyStatus, saveAgentProxy,
  t, hasSearch, hasBrowser }) {
  return React.cloneElement(SectionBlock(t("settings.agentProxy"), t("settings.agentProxyHint"),
      FieldRow(t("settings.agentProxyEnabled"), t("settings.agentProxyEnabledHint"),
        Toggle(agentProxyEnabled, function () { saveAgentProxy(!agentProxyEnabled, agentProxyAddress); }, false, t("settings.agentProxyEnabled")),
      ),
      FieldRow(t("settings.agentProxyAddress"), t("settings.agentProxyAddressHint"),
        React.createElement("div", { className: "wb-inline-row" },
          React.createElement("input", {
            className: "wb-input wb-proxy-address-input",
            type: "text",
            inputMode: "url",
            value: agentProxyAddress,
            placeholder: "http://proxy.example.com:7897",
            autoCapitalize: "none",
            autoCorrect: "off",
            spellCheck: false,
            "aria-label": t("settings.agentProxyAddress"),
            onChange: function (event) { setAgentProxyAddress(event.target.value); setAgentProxyStatus(""); },
            onBlur: function () { saveAgentProxy(agentProxyEnabled, agentProxyAddress); },
            onKeyDown: function (event) { if (event.key === "Enter") { event.preventDefault(); saveAgentProxy(agentProxyEnabled, agentProxyAddress); } },
          }),
        ),
        agentProxyStatus && React.createElement("span", { className: "wb-hint saved", role: "status", "aria-live": "polite" }, agentProxyStatus),
      ),
      FieldRow(t("settings.proxyExternalAgents"), t("settings.proxyExternalAgentsHint"),
        React.createElement("span", { className: "wb-proxy-scope-status" }, agentProxyEnabled ? t("settings.proxyScopeActive") : t("settings.proxyScopeWaiting")),
      ),
      hasSearch && FieldRow(t("settings.proxySearch"), t("settings.proxySearchHint"),
        Toggle(proxySearchEnabled, function () { saveAgentProxy(agentProxyEnabled, agentProxyAddress, { search: !proxySearchEnabled }); }, !agentProxyEnabled, t("settings.proxySearch")),
      ),
      hasBrowser && FieldRow(t("settings.proxyBrowser"), t("settings.proxyBrowserHint"),
        Toggle(proxyBrowserEnabled, function () { saveAgentProxy(agentProxyEnabled, agentProxyAddress, { browser: !proxyBrowserEnabled }); }, !agentProxyEnabled, t("settings.proxyBrowser")),
      ),
      FieldRow(t("settings.proxyUpdates"), t("settings.proxyUpdatesHint"),
        Toggle(proxyUpdatesEnabled, function () { saveAgentProxy(agentProxyEnabled, agentProxyAddress, { updates: !proxyUpdatesEnabled }); }, !agentProxyEnabled, t("settings.proxyUpdates")),
      ),
      FieldRow(t("settings.proxyExtensions"), t("settings.proxyExtensionsHint"),
        Toggle(proxyExtensionsEnabled, function () { saveAgentProxy(agentProxyEnabled, agentProxyAddress, { extensions: !proxyExtensionsEnabled }); }, !agentProxyEnabled, t("settings.proxyExtensions")),
      ),
    ), { className: "wb-section-block wb-agent-proxy-settings", id: "setting-agent-proxy" });
}
