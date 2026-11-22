import {
  PageDescription,
  workbenchServices,
  useStateSt,
  useEffectSt,
  readSettingsResponse,
  settingsFetch,
  showSettingsToast,
  SectionBlock,
  FieldRow,
  Toggle,
} from "./shared.jsx"

import {ChoiceCards} from "./choice-cards.jsx"

// ── Data Panel ──
var STORAGE_LABEL = {
  database: "settings.storageDatabase",
  knowledge: "settings.storageKnowledge",
  memory: "settings.storageMemory",
  conversations: "settings.storageConversations",
  plans: "settings.storagePlans",
  projects: "settings.storageProjects",
  sessions: "settings.storageSessions",
  inbox: "settings.storageInbox",
  skills: "settings.storageSkills",
  attachments: "settings.storageAttachments",
  backups: "settings.storageBackups",
  local_models: "settings.storageLocalModels",
  codex_cli: "settings.storageCodexCli",
  opencv_runtime: "settings.storageOpencvRuntime",
  browser: "settings.storageBrowser",
  caches: "settings.storageCaches",
  extensions: "settings.storageExtensions",
  media: "settings.storageMedia",
  maps: "settings.storageMaps",
};

var STORAGE_COLORS = {
  database: "#3b82f6",
  knowledge: "#a855f7",
  memory: "#d946ef",
  conversations: "#22c55e",
  plans: "#f59e0b",
  projects: "#06b6d4",
  sessions: "#ec4899",
  inbox: "#84cc16",
  skills: "#8b5cf6",
  attachments: "#f97316",
  backups: "#64748b",
  local_models: "#14b8a6",
  codex_cli: "#0ea5e9",
  opencv_runtime: "#eab308",
  maps: "#38bdf8",
  browser: "#6366f1",
  caches: "#78716c",
  extensions: "#7c3aed",
  media: "#fb7185",
};

// DataPanel is intentionally remounted when the user switches settings tabs.
// Keep the last successful scan outside the component so returning to Data can
// paint the table immediately while a stale snapshot refreshes in the
// background. A shared promise also prevents rapid tab changes from starting
// overlapping disk scans.
var DATA_PANEL_STORAGE_TTL_MS = 30000;
var DATA_PANEL_STORAGE_CACHE_KEY = "cyrene.settings.storageSnapshot.v1";

function isDataPanelStorageSnapshot(payload) {
  return !!(payload
    && typeof payload.total === "number"
    && Array.isArray(payload.categories)
    && payload.categories.every(function (category) {
      return category
        && typeof category.key === "string"
        && typeof category.bytes === "number"
        && typeof category.files === "number";
    }));
}

function readDataPanelStorageCache() {
  try {
    var cached = JSON.parse(localStorage.getItem(DATA_PANEL_STORAGE_CACHE_KEY) || "null");
    if (!cached || !isDataPanelStorageSnapshot(cached.payload)) return null;
    return {
      payload: cached.payload,
      cachedAt: Number(cached.cachedAt) || 0,
    };
  } catch (e) {
    return null;
  }
}

function persistDataPanelStorageCache(payload, cachedAt) {
  try {
    localStorage.setItem(DATA_PANEL_STORAGE_CACHE_KEY, JSON.stringify({
      payload: payload,
      cachedAt: cachedAt,
    }));
  } catch (e) {}
}

var persistedDataPanelStorage = readDataPanelStorageCache();
var dataPanelStorageCache = persistedDataPanelStorage ? persistedDataPanelStorage.payload : null;
var dataPanelStorageCachedAt = persistedDataPanelStorage ? persistedDataPanelStorage.cachedAt : 0;
var dataPanelStorageRequest = null;

function requestDataPanelStorage() {
  var cacheIsFresh = dataPanelStorageCache
    && Date.now() - dataPanelStorageCachedAt < DATA_PANEL_STORAGE_TTL_MS;
  if (cacheIsFresh) return Promise.resolve(dataPanelStorageCache);
  if (dataPanelStorageRequest) return dataPanelStorageRequest;
  dataPanelStorageRequest = settingsFetch("/api/settings/storage")
    .then(function (response) { return response.json(); })
    .then(function (payload) {
      if (!isDataPanelStorageSnapshot(payload)) {
        throw new Error(workbenchServices.i18n().t("settings.invalidStorageSnapshot"));
      }
      dataPanelStorageCache = payload;
      dataPanelStorageCachedAt = Date.now();
      persistDataPanelStorageCache(payload, dataPanelStorageCachedAt);
      return payload;
    })
    .finally(function () {
      dataPanelStorageRequest = null;
    });
  return dataPanelStorageRequest;
}

function SessionExportIcon() {
  return React.createElement("svg", {
    width: "16",
    height: "16",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": "true",
  },
    React.createElement("path", { d: "M12 3v12" }),
    React.createElement("path", { d: "m7 10 5 5 5-5" }),
    React.createElement("path", { d: "M5 21h14" }),
  );
}

function DataPanel(p) {
  var { t, redactSecrets, saveRedactSecrets, config, configLoading, resetStatus, setResetStatus, resetting, setResetting, backupList, exportSids, setExportSids, exportFmt, setExportFmt, exportMsg, setExportMsg, formatBytes, formatDate } = p;

  var [storage, setStorage] = useStateSt(dataPanelStorageCache);
  var [backupBusy, setBackupBusy] = useStateSt(false);
  var [storageError, setStorageError] = useStateSt("");

  useEffectSt(function () {
    var mounted = true;
    setStorageError("");
    requestDataPanelStorage().then(function (payload) {
      if (mounted) setStorage(payload);
    }).catch(function (e) {
      // A stale snapshot remains useful. Do not replace or visually disturb it
      // just because a background refresh failed.
      if (mounted && !dataPanelStorageCache) setStorageError(e.message || String(e));
    });
    return function () { mounted = false; };
  }, []);

  var storageList = (storage ? storage.categories : []).slice().sort(function (a, b) { return b.bytes - a.bytes; });
  var storageNonEmpty = storageList.filter(function (c) { return c.bytes > 0; });

  var dataStore = workbenchServices.data();
  dataStore.useVersion();
  var dataState = dataStore.state;
  useEffectSt(function () {
    dataStore.refreshSessions();
  }, []);
  var exportSessions = (dataState.sessions || []).filter(function (session) {
    return !!String(session && session.id || "");
  });

  function clearSession() {
    var session = exportSessions[0];
    if (!session) return;
    settingsFetch("/api/workbench/sessions/" + encodeURIComponent(session.id) + "/clear", { method: "POST" })
      .then(readSettingsResponse)
      .then(function () {
        setExportSids(exportSids.filter(function (id) { return id !== session.id; }));
        return dataStore.refreshSessions();
      })
      .catch(function (error) {
        showSettingsToast(t("settings.error") + ": " + (error.message || String(error)), "error");
      });
  }

  function resetData() {
    var title = t("settings.resetConfirmTitle");
    var body = t("settings.resetConfirmBody");
    var feedback = workbenchServices.feedback();
    var confirmed = feedback && typeof feedback.confirmModal === "function"
      ? feedback.confirmModal({
        title: title,
        body: body,
        confirmLabel: t("settings.resetConfirmAction"),
        danger: true,
      })
      : Promise.resolve(window.confirm([title, "", body].join("\n")));
    confirmed.then(function (ok) {
      if (!ok) return;
      setResetting(true);
      setResetStatus("");
      return settingsFetch("/api/settings/reset-data", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation: "RESET CYRENE DATA" }),
      }).then(function (r) { return r.json(); }).then(function (payload) {
        if (!payload.ok) throw new Error(payload.detail || payload.error || t("settings.resetAppDataFailed"));
        try { localStorage.clear(); } catch (e) {}
        try { sessionStorage.clear(); } catch (e) {}
        window.location.reload();
      });
    }).catch(function (e) {
      showSettingsToast(t("settings.resetAppDataFailed") + ": " + (e.message || String(e)), "error");
      setResetting(false);
    });
  }

  async function resetAssistant() {
    if (resetting) return;
    setResetting(true);
    try {
      var feedback = workbenchServices.feedback();
      var title = t("settings.resetAssistant");
      var body = t("settings.resetAssistantConfirm");
      var confirmed = feedback && typeof feedback.confirmModal === "function"
        ? await feedback.confirmModal({ title: title, body: body, confirmLabel: title, danger: true })
        : window.confirm([title, "", body].join("\n"));
      if (!confirmed) return;
      await settingsFetch("/api/workbench/assistant/reset", {
        method: "POST",
      }).then(readSettingsResponse);
      window.location.reload();
    } catch (error) {
      showSettingsToast(t("settings.resetAssistantFailed") + ": " + (error.message || String(error)), "error");
    } finally {
      setResetting(false);
    }
  }

  async function runBackup(action, path) {
    if (backupBusy) return;
    setBackupBusy(true);
    try {
      var actions = await import("./backup-actions.jsx");
      await actions[action](p, path);
    } catch (error) {
      showSettingsToast(t("settings.failed") + ": " + error.message, "error");
    } finally {
      setBackupBusy(false);
    }
  }

  function toggleExportSession(sessionId) {
    var id = String(sessionId || "");
    setExportSids(exportSids.indexOf(id) >= 0
      ? exportSids.filter(function (value) { return value !== id; })
      : exportSids.concat(id));
    setExportMsg("");
  }

  function exportSelectedSessions() {
    if (!exportSids.length) return;
    exportSids.forEach(function (sessionId) {
      var url = "/api/workbench/sessions/" + encodeURIComponent(sessionId) + "/export?format=" + exportFmt;
      var a = document.createElement("a");
      a.href = url;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    });
    setExportMsg("");
    showSettingsToast(t("settings.sessionExportStarted", { n: exportSids.length }), "success");
  }

  return React.createElement("div", { className: "settings-panel" },
    PageDescription(t("settings.dataSubtitle")),

    // Storage usage
    React.cloneElement(SectionBlock(t("settings.storageUsage"), t("settings.storageUsageHint"),
      React.createElement("div", { className: "wb-inline-row" },
        React.createElement("b", { className: "mono" }, storage ? formatBytes(storage.total) : t("settings.storageLoading")),
      ),
      storageError
        ? React.createElement("p", { className: "wb-hint" }, t("settings.storageError") + ": " + storageError)
        : null,
      storage && React.createElement("div", { className: "wb-storage" },
            storage.total > 0 && React.createElement("div", { className: "wb-storage-bar", role: "img", "aria-label": t("settings.storageUsage") },
              storageNonEmpty.map(function (c) {
                return React.createElement("span", {
                  key: c.key,
                  className: "wb-storage-seg",
                  style: { width: (c.bytes / storage.total * 100) + "%", background: STORAGE_COLORS[c.key] },
                  title: t(STORAGE_LABEL[c.key] || c.key) + ": " + formatBytes(c.bytes) + " · " + t("settings.storageFiles", { n: c.files }),
                });
              }),
            ),
            React.createElement("div", { className: "wb-storage-legend" },
              storageList.map(function (c) {
                return React.createElement("div", { className: "wb-storage-legend-row" + (c.bytes === 0 ? " empty" : ""), key: c.key },
                  React.createElement("span", { className: "wb-storage-swatch", style: { background: STORAGE_COLORS[c.key] } }),
                  React.createElement("span", { className: "wb-storage-legend-name" }, t(STORAGE_LABEL[c.key] || c.key)),
                  React.createElement("b", { className: "mono" }, formatBytes(c.bytes)),
                );
              }),
            ),
          ),
      storage && storage.truncated ? React.createElement("p", { className: "wb-hint" }, t("settings.storageTruncated")) : null,
    ), { id: "setting-storage" }),

    FieldRow(t("settings.redactSecrets"), t("settings.redactSecretsHint"), Toggle(redactSecrets, function () { saveRedactSecrets(!redactSecrets); }),
      undefined, "setting-redact-secrets"),
    FieldRow(t("settings.clearSession"), t("settings.clearSessionHint"),
      React.createElement("button", { className: "wb-btn muted", onClick: clearSession, disabled: !exportSessions.length }, t("settings.clearSessionBtn")),
      undefined, "setting-clear-session",
    ),
    FieldRow(t("settings.resetAssistant"), t("settings.resetAssistantHint"),
      React.createElement("button", { className: "wb-btn danger", "data-cyrene-risk": "R3", onClick: resetAssistant, disabled: resetting }, t("settings.resetAssistant")),
      undefined, "setting-reset-assistant",
    ),
    React.createElement("div", { className: "wb-field wb-field-stack wb-field-danger", id: "setting-reset-app-data" },
      React.createElement("div", { className: "wb-label" },
        t("settings.resetAppData"),
        React.createElement("small", null, t("settings.resetAppDataHint")),
      ),
      React.createElement("div", { className: "wb-controls" },
        React.createElement("div", { className: "wb-inline-row wb-inline-row-start" },
          React.createElement("button", { className: "wb-btn danger", onClick: resetData, disabled: resetting }, resetting ? t("settings.resettingData") : t("settings.resetAppDataBtn")),
        ),
      ),
    ),

    // Path info
    React.cloneElement(SectionBlock(t("settings.pathInfo"), null,
      FieldRow(t("settings.baseDir"), null, React.createElement("input", { className: "wb-input mono wb-path-display", value: configLoading ? t("settings.pathLoading") : config.base_dir, readOnly: true })),
      FieldRow(t("settings.dataDir"), null, React.createElement("input", { className: "wb-input mono wb-path-display", value: configLoading ? t("settings.pathLoading") : config.data_dir, readOnly: true })),
      FieldRow(t("settings.workspaceDir"), null, React.createElement("input", { className: "wb-input mono wb-path-display", value: configLoading ? t("settings.pathLoading") : config.workspace_dir, readOnly: true })),
      FieldRow(t("settings.soulPath"), null, React.createElement("input", { className: "wb-input mono wb-path-display", value: configLoading ? t("settings.pathLoading") : config.soul_path, readOnly: true })),
    ), { id: "setting-paths" }),

    // Backup
    React.cloneElement(SectionBlock(t("settings.backup"),
      <div className="wb-inline-row">
        <button type="button" className="wb-btn primary" disabled={backupBusy} onClick={() => runBackup("createBackup")}>{t("settings.backupExportBtn")}…</button>
        <button type="button" className="wb-btn" disabled={backupBusy} data-cyrene-risk="R3" onClick={() => runBackup("restoreBackup")}>{t("settings.backupFromFile")}</button>
      </div>,
      <div className="wb-export-session-toolbar"><b>{t("settings.backupLocal")}</b></div>,
      <div className="wb-export-area">
        {backupList.length ? backupList.map(b => FieldRow(
          b.name,
          formatDate(b.modified) + " · " + formatBytes(b.size),
          <button type="button" className="wb-btn" disabled={backupBusy} data-cyrene-risk="R3" onClick={() => runBackup("restoreBackup", b.path)}>{t("settings.backupRestoreBtn")}…</button>,
          b.name,
        )) : <p className="wb-hint">{t("settings.backupNoBackups")}</p>}
      </div>,
    ), { id: "setting-backup" }),

    // Session export
    React.cloneElement(SectionBlock(t("settings.sessionExport"), t("settings.sessionExportHint"),
      exportSessions.length > 0 ? React.createElement("div", { className: "wb-export-area" },
        React.createElement("div", { className: "wb-export-session-toolbar" },
          React.createElement("b", null, t("settings.sessionExportSelected", { n: exportSids.length })),
          React.createElement("div", { className: "wb-inline-row" },
            React.createElement("button", { type: "button", className: "wb-btn muted", disabled: exportSids.length === exportSessions.length, onClick: function () { setExportSids(exportSessions.map(function (s) { return s.id; })); setExportMsg(""); } }, t("settings.selectAll")),
            React.createElement("button", { type: "button", className: "wb-btn muted", disabled: !exportSids.length, onClick: function () { setExportSids([]); setExportMsg(""); } }, t("settings.clearSelection")),
          ),
        ),
        React.createElement("div", { className: "wb-export-session-list", role: "group", "aria-label": t("settings.sessionExportSelectLabel") },
          exportSessions.map(function (s) {
            var selected = exportSids.indexOf(s.id) >= 0;
            var sessionDate = s.updatedAt || s.createdAt;
            return React.createElement("label", { className: "wb-export-session-option" + (selected ? " selected" : ""), key: s.id },
              React.createElement("input", { type: "checkbox", checked: selected, onChange: function () { toggleExportSession(s.id); } }),
              React.createElement("span", { className: "wb-export-session-copy" },
                React.createElement("span", null, s.title || s.id),
                sessionDate ? React.createElement("small", null, formatDate(sessionDate)) : null,
              ),
            );
          }),
        ),
        React.createElement("div", { className: "wb-export-footer" },
          React.createElement("div", { className: "wb-export-format" },
            React.createElement("span", null, t("settings.sessionExportFormat")),
            <ChoiceCards compact label={t("settings.sessionExportFormat")} value={exportFmt} onChange={setExportFmt} options={[
              { id: "markdown", title: "Markdown" },
              { id: "json", title: "JSON" },
            ]} />,
          ),
          React.createElement("button", { type: "button", className: "wb-btn primary wb-export-submit", disabled: !exportSids.length, onClick: exportSelectedSessions },
            React.createElement(SessionExportIcon),
            React.createElement("span", null, t("settings.sessionExportBtn")),
          ),
        ),
      ) : React.createElement("p", { className: "wb-hint" }, t("settings.sessionExportNoSessions")),
    ), { id: "setting-session-export", className: "wb-section-block wb-session-export-block" }),
  );
}

export { DataPanel, requestDataPanelStorage };
