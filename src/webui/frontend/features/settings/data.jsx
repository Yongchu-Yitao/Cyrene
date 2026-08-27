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
  browser: "#6366f1",
  caches: "#78716c",
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
      if (!isDataPanelStorageSnapshot(payload)) throw new Error("Invalid storage snapshot");
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
  var { t, redactSecrets, saveRedactSecrets, config, configLoading, resetStatus, setResetStatus, resetting, setResetting, backupList, backupMsg, setBackupMsg, loadBackups, exportSids, setExportSids, exportFmt, setExportFmt, exportMsg, setExportMsg, formatBytes, formatDate } = p;

  var [storage, setStorage] = useStateSt(dataPanelStorageCache);
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

  function backupDefaultName() {
    var now = new Date();
    function pad(value) { return String(value).padStart(2, "0"); }
    return "cyrene_backup_" + now.getFullYear() + pad(now.getMonth() + 1) + pad(now.getDate()) + "_" + pad(now.getHours()) + pad(now.getMinutes()) + pad(now.getSeconds()) + ".zip";
  }

  async function createBackup() {
    var bridge = window.cyrene;
    if (!bridge || typeof bridge.pickBackupSavePath !== "function") {
      showSettingsToast(t("settings.backupPickerUnavailable"), "error");
      return;
    }
    try {
      var selection = await bridge.pickBackupSavePath({ title: t("settings.backupChooseSaveTitle"), defaultName: backupDefaultName() });
      if (!selection || selection.cancelled || !selection.path) return;
      setBackupMsg("");
      showSettingsToast(t("settings.backupExporting"), "info");
      var response = await settingsFetch("/api/backup/export", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: selection.path }) });
      var result = await response.json();
      if (!result.ok) throw new Error(result.error || t("settings.failed"));
      showSettingsToast(t("settings.backupExported", { n: result.entries.length, size: formatBytes(result.size) }), "success");
      loadBackups();
    } catch (e) {
      showSettingsToast(t("settings.failed") + ": " + e.message, "error");
    }
  }

  async function restoreBackup() {
    var bridge = window.cyrene;
    if (!bridge || typeof bridge.pickBackupFile !== "function") {
      showSettingsToast(t("settings.backupPickerUnavailable"), "error");
      return;
    }
    try {
      var selection = await bridge.pickBackupFile({ title: t("settings.backupChooseFileTitle") });
      if (!selection || selection.cancelled || !selection.path) return;
      var response = await settingsFetch("/api/backup/restore", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: selection.path }) });
      var result = await response.json();
      if (!result.ok) throw new Error(result.error || (result.errors || []).join(";") || t("settings.backupRestoreFailed"));
      showSettingsToast(t("settings.backupRestored", { n: result.restored.length }) + " " + t("settings.backupRestartRequired"), "success");
    } catch (e) {
      showSettingsToast(t("settings.backupRestoreFailed") + ": " + e.message, "error");
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
    SectionTitle(t("settings.data"), t("settings.dataSubtitle")),

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
    React.cloneElement(SectionBlock(t("settings.backup"), t("settings.backupHint"),
      React.createElement("div", { className: "wb-inline-row" },
        React.createElement("button", { className: "wb-btn primary", onClick: createBackup }, t("settings.backupExportBtn")),
        React.createElement("button", { className: "wb-btn", "data-cyrene-risk": "R3", onClick: restoreBackup }, t("settings.backupRestoreBtn")),
      ),
      backupList.map(function (b) {
        return React.createElement("div", { className: "wb-backup-row", key: b.name },
          React.createElement("span", { className: "wb-backup-name" }, b.name),
          React.createElement("span", { className: "wb-backup-meta" }, formatBytes(b.size), " · ", formatDate(b.modified)),
        );
      }),
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
            React.createElement("div", { className: "wb-seg", role: "radiogroup", "aria-label": t("settings.sessionExportFormat") },
              React.createElement("button", { type: "button", role: "radio", "aria-checked": exportFmt === "markdown", className: "wb-seg-btn" + (exportFmt === "markdown" ? " active" : ""), onClick: function () { setExportFmt("markdown"); } }, "Markdown"),
              React.createElement("button", { type: "button", role: "radio", "aria-checked": exportFmt === "json", className: "wb-seg-btn" + (exportFmt === "json" ? " active" : ""), onClick: function () { setExportFmt("json"); } }, "JSON"),
            ),
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
