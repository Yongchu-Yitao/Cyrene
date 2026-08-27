import { workbenchServices } from "../../shared/runtime/services.jsx"
import { wbErrorText } from "../../shared/errors.jsx"
import { wbSetBrowserOverlayObscured } from "../../shared/browser/overlays.jsx"

var { useEffect: useWorkbenchEffect, useMemo: useWorkbenchMemo, useRef: useWorkbenchRef, useState: useWorkbenchState } = React;

// Help center external destinations (kept in sync with the About section in
// settings-overlay.jsx).
var WB_HELP_DOCS_URL = "https://github.com/ikerrrrrrrrrrr/Cyrene#readme";
var WB_HELP_FEEDBACK_URL = "https://github.com/ikerrrrrrrrrrr/Cyrene/issues/new";

// Platform-aware keyboard shortcut rendering. Mac shows ⌘/⇧/⌥/⌃ glyphs; other
// platforms fall back to Ctrl/Shift/Alt text so the help center mirrors whatever
// modifier the user's OS actually uses.
function wbIsMacPlatform() {
  try {
    var nav = window.navigator || {};
    var uaData = nav.userAgentData;
    if (uaData && uaData.platform) return /mac/i.test(uaData.platform);
    if (nav.platform) return /mac|iphone|ipad|ipod/i.test(nav.platform);
    return /mac|iphone|ipad|ipod/i.test(nav.userAgent || "");
  } catch (e) {
    return false;
  }
}

function wbShortcutKey(token, isMac) {
  if (token === "mod") return isMac ? "⌘" : "Ctrl";
  if (token === "shift") return isMac ? "⇧" : "Shift";
  if (token === "alt") return isMac ? "⌥" : "Alt";
  if (token === "ctrl") return isMac ? "⌃" : "Ctrl";
  return token;
}

function WorkbenchHelpCenter({ onNewProject, onNewTask, onOpenPage, onSettings }) {
  var { t } = workbenchServices.i18n().use();
  var dataState = workbenchServices.data().state;
  var [open, setOpen] = useWorkbenchState(false);
  var rootRef = useWorkbenchRef(null);
  var isMac = useWorkbenchMemo(wbIsMacPlatform, []);
  // Refresh the shortcut list every time the popover opens so it reflects any
  // rebinding done in Settings → Shortcuts. Mirror the module's glyph renderer
  // so the help center and the settings panel stay visually consistent.
  var shortcutList = useWorkbenchMemo(function () {
    var shortcuts = workbenchServices.shortcuts();
    var list = shortcuts.list();
    // Show the same set the help center always showed (global actions only);
    // composer bindings live in the settings panel where they can be rebound.
    return list.filter(function (item) { return item.group === "global"; });
  }, [open]);

  useWorkbenchEffect(function () {
    if (!open) return undefined;
    function handlePointer(event) {
      if (rootRef.current && !rootRef.current.contains(event.target)) setOpen(false);
    }
    function handleKey(event) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", handlePointer);
    document.addEventListener("keydown", handleKey);
    return function () {
      document.removeEventListener("mousedown", handlePointer);
      document.removeEventListener("keydown", handleKey);
    };
  }, [open]);

  // See WorkbenchNotificationCenter: native browser content cannot be layered
  // beneath a DOM popover with CSS alone.
  useWorkbenchEffect(function () {
    if (!open) return undefined;
    wbSetBrowserOverlayObscured(1);
    return function () { wbSetBrowserOverlayObscured(-1); };
  }, [open]);

  function run(action) {
    setOpen(false);
    if (typeof action === "function") action();
  }

  var pluginModules = Array.isArray(dataState.pluginModules) ? dataState.pluginModules : [];
  var quickItems = [
    {
      id: "tutorial", tone: "cyan", title: t("help.tutorial"), desc: t("help.tutorialDesc"),
      icon: <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V2H6.5A2.5 2.5 0 0 0 4 4.5v15Z"/><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5"/><path d="M8.5 7h7M8.5 11h7"/></svg>,
      action: function () { workbenchServices.tour().open(); },
    },
    {
      id: "new-project", tone: "blue", title: t("help.newProject"), desc: t("help.newProjectDesc"),
      icon: <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 9h18"/><path d="M7 14h7M7 17h4"/></svg>,
      action: function () { onNewProject && onNewProject(); },
    },
    {
      id: "new-task", tone: "green", title: t("help.createTask"), desc: t("help.createTaskDesc"),
      icon: <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3.5" y="3.5" width="17" height="17" rx="4.5"/><path d="m8 12 2.8 2.8L16.5 9"/></svg>,
      action: function () { onNewTask && onNewTask(); },
    },
    {
      id: "knowledge", tone: "amber", title: t("help.uploadKnowledge"), desc: t("help.uploadKnowledgeDesc"),
      icon: <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 15V4"/><path d="m8 8 4-4 4 4"/><path d="M5 15v3a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-3"/></svg>,
      action: function () { onOpenPage && onOpenPage("knowledge"); },
    },
    {
      id: "agent", tone: "purple", title: t("help.connectAgent"), desc: t("help.connectAgentDesc"),
      icon: <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="5" y="8.5" width="14" height="11" rx="3.5"/><path d="M12 8.5V4.5M12 4.5a1.5 1.5 0 1 0 0-.01"/><path d="M3.5 13.5v3M20.5 13.5v3"/><circle cx="9.5" cy="13.5" r="1.1" fill="currentColor" stroke="none"/><circle cx="14.5" cy="13.5" r="1.1" fill="currentColor" stroke="none"/></svg>,
      action: function () { onSettings && onSettings("agents"); },
    },
  ].filter(function (item) {
    return item.id !== "knowledge" || pluginModules.indexOf("knowledge") >= 0;
  });

  var shortcuts = shortcutList.map(function (item) {
    return {
      id: item.id,
      label: t(item.labelKey),
      keys: item.keys,
    };
  });

  var version = dataState.appVersion || "1.0.0";

  return (
    <div className={"workbench-help-anchor" + (open ? " open" : "")} ref={rootRef}>
      <button type="button" data-tour="topbar_help" className={"workbench-icon-btn" + (open ? " active" : "")} title={t("workbench.help")} aria-label={t("workbench.help")} aria-expanded={open} onClick={function () { setOpen(!open); }}>
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3"/><path d="M12 17h.01"/></svg>
      </button>
      {open ? (
        <div className="workbench-help-popover" role="dialog" aria-label={t("help.title")}>
          <div className="workbench-help-popover-arrow"></div>
          <div className="workbench-help-head">
            <b>{t("help.title")}</b>
            <button type="button" className="workbench-icon-btn" title={t("common.close")} onClick={function () { setOpen(false); }}>
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="m6 6 12 12M18 6 6 18"/></svg>
            </button>
          </div>
          <div className="workbench-help-body">
            <div className="workbench-help-section">
              <span className="workbench-help-section-title">{t("help.quickStart")}</span>
              <div className="workbench-help-quick">
                {quickItems.map(function (item) {
                  return (
                    <button key={item.id} type="button" className="workbench-help-quick-item" onClick={function () { run(item.action); }}>
                      <span className={"workbench-help-quick-icon " + item.tone}>{item.icon}</span>
                      <span className="workbench-help-quick-main">
                        <b>{item.title}</b>
                        <small>{item.desc}</small>
                      </span>
                      <svg className="workbench-help-quick-chevron" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m9 6 6 6-6 6"/></svg>
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="workbench-help-divider"></div>
            <div className="workbench-help-section">
              <span className="workbench-help-section-title">{t("help.shortcuts")}</span>
              <div className="workbench-help-shortcuts">
                {shortcuts.map(function (item) {
                  return (
                    <div key={item.id} className="workbench-help-shortcut">
                      <span>{item.label}</span>
                      <span className="workbench-help-keys">
                        {item.keys.map(function (token, idx) {
                          return <kbd key={idx}>{wbShortcutKey(token, isMac)}</kbd>;
                        })}
                      </span>
                    </div>
                  );
                })}
              </div>
              <button type="button" className="workbench-help-customize" onClick={function () { run(function () { onSettings && onSettings("shortcuts"); }); }}>
                {t("help.customizeShortcuts", "Customize shortcuts")}
              </button>
            </div>
            <div className="workbench-help-divider"></div>
            <div className="workbench-help-links">
              <a className="workbench-help-link" href={WB_HELP_DOCS_URL} target="_blank" rel="noopener noreferrer">
                <span>{t("help.docs")}</span>
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M14 4h6v6"/><path d="M20 4 10 14"/><path d="M19 13.5V18a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4.5"/></svg>
              </a>
              <a className="workbench-help-link" href={WB_HELP_FEEDBACK_URL} target="_blank" rel="noopener noreferrer">
                <span>{t("help.feedback")}</span>
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M14 4h6v6"/><path d="M20 4 10 14"/><path d="M19 13.5V18a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4.5"/></svg>
              </a>
            </div>
          </div>
          <div className="workbench-help-foot">{t("help.version", { version: version })}</div>
        </div>
      ) : null}
    </div>
  );
}

function WorkbenchEditProjectModal({ project, onClose, onSave }) {
  var { t } = workbenchServices.i18n().use();
  var [name, setName] = useWorkbenchState(project.name || "");
  var [description, setDescription] = useWorkbenchState(project.description || "");
  var [workspacePath, setWorkspacePath] = useWorkbenchState(project.workspacePath || "");
  var [color, setColor] = useWorkbenchState(project.color || "#22b07a");
  var [busy, setBusy] = useWorkbenchState(false);
  var [error, setError] = useWorkbenchState("");
  function save() {
    var trimmed = name.trim();
    if (!trimmed) { setError(t("create.project.error.nameRequired")); return; }
    setBusy(true);
    setError("");
    Promise.resolve(onSave({
      name: trimmed,
      description: description.trim(),
      workspacePath: workspacePath.trim(),
      color: color,
    })).catch(function (err) {
      setBusy(false);
      setError(wbErrorText(err));
    });
  }
  return (
    <div className="workbench-modal-scrim" onMouseDown={function (e) { if (e.target === e.currentTarget) onClose(); }}>
      <div className="workbench-project-edit-modal" role="dialog" aria-modal="true">
        <div className="workbench-project-edit-head">
          <b>{t("rail.editProject")}</b>
          <button type="button" className="workbench-icon-btn" onClick={onClose} title={t("common.close")}>
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="m6 6 12 12M18 6 6 18" /></svg>
          </button>
        </div>
        <div className="workbench-project-edit-body">
          <label>{t("create.project.name")}</label>
          <input value={name} maxLength={60} onChange={function (e) { setName(e.target.value); }} />
          <label>{t("create.project.description")}</label>
          <textarea value={description} rows={3} maxLength={240} onChange={function (e) { setDescription(e.target.value); }} />
          <label>{t("create.project.workspacePath")}</label>
          <input value={workspacePath} onChange={function (e) { setWorkspacePath(e.target.value); }} />
          <label>{t("create.project.color")}</label>
          <input className="workbench-project-color-input" type="color" value={color || "#22b07a"} onChange={function (e) { setColor(e.target.value); }} />
        </div>
        {error && <div className="workbench-project-edit-error">{error}</div>}
        <div className="workbench-project-edit-foot">
          <button type="button" className="wb-btn ghost" disabled={busy} onClick={onClose}>{t("common.cancel")}</button>
          <button type="button" className="wb-btn primary" disabled={busy} onClick={save}>{busy ? t("settings.saving") : t("common.save")}</button>
        </div>
      </div>
    </div>
  );
}

function wbProjectMemoryDate(value) {
  if (!value) return "—";
  var parsed = new Date(value);
  return isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
}

function WorkbenchProjectMemoryModal({ project, onClose }) {
  var { t } = workbenchServices.i18n().use();
  var [payload, setPayload] = useWorkbenchState(null);
  var [draft, setDraft] = useWorkbenchState("");
  var [selectedModifiedAt, setSelectedModifiedAt] = useWorkbenchState("");
  var [loading, setLoading] = useWorkbenchState(true);
  var [busy, setBusy] = useWorkbenchState(false);
  var [error, setError] = useWorkbenchState("");
  var draftRef = useWorkbenchRef("");
  var payloadRef = useWorkbenchRef(null);
  var selectedModifiedAtRef = useWorkbenchRef("");
  draftRef.current = draft;
  payloadRef.current = payload;
  selectedModifiedAtRef.current = selectedModifiedAt;

  function load(options) {
    options = options || {};
    if (!options.background) setLoading(true);
    return workbenchServices.api().json(
      "/api/projects/" + encodeURIComponent(project.id) + "/memory-prompt?include_memories=false",
      { toast: false }
    ).then(function (next) {
      var previousPrompt = String(payloadRef.current && payloadRef.current.current && payloadRef.current.current.prompt || "");
      var hasLocalEdit = options.keepDraft && draftRef.current.trim() !== previousPrompt.trim();
      setPayload(next);
      if (!hasLocalEdit) setDraft(String(next && next.current && next.current.prompt || ""));
      var nextVersions = next && Array.isArray(next.versions) ? next.versions : [];
      if (selectedModifiedAtRef.current && !nextVersions.some(function (version) { return version.modifiedAt === selectedModifiedAtRef.current; })) {
        setSelectedModifiedAt("");
      }
      setError("");
      return next;
    }).catch(function (err) {
      setError(wbErrorText(err));
      return null;
    }).then(function (value) {
      if (!options.background) setLoading(false);
      return value;
    });
  }

  useWorkbenchEffect(function () { load(); }, [project.id]);
  var learningStatus = payload && payload.learningStatus;
  var learningPhase = String(learningStatus && learningStatus.status || "");
  useWorkbenchEffect(function () {
    if (learningPhase !== "queued" && learningPhase !== "running") return undefined;
    var timer = window.setInterval(function () { load({ background: true, keepDraft: true }); }, 2000);
    return function () { window.clearInterval(timer); };
  }, [project.id, learningPhase]);

  var current = payload && payload.current || { prompt: "", modifiedAt: "" };
  var versions = payload && Array.isArray(payload.versions) ? payload.versions : [];
  var historicalVersions = versions.filter(function (version) { return version.modifiedAt !== current.modifiedAt; });
  var selectedVersion = selectedModifiedAt
    ? versions.find(function (version) { return version.modifiedAt === selectedModifiedAt; }) || null
    : null;
  var displayedPrompt = selectedVersion ? String(selectedVersion.prompt || "") : draft;
  var displayedModel = selectedVersion && selectedVersion.model || {};
  var displayedTrigger = selectedVersion && selectedVersion.trigger || {};
  var displayedModifiedAt = selectedVersion ? selectedVersion.modifiedAt : current.modifiedAt;
  var displayedModifiedBy = selectedVersion ? selectedVersion.modifiedBy : current.modifiedBy;
  var promptChanged = draft.trim() !== String(current.prompt || "").trim();

  function savePrompt() {
    if (busy || !promptChanged) return;
    setBusy(true);
    setError("");
    workbenchServices.api().json(
      "/api/projects/" + encodeURIComponent(project.id) + "/memory-prompt",
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: draft, baseModifiedAt: current.modifiedAt || "" }),
        toast: false,
      }
    ).then(function (next) {
      setPayload(function (previous) { return { ...(previous || {}), ...next }; });
      setDraft(String(next && next.current && next.current.prompt || ""));
      setSelectedModifiedAt("");
      workbenchServices.feedback().showToast(t("projectMemory.saved"), "success");
    }).catch(function (err) {
      setError(wbErrorText(err));
      if (Number(err && err.status || 0) === 409) load({ keepDraft: true });
    }).then(function () { setBusy(false); });
  }

  function restoreVersion(version) {
    if (busy || !version) return;
    setBusy(true);
    setError("");
    workbenchServices.api().json(
      "/api/projects/" + encodeURIComponent(project.id) + "/memory-prompt/restore",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ modifiedAt: version.modifiedAt, baseModifiedAt: current.modifiedAt || "" }),
        toast: false,
      }
    ).then(function (next) {
      setPayload(function (previous) { return { ...(previous || {}), ...next }; });
      setDraft(String(next && next.current && next.current.prompt || ""));
      setSelectedModifiedAt("");
      workbenchServices.feedback().showToast(t("projectMemory.restored"), "success");
    }).catch(function (err) {
      setError(wbErrorText(err));
      if (Number(err && err.status || 0) === 409) load({ keepDraft: true });
    }).then(function () { setBusy(false); });
  }

  return (
    <div className="workbench-modal-scrim workbench-project-memory-scrim" onMouseDown={function (event) { if (event.target === event.currentTarget && !busy) onClose(); }}>
      <div className="workbench-project-memory-modal" role="dialog" aria-modal="true" aria-label={t("projectMemory.title")}>
        <div className="workbench-project-edit-head workbench-project-memory-head">
          <span className="workbench-project-memory-title-copy">
            <b>{project.name}</b>
            <p>{selectedVersion ? t("projectMemory.historicalHint") : t("projectMemory.promptHint")}</p>
            {displayedModifiedAt ? <i>{wbProjectMemoryDate(displayedModifiedAt)} · {displayedModifiedBy === "memory_agent" ? t("projectMemory.byAgent") : t("projectMemory.byUser")}</i> : null}
          </span>
          <div className="workbench-project-memory-head-actions">
            {selectedVersion ? <button type="button" className="wb-btn primary" disabled={busy} onClick={function () { restoreVersion(selectedVersion); }}>{busy ? t("settings.saving") : t("projectMemory.restore")}</button> : <button type="button" className="wb-btn primary" disabled={busy || !promptChanged} onClick={savePrompt}>{busy ? t("settings.saving") : t("common.save")}</button>}
            <button type="button" className="workbench-icon-btn" disabled={busy} onClick={onClose} title={t("common.close")}>
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="m6 6 12 12M18 6 6 18" /></svg>
            </button>
          </div>
        </div>
        <div className="workbench-project-memory-overview">
          <span className="workbench-project-memory-overview-title">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="4" y="5" width="16" height="15" rx="2"/><path d="M8 3v4M16 3v4M8 11h8"/></svg>
            <span><b>{t("projectMemory.overview")}</b><em>{selectedVersion ? t("projectMemory.historyStatus") : current.modifiedAt ? t("projectMemory.currentStatus") : t("projectMemory.unsavedStatus")}</em></span>
          </span>
          <span className="workbench-project-memory-overview-metric"><small>{t("projectMemory.versionCount")}</small><b>{versions.length.toLocaleString()}</b></span>
          <span className="workbench-project-memory-overview-metric"><small>{t("projectMemory.characterCount")}</small><b>{displayedPrompt.length.toLocaleString()}</b></span>
          <div className="workbench-project-memory-head-version">
            <label htmlFor="workbench-project-memory-version">{t("projectMemory.versionSelector")}</label>
            <select id="workbench-project-memory-version" value={selectedModifiedAt} onChange={function (event) { setSelectedModifiedAt(event.target.value); }}>
              <option value="">{current.modifiedAt ? t("projectMemory.currentOption", { time: wbProjectMemoryDate(current.modifiedAt) }) : t("projectMemory.currentUnsavedOption")}</option>
              {historicalVersions.map(function (version) {
                return <option key={version.revisionId || version.modifiedAt} value={version.modifiedAt}>{t("projectMemory.historyOption", { time: wbProjectMemoryDate(version.modifiedAt) })}</option>;
              })}
            </select>
          </div>
        </div>
        <div className="workbench-project-memory-body">
          {loading ? <div className="workbench-project-memory-state"><span className="wbc-spinner" /> {t("common.loading")}</div> : null}
          {!loading ? (
            <section className="workbench-project-memory-prompt">
              <div className="workbench-project-memory-editor">
                {learningStatus ? (
                  <div className={"workbench-project-memory-learning-status " + learningPhase} title={learningStatus.error || ""}>
                    <span>{t("projectMemory.learningStatus." + learningPhase)}</span>
                    {learningPhase === "failed" ? (
                      <small>{[
                        learningStatus.updatedAt ? wbProjectMemoryDate(learningStatus.updatedAt) : "",
                        learningStatus.model && learningStatus.model.model || "",
                        learningStatus.error || "",
                      ].filter(Boolean).join(" · ")}</small>
                    ) : null}
                  </div>
                ) : null}
                {selectedVersion ? <div className="workbench-project-memory-selected-version">
                  <b>{selectedVersion.changeSummary || t("projectMemory.versionChange")}</b>
                  <span>{selectedVersion.modifiedBy === "memory_agent" ? t("projectMemory.byAgent") : t("projectMemory.byUser")}</span>
                  {displayedModel.model ? <span>{t("projectMemory.model")}: {displayedModel.provider ? displayedModel.provider + " · " : ""}{displayedModel.model}{displayedModel.reasoningEffort ? " · " + displayedModel.reasoningEffort : ""}</span> : null}
                  {displayedTrigger.conversationId ? <span>{t("projectMemory.trigger")}: {displayedTrigger.conversationId}{displayedTrigger.roundId ? " · " + displayedTrigger.roundId : ""}{displayedTrigger.turn ? " · " + t("projectMemory.turn", { turn: displayedTrigger.turn }) : ""}</span> : null}
                  {selectedVersion.restoredFromModifiedAt ? <span>{t("projectMemory.restoredFrom", { time: wbProjectMemoryDate(selectedVersion.restoredFromModifiedAt) })}</span> : null}
                </div> : null}
                <div className="workbench-project-memory-editor-field">
                  <textarea className={selectedVersion ? "is-historical" : ""} value={displayedPrompt} readOnly={!!selectedVersion} maxLength={16000} onChange={function (event) { if (!selectedVersion) setDraft(event.target.value); }} placeholder={t("projectMemory.promptPlaceholder")} />
                  <div className="workbench-project-memory-count">{displayedPrompt.length.toLocaleString()} / 16,000</div>
                </div>
              </div>
            </section>
          ) : null}
          {error ? <div className="workbench-project-edit-error workbench-project-memory-inline-error">{error}</div> : null}
        </div>
      </div>
    </div>
  );
}

function WorkbenchSidebarCollapseControl({ collapsed, onToggle }) {
  var { t } = workbenchServices.i18n().use();
  var label = collapsed ? t("rail.expand", null, "Expand sidebar") : t("rail.collapse", null, "Collapse sidebar");
  return (
    <button
      type="button"
      className="workbench-sidebar-collapse-control"
      title={label}
      aria-label={label}
      aria-expanded={!collapsed}
      onClick={onToggle}
    >
      <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <rect x="3" y="3" width="18" height="18" rx="2"/>
        <path d="M15 3v18"/>
        <path d={collapsed ? "m8 10 2 2-2 2" : "m9 10-2 2 2 2"}/>
      </svg>
    </button>
  );
}

function WorkbenchSidebarDock({ activePage, activeDestination, onOpenPage, onSettings, collapsed, persistent, enabledModules }) {
  var { t } = workbenchServices.i18n().use();
  var items = [
    { id: "schedule", label: t("workbench.page.schedule"), icon: (
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4.5" width="18" height="17" rx="2.5"/><path d="M3 9.5h18M8 2.5v4M16 2.5v4"/></svg>
    ) },
    { id: "board", label: t("workbench.page.board", null, "Board"), icon: (
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M8.5 4v16M15.5 4v16"/><path d="M5.5 8h1M10.5 11h3M17.5 7h1M17.5 13h1"/></svg>
    ) },
    { id: "work", label: t("workbench.page.work", null, "Work"), icon: (
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="7" width="18" height="13" rx="2.5"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 12h18M9.5 12v2h5v-2"/></svg>
    ) },
    { id: "knowledge", label: t("workbench.page.knowledge"), icon: (
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M5 4.5A2.5 2.5 0 0 1 7.5 2H20v15H7.5A2.5 2.5 0 0 0 5 19.5Z"/><path d="M5 19.5A2.5 2.5 0 0 0 7.5 22H20"/></svg>
    ) },
    { id: "memory", label: t("workbench.page.memory"), icon: (
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M12 4 13.6 10.4 20 12 13.6 13.6 12 20 10.4 13.6 4 12 10.4 10.4Z"/></svg>
    ) },
  ].filter(function (item) {
    return !Array.isArray(enabledModules) || enabledModules.indexOf(item.id) >= 0;
  });
  return (
    <div className={"workbench-sidebar-dock" + (persistent ? " is-persistent" : "") + (collapsed ? " is-collapsed" : "")}>
      <nav className="workbench-sidebar-dock-nav" aria-label={t("workbench.navigation", "Workbench navigation")}>
        {items.map(function (item) {
          var active = String(activeDestination || "") === item.id;
          return (
            <button
              key={item.id}
              type="button"
              data-tour={"rail_" + item.id}
              className={active ? "active" : ""}
              title={item.label}
              aria-label={item.label}
              aria-current={active ? "page" : undefined}
              onClick={function () { if (onOpenPage) onOpenPage(item.id); }}
            >
              <span aria-hidden="true">{item.icon}</span>
              <b>{item.label}</b>
            </button>
          );
        })}
      </nav>
    </div>
  );
}

function WorkbenchFullPage({ config, onClose }) {
  var { t } = workbenchServices.i18n().use();
  return (
    <div className="workbench-fullscreen">
      <div className="workbench-fullscreen-head">
        <button type="button" onClick={onClose}>← {t("workbench.back", null, "Back to workbench")}</button>
        <b>{config.title}</b>
      </div>
      <div className="workbench-fullscreen-body">
        {config.render()}
      </div>
    </div>
  );
}

function workbenchFullPageConfig(page, setFullPage, store) {
  return { title: page, render: function () { return <div className="workbench-empty">{workbenchServices.i18n().t("workbench.pageNotFound", null, "Page not found.")}</div>; } };
}

export { WorkbenchEditProjectModal, WorkbenchFullPage, WorkbenchHelpCenter, WorkbenchProjectMemoryModal, WorkbenchSidebarCollapseControl, WorkbenchSidebarDock, workbenchFullPageConfig }
