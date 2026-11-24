import {ActionIcon} from "./action-icon.jsx"
import {Disclosure} from "./disclosure.jsx"
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
  Toggle,
} from "./shared.jsx"

// ── About Panel ──
function AboutPanel(p) {
  var { t, config } = p;

  return React.createElement("div", { className: "settings-panel wb-about-settings" },

    React.createElement(UpdateSection, { t: t, config: config }),
  );
}

// ── Update Section (inlined) ──
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
  var [releaseNotes, setReleaseNotes] = useStateSt(null);

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
      if (d.error) setError(d.error);
      syncDownloadState();
    }).catch(function () { setError(t("settings.updateCheckFailed")); }).finally(function () { setChecking(false); });
  }

  useEffectSt(function () {
    if (!changelogOpen || info?.update_available) return;
    let active = true;
    setReleaseNotes(null);
    settingsFetch("/api/update/changelog")
      .then(response => response.ok ? response.json() : null)
      .then(notes => { if (active) setReleaseNotes(notes); })
      .catch(() => {});
    return () => { active = false; };
  }, [changelogOpen, info?.update_available, info?.latest_version]);

  // A pending upgrade owns its release notes, even when the publisher left them empty.
  const changelog = info?.update_available ? {
    version: info.latest_version,
    published_at: info.published_at,
    release_notes: info.release_notes,
  } : {
    version: releaseNotes?.version || info?.latest_version,
    published_at: releaseNotes?.published_at || info?.published_at,
    release_notes: releaseNotes?.release_notes || info?.release_notes,
  };

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

  function downloadStatus() {
    if (!info || downloading) return "";
    if (downloaded && progress.verified) return t("settings.updateVerified", null, "Downloaded and verified");
    if (progress && progress.verification_error) return t("settings.updateVerificationFailed", null, "Verification failed") + ": " + progress.verification_error;
    return "";
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

  const lv = info?.latest_version ? "v" + String(info.latest_version).replace(/^v/i, "") : "";
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

  if (downloading) actionLabel = t("settings.updateDownloading");
  var actionHandler = downloaded ? confirmInstall : (info && info.update_available ? startDownload : checkUpdate);
  var statusDetail = checking ? "" : (error || downloadStatus());
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
    { icon: "website", title: t("settings.relatedWebsite", null, "Official website"), action: t("settings.view", null, "View"), href: REPO_URL },
    { icon: "github", title: t("settings.relatedGithub", null, "GitHub repository"), action: t("settings.view", null, "View"), href: REPO_URL },
    { icon: "issue", title: t("settings.relatedIssue", null, "Submit Issue"), action: t("settings.feedback", null, "Feedback"), href: REPO_ISSUES_URL },
    { icon: "log", title: t("settings.exportLogs", null, "Export logs"), action: exporting ? t("common.loading", null, "Loading...") : t("settings.exportLogsAction", null, "Download"), onClick: exportLogs, disabled: exporting },
  ];

  const zh=workbenchServices.i18n().getLang()==="zh";
  const version = info?.current_version || dataState.appVersion;
  const installedLabel = version ? "v" + String(version).replace(/^v/i, "") : "—";
  const versionLabel = info?.update_available && lv ? installedLabel + " → " + lv : installedLabel;

  function resource(item){
    const footerIcon={issue:"alert-circle",log:"download"}[item.icon];
    const content = <>
      {footerIcon ? <ActionIcon name={footerIcon}/> : AboutRelatedIcon(item.icon)}
      <span>{item.title}</span>
      {!footerIcon && <svg className="about-outbound" aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M4 12l8-8M5 4h7v7"/></svg>}
    </>;
    return item.href?<a key={item.icon} href={item.href} target="_blank" rel="noopener noreferrer">{content}</a>:<button key={item.icon} disabled={item.disabled} onClick={item.onClick}>{content}</button>;
  }
  return <div className="about-window">
    <header className="about-mast">
      <div className="about-banner">
        <div className="about-brand">
          <img src="logo-mark.png" alt="" width="64" height="64"/>
          <h2>Cyrene</h2>
        </div>
        <div className="about-release">
          <div className="about-version-line">
            <button onClick={() => setChangelogOpen(open => !open)} aria-expanded={changelogOpen} aria-controls="about-notes" aria-label={versionLabel + " · " + t("settings.relatedChangelog")}>{versionLabel}</button>
            <span role="status" data-current={!!info && !info.update_available && !checking && !error}>{error && !info?.update_available ? "" : statusText}</span>
          </div>
          <div className="about-buttons">
            <button className="wb-btn" data-cyrene-risk={downloaded ? "R3" : "R2"} disabled={actionDisabled} onClick={actionHandler}>
              {!downloaded && <ActionIcon name={actionHandler === startDownload ? "download" : "refresh"}/>}{actionLabel}
            </button>
          </div>
        </div>
      </div>
      {(downloading || downloaded) && <div className="about-download">
        <div className="about-download-values">
          <span>{downloaded && progressTotal ? fmtBytes(progressTotal) : fmtBytes(progress.downloaded)}{!downloaded && progressTotal > 0 && " / " + fmtBytes(progressTotal)}</span>
          {(progressTotal > 0 || downloaded) && <strong>{heroProgress}%</strong>}
        </div>
        {progressTotal > 0 || downloaded
          ? <progress max="100" value={heroProgress} aria-label={actionLabel}/>
          : <progress aria-label={actionLabel}/>}
      </div>}
      {statusDetail&&<p role="status">{statusDetail}</p>}
    </header>
    <div className="about-drawers">
      <Disclosure id="about-notes" open={changelogOpen}><div className="about-notes-text">
        <h4>{t("settings.relatedChangelog")} {changelog.version && "· v" + String(changelog.version).replace(/^v/i, "")} {changelog.published_at && fmtDate(changelog.published_at)}</h4>
        <div className="markdown" dangerouslySetInnerHTML={{__html: renderSettingsMarkdown(changelog.release_notes || t("settings.updateNoReleaseNotes"))}}/>
        <dl className="about-meta">{[
        ["settings.updateCurrentVersion",version?"v"+String(version).replace(/^v/,""):"—"],["settings.updateLatestVersion",lv||dataState.appVersion||"—"],["settings.updateReleaseBranch","main"],["settings.updatePublishedAt",fmtDate(info?.published_at)]
      ].map(([k,x])=><div key={k}><dt>{t(k)}</dt><dd>{x}</dd></div>)}</dl>
      </div></Disclosure>
    </div>
    <div className="about-columns">
      <section className="about-options"><h3>{t("settings.updateSettings")}</h3>
        <div className="about-group">{[[autoUpdate,toggleAutoUpdate,"autoUpdate"],[beta,toggleBeta,"betaUpdates"]].map(([checked,apply,key])=><div className="about-pref" key={key}><div><strong>{t("settings."+key)}</strong><small>{t("settings."+key+"Hint")}</small></div>{Toggle(checked,apply,checking||downloading,t("settings."+key))}</div>)}</div>
      </section>
      <section className="about-resources"><h3>{zh?"项目资源":"Project resources"}</h3>
        <div className="about-resource-grid">{relatedLinks.filter(x=>["docs","website","github"].includes(x.icon)).map(resource)}</div>
      </section>
    </div>
    <footer className="about-support">
      <strong>{t("settings.feedbackDiagnostics")}</strong>
      <div className="about-support-actions">{relatedLinks.filter(x=>["issue","log"].includes(x.icon)).map(resource)}</div>
    </footer>
  </div>;
}
export { AboutPanel };
