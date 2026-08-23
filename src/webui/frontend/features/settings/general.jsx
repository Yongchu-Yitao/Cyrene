import {
  workbenchServices,
  useStateSt,
  useEffectSt,
  readSettingsResponse,
  settingsFetch,
  showSettingsToast,
  SectionTitle,
  SectionBlock,
  FieldRow,
  Toggle,
} from "./shared.jsx"

// ── General Panel ──
var GENERAL_TIMEZONE_OPTIONS = [
  "Pacific/Honolulu", "America/Los_Angeles", "America/Denver",
  "America/Chicago", "America/New_York", "America/Sao_Paulo",
  "UTC", "Europe/London", "Europe/Paris", "Africa/Cairo",
  "Asia/Dubai", "Asia/Kolkata", "Asia/Bangkok", "Asia/Shanghai",
  "Asia/Tokyo", "Australia/Sydney", "Pacific/Auckland",
];

function OfficeIntegrationSection(p) {
  var t = p.t;
  var [status, setStatus] = useStateSt(null);
  var [busy, setBusy] = useStateSt("");

  useEffectSt(function () {
    var cancelled = false;
    settingsFetch("/api/settings/integrations/office").then(readSettingsResponse).then(function (payload) {
      if (!cancelled) setStatus(payload);
    }).catch(function () {
      if (!cancelled) setStatus({ load_error: true });
    });
    var unsubscribe = window.CyreneUI.has("events")
      ? workbenchServices.events().subscribe(function (event) {
        if (!event || event.type !== "office_session_update") return;
        var sessions = Array.isArray(event.sessions) ? event.sessions : [];
        setStatus(function (current) {
          if (!current) return current;
          return {
            ...current,
            addin_installed: current.addin_installed || sessions.length > 0,
            connected_presentations: sessions.length,
            sessions: sessions,
          };
        });
      })
      : null;
    return function () {
      cancelled = true;
      if (unsubscribe) unsubscribe();
    };
  }, []);

  function revealManifest(currentStatus) {
    if (currentStatus && currentStatus.manifest_path && window.cyrene && typeof window.cyrene.showItemInFolder === "function") {
      window.cyrene.showItemInFolder(currentStatus.manifest_path).catch(function (error) {
        showSettingsToast(t("settings.error") + ": " + (error.message || ""), "error");
      });
      return;
    }
    settingsFetch("/api/settings/integrations/office/manifest").then(function (response) {
      if (!response.ok) return readSettingsResponse(response);
      return response.blob();
    }).then(function (blob) {
      if (!blob || typeof blob.size !== "number") return;
      var url = URL.createObjectURL(blob);
      var link = document.createElement("a");
      link.href = url;
      link.download = "cyrene-powerpoint-addin.xml";
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    }).catch(function (error) {
      showSettingsToast(t("settings.error") + ": " + (error.message || ""), "error");
    });
  }

  function install() {
    setBusy("install");
    settingsFetch("/api/settings/integrations/office/install", { method: "POST" }).then(readSettingsResponse).then(function (payload) {
      setStatus(payload);
      if (payload.message_code === "prepared_manual") {
        showSettingsToast(t("settings.officeWindowsPrepared"), "info");
        revealManifest(payload);
      } else {
        showSettingsToast(t("settings.officeInstalledRestart"), "success");
      }
    }).catch(function (error) {
      showSettingsToast(t("settings.officeInstallFailed") + ": " + (error.message || ""), "error");
    }).finally(function () { setBusy(""); });
  }

  return React.createElement("section", { className: "wb-section-block wb-office-integration", id: "setting-office-powerpoint" },
    React.createElement("div", { className: "wb-section-block-head wb-office-integration-head" },
      React.createElement("div", { className: "wb-office-integration-copy" },
        React.createElement("b", null, t("settings.officePowerPointIntegration")),
        React.createElement("small", null, t(status && status.addin_installed ? "settings.officeInstalledUsageHint" : "settings.officePowerPointIntegrationHint"))),
      React.createElement("button", { className: "wb-btn primary wb-office-install-button", disabled: !!busy || status === null, onClick: install },
        busy === "install" || status === null
          ? t(busy === "install" ? "settings.officeInstalling" : "settings.loading")
          : t(status && status.addin_installed ? "settings.officeReinstall" : "settings.officeInstall"))));
}

function GeneralPanel(p) {
  var { t, lang, setLang, desktopNotifications, toggleDesktopNotifications, mapProvider, setMapProvider, amapKey, setAmapKey, amapKeySaved, setAmapKeySaved } = p;
  var timezoneOptions = GENERAL_TIMEZONE_OPTIONS;
  var [selectedTimezone, setSelectedTimezone] = useStateSt(function () {
    try {
      var stored = localStorage.getItem("cyrene-timezone") || "";
      return timezoneOptions.indexOf(stored) >= 0 ? stored : "Asia/Shanghai";
    } catch (e) {
      return "Asia/Shanghai";
    }
  });

  // Desktop-only (Electron) toggles. Quick chat depends on background residency,
  // so its toggle is gated on runInBackground.
  var supportsDesktop = !!(
    window.cyrene
    && typeof window.cyrene.getDesktopSettings === "function"
    && typeof window.cyrene.updateDesktopSettings === "function"
  );
  var [runInBackground, setRunInBackground] = useStateSt(false);
  var [quickChatEnabled, setQuickChatEnabled] = useStateSt(false);
  var [desktopBusy, setDesktopBusy] = useStateSt(false);
  var [desktopNotice, setDesktopNotice] = useStateSt("");
  var [zoteroSettings, setZoteroSettings] = useStateSt({
    base_url: "http://127.0.0.1:23119/api", auto_sync: false, copy_attachments: true,
  });
  var [zoteroStatus, setZoteroStatus] = useStateSt(null);
  var [integrationBusy, setIntegrationBusy] = useStateSt("");
  var [agentProxyEnabled, setAgentProxyEnabled] = useStateSt(false);
  var [agentProxyPort, setAgentProxyPort] = useStateSt("7897");
  var [agentProxyStatus, setAgentProxyStatus] = useStateSt("");
  useEffectSt(function () {
    var cancelled = false;
    settingsFetch("/api/settings/config").then(readSettingsResponse).then(function (payload) {
      if (cancelled) return;
      var savedTimezone = String(payload.timezone || "");
      setAgentProxyEnabled(payload.external_agent_proxy_enabled === true);
      setAgentProxyPort(String(payload.external_agent_proxy_port || 7897));
      if (timezoneOptions.indexOf(savedTimezone) < 0) return;
      var previousTimezone = "";
      try { previousTimezone = localStorage.getItem("cyrene-timezone") || ""; } catch (e) {}
      setSelectedTimezone(savedTimezone);
      try { localStorage.setItem("cyrene-timezone", savedTimezone); } catch (e) {}
      if (previousTimezone && previousTimezone !== savedTimezone) {
        try { workbenchServices.data().reload(); } catch (e) {}
      }
    }).catch(function () {});
    return function () { cancelled = true; };
  }, []);

  useEffectSt(function () {
    var cancelled = false;
    settingsFetch("/api/settings/integrations").then(readSettingsResponse).then(function (payload) {
      if (cancelled) return;
      if (payload.zotero) setZoteroSettings(payload.zotero);
    }).catch(function () {
      if (!cancelled) setZoteroStatus({ kind: "error", text: t("settings.integrationLoadFailed") });
    });
    return function () { cancelled = true; };
  }, []);

  useEffectSt(function () {
    if (!supportsDesktop) return undefined;
    var cancelled = false;
    window.cyrene.getDesktopSettings().then(function (s) {
      if (cancelled || !s) return;
      setRunInBackground(s.runInBackground === true);
      setQuickChatEnabled(s.quickChatEnabled === true);
      if ((s.language === "en" || s.language === "zh") && s.language !== lang) {
        setLang(s.language);
      } else if (!s.language) {
        window.cyrene.updateDesktopSettings({ language: lang }).catch(function () {});
      }
    }).catch(function () {});
    return function () { cancelled = true; };
  }, []);

  function applyDesktop(updates) {
    setDesktopBusy(true);
    setDesktopNotice("");
    window.cyrene.updateDesktopSettings(updates).then(function (s) {
      if (!s) return;
      setRunInBackground(s.runInBackground === true);
      setQuickChatEnabled(s.quickChatEnabled === true);
      if (s.shortcutUpdateOk === false) showSettingsToast(t("settings.quickChatShortcutConflict"), "error");
    }).catch(function (error) {
      showSettingsToast(t("settings.error") + ": " + (error.message || ""), "error");
    }).finally(function () { setDesktopBusy(false); });
  }

  function timezoneOptionLabel(timezone) {
    try {
      var part = new Intl.DateTimeFormat("en", {
        timeZone: timezone,
        timeZoneName: "longOffset",
      }).formatToParts(new Date()).find(function (item) { return item.type === "timeZoneName"; });
      var offset = part && part.value ? part.value.replace("GMT", "UTC") : "UTC";
      return "(" + offset + ") " + timezone;
    } catch (e) {
      return timezone;
    }
  }

  function changeTimezone(event) {
    var nextTimezone = event.target.value;
    if (timezoneOptions.indexOf(nextTimezone) < 0) return;
    var previousTimezone = selectedTimezone;
    setSelectedTimezone(nextTimezone);
    try { localStorage.setItem("cyrene-timezone", nextTimezone); } catch (e) {}
    try { workbenchServices.data().reload(); } catch (e) {}
    settingsFetch("/api/settings/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ timezone: nextTimezone }),
    }).catch(function () {
      setSelectedTimezone(previousTimezone);
      try { localStorage.setItem("cyrene-timezone", previousTimezone); } catch (e) {}
      try { workbenchServices.data().reload(); } catch (e) {}
    });
  }

  function saveAgentProxy(nextEnabled, nextPort) {
    var port = Number(nextPort);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      setAgentProxyStatus(t("settings.agentProxyPortInvalid"));
      return;
    }
    setAgentProxyStatus(t("settings.saving"));
    settingsFetch("/api/settings/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        external_agent_proxy_enabled: !!nextEnabled,
        external_agent_proxy_port: port,
      }),
    }).then(readSettingsResponse).then(function () {
      setAgentProxyEnabled(!!nextEnabled);
      setAgentProxyPort(String(port));
      setAgentProxyStatus("");
      showSettingsToast(t("settings.agentProxySaved"), "success");
    }).catch(function (error) {
      setAgentProxyStatus("");
      showSettingsToast(t("settings.error") + ": " + (error.message || ""), "error");
    });
  }

  function saveAmapKey() {
    if (!amapKey || amapKey.startsWith("••")) { showSettingsToast(t("settings.noChanges"), "info"); return; }
    setAmapKeySaved(t("settings.saving"));
    settingsFetch("/api/settings/keys", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ AMAP_API_KEY: amapKey }) })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function () {
        settingsFetch("/api/amap/verify").then(function (r) { return r.json(); }).then(function (vd) {
          if (vd.valid) { setAmapKeySaved(""); showSettingsToast(t("settings.amapKeySaved"), "success"); localStorage.setItem("cyrene-tweak-map-provider", "amap"); }
          else { setAmapKeySaved(""); showSettingsToast(t("settings.amapKeyVerifyFail") + " " + (vd.error || ""), "error"); }
        }).catch(function () { setAmapKeySaved(""); showSettingsToast(t("settings.saved"), "success"); });
      }).catch(function (error) { setAmapKeySaved(""); showSettingsToast(t("settings.error") + ": " + (error.message || ""), "error"); });
  }

  function saveIntegration() {
    setIntegrationBusy("save-zotero");
    setZoteroStatus({ kind: "info", text: t("settings.saving") });
    settingsFetch("/api/settings/integrations", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ zotero: zoteroSettings }),
    }).then(readSettingsResponse).then(function (payload) {
      if (payload.zotero) setZoteroSettings(payload.zotero);
      setZoteroStatus(null);
      showSettingsToast(t("settings.saved"), "success");
    }).catch(function (error) {
      setZoteroStatus(null);
      showSettingsToast(t("settings.error") + ": " + (error.message || ""), "error");
    }).finally(function () { setIntegrationBusy(""); });
  }

  function testIntegration() {
    setIntegrationBusy("test-zotero");
    setZoteroStatus(null);
    settingsFetch("/api/settings/integrations/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ service: "zotero", config: zoteroSettings }),
    }).then(readSettingsResponse).then(function (payload) {
      showSettingsToast(t("settings.zoteroConnected"), "success");
    }).catch(function (error) {
      showSettingsToast(t("settings.connectionFailed") + ": " + (error.message || ""), "error");
    }).finally(function () { setIntegrationBusy(""); });
  }

  function importFromZotero() {
    if (!(p.project && p.project.id)) {
      showSettingsToast(t("settings.zoteroImportNoProject"), "error");
      return;
    }
    setIntegrationBusy("import-zotero");
    setZoteroStatus(null);
    settingsFetch("/api/settings/integrations", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ zotero: zoteroSettings }),
    }).then(readSettingsResponse).then(function (payload) {
      if (payload.zotero) setZoteroSettings(payload.zotero);
      return settingsFetch("/api/workbench/library/zotero/sync?workspace=" + encodeURIComponent(String(p.project.id)), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ library_id: "0", library_type: "user", collection_key: "" }),
      });
    }).then(readSettingsResponse).then(function (result) {
      showSettingsToast(t("settings.zoteroImportDone", {
        created: Number(result.created || result.imported || 0),
        updated: Number(result.updated || 0),
      }), "success");
    }).catch(function (error) {
      showSettingsToast(t("settings.connectionFailed") + ": " + (error.message || ""), "error");
    }).finally(function () { setIntegrationBusy(""); });
  }

  function integrationStatus(status) {
    if (!status) return null;
    return React.createElement("div", {
      className: "wb-integration-status " + status.kind,
      role: status.kind === "error" ? "alert" : "status",
      "aria-live": "polite",
    }, status.text);
  }

  return React.createElement("div", { className: "settings-panel wb-general-settings" },
    SectionTitle(t(p.integrationsOnly ? "settings.integrations" : "settings.general")),
    !p.integrationsOnly && FieldRow(t("settings.language"), t("settings.languageHint"),
      React.createElement("div", { className: "wb-seg" },
        React.createElement("button", { className: "wb-seg-btn" + (lang === "en" ? " active" : ""), onClick: function () { setLang("en"); } }, "English"),
        React.createElement("button", { className: "wb-seg-btn" + (lang === "zh" ? " active" : ""), onClick: function () { setLang("zh"); } }, "中文"),
      ),
      undefined, "setting-language",
    ),
    !p.integrationsOnly && FieldRow(t("settings.timezone"), t("settings.timezoneHint"),
      React.createElement("select", {
        className: "wb-select",
        value: selectedTimezone,
        "aria-label": t("settings.timezone"),
        onChange: changeTimezone,
      },
        timezoneOptions.map(function (timezone) {
          return React.createElement("option", { key: timezone, value: timezone }, timezoneOptionLabel(timezone));
        }),
      ),
      undefined, "setting-timezone",
    ),
    !p.integrationsOnly && FieldRow(t("settings.desktopNotifications"), t("settings.desktopNotificationsHint"),
      Toggle(desktopNotifications, toggleDesktopNotifications),
      undefined, "setting-desktop-notifications",
    ),
    !p.integrationsOnly && React.cloneElement(SectionBlock(t("settings.agentProxy"), t("settings.agentProxyHint"),
      FieldRow(t("settings.agentProxyEnabled"), t("settings.agentProxyEnabledHint"),
        Toggle(agentProxyEnabled, function () { saveAgentProxy(!agentProxyEnabled, agentProxyPort); }, false, t("settings.agentProxyEnabled")),
      ),
      FieldRow(t("settings.agentProxyPort"), t("settings.agentProxyPortHint"),
        React.createElement("div", { className: "wb-inline-row" },
          React.createElement("input", {
            className: "wb-input",
            type: "number",
            min: "1",
            max: "65535",
            inputMode: "numeric",
            value: agentProxyPort,
            disabled: !agentProxyEnabled,
            "aria-label": t("settings.agentProxyPort"),
            onChange: function (event) { setAgentProxyPort(event.target.value); setAgentProxyStatus(""); },
            onBlur: function () { if (agentProxyEnabled) saveAgentProxy(true, agentProxyPort); },
            onKeyDown: function (event) { if (event.key === "Enter" && agentProxyEnabled) { event.preventDefault(); saveAgentProxy(true, agentProxyPort); } },
          }),
          React.createElement("span", { className: "wb-hint" }, "127.0.0.1:" + (agentProxyPort || "—")),
        ),
        agentProxyStatus && React.createElement("span", { className: "wb-hint saved", role: "status", "aria-live": "polite" }, agentProxyStatus),
      ),
    ), { className: "wb-section-block wb-agent-proxy-settings", id: "setting-agent-proxy" }),
    !p.integrationsOnly && FieldRow(t("settings.mapProvider"), t("settings.mapProviderHint"),
      React.createElement("div", { className: "wb-seg" },
        React.createElement("button", { className: "wb-seg-btn" + (mapProvider === "direct" ? " active" : ""), onClick: function () { setMapProvider("direct"); localStorage.setItem("cyrene-tweak-map-provider", "direct"); } }, t("settings.mapProviderDirect")),
        React.createElement("button", { className: "wb-seg-btn" + (mapProvider === "amap" ? " active" : ""), onClick: function () { setMapProvider("amap"); } }, t("settings.mapProviderAmap")),
      ),
      undefined, "setting-map-provider",
    ),
    !p.integrationsOnly && mapProvider === "amap" && FieldRow(t("settings.amapKey"), t("settings.amapKeyHint"),
      [
        React.createElement("div", { className: "wb-inline-row" },
          React.createElement("input", { className: "wb-input mono", type: "password", value: amapKey, onChange: function (e) { setAmapKey(e.target.value); }, placeholder: t("settings.amapKeyPlaceholder") }),
          React.createElement("button", { className: "wb-btn primary", onClick: saveAmapKey }, t("settings.save")),
        ),
        amapKeySaved && React.createElement("span", { className: "wb-hint saved" }, amapKeySaved),
      ],
      undefined, "setting-amap-key",
    ),
    !p.integrationsOnly && supportsDesktop && FieldRow(t("settings.runInBackground"), t("settings.runInBackgroundHint"),
      Toggle(runInBackground, function () { applyDesktop({ runInBackground: !runInBackground }); }, desktopBusy),
      undefined, "setting-run-in-background",
    ),
    !p.integrationsOnly && supportsDesktop && FieldRow(t("settings.quickChatAssistant"),
      runInBackground ? t("settings.quickChatAssistantHint") : t("settings.quickChatAssistantNeedsResident"),
      Toggle(quickChatEnabled, function () { applyDesktop({ quickChatEnabled: !quickChatEnabled }); }, desktopBusy || !runInBackground),
      undefined, "setting-quick-chat",
    ),
    !p.integrationsOnly && supportsDesktop && desktopNotice
      && React.createElement("div", { className: "wb-hint", style: { color: "var(--wb-error-text)" } }, desktopNotice),
    p.integrationsOnly && React.createElement(OfficeIntegrationSection, { t: t }),
    p.integrationsOnly && React.cloneElement(SectionBlock(t("settings.zoteroIntegration"), t("settings.zoteroIntegrationHint"),
      FieldRow(t("settings.zoteroLocalApiUrl"), t("settings.zoteroLocalApiUrlHint"),
        React.createElement("div", { className: "wb-integration-control" },
          React.createElement("input", {
            className: "wb-input mono", type: "url", value: zoteroSettings.base_url,
            "aria-label": t("settings.zoteroLocalApiUrl"),
            onChange: function (e) { setZoteroSettings({ ...zoteroSettings, base_url: e.target.value }); },
          }),
        ),
      ),
      FieldRow(t("settings.zoteroAutoSync"), t("settings.zoteroAutoSyncHint"),
        Toggle(zoteroSettings.auto_sync, function () { setZoteroSettings({ ...zoteroSettings, auto_sync: !zoteroSettings.auto_sync }); }, false, t("settings.zoteroAutoSync")),
      ),
      FieldRow(t("settings.zoteroCopyAttachments"), t("settings.zoteroCopyAttachmentsHint"),
        Toggle(zoteroSettings.copy_attachments, function () { setZoteroSettings({ ...zoteroSettings, copy_attachments: !zoteroSettings.copy_attachments }); }, false, t("settings.zoteroCopyAttachments")),
      ),
      FieldRow(
        t("settings.zoteroImport"),
        t("settings.zoteroImportHint", { project: (p.project && p.project.name) || t("settings.zoteroImportNoProjectLabel") }),
        React.createElement("button", {
          className: "wb-btn primary",
          disabled: !!integrationBusy || !(p.project && p.project.id),
          onClick: importFromZotero,
        }, integrationBusy === "import-zotero" ? t("settings.zoteroImporting") : t("settings.zoteroImportAction")),
      ),
      React.createElement("div", { className: "wb-integration-footer" },
        integrationStatus(zoteroStatus),
        React.createElement("div", { className: "wb-integration-actions" },
          React.createElement("button", {
            className: "wb-btn", disabled: !!integrationBusy,
            onClick: testIntegration,
          }, integrationBusy === "test-zotero" ? t("settings.testingConnection") : t("settings.testConnection")),
          React.createElement("button", {
            className: "wb-btn", disabled: !!integrationBusy,
            onClick: saveIntegration,
          }, integrationBusy === "save-zotero" ? t("settings.saving") : t("settings.save")),
        ),
      ),
    ), { id: "setting-zotero" }),
  );
}

export { GeneralPanel };
