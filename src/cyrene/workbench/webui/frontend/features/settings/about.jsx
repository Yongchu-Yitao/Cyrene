import {
  workbenchServices,
  useStateSt,
  useEffectSt,
  REPO_URL,
  REPO_ISSUES_URL,
  REPO_DOCS_URL,
  settingsFetch,
  showSettingsToast,
  renderSettingsMarkdown,
  AboutRelatedIcon,
  SectionTitle,
  Toggle,
} from "./shared.jsx"

// ── About Panel ──
function AboutPanel(p) {
  var { t, config } = p;

  return React.createElement("div", { className: "settings-panel wb-about-settings" },
    SectionTitle(t("settings.about"), t("settings.aboutSubtitle")),
    React.createElement(UpdateSection, { t: t, config: config }),
  );
}

// ── Update Section (inlined) ──
function UpdateSection({ t, config }) {
  var dataState = workbenchServices.data().state;
  var [checking, setChecking] = useStateSt(false);
  var [info, setInfo] = useStateSt(null);
  var [downloading, setDownloading] = useStateSt(false);
  var [progress, setProgress] = useStateSt({ downloaded: 0, total: 0, done: false });
  var [downloaded, setDownloaded] = useStateSt(false);
  var [error, setError] = useStateSt("");
  var [exporting, setExporting] = useStateSt(false);
  var [beta, setBeta] = useStateSt(!!(config && config.beta_updates));
  var [autoUpdate, setAutoUpdate] = useStateSt(!!(!config || config.auto_update !== false));
  var [changelogOpen, setChangelogOpen] = useStateSt(false);
  var [changelog, setChangelog] = useStateSt({ version: "", published_at: "", release_notes: "" });

  useEffectSt(function () { checkUpdate(); }, []);
  // 后台自动下载可能已完成/进行中，页面打开时恢复其状态（checkUpdate 失败也兜底）。
  useEffectSt(function () { syncDownloadState(); }, []);
  // Sync local toggle with config once it loads from the server.
  useEffectSt(function () { setBeta(!!(config && config.beta_updates)); }, [config && config.beta_updates]);
  useEffectSt(function () { setAutoUpdate(!!(!config || config.auto_update !== false)); }, [config && config.auto_update]);

  function syncDownloadState() {
    settingsFetch("/api/update/progress").then(function (r) { return r.json(); }).then(function (d) {
      if (!d || typeof d.done === "undefined") return;
      setProgress(d);
      if (d.done) {
        setDownloading(false);
        if (d.verified) {
          setDownloaded(true);
        } else if (d.verification_error) {
          setError(d.verification_error);
        }
      } else if (d.downloaded > 0 && d.total > 0) {
        setDownloading(true);
      }
    }).catch(function () {});
  }

  function checkUpdate() {
    setChecking(true); setError("");
    settingsFetch("/api/update/check").then(function (r) { return r.json(); }).then(function (d) {
      setInfo(d);
      setChangelog({ version: d.latest_version || "", published_at: d.published_at || "", release_notes: d.release_notes || "" });
      syncDownloadState();
    }).catch(function () { setError(t("settings.updateCheckFailed")); }).finally(function () { setChecking(false); });
  }

  function openChangelog() {
    settingsFetch("/api/update/changelog").then(function (r) { return r.json(); }).then(function (d) {
      setChangelog({
        version: d.version || (info && info.latest_version) || "",
        published_at: d.published_at || (info && info.published_at) || "",
        release_notes: d.release_notes || (info && info.release_notes) || "",
      });
      setChangelogOpen(true);
    }).catch(function () {
      setChangelog({
        version: (info && info.latest_version) || "",
        published_at: (info && info.published_at) || "",
        release_notes: (info && info.release_notes) || "",
      });
      setChangelogOpen(true);
    });
  }

  function toggleBeta() {
    if (checking || downloading) return;
    var next = !beta;
    setBeta(next);
    settingsFetch("/api/settings/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ beta_updates: next }) })
      .then(function () { checkUpdate(); })
      .catch(function () { setBeta(!next); });
  }

  function toggleAutoUpdate() {
    if (checking || downloading) return;
    var next = !autoUpdate;
    setAutoUpdate(next);
    settingsFetch("/api/settings/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ auto_update: next }) })
      .catch(function () { setAutoUpdate(!next); });
  }

  function startDownload() {
    setDownloading(true); setError("");
    settingsFetch("/api/update/download", { method: "POST" }).then(function (r) { return r.json(); }).then(function (d) {
      if (d.ok && d.verified) {
        setDownloaded(true);
        setProgress(function (p) { return Object.assign({}, p, { done: true, verified: true, actual_sha256: d.sha256 || p.actual_sha256 || "" }); });
        return "done";
      }
      if (d.code === "update_download_in_progress") {
        // 后台已在下载：保持 downloading=true，由下方轮询 effect 直接展示后台进度，
        // 完成后按钮自动变为「重启更新」，不再报「already in progress」错误。
        return "following";
      }
      setDownloaded(false);
      setProgress(function (p) { return Object.assign({}, p, { done: !!d.done, verified: false, verification_error: d.error || "" }); });
      setError(d.error || t("settings.updateDownloadFailed"));
      return "done";
    }).catch(function () { setError(t("settings.updateDownloadFailed")); return "done"; })
      .then(function (mode) { if (mode !== "following") setDownloading(false); });
  }

  function fmtBytes(n) {
    n = Number(n || 0);
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
    if (n < 1073741824) return (n / 1048576).toFixed(1) + " MB";
    return (n / 1073741824).toFixed(1) + " GB";
  }

  function fmtDate(value) {
    if (!value) return "—";
    return workbenchServices.i18n().formatDate(value, { dateStyle: "medium" }) || "—";
  }

  function notesText() {
    return String((info && info.release_notes) || "").trim() || t("settings.updateNoReleaseNotes", null, "No release notes provided.");
  }

  function downloadStatus() {
    if (!info) return "—";
    if (downloading) return t("settings.updateDownloading", null, "Downloading...") + " " + fmtBytes(progress.downloaded) + " / " + fmtBytes(progress.total || info.asset_size);
    if (downloaded && progress.verified) return t("settings.updateVerified", null, "Downloaded and verified");
    if (progress && progress.verification_error) return t("settings.updateVerificationFailed", null, "Verification failed") + ": " + progress.verification_error;
    if (info.update_available && !info.checksum_available) return t("settings.updateCannotVerify", null, "Cannot verify: release has no sha256 checksum.");
    if (info.update_available) return t("settings.updateReadyToDownload", null, "Ready to download");
    return t("settings.upToDate");
  }

  function statusDetailText() {
    if (!info || checking) return "";
    var detail = downloadStatus();
    if (!detail || detail === "—" || detail === statusText) return "";
    if (!info.update_available && detail === t("settings.upToDate")) return "";
    return detail;
  }

  function confirmInstall() {
    var version = info && info.latest_version ? "v" + info.latest_version : "—";
    var confirmTitle = t("settings.updateConfirmTitle", { version: version }, "Install update to {version}?");
    var confirmBody = t("settings.updateConfirmRestart", null, "Cyrene will close and restart during installation.");
    var confirmed = workbenchServices.feedback().confirmModal
      ? workbenchServices.feedback().confirmModal({
        title: confirmTitle,
        body: confirmBody,
        confirmLabel: t("common.confirm", null, "Confirm"),
      })
      : Promise.resolve(window.confirm([confirmTitle, "", confirmBody].join("\n")));
    confirmed.then(function (ok) {
      if (!ok) return;
      settingsFetch("/api/update/restart", { method: "POST" }).then(function (r) {
        if (!r.ok) return r.json().then(function (d) { throw new Error(d.message || d.error || t("settings.updateRestartFailed", null, "Restart failed")); });
      }).catch(function (err) {
        if (err && err.message) setError(err.message);
      });
    });
  }

  useEffectSt(function () {
    if (!downloading) return;
    var timer = setInterval(function () {
      settingsFetch("/api/update/progress").then(function (r) { return r.json(); }).then(function (d) {
        setProgress(d);
        if (d.done) {
          clearInterval(timer);
          setDownloading(false);
          if (d.verified) setDownloaded(true);
          else if (d.verification_error) setError(d.verification_error);
        }
      }).catch(function () { clearInterval(timer); setDownloading(false); });
    }, 500);
    return function () { clearInterval(timer); };
  }, [downloading]);

  var lv = info && info.latest_version ? "v" + info.latest_version : "";
  var statusText = checking
    ? t("settings.updateChecking")
    : (info && info.update_available
      ? t("settings.updateAvailable")
      : (info ? t("settings.upToDate") : "—"));
  var actionDisabled = checking || downloading || !!(info && info.update_available && !downloaded && !info.checksum_available);
  var actionLabel = downloaded
    ? t("settings.updateRestartNow")
    : (checking
      ? t("settings.updateChecking")
      : (info && info.update_available ? t("settings.updateToVersion", { version: lv }) : t("settings.checkForUpdates")));
  var actionHandler = downloaded ? confirmInstall : (info && info.update_available ? startDownload : checkUpdate);
  var statusDetail = statusDetailText();
  var progressTotal = Number(progress.total || (info && info.asset_size) || 0);
  // A verified download is the terminal state. The last byte-progress event can
  // arrive just below the reported total, so completion must take precedence
  // over that stale ratio or the hero fill remains visibly short of the card.
  var heroProgress = downloaded
    ? 100
    : (progressTotal > 0
      ? Math.max(0, Math.min(100, Math.round((Number(progress.downloaded || 0) / progressTotal) * 100)))
      : 0);
  function exportLogs() {
    if (exporting) return;
    setExporting(true);
    settingsFetch("/api/logs/export", { method: "GET" })
      .then(function (response) { return response.blob(); })
      .then(function (blob) {
        var url = URL.createObjectURL(blob);
        var link = document.createElement("a");
        link.href = url;
        link.download = "cyrene-logs-" + new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19) + ".zip";
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
        showSettingsToast(t("settings.logExportDone", null, "Logs exported"), "success");
      })
      .catch(function (err) {
        showSettingsToast(t("settings.logExportFailed", null, "Log export failed") + ": " + String((err && err.message) || err), "error");
      })
      .finally(function () { setExporting(false); });
  }

  var relatedLinks = [
    { icon: "docs", title: t("settings.relatedDocs", null, "Help docs"), action: t("settings.view", null, "View"), href: REPO_DOCS_URL },
    { icon: "changelog", title: t("settings.relatedChangelog", null, "Changelog"), action: t("settings.view", null, "View"), onClick: openChangelog },
    { icon: "website", title: t("settings.relatedWebsite", null, "Official website"), action: t("settings.view", null, "View"), href: REPO_URL },
    { icon: "github", title: t("settings.relatedGithub", null, "GitHub repository"), action: t("settings.view", null, "View"), href: REPO_URL },
    { icon: "issue", title: t("settings.relatedIssue", null, "Submit Issue"), action: t("settings.feedback", null, "Feedback"), href: REPO_ISSUES_URL },
    { icon: "log", title: t("settings.exportLogs", null, "Export logs"), action: exporting ? t("common.loading", null, "Loading...") : t("settings.exportLogsAction", null, "Download"), onClick: exportLogs, disabled: exporting },
  ];

  return React.createElement("div", { className: "wb-about-stack" },
    React.createElement("section", {
      className: "wb-about-product-card" + (downloading ? " is-downloading" : "") + (downloaded ? " is-downloaded" : ""),
      style: { "--wb-about-download-progress": heroProgress + "%" },
      "aria-busy": downloading ? "true" : undefined,
    },
      React.createElement("div", { className: "wb-about-hero-progress", "aria-hidden": "true" }),
      React.createElement("div", { className: "wb-about-product-copy" },
        React.createElement("div", { className: "wb-about-logo", "aria-hidden": "true" },
          React.createElement("div", { className: "brand-mark" }),
        ),
        React.createElement("div", { className: "wb-about-product-text" },
          React.createElement("div", { className: "wb-about-title-row" },
            React.createElement("h3", null, "Cyrene"),
            React.createElement("span", { className: "wb-about-version-chip" }, dataState.appVersion || "—"),
          ),
          React.createElement("p", null, t("settings.aboutHeroCopy")),
        ),
      ),
      React.createElement("div", { className: "wb-about-hero-action" },
        React.createElement("button", {
          className: "wb-btn primary wb-about-check-btn",
          "data-cyrene-risk": downloaded ? "R3" : "R2",
          disabled: actionDisabled,
          onClick: actionHandler,
        }, actionLabel),
      ),
    ),

    React.createElement("section", { className: "wb-about-update-card" },
      React.createElement("div", { className: "wb-about-card-head" },
        React.createElement("h3", null, t("settings.updateSettings", null, "Update settings")),
        (info || checking) && React.createElement("span", { className: "wb-about-status-pill" }, statusText),
      ),
      React.createElement("div", { className: "wb-about-toggle-list" },
        React.createElement("label", { className: "wb-about-toggle-row" },
          React.createElement("span", null,
            React.createElement("strong", null, t("settings.autoUpdate", null, "Automatic updates")),
            React.createElement("small", null, t("settings.autoUpdateHint", null, "Automatically download and install new versions")),
          ),
          Toggle(autoUpdate, toggleAutoUpdate),
        ),
        React.createElement("label", { className: "wb-about-toggle-row" },
          React.createElement("span", null,
            React.createElement("strong", null, t("settings.betaUpdates")),
            React.createElement("small", null, t("settings.betaUpdatesHint", null, "Preview the latest features and improvements")),
          ),
          Toggle(beta, toggleBeta),
        ),
      ),
      React.createElement("div", { className: "wb-about-version-grid" },
        React.createElement("div", null, React.createElement("span", null, t("settings.updateCurrentVersion", null, "Current version")), React.createElement("strong", null, info && info.current_version ? "v" + info.current_version : (dataState.appVersion || "—"))),
        React.createElement("div", null, React.createElement("span", null, t("settings.updateLatestVersion", null, "Latest version")), React.createElement("strong", null, lv || (dataState.appVersion || "—"))),
        React.createElement("div", null, React.createElement("span", null, t("settings.updateReleaseBranch", null, "Release branch")), React.createElement("strong", null, "main")),
        React.createElement("div", null, React.createElement("span", null, t("settings.updatePublishedAt", null, "Published")), React.createElement("strong", null, fmtDate(info && info.published_at))),
      ),
      statusDetail && React.createElement("p", { className: "wb-about-update-status" }, statusDetail),
      error && React.createElement("p", { className: "wb-hint", style: { color: "var(--wb-red)" } }, error),
      info && info.update_available && React.createElement("div", { className: "wb-update-notes" },
        React.createElement("span", null, t("settings.updateReleaseNotes", null, "Release notes")),
        React.createElement("div", {
          className: "wb-update-notes-body markdown",
          dangerouslySetInnerHTML: { __html: renderSettingsMarkdown(notesText()) },
        })
      ),
    ),

    React.createElement("section", { className: "wb-about-related-card" },
      React.createElement("div", { className: "wb-about-card-head" },
        React.createElement("h3", null, t("settings.relatedLinks", null, "Related links")),
        React.createElement("small", null, t("settings.relatedLinksHint", null, "Documentation, releases, support, and diagnostics.")),
      ),
      React.createElement("div", { className: "wb-about-related-list" },
        relatedLinks.map(function (item) {
          var action = item.onClick
            ? React.createElement("button", { type: "button", className: "wb-about-related-action", disabled: item.disabled, onClick: item.onClick }, item.action)
            : React.createElement("a", { className: "wb-about-related-action", href: item.href, target: "_blank", rel: "noopener noreferrer" }, item.action);
          return React.createElement("div", { key: item.title, className: "wb-about-related-row" },
            React.createElement("span", { className: "wb-about-related-icon" }, AboutRelatedIcon(item.icon)),
            React.createElement("strong", null, item.title),
            action,
          );
        })
      ),
    ),
    changelogOpen && React.createElement("div", { className: "wb-changelog-modal-scrim", onMouseDown: function (e) { if (e.target === e.currentTarget) setChangelogOpen(false); } },
      React.createElement("div", { className: "wb-changelog-modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "wb-changelog-title" },
        React.createElement("div", { className: "wb-changelog-head" },
          React.createElement("div", null,
            React.createElement("h3", { id: "wb-changelog-title" }, t("settings.relatedChangelog", null, "Changelog")),
            React.createElement("p", null,
              changelog.version ? "v" + changelog.version : (dataState.appVersion || "—"),
              changelog.published_at ? " · " + fmtDate(changelog.published_at) : "",
            ),
          ),
          React.createElement("button", { className: "wb-btn", onClick: function () { setChangelogOpen(false); } }, t("settings.close", null, "Close")),
        ),
        React.createElement("div", {
          className: "wb-changelog-body markdown",
          dangerouslySetInnerHTML: { __html: renderSettingsMarkdown(String(changelog.release_notes || "").trim() || t("settings.updateNoReleaseNotes", null, "No release notes provided.")) },
        }),
      )
    ),
  );
}

export { AboutPanel };
