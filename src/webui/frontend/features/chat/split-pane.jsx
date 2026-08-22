import { WBC_ICONS, WBC_SIDE_TAB_ICONS, WbcSplitPickerMenu, WorkbenchChatModel, useWbcEffect, useWbcLayoutEffect, useWbcMemo, useWbcRef, useWbcState, wbcAgentColor, wbcAgentInitials, wbcAttachmentTypeLabel, wbcBrowserTabPickerPayload, wbcBrowserTabPickerToggleIsDebounced, wbcClampSideSplitWidthForPage, wbcCreateDetachedRuntime, wbcErrorText, wbcFileViewKind, wbcFormatTime, wbcHighlightMentions, wbcMergeChronologicalMessages, wbcNormalizePermissionMode, wbcNotifyBrowserLayoutChanged, wbcReduceDetachedRuntime, wbcRenderMapMarkdown, wbcRenderMarkdown, wbcSubagentStatusClass, wbcSubagentStatusText, wbcT } from "../../workbench-chat.jsx"
import { WbcComposer, wbcBranchConnectors, wbcBranchKindLabel, wbcBranchLineage, wbcBranchRows, wbcBrowserStateForChat } from "./composer.jsx"
import { wbcProjectFileResource } from "./rail.jsx"
import { wbcCanOpenExternally, wbcChatUsedMap, wbcDownloadLink, wbcStartFileDrag } from "./file-resources.jsx"
import { WbcMapTab, WbcViewerTab } from "./viewer.jsx"
import { WbcThreadItem, wbcIsLiveAgentRequest } from "./conversation.jsx"
import { WbcAgentNotification, WbcAssistantMessage, WbcModelStatusMessage, WbcQuestionPrompt, WbcRuntimeTranscript, WbcUserMessage } from "./messages.jsx"
import { WbcArtifactsTab, WbcContextTab, WbcOverviewTab, useWbcLiveChatMetrics, useWbcLiveContextBlocks, useWbcLiveInbox } from "./context-panel.jsx"

// Workbench chat feature module with explicit ESM dependencies.
function WbcBranchTab({ chats, activeChatId, onSelectChat }) {
  var rows = useWbcMemo(function () {
    return wbcBranchRows(wbcBranchLineage(chats, activeChatId));
  }, [chats, activeChatId]);
  if (!rows.length) {
    return <p className="workbench-muted wbc-branch-empty">{wbcT("workbenchChat.branchEmpty", "This conversation has no branches.")}</p>;
  }
  var maxDepth = rows.reduce(function (depth, row) {
    return Math.max(depth, Number(row.depth) || 0);
  }, 0);
  return (
    <div className="wbc-branch" style={{ "--wbc-branch-rail": (maxDepth * 14 + 30) + "px" }}>
      <ul className="wbc-branch-tree">
        {rows.map(function (row, index) {
          var isActive = row.chatId === activeChatId;
          var isCurrent = isActive && row.isHead;
          var lane = row.depth > 0 ? " lane-fork" : " lane-main";
          var cls = "wbc-branch-row depth-" + row.depth + " kind-" + row.kind + lane + (isActive ? " on-current-branch" : "") + (isCurrent ? " current" : "");
          return (
            <li
              key={row.chatId + ":" + row.kind + ":" + index}
              className={cls}
            >
              <button
                type="button"
                className="wbc-branch-button"
                title={row.text || row.title || ""}
                aria-current={isCurrent ? "true" : undefined}
                onClick={function () { onSelectChat(row.chatId); }}
              >
                {wbcBranchConnectors(row).map(function (seg, segIndex) {
                  return <span key={"seg" + segIndex} className={"wbc-branch-line " + seg.cls} style={seg.style} aria-hidden="true" />;
                })}
                <span className="wbc-branch-node" style={{ left: (row.depth * 14 + 14) + "px" }} aria-hidden="true">
                  <span className="wbc-branch-node-core" />
                </span>
                <span className="wbc-branch-card">
                  <span className="wbc-branch-kind">{wbcBranchKindLabel(row.kind)}</span>
                  <span className="wbc-branch-text">{row.text || wbcT("workbenchChat.branchNoText", "(empty message)")}</span>
                  {isCurrent && <span className="wbc-branch-here">{wbcT("workbenchChat.branchHere", "Current")}</span>}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Right context panel (column 4)
// ---------------------------------------------------------------------------

function wbcChatArtifactFiles(chat) {
  var files = [];
  var seen = new Set();
  var seenAgentNames = new Set();
  function add(file, role) {
    if (!file) return;
    var agentOriginated = file.source === "agent";
    var pathKey = String(file.path || "").trim().replace(/\\/g, "/");
    var key = pathKey || String(file.url || file.id || file.name || "").trim();
    var agentName = role === "assistant" ? String(file.name || "").trim().toLowerCase() : "";
    if (agentOriginated && agentName && seenAgentNames.has(agentName)) return;
    if (!key || seen.has(key)) return;
    seen.add(key);
    if (agentName) seenAgentNames.add(agentName);
    files.push({ file: wbcEditableChatFileResource(chat, file), role: role });
  }
  (chat && chat.messages || []).forEach(function (msg) {
    (msg.attachments || []).forEach(function (file) {
      add(file, msg.role);
    });
  });
  (chat && chat.files || []).forEach(function (file) {
    if (!file) return;
    var path = String(file.path || "").trim().replace(/\\/g, "/");
    var withUrl = path && !file.url
      ? { ...file, url: "/api/workbench/chats/" + encodeURIComponent(chat.id) + "/files/" + path.split("/").map(encodeURIComponent).join("/") }
      : file;
    add(withUrl, "assistant");
  });
  (chat && chat.liveAgentArtifacts || []).forEach(function (file) {
    add(file, "assistant");
  });
  return files;
}

// User-facing deliverables are the files attached by assistant messages. They
// are kept separate from the broader Files index, which also contains uploads
// and ordinary workspace changes.
function wbcChatDeliveredArtifacts(chat) {
  var files = [];
  var seen = new Set();
  (chat && chat.messages || []).forEach(function (message) {
    if (!message || message.role !== "assistant") return;
    (message.attachments || []).forEach(function (file) {
      if (!file) return;
      var key = String(file.id || file.url || file.path || file.name || "").trim();
      if (!key || seen.has(key)) return;
      seen.add(key);
      files.push({ file: wbcEditableChatFileResource(chat, file), role: "assistant" });
    });
  });
  (chat && chat.liveAgentArtifacts || []).forEach(function (file) {
    if (!file) return;
    var key = String(file.id || file.url || file.path || file.name || "").trim();
    if (!key || seen.has(key)) return;
    seen.add(key);
    files.push({ file: wbcEditableChatFileResource(chat, file), role: "assistant", live: true });
  });
  return files;
}

function wbcEditableChatFileResource(chat, file) {
  if (!file || file.source !== "agent" || !file.path) return file;
  var projectId = String(file.projectId || (chat && chat.projectId) || "");
  if (!projectId) return file;
  // Conversation-generated files and Files-rail entries point at the same
  // workspace. Reuse the project resource shape so every viewer entry reaches
  // the shared editor, autosave and conflict handling instead of a read-only
  // chat download URL.
  var projectFile = wbcProjectFileResource(projectId, Object.assign({}, file, { kind: "file" }));
  return projectFile ? Object.assign({}, file, projectFile) : file;
}

function wbcViewerFileFromItems(viewerFile, items) {
  var selectedKey = wbcArtifactFileKey(viewerFile);
  if (!selectedKey) return null;
  var match = (Array.isArray(items) ? items : []).find(function (item) {
    return wbcArtifactFileKey(item && item.file) === selectedKey;
  });
  return match && match.file || null;
}

function wbcArtifactFileKey(file) {
  if (!file) return "";
  return String(file.id || file.url || file.path || file.name || "");
}

var WBC_PROJECT_FILE_DRAFTS = Object.create(null);

function wbcProjectFileDraftKey(file) {
  if (!file || file.source !== "project") return "";
  return String(file.projectId || "") + ":" + String(file.path || "");
}

function wbcProjectFileEditUrl(file) {
  if (!file || file.source !== "project" || !file.projectId || !file.path) return "";
  var encodedPath = String(file.path).replace(/\\/g, "/").split("/").filter(Boolean).map(encodeURIComponent).join("/");
  if (!encodedPath) return "";
  return "/api/projects/" + encodeURIComponent(file.projectId) + "/files/edit/" + encodedPath;
}

function wbcCanEditProjectTextFile(file) {
  var kind = wbcFileViewKind(file);
  return !!(wbcProjectFileEditUrl(file) && (kind === "markdown" || kind === "code" || kind === "html"));
}

function wbcDiscardProjectFileDraft(file) {
  var key = wbcProjectFileDraftKey(file);
  if (key) delete WBC_PROJECT_FILE_DRAFTS[key];
}

function WbcArtifactSplitHost({ file, items, width, label, onSelect, onResize, onClose, onViewed, splitSide, onToggleSide, onSplitDragStart, onSplitDragEnd }) {
  return (
       <WbcResourceSplitHost openKey={wbcArtifactFileKey(file)} width={width} onResize={onResize} splitSide={splitSide} onToggleSide={onToggleSide} onClose={onClose} onSplitDragStart={onSplitDragStart} onSplitDragEnd={onSplitDragEnd}>
      {file ? (
        <WbcArtifactSplit
          file={file}
          items={items}
          label={label}
          onSelect={onSelect}
          onClose={onClose}
          onViewed={onViewed}
        />
      ) : null}
    </WbcResourceSplitHost>
  );
}

function WbcArtifactSplit({ file, items, label, onSelect, onClose, onViewed }) {
  var [pickerOpen, setPickerOpen] = useWbcState(false);
  var [htmlMode, setHtmlMode] = useWbcState("rendered");
  var [markdownMode, setMarkdownMode] = useWbcState("rendered");
  var [editorDirty, setEditorDirty] = useWbcState(false);
  var headerRef = useWbcRef(null);
  var files = Array.isArray(items) ? items : [];
  var currentKey = wbcArtifactFileKey(file);
  var kind = wbcFileViewKind(file);
  var splitLabel = label || wbcT("workbenchChat.files", "Files");

  useWbcEffect(function () {
    setHtmlMode("rendered");
    setMarkdownMode("rendered");
    setEditorDirty(false);
  }, [currentKey]);

  function afterDiscard(callback) {
    if (!editorDirty) {
      callback();
      return;
    }
    var feedback = window.CyreneUI.require("feedback");
    var request = feedback.confirmModal ? feedback.confirmModal({
      title: wbcT("workbenchChat.editorUnsavedTitle", "Unsaved changes"),
      body: wbcT("workbenchChat.editorUnsavedBody", "Discard the changes made to this file?"),
      confirmLabel: wbcT("workbenchChat.editorDiscard", "Discard changes"),
      danger: true,
    }) : Promise.resolve(window.confirm(wbcT("workbenchChat.editorUnsavedBody", "Discard the changes made to this file?")));
    Promise.resolve(request).then(function (confirmed) {
      if (!confirmed) return;
      wbcDiscardProjectFileDraft(file);
      setEditorDirty(false);
      callback();
    });
  }

  useWbcEffect(function () {
    if (!pickerOpen) return undefined;
    function closePicker(event) {
      if (headerRef.current && !headerRef.current.contains(event.target)) setPickerOpen(false);
    }
    document.addEventListener("pointerdown", closePicker);
    return function () { document.removeEventListener("pointerdown", closePicker); };
  }, [pickerOpen]);

  return (
    <aside className="wbc-side-agent-split wbc-artifact-split" aria-label={splitLabel}>
      <header className="wbc-side-agent-split-head" ref={headerRef}>
        <button
          type="button"
          className="wbc-side-agent-split-picker"
          onClick={function () { setPickerOpen(function (open) { return !open; }); }}
          aria-expanded={pickerOpen}
          aria-haspopup="listbox"
        >
          <span className="wbc-side-agent-split-title">
            <span>{splitLabel}</span>
            <b title={file && file.name}>{file && file.name || "file"}</b>
          </span>
          <span className="wbc-side-agent-split-picker-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
        </button>
        <span className="wbc-artifact-split-actions">
          {kind === "html" && (
            <span className="wbc-artifact-mode-switch">
              <button type="button" className={htmlMode === "rendered" ? "active" : ""} onClick={function () { setHtmlMode("rendered"); }}>{wbcT("workbenchChat.viewerRendered", "Rendered")}</button>
              <button type="button" className={htmlMode === "source" ? "active" : ""} onClick={function () { setHtmlMode("source"); }}>{wbcT("workbenchChat.viewerSource", "Source")}</button>
            </span>
          )}
          {kind === "markdown" && wbcCanEditProjectTextFile(file) && (
            <span className="wbc-artifact-mode-switch">
              <button type="button" className={markdownMode === "rendered" ? "active" : ""} onClick={function () { setMarkdownMode("rendered"); }}>{wbcT("workbenchChat.viewerRendered", "Rendered")}</button>
              <button type="button" className={markdownMode === "source" ? "active" : ""} onClick={function () { setMarkdownMode("source"); }}>{wbcT("workbenchChat.viewerSource", "Source")}</button>
            </span>
          )}
          {wbcCanOpenExternally(file) ? (
            <a className="wbc-side-agent-split-action" href={file.url} target="_blank" rel="noreferrer" title={wbcT("workbenchChat.viewerOpenExternal", "Open in a new window")} aria-label={wbcT("workbenchChat.viewerOpenExternal", "Open in a new window")}>{WBC_ICONS.openExternal}</a>
          ) : null}
          {wbcDownloadLink(file, { className: "wbc-side-agent-split-action", "aria-label": wbcT("workbenchChat.download", "Download") })}
          <button
            type="button"
            className="wbc-side-agent-split-close"
            onClick={function () { afterDiscard(onClose); }}
            title={wbcT("workbenchChat.closeFilePreview", "Close file preview")}
            aria-label={wbcT("workbenchChat.closeFilePreview", "Close file preview")}
          >{WBC_ICONS.x}</button>
        </span>
        <WbcSplitPickerMenu open={pickerOpen} role="listbox" aria-label={splitLabel}>
            {files.map(function (item, index) {
              var itemFile = item && item.file;
              var selected = wbcArtifactFileKey(itemFile) === currentKey;
              return (
                <button
                  type="button"
                  key={wbcArtifactFileKey(itemFile) + ":" + index}
                  className={selected ? "active" : ""}
                  role="option"
                  aria-selected={selected}
                  draggable="true"
                  onDragStart={function (event) { wbcStartFileDrag(event, itemFile); }}
                  onClick={function () {
                    if (selected) {
                      setPickerOpen(false);
                      return;
                    }
                    afterDiscard(function () {
                      setPickerOpen(false);
                      if (onSelect) onSelect(itemFile);
                    });
                  }}
                >
                  <span className="wbc-artifact-picker-icon" aria-hidden="true">{WBC_ICONS.file}</span>
                  <b>{itemFile && itemFile.name || "file"}</b>
                </button>
              );
            })}
        </WbcSplitPickerMenu>
      </header>
      <div className="wbc-artifact-split-viewer">
        <WbcViewerTab
          file={file}
          onViewed={onViewed}
          hideHeader={true}
          htmlMode={htmlMode}
          onHtmlModeChange={setHtmlMode}
          markdownMode={markdownMode}
          onMarkdownModeChange={setMarkdownMode}
          onDirtyChange={setEditorDirty}
        />
      </div>
    </aside>
  );
}

function WbcChangeSplitHost({ change, width, onSelect, onResize, onClose, splitSide, onToggleSide, onSplitDragStart, onSplitDragEnd }) {
  var changeKey = change ? String(change.setId || "") + ":" + String(change.path || "") : "";
  return (
       <WbcResourceSplitHost openKey={changeKey} width={width} onResize={onResize} splitSide={splitSide} onToggleSide={onToggleSide} onClose={onClose} onSplitDragStart={onSplitDragStart} onSplitDragEnd={onSplitDragEnd}>
      {change ? <WbcChangeSplit change={change} onSelect={onSelect} onClose={onClose} /> : null}
    </WbcResourceSplitHost>
  );
}

function WbcChangeSplit({ change, onSelect, onClose }) {
  var [pickerOpen, setPickerOpen] = useWbcState(false);
  var [diffState, setDiffState] = useWbcState({ loading: true, diff: "", error: "", change: null });
  var headerRef = useWbcRef(null);
  var files = Array.isArray(change && change.files) ? change.files : [];

  useWbcEffect(function () {
    if (!pickerOpen) return undefined;
    function closePicker(event) {
      if (headerRef.current && !headerRef.current.contains(event.target)) setPickerOpen(false);
    }
    document.addEventListener("pointerdown", closePicker);
    return function () { document.removeEventListener("pointerdown", closePicker); };
  }, [pickerOpen]);

  useWbcEffect(function () {
    if (!change || !change.chatId || !change.setId || !change.path) return undefined;
    var cancelled = false;
    setDiffState({ loading: true, diff: "", error: "", change: null });
    WorkbenchChatModel.getChangeDiff(change.chatId, change.setId, change.path, { toast: false })
      .then(function (detail) {
        if (!cancelled) setDiffState({ loading: false, diff: String(detail.diff || ""), error: "", change: detail });
      })
      .catch(function (err) {
        if (!cancelled) setDiffState({ loading: false, diff: "", error: wbcErrorText(err), change: null });
      });
    return function () { cancelled = true; };
  }, [change && change.chatId, change && change.setId, change && change.path]);

  return (
    <aside className="wbc-side-agent-split wbc-change-split" aria-label={wbcT("workbenchChat.changePreview", "Change preview")}>
      <header className="wbc-side-agent-split-head" ref={headerRef}>
        <button
          type="button"
          className="wbc-side-agent-split-picker"
          onClick={function () { setPickerOpen(function (open) { return !open; }); }}
          aria-expanded={pickerOpen}
          aria-haspopup="listbox"
        >
          <span className="wbc-side-agent-split-title">
            <span>{wbcT("workbenchChat.changes", "Changes")}</span>
            <b title={change && change.path}>{change && change.path || "file"}</b>
          </span>
          <span className="wbc-side-agent-split-picker-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
        </button>
        <button type="button" className="wbc-side-agent-split-close" onClick={onClose} aria-label={wbcT("workbenchChat.closeChangePreview", "Close change preview")}>{WBC_ICONS.x}</button>
        <WbcSplitPickerMenu open={pickerOpen} role="listbox" aria-label={wbcT("workbenchChat.changes", "Changes")}>
            {files.map(function (item) {
              var selected = item.path === change.path;
              return (
                <button
                  type="button"
                  key={item.id || item.path}
                  className={selected ? "active" : ""}
                  role="option"
                  aria-selected={selected}
                  onClick={function () {
                    setPickerOpen(false);
                    if (onSelect) onSelect(Object.assign({}, change, { path: item.path, file: item }));
                  }}
                >
                  <span className="wbc-artifact-picker-icon" aria-hidden="true">{WBC_SIDE_TAB_ICONS.changes}</span>
                  <b>{item.path}</b>
                </button>
              );
            })}
        </WbcSplitPickerMenu>
      </header>
      <div className="wbc-change-split-diff wbc-change-diff">
        {diffState.loading ? (
          <p className="workbench-muted wbc-changes-state">{wbcT("workbenchChat.changes.loadingDiff", "Loading diff...")}</p>
        ) : diffState.error ? (
          <p className="workbench-muted wbc-changes-state">{diffState.error}</p>
        ) : diffState.diff && window.CyreneUI.require("diff").Panel ? (
          React.createElement(window.CyreneUI.require("diff").Panel, { diff: diffState.diff, mode: "text", hideHeader: true, hideHunkHeaders: true })
        ) : diffState.change && diffState.change.binary ? (
          <p className="workbench-muted wbc-changes-state">{wbcT("workbenchChat.changes.binary", "Binary or large file changed; text diff is unavailable.")}</p>
        ) : (
          <p className="workbench-muted wbc-changes-state">{wbcT("workbenchChat.changes.noDiff", "No text diff is available.")}</p>
        )}
      </div>
    </aside>
  );
}

function WbcViewerList({ files, selectedFile, onSelect }) {
  var items = Array.isArray(files) ? files : [];
  var selectedKey = wbcArtifactFileKey(selectedFile);
  return (
    <div className="wbc-resource-list">
      {items.map(function (item, index) {
        var file = item && item.file;
        var active = wbcArtifactFileKey(file) === selectedKey;
        return (
          <button type="button" className={"wbc-resource-list-row" + (active ? " current" : "")} key={wbcArtifactFileKey(file) + ":" + index} draggable="true" onDragStart={function (event) { wbcStartFileDrag(event, file); }} onClick={function () { if (onSelect) onSelect(file); }}>
            <span className="wbc-resource-list-icon" aria-hidden="true">{WBC_SIDE_TAB_ICONS.viewer}</span>
            <span className="wbc-resource-list-copy"><b>{file && file.name || "file"}</b><small>{wbcAttachmentTypeLabel(file)}</small></span>
            <span className="wbc-resource-list-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
          </button>
        );
      })}
    </div>
  );
}

function WbcBrowserList({ browserState, onSelect }) {
  var tabs = browserState && Array.isArray(browserState.tabs) ? browserState.tabs : [];
  var activeId = String(browserState && browserState.activeTabId || "");
  if (!tabs.length) return <p className="workbench-muted wbc-resource-empty">{wbcT("chat.side.browserUnavailable", "Browser view is unavailable.")}</p>;
  return (
    <div className="wbc-resource-list">
      {tabs.map(function (item, index) {
        var title = String(item.title || item.url || wbcT("chat.side.browser", "Browser"));
        var host = "";
        try { host = new URL(item.url).host; } catch (e) { host = String(item.url || ""); }
        return (
          <button type="button" className={"wbc-resource-list-row" + (String(item.id || "") === activeId ? " current" : "")} key={item.id || index} onClick={function () { if (onSelect) onSelect(item.id); }}>
            <span className="wbc-resource-list-icon" aria-hidden="true">{WBC_SIDE_TAB_ICONS.browser}</span>
            <span className="wbc-resource-list-copy"><b>{title}</b><small>{host || wbcT("chat.side.browser", "Browser")}</small></span>
            <span className="wbc-resource-list-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
          </button>
        );
      })}
    </div>
  );
}

function wbcMapItemKey(item) {
  return item ? String(item.kind || "map") + ":" + String(item.id || item.name || item.from_name || "") : "";
}

function wbcMapItemLabel(item) {
  if (!item) return wbcT("chat.side.map", "Map");
  if (item.kind === "route") return [item.from_name || item.from, item.to_name || item.to].filter(Boolean).join(" → ") || wbcT("workbenchChat.mapRoute", "Route");
  return String(item.name || wbcT("workbenchChat.mapPin", "Location"));
}

function useWbcMapData(chatId) {
  var [data, setData] = useWbcState({ chatId: "", loading: true, pins: [], routes: [] });
  useWbcEffect(function () {
    if (!chatId) { setData({ chatId: "", loading: false, pins: [], routes: [] }); return undefined; }
    var cancelled = false;
    setData({ chatId: chatId, loading: true, pins: [], routes: [] });
    fetch("/api/map/pins?session_id=" + encodeURIComponent(chatId))
      .then(function (r) { return r.json(); })
      .then(function (payload) {
        if (!cancelled) setData({ chatId: chatId, loading: false, pins: Array.isArray(payload.pins) ? payload.pins : [], routes: Array.isArray(payload.routes) ? payload.routes : [] });
      })
      .catch(function () { if (!cancelled) setData({ chatId: chatId, loading: false, pins: [], routes: [] }); });
    return function () { cancelled = true; };
  }, [chatId]);
  return data.chatId === chatId ? data : { chatId: chatId, loading: true, pins: [], routes: [] };
}

function wbcMapItems(data) {
  return (data.pins || []).map(function (item) { return Object.assign({ kind: "pin" }, item); })
    .concat((data.routes || []).map(function (item) { return Object.assign({ kind: "route" }, item); }));
}

function WbcMapList({ chatId, onSelect }) {
  var data = useWbcMapData(chatId);
  var items = wbcMapItems(data);
  if (data.loading) return <p className="workbench-muted wbc-resource-empty">{wbcT("workbenchChat.mapLoading", "Loading maps...")}</p>;
  if (!items.length) return <p className="workbench-muted wbc-resource-empty">{wbcT("workbenchChat.mapEmpty", "No map pins in this chat yet.")}</p>;
  return (
    <div className="wbc-resource-list">
      {items.map(function (item, index) {
        var detail = item.kind === "route" ? (item.transport || item.route_note || "") : (item.note || item.note_md || [item.lat, item.lng].filter(function (value) { return value !== undefined; }).join(", "));
        var detailHtml = detail ? wbcRenderMapMarkdown(detail) : "";
        return (
          <button type="button" className="wbc-resource-list-row" key={wbcMapItemKey(item) + ":" + index} onClick={function () { if (onSelect) onSelect(item); }}>
            <span className="wbc-resource-list-icon" aria-hidden="true">{WBC_SIDE_TAB_ICONS.map}</span>
            <span className="wbc-resource-list-copy">
              <b>{wbcMapItemLabel(item)}</b>
              {detailHtml
                ? <span className="wbc-resource-list-summary wbc-resource-list-markdown" dangerouslySetInnerHTML={{ __html: detailHtml }} />
                : <small>{item.kind === "route" ? wbcT("workbenchChat.mapRoute", "Route") : wbcT("workbenchChat.mapPin", "Location")}</small>}
            </span>
            <span className="wbc-resource-list-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
          </button>
        );
      })}
    </div>
  );
}

function WbcResourceSplitHost({ openKey, children, closingChildren, width, onResize, splitSide, onToggleSide, onClose, onSplitDragStart, onSplitDragEnd }) {
  var [lastChildren, setLastChildren] = useWbcState(children || null);
  var [entered, setEntered] = useWbcState(false);
  // A closing variant is meaningful only after this host has actually shown
  // content. Without the lastChildren guard, browser hosts would mount an
  // invisible off-canvas closing panel on conversations that never opened a
  // split, allowing it to steal the native browser surface from PiP.
  var visibleChildren = openKey && children
    ? children
    : (lastChildren ? (closingChildren || lastChildren) : null);
  useWbcEffect(function () { if (openKey && children) setLastChildren(children); }, [openKey]);
  useWbcEffect(function () {
    if (openKey) {
      setEntered(false);
      var frame = requestAnimationFrame(function () { setEntered(true); });
      return function () { cancelAnimationFrame(frame); };
    }
    setEntered(false);
    var timer = setTimeout(function () { setLastChildren(null); }, 540);
    return function () { clearTimeout(timer); };
  }, [openKey]);
  if (!visibleChildren) return null;
  return <div className={"wbc-side-agent-split-motion" + (entered ? " open" : "")} data-split-open={openKey ? "true" : "false"}>
    <WbcSideAgentSplitResizer width={width} onResize={onResize} splitSide={splitSide} />
    {visibleChildren}
  </div>;
}

function WbcMapSplitHost({ chatId, item, width, onSelect, onResize, onClose, splitSide, onToggleSide, onSplitDragStart, onSplitDragEnd }) {
  var data = useWbcMapData(chatId);
  var items = wbcMapItems(data);
  var key = wbcMapItemKey(item);
  return (
       <WbcResourceSplitHost openKey={key} width={width} onResize={onResize} splitSide={splitSide} onToggleSide={onToggleSide} onClose={onClose} onSplitDragStart={onSplitDragStart} onSplitDragEnd={onSplitDragEnd}>
      {item ? <WbcMapSplit chatId={chatId} item={item} items={items} onSelect={onSelect} onClose={onClose} /> : null}
    </WbcResourceSplitHost>
  );
}

function WbcMapSplit({ chatId, item, items, onSelect, onClose }) {
  var [pickerOpen, setPickerOpen] = useWbcState(false);
  return (
    <aside className="wbc-side-agent-split wbc-map-split" aria-label={wbcT("chat.side.map", "Map")}>
      <div className="wbc-resource-split-picker-wrap">
        <header className="wbc-side-agent-split-head">
          <button type="button" className="wbc-side-agent-split-picker" onClick={function () { setPickerOpen(function (open) { return !open; }); }} aria-expanded={pickerOpen}>
            <span className="wbc-side-agent-split-title"><span>{wbcT("chat.side.map", "Map")}</span><b>{wbcMapItemLabel(item)}</b></span><span className="wbc-side-agent-split-picker-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
          </button>
          <button type="button" className="wbc-side-agent-split-close" onClick={onClose} aria-label={wbcT("workbenchChat.closeMap", "Close map")}>{WBC_ICONS.x}</button>
        </header>
        <WbcSplitPickerMenu open={pickerOpen} className="wbc-side-agent-split-menu wbc-resource-picker-menu" role="listbox">{items.map(function (next) { var selected = wbcMapItemKey(next) === wbcMapItemKey(item); return <button type="button" key={wbcMapItemKey(next)} className={selected ? "active" : ""} role="option" aria-selected={selected} onClick={function () { setPickerOpen(false); if (onSelect) onSelect(next); }}><span aria-hidden="true">{WBC_SIDE_TAB_ICONS.map}</span><b>{wbcMapItemLabel(next)}</b></button>; })}</WbcSplitPickerMenu>
      </div>
      <div className="wbc-resource-split-body"><WbcMapTab chatId={chatId} focusItem={item} /></div>
    </aside>
  );
}

function WbcMapPaneContent({ chatId, item, onSelect, onClose }) {
  var data = useWbcMapData(chatId);
  return <WbcMapSplit chatId={chatId} item={item} items={wbcMapItems(data)} onSelect={onSelect} onClose={onClose} />;
}

function WbcBrowserSplitHost({ tabId, browserState, browserSessionId, width, onSelect, onResize, onClose, onTakeoverComplete, splitSide, onToggleSide, onSplitDragStart, onSplitDragEnd }) {
  var tabs = browserState && Array.isArray(browserState.tabs) ? browserState.tabs : [];
  var activeStateTab = browserState && browserState.activeTab || {};
  var resolvedTabId = tabId === "__active__"
    ? String(browserState && browserState.activeTabId || activeStateTab.id || "")
    : String(tabId || "");
  var browserSplit = <WbcBrowserSplit active={!!tabId} tabId={resolvedTabId} tabs={tabs} browserState={browserState} browserSessionId={browserSessionId} onSelect={onSelect} onClose={onClose} onTakeoverComplete={onTakeoverComplete} />;
  return (
       <WbcResourceSplitHost openKey={tabId} closingChildren={!tabId ? browserSplit : null} width={width} onResize={onResize} splitSide={splitSide} onToggleSide={onToggleSide} onClose={onClose} onSplitDragStart={onSplitDragStart} onSplitDragEnd={onSplitDragEnd}>
      {tabId ? browserSplit : null}
    </WbcResourceSplitHost>
  );
}

function WbcBrowserSplit({ active: splitActive = true, tabId, tabs, browserState, browserSessionId, onSelect, onClose, onTakeoverComplete }) {
  var [pickerOpen, setPickerOpen] = useWbcState(false);
  var [maximized, setMaximized] = useWbcState(false);
  var browserPickerRef = useWbcRef(null);
  var browserPickerToggleAtRef = useWbcRef(0);
  var [liveState, setLiveState] = useWbcState(browserState || {});
  var bridge = window.cyrene && window.cyrene.browser;
  var hasNativeTabPicker = !!(bridge && typeof bridge.setTabPicker === "function");
  var BrowserIcon = window.CyreneUI.require("browser").Icon;
  var stateTabs = liveState && Array.isArray(liveState.tabs) ? liveState.tabs : [];
  var propTabs = Array.isArray(tabs) ? tabs : [];
  // setContext briefly reports an empty state while ownership moves from the
  // floating window to the split. Preserve the last useful list during that
  // handoff so an open picker cannot collapse into an empty strip by itself.
  var liveTabs = stateTabs.length ? stateTabs : propTabs;
  var active = liveTabs.find(function (tab) { return String(tab.id || "") === String(tabId || ""); }) || liveState && liveState.activeTab || browserState && browserState.activeTab || liveTabs[0] || {};

  useWbcEffect(function () {
    if (!bridge || !browserSessionId) return undefined;
    if (browserState) setLiveState(browserState);
    if (typeof bridge.getState === "function") {
      bridge.getState(browserSessionId).then(function (next) {
        if (next && next.ok !== false) setLiveState(next);
      }).catch(function () {});
    }
    if (typeof bridge.onState !== "function") return undefined;
    return bridge.onState(function (next) {
      if (next && next.ok !== false && String(next.sessionId || "") === String(browserSessionId || "")) setLiveState(next);
    });
  }, [browserSessionId]);

  function updateFrom(next) {
    if (next && next.ok !== false && Array.isArray(next.tabs)) setLiveState(next);
    return next;
  }

  function setBrowserPickerOpen(open) {
    var visible = open === true;
    setPickerOpen(visible);
    if (!hasNativeTabPicker) return;
    bridge.setTabPicker(
      wbcBrowserTabPickerPayload(browserSessionId, visible, "split")
    ).catch(function () { setPickerOpen(false); });
  }

  useWbcEffect(function () {
    if (!hasNativeTabPicker || typeof bridge.onTabPickerAction !== "function") return undefined;
    return bridge.onTabPickerAction(function (action) {
      if (!action || String(action.sessionId || "") !== String(browserSessionId || "")) return;
      if (action.variant !== "split") return;
      setPickerOpen(action.visible === true);
      if (action.type === "select" && action.activeTabId && onSelect) {
        onSelect(action.activeTabId);
      } else if (action.type === "close") {
        if (!Number(action.tabCount || 0)) {
          if (onClose) onClose();
        } else if (action.activeTabId && String(action.tabId || "") === String(active.id || tabId || "") && onSelect) {
          onSelect(action.activeTabId);
        }
      }
    });
  }, [hasNativeTabPicker, browserSessionId, active.id, tabId, onSelect, onClose]);

  useWbcEffect(function () {
    if (!pickerOpen) return undefined;
    function closeBrowserPicker(event) {
      if (event && event.type === "keydown" && event.key !== "Escape") return;
      if (event && event.type === "pointerdown" && browserPickerRef.current && browserPickerRef.current.contains(event.target)) return;
      setBrowserPickerOpen(false);
    }
    document.addEventListener("pointerdown", closeBrowserPicker);
    window.addEventListener("keydown", closeBrowserPicker);
    if (!hasNativeTabPicker) window.addEventListener("blur", closeBrowserPicker);
    return function () {
      document.removeEventListener("pointerdown", closeBrowserPicker);
      window.removeEventListener("keydown", closeBrowserPicker);
      if (!hasNativeTabPicker) window.removeEventListener("blur", closeBrowserPicker);
    };
  }, [pickerOpen, browserSessionId, hasNativeTabPicker]);

  useWbcEffect(function () {
    if (!hasNativeTabPicker) return undefined;
    return function () {
      bridge.setTabPicker(
        wbcBrowserTabPickerPayload(browserSessionId, false, "split")
      ).catch(function () {});
    };
  }, [hasNativeTabPicker, browserSessionId]);

  useWbcEffect(function () {
    if (!maximized) return undefined;
    function restoreOnEscape(event) {
      if (event.key === "Escape") setMaximized(false);
    }
    window.addEventListener("keydown", restoreOnEscape);
    return function () { window.removeEventListener("keydown", restoreOnEscape); };
  }, [maximized]);

  function selectTab(tab) {
    if (!tab || !tab.id) return;
    setBrowserPickerOpen(false);
    if (onSelect) onSelect(tab.id);
    if (bridge && typeof bridge.activateTab === "function") {
      bridge.activateTab({ sessionId: browserSessionId, tabId: tab.id }).then(updateFrom).catch(function () {});
    }
  }

  function refreshTab(tab, event) {
    if (event) { event.preventDefault(); event.stopPropagation(); }
    if (!bridge || !tab || !tab.id) return;
    bridge.reload({ sessionId: browserSessionId, tabId: tab.id }).then(updateFrom).catch(function () {});
  }

  function toggleMute(tab, event) {
    if (event) { event.preventDefault(); event.stopPropagation(); }
    if (!bridge || !tab || !tab.id || typeof bridge.setMuted !== "function") return;
    bridge.setMuted({ sessionId: browserSessionId, tabId: tab.id, muted: !tab.muted }).then(updateFrom).catch(function () {});
  }

  function toggleMaximized(event) {
    if (event) { event.preventDefault(); event.stopPropagation(); }
    setBrowserPickerOpen(false);
    setMaximized(function (value) { return !value; });
  }

  function closeTab(tab, event) {
    if (event) { event.preventDefault(); event.stopPropagation(); }
    if (!bridge || !tab || !tab.id || typeof bridge.closeTab !== "function") return;
    bridge.closeTab({ sessionId: browserSessionId, tabId: tab.id }).then(function (next) {
      updateFrom(next);
      var remaining = next && Array.isArray(next.tabs) ? next.tabs : [];
      if (!remaining.length) {
        setBrowserPickerOpen(false);
        if (onClose) onClose();
        return;
      }
      if (String(tab.id || "") === String(active.id || tabId || "")) {
        var nextId = String(next.activeTabId || next.activeTab && next.activeTab.id || remaining[0].id || "");
        if (nextId && onSelect) onSelect(nextId);
      }
    }).catch(function () {});
  }

  var browserSplit = (
    <aside className={"wbc-side-agent-split wbc-browser-split" + (maximized ? " maximized" : "")} aria-label={wbcT("chat.side.browser", "Browser")}>
      <div ref={browserPickerRef} className="wbc-resource-split-picker-wrap">
        <header className="wbc-side-agent-split-head">
          <button type="button" className="wbc-side-agent-split-picker" onClick={function () {
            if (wbcBrowserTabPickerToggleIsDebounced(browserPickerToggleAtRef)) return;
            setBrowserPickerOpen(!pickerOpen);
          }} aria-expanded={pickerOpen}>
            <span className="wbc-side-agent-split-title"><span>{wbcT("chat.side.browser", "Browser")}</span><b>{active.title || active.url || wbcT("chat.side.browser", "Browser")}</b></span><span className="wbc-side-agent-split-picker-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
          </button>
          <button type="button" className="wbc-browser-split-action" onClick={function (event) { refreshTab(active, event); }} aria-label={wbcT("browser.context.reload", "Reload")} title={wbcT("browser.context.reload", "Reload")}>{BrowserIcon ? <BrowserIcon name="reload" size={15} /> : WBC_ICONS.retry}</button>
          <button type="button" className={"wbc-browser-split-action" + (active.muted ? " active" : "")} onClick={function (event) { toggleMute(active, event); }} aria-label={active.muted ? wbcT("browser.context.unmute", "Unmute") : wbcT("browser.context.mute", "Mute")} title={active.muted ? wbcT("browser.context.unmute", "Unmute") : wbcT("browser.context.mute", "Mute")}>{BrowserIcon ? <BrowserIcon name={active.muted ? "muted" : "volume"} size={15} /> : null}</button>
          <button type="button" className="wbc-browser-split-action" onClick={toggleMaximized} aria-label={maximized ? wbcT("workbenchChat.browserRestoreSize", "Restore") : wbcT("workbenchChat.browserMaximize", "Maximize")} title={maximized ? wbcT("workbenchChat.browserRestoreSize", "Restore") : wbcT("workbenchChat.browserMaximize", "Maximize")}>{maximized ? WBC_ICONS.windowRestore : WBC_ICONS.windowMaximize}</button>
          <button type="button" className="wbc-side-agent-split-close" onClick={onClose} aria-label={wbcT("workbenchChat.closeBrowser", "Close browser")}>{WBC_ICONS.x}</button>
        </header>
        {!hasNativeTabPicker && pickerOpen && <div className="wbc-side-agent-split-menu wbc-resource-picker-menu wbc-browser-picker-menu open" role="listbox">{liveTabs.map(function (tab) { var selected = String(tab.id || "") === String(active.id || tabId || ""); return <div key={tab.id} className={"wbc-browser-picker-row" + (selected ? " active" : "")} role="option" aria-selected={selected}><button type="button" className="wbc-browser-picker-select" onClick={function () { selectTab(tab); }}><span className="wbc-browser-picker-favicon" aria-hidden="true"><span className="wbc-browser-picker-favicon-fallback">{WBC_SIDE_TAB_ICONS.browser}</span>{tab.favicon ? <img src={tab.favicon} alt="" draggable="false" onError={function (event) { event.currentTarget.hidden = true; }} /> : null}</span><b>{tab.title || tab.url || wbcT("chat.side.browser", "Browser")}</b></button><span className="wbc-browser-picker-actions"><button type="button" onClick={function (event) { refreshTab(tab, event); }} aria-label={wbcT("browser.context.reload", "Reload")} title={wbcT("browser.context.reload", "Reload")}>{BrowserIcon ? <BrowserIcon name="reload" size={14} /> : WBC_ICONS.retry}</button><button type="button" className={tab.muted ? "active" : ""} onClick={function (event) { toggleMute(tab, event); }} aria-label={tab.muted ? wbcT("browser.context.unmute", "Unmute") : wbcT("browser.context.mute", "Mute")} title={tab.muted ? wbcT("browser.context.unmute", "Unmute") : wbcT("browser.context.mute", "Mute")}>{BrowserIcon ? <BrowserIcon name={tab.muted ? "muted" : "volume"} size={14} /> : null}</button><button type="button" onClick={function (event) { closeTab(tab, event); }} aria-label={wbcT("browser.context.closeTab", "Close tab")} title={wbcT("browser.context.closeTab", "Close tab")}>{WBC_ICONS.x}</button></span></div>; })}</div>}
      </div>
      <div className="wbc-resource-split-body wbc-browser-split-body">
        {splitActive && window.CyreneUI.require("browser").ViewportPanel ? React.createElement(window.CyreneUI.require("browser").ViewportPanel, { browserState: liveState, browserSessionId: browserSessionId, roundId: liveState && liveState.roundId || browserState && browserState.roundId || "", onClose: onClose, onTakeoverComplete: onTakeoverComplete, zoomEnabled: true, resizeEdgeHintEnabled: true, hideTabStrip: true, hideReload: true, hideMute: true, splitChrome: true }) : null}
      </div>
    </aside>
  );
  if (maximized && window.ReactDOM && typeof window.ReactDOM.createPortal === "function") {
    var workbenchPortalRoot = document.querySelector(".workbench-shell") || document.body;
    return window.ReactDOM.createPortal(browserSplit, workbenchPortalRoot);
  }
  return browserSplit;
}

function WbcSubagentsSplitHost({ open, data, loading, width, onSelectRound, onResize, onClose, splitSide, onToggleSide, onSplitDragStart, onSplitDragEnd }) {
  return (
       <WbcResourceSplitHost openKey={open ? "subagents" : ""} width={width} onResize={onResize} splitSide={splitSide} onToggleSide={onToggleSide} onClose={onClose} onSplitDragStart={onSplitDragStart} onSplitDragEnd={onSplitDragEnd}>
      {open ? <aside className="wbc-side-agent-split wbc-subagents-split" aria-label={wbcT("workbenchChat.subagents", "Subagents")}><header className="wbc-side-agent-split-head wbc-static-split-head"><span className="wbc-side-agent-split-title"><span>{wbcT("workbenchChat.subagents", "Subagents")}</span><b>{data && Array.isArray(data.agents) ? wbcT("workbenchChat.subagent.count", "{n} agents", { n: data.agents.length }) : ""}</b></span><button type="button" className="wbc-side-agent-split-close" onClick={onClose} aria-label={wbcT("workbenchChat.closeSubagents", "Close subagents")}>{WBC_ICONS.x}</button></header><div className="wbc-resource-split-body wbc-subagents-split-body"><WbcSubagentsTab data={data} loading={loading} onSelectRound={onSelectRound} /></div></aside> : null}
    </WbcResourceSplitHost>
  );
}

function WbcSideAgentSplitHost({ agent, agents, width, project, onOpenFile, onUpdate, onSelect, onResize, onClose, splitSide, onToggleSide, onSplitDragStart, onSplitDragEnd }) {
  return (
       <WbcResourceSplitHost openKey={agent && agent.id || ""} width={width} onResize={onResize} splitSide={splitSide} onToggleSide={onToggleSide} onClose={onClose} onSplitDragStart={onSplitDragStart} onSplitDragEnd={onSplitDragEnd}>
      {agent ? (
        <WbcSideAgentSplit
          agent={agent}
          agents={agents}
          project={project}
          onOpenFile={onOpenFile}
          onUpdate={onUpdate}
          onSelect={onSelect}
          onClose={onClose}
        />
      ) : null}
    </WbcResourceSplitHost>
  );
}

// A rail chat dragged onto the side panel opens as a read-only conversation
// beside the main thread. It polls while the source chat is running so a
// background run keeps the split in sync.
function WbcChatSplitHost({ chatId, width, onOpenContent, browserActiveByChat, onResize, onClose, onOpenInMain, splitSide, onToggleSide, project, onSplitDragStart, onSplitDragEnd }) {
  // The openKey must be empty when no chat is split, otherwise the host's
  // close branch (exit animation + lastChildren cleanup) never runs.
  var key = chatId ? "chat:" + chatId : "";
  return (
     <WbcResourceSplitHost openKey={key} width={width} onResize={onResize} splitSide={splitSide} onToggleSide={onToggleSide} onClose={onClose} onSplitDragStart={onSplitDragStart} onSplitDragEnd={onSplitDragEnd}>
      {chatId ? <WbcChatSplit chatId={chatId} project={project} onOpenContent={onOpenContent} browserActiveByChat={browserActiveByChat} onClose={onClose} onOpenInMain={onOpenInMain} splitSide={splitSide} onToggleSide={onToggleSide} onSplitDragStart={onSplitDragStart} onSplitDragEnd={onSplitDragEnd} /> : null}
    </WbcResourceSplitHost>
  );
}

function WbcChatSplit({ chatId, project, onOpenContent, browserActiveByChat, onClose, onDeleted, onOpenInMain, splitSide, onToggleSide, onSplitPointerDown, onSplitDragStart, onSplitDragEnd, menuDisabled }) {
  var [chat, setChat] = useWbcState(null);
  var [loading, setLoading] = useWbcState(true);
  var [error, setError] = useWbcState("");
  var [streamText, setStreamText] = useWbcState("");
  var [streamNotifications, setStreamNotifications] = useWbcState([]);
  var [streamRuntime, setStreamRuntime] = useWbcState(null);
  var [running, setRunning] = useWbcState(false);
  // The grip menu's "open conversation panel" action floats this split chat's
  // own conversation panel here — never the main conversation's panel. Like
  // the main panel it starts collapsed (no accordion tab open), and a
  // pointer-down anywhere outside the panel dismisses it.
  var [splitPanelOpen, setSplitPanelOpen] = useWbcState(false);
  var [splitPanelTab, setSplitPanelTab] = useWbcState("");
  var splitPanelRef = useWbcRef(null);
  var splitRef = useWbcRef(null);
  var scrollRef = useWbcRef(null);
  var chatIdRef = useWbcRef(chatId);
  var pollTimerRef = useWbcRef(null);
  var disposedRef = useWbcRef(false);
  var streamAttachedRef = useWbcRef(false);
  var runStartedAtRef = useWbcRef(Date.now());
  chatIdRef.current = chatId;

  function stopPolling() {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }

  function refresh(background) {
    var requestedId = chatIdRef.current;
    if (!requestedId) {
      setLoading(false);
      return Promise.resolve(null);
    }
    if (!background) setLoading(true);
    return WorkbenchChatModel.getChat(requestedId, { toast: false })
      .then(function (fresh) {
        if (disposedRef.current || String(chatIdRef.current || "") !== requestedId) return null;
        setChat(fresh);
        setLoading(false);
        return fresh;
      })
      .catch(function (err) {
        if (disposedRef.current || String(chatIdRef.current || "") !== requestedId) return null;
        if (Number(err && err.status) === 404 && onDeleted) {
          onDeleted(requestedId);
          return null;
        }
        setError(wbcErrorText(err));
        setLoading(false);
        return null;
      });
  }

  useWbcEffect(function () {
    disposedRef.current = false;
    setChat(null);
    setError("");
    setStreamNotifications([]);
    setStreamRuntime(null);
    setLoading(true);
    setSplitPanelOpen(false);
    setSplitPanelTab("");
    stopPolling();
    refresh(true).then(function (fresh) {
      if (disposedRef.current || !fresh || fresh.status !== "running") return;
      pollTimerRef.current = setInterval(function () {
        refresh(true).then(function (next) {
          if (next && next.status !== "running") stopPolling();
        });
      }, 5000);
    });
    return function () {
      disposedRef.current = true;
      stopPolling();
    };
  }, [chatId]);

  useWbcLayoutEffect(function () {
    var el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [chat && chat.messages && chat.messages.length, loading, running, streamText]);

  // Split conversations own an absolutely positioned composer. Measure it on
  // the split itself instead of relying on the main page's reserve variable:
  // detached windows do not have a `.wbc-page` ancestor, and an invalid/missing
  // reserve would leave the end of the transcript underneath the composer.
  useWbcLayoutEffect(function () {
    var split = splitRef.current;
    var composer = split && split.querySelector(":scope > .wbc-composer");
    if (!split || !composer) return undefined;
    var resizeRaf = 0;
    var lastHeight = 0;
    function commitComposerReserveHeight() {
      resizeRaf = 0;
      var height = Math.ceil(composer.getBoundingClientRect().height);
      if (height <= 0 || height === lastHeight) return;
      lastHeight = height;
      split.style.setProperty("--wbc-composer-reserve-height", height + "px");
    }
    function scheduleComposerReserveHeight() {
      if (resizeRaf) return;
      resizeRaf = requestAnimationFrame(commitComposerReserveHeight);
    }
    commitComposerReserveHeight();
    var observer = typeof ResizeObserver === "function"
      ? new ResizeObserver(scheduleComposerReserveHeight)
      : null;
    if (observer) observer.observe(composer);
    window.addEventListener("resize", scheduleComposerReserveHeight);
    return function () {
      if (observer) observer.disconnect();
      window.removeEventListener("resize", scheduleComposerReserveHeight);
      if (resizeRaf) cancelAnimationFrame(resizeRaf);
      split.style.removeProperty("--wbc-composer-reserve-height");
    };
  }, [chatId]);

  // Dismiss the panel when the user interacts with the split conversation
  // around it (transcript, composer, grip); clicks inside the panel keep it.
  useWbcEffect(function () {
    if (!splitPanelOpen) return undefined;
    function closeOutside(event) {
      if (splitPanelRef.current && !splitPanelRef.current.contains(event.target)) {
        setSplitPanelOpen(false);
      }
    }
    document.addEventListener("pointerdown", closeOutside);
    return function () { document.removeEventListener("pointerdown", closeOutside); };
  }, [splitPanelOpen]);

  // Sending goes through the same streamed sendMessage path as the main
  // conversation, so the split transcript updates live while the agent works.
  function streamHandlers() {
    return {
      onReplyStart: function () {
        if (disposedRef.current) return;
        setStreamText("");
        setStreamRuntime(function (current) { return wbcReduceDetachedRuntime(current, "reply_start"); });
      },
      onReplyDelta: function (delta) {
        if (disposedRef.current) return;
        setStreamText(function (current) { return current + delta; });
        setStreamRuntime(function (current) { return wbcReduceDetachedRuntime(current, "reply_delta", delta); });
      },
      onReplyDone: function (text) {
        if (disposedRef.current) return;
        setStreamText(String(text || ""));
        setStreamRuntime(function (current) { return wbcReduceDetachedRuntime(current, "reply_done", text); });
      },
      onNotification: function (notice) {
        if (disposedRef.current || !notice || !notice.message) return;
        setStreamNotifications(function (current) {
          var key = String(notice.id || (notice.category + "\n" + notice.message));
          if (current.some(function (item) { return String(item.id || (item.category + "\n" + item.message)) === key; })) return current;
          return current.concat([notice]);
        });
        setStreamRuntime(function (current) { return wbcReduceDetachedRuntime(current, "notification", notice); });
      },
      onReasoningStart: function () { if (!disposedRef.current) setStreamRuntime(function (current) { return wbcReduceDetachedRuntime(current, "reasoning_start"); }); },
      onReasoningDelta: function (delta) { if (!disposedRef.current) setStreamRuntime(function (current) { return wbcReduceDetachedRuntime(current, "reasoning_delta", delta); }); },
      onReasoningDone: function (text) { if (!disposedRef.current) setStreamRuntime(function (current) { return wbcReduceDetachedRuntime(current, "reasoning_done", text); }); },
      onFinalizing: function () { if (!disposedRef.current) setStreamRuntime(function (current) { return wbcReduceDetachedRuntime(current, "finalizing"); }); },
      onToolStarted: function (event) { if (!disposedRef.current) setStreamRuntime(function (current) { return wbcReduceDetachedRuntime(current, "tool", event); }); },
      onToolUpdated: function (event) { if (!disposedRef.current) setStreamRuntime(function (current) { return wbcReduceDetachedRuntime(current, "tool", event); }); },
      onToolCompleted: function (event) { if (!disposedRef.current) setStreamRuntime(function (current) { return wbcReduceDetachedRuntime(current, "tool", event); }); },
      onArtifactEvent: function (event) { if (!disposedRef.current) setStreamRuntime(function (current) { return wbcReduceDetachedRuntime(current, "artifact", null, event); }); },
      onSaved: function () {
        if (disposedRef.current) return;
        setRunning(false);
        streamAttachedRef.current = false;
        refresh(true).finally(function () {
          if (disposedRef.current) return;
          setStreamText("");
          setStreamNotifications([]);
          setStreamRuntime(null);
        });
      },
      onAwaitingUser: function (pending) {
        if (disposedRef.current) return;
        if (wbcIsLiveAgentRequest(pending)) {
          setChat(function (prev) {
            return prev ? { ...prev, pendingQuestion: pending, status: "running" } : prev;
          });
          return;
        }
        setRunning(false);
        streamAttachedRef.current = false;
        refresh(true).finally(function () {
          if (disposedRef.current) return;
          setStreamText("");
          setStreamNotifications([]);
          setStreamRuntime(null);
        });
      },
      onGuidanceReceived: function (event) {
        if (disposedRef.current || !event || !event.userMessage) return;
        setChat(function (prev) {
          if (!prev || String(prev.id || "") !== String(chatIdRef.current || "")) return prev;
          return {
            ...prev,
            messages: wbcMergeChronologicalMessages(prev.messages || [], [event.userMessage]),
          };
        });
      },
      onPermissionResolved: function () {
        if (!disposedRef.current) setChat(function (prev) { return prev ? { ...prev, pendingQuestion: null } : prev; });
      },
      onElicitationResolved: function () {
        if (!disposedRef.current) setChat(function (prev) { return prev ? { ...prev, pendingQuestion: null } : prev; });
      },
      onError: function (err) {
        if (disposedRef.current) return;
        setError(wbcErrorText(err));
        setRunning(false);
        streamAttachedRef.current = false;
        setStreamText("");
        setStreamNotifications([]);
        setStreamRuntime(null);
      },
    };
  }

  function ownStream(promise) {
    streamAttachedRef.current = true;
    setRunning(true);
    promise.catch(function (err) {
      if (!disposedRef.current && !(err && err.name === "AbortError")) {
        setError(wbcErrorText(err));
      }
    }).finally(function () {
      if (disposedRef.current) return;
      streamAttachedRef.current = false;
      setRunning(false);
      refresh(true).catch(function () {}).finally(function () {
        if (disposedRef.current) return;
        setStreamText("");
        setStreamNotifications([]);
        setStreamRuntime(null);
      });
    });
  }

  function submit(payload) {
    var question = String(payload && payload.message || "").trim();
    var attachments = payload && Array.isArray(payload.attachments) ? payload.attachments : [];
    var current = chatIdRef.current;
    if ((!question && !attachments.length) || running || !current) return;
    var optimistic = {
      id: "chat_split_pending_" + Date.now(),
      role: "user",
      content: question,
      attachments: attachments,
      createdAt: new Date().toISOString(),
      optimistic: true,
    };
    setChat(function (prev) {
      if (!prev || !prev.id) return prev;
      return { ...prev, messages: (prev.messages || []).concat([optimistic]) };
    });
    setError("");
    setStreamText("");
    runStartedAtRef.current = Date.now();
    setStreamRuntime(wbcCreateDetachedRuntime(runStartedAtRef.current));
    ownStream(WorkbenchChatModel.sendMessage(current, {
      message: question,
      attachments: attachments,
      mode: chat && chat.permissionMode || payload.mode || "default",
      model: chat && chat.modelSelectionId || payload.model || "",
      reasoningEffort: chat && chat.reasoningEffort || payload.reasoningEffort || "",
    }, streamHandlers()));
  }

  function stop() {
    if (!running) return;
    setError("");
    WorkbenchChatModel.interrupt(chatIdRef.current).catch(function (err) {
      if (!disposedRef.current) setError(wbcErrorText(err));
    });
  }

  function guide(message) {
    var current = chatIdRef.current;
    var text = String(message || "").trim();
    if (!running || !current || !text) return Promise.resolve(null);
    setError("");
    return WorkbenchChatModel.sendGuidance(
      current,
      text,
      "guide_" + Date.now() + "_" + Math.random().toString(36).slice(2, 8)
    ).then(function (response) {
      if (response && response.userMessage && !disposedRef.current) {
        setChat(function (prev) {
          if (!prev || String(prev.id || "") !== String(current)) return prev;
          return {
            ...prev,
            messages: wbcMergeChronologicalMessages(prev.messages || [], [response.userMessage]),
          };
        });
      }
      return response;
    }).catch(function (err) {
      if (!disposedRef.current) setError(wbcErrorText(err));
      throw err;
    });
  }

  // Resume a clarification / permission request in the split conversation
  // itself. The main thread's answer handler is bound to activeChatId, which
  // may be a different chat while this pane is open.
  function answerPendingQuestion(questionId, optionText, resumeMode) {
    var current = chatIdRef.current;
    var pending = chat && chat.pendingQuestion || null;
    var formAnswer = optionText && typeof optionText === "object" && optionText.__agentForm === true;
    var answer = formAnswer ? optionText : String(optionText || "").trim();
    var liveRequest = wbcIsLiveAgentRequest(pending);
    if (!current || !questionId || (!formAnswer && !answer) || (running && !liveRequest)) return;
    if (liveRequest) {
      var response = String(pending.kind || "") === "permission.requested"
        ? { type: "option", optionId: String(answer || "") }
        : (formAnswer
          ? { type: "form", form: answer.values && typeof answer.values === "object" ? answer.values : {} }
          : { type: "text", text: String(answer || "") });
      setError("");
      setChat(function (prev) { return prev ? { ...prev, pendingQuestion: null, status: "running" } : prev; });
      return WorkbenchChatModel.answerAgentRequest(current, questionId, response).catch(function (err) {
        if (disposedRef.current) return;
        setError(wbcErrorText(err));
        setChat(function (prev) { return prev ? { ...prev, pendingQuestion: pending } : prev; });
      });
    }
    var optimisticAnswer = {
      id: "chat_split_answer_pending_" + Date.now(),
      role: "user",
      content: String(answer),
      createdAt: new Date().toISOString(),
      answerToQuestionId: questionId,
      optimistic: true,
    };
    setError("");
    setStreamText("");
    runStartedAtRef.current = Date.now();
    setRunning(true);
    setChat(function (prev) {
      if (!prev || String(prev.id || "") !== String(current)) return prev;
      return {
        ...prev,
        pendingQuestion: null,
        status: "running",
        messages: wbcMergeChronologicalMessages(prev.messages || [], [optimisticAnswer]),
      };
    });
    var answerMode = wbcNormalizePermissionMode(
      resumeMode,
      chat && chat.permissionMode ? chat.permissionMode : "default"
    );
    WorkbenchChatModel.answerChat(current, questionId, answer, { mode: answerMode })
      .then(function () { return refresh(true); })
      .catch(function (err) {
        if (disposedRef.current || String(chatIdRef.current || "") !== String(current)) return;
        setError(wbcErrorText(err));
        // Restore the durable pending question so the user can retry.
        refresh(true).catch(function () {});
      })
      .finally(function () {
        if (disposedRef.current || String(chatIdRef.current || "") !== String(current)) return;
        setRunning(false);
        setStreamText("");
      });
  }

  function openContent(type, payload) {
    setSplitPanelOpen(false);
    if (onOpenContent) onOpenContent(type, payload, chat);
  }

  function openFile(file) {
    openContent("viewer", file);
  }

  var messages = chat && Array.isArray(chat.messages) ? chat.messages : [];
  var errorText = error;
  return (
    <aside ref={splitRef} className="wbc-side-agent-split wbc-chat-split wbc-conversation-split" data-tour="chat_split_pane" aria-label={wbcT("workbenchChat.chatSplitLabel", "Chat")}>
      <div className="wbc-split-panel-grip">
        <WbcSplitGripBar
          dragSource="split"
          side={splitSide}
          onToggleSide={onToggleSide}
          onClose={onClose}
          onOpenConversationPanel={function () { setSplitPanelOpen(true); }}
        onSplitPointerDown={onSplitPointerDown}
        onSplitDragStart={onSplitDragStart}
        onSplitDragEnd={onSplitDragEnd}
        menuDisabled={menuDisabled}
      />
      </div>
      {splitPanelOpen && (
        <div className="wbc-split-chat-panel" ref={splitPanelRef} role="dialog" aria-label={wbcT("workbenchChat.sidePanelTitle", "Conversation panel")}>
          <WbcSide
            project={project}
            chat={chat}
            chatLoading={loading}
            chatDetailed={!!chat}
            chats={chat ? [chat] : []}
            activeChatId={chatId}
            onSelectChat={function () {}}
            runtime={running ? { text: streamText } : null}
            subagentData={null}
            subagentLoading={false}
            onSelectSubagentRound={function () {}}
            tab={splitPanelTab}
            onTabChange={setSplitPanelTab}
            viewerFile={null}
            onOpenFile={openFile}
            onSelectArtifact={function (file) { openContent("artifact", file); }}
            onSelectChange={function (change) { openContent("change", change); }}
            onSelectViewer={function (file) { openContent("viewer", file); }}
            onSelectMap={function (item) { openContent("map", item); }}
            onSelectBrowser={function (tabId) { openContent("browser", tabId); }}
            onOpenSubagents={function () { openContent("subagents", true); }}
            onViewerViewed={function () {}}
            onRename={function () {}}
            onDelete={function () {}}
            onToTask={function () {}}
            toTaskBusy={false}
            onCompact={function () {}}
            compactBusy={false}
            sideAgents={[]}
            sideAgentsLoading={false}
            activeSideAgentId=""
            onSelectSideAgent={function () {}}
            onUpdateSideAgent={function () {}}
            onDeleteSideAgent={function () {}}
            onBrowserTakeoverComplete={function () { return Promise.resolve(); }}
            browserActiveByChat={browserActiveByChat}
            browserSuppressed={false}
            onToggleSide={function () {}}
            floating={true}
            onCloseFloating={function () { setSplitPanelOpen(false); }}
          />
        </div>
      )}
      <div className="wbc-thread-stage wbc-chat-split-stage">
        <div className="wbc-thread" data-cyrene-revision-volatile="true" ref={scrollRef}>
        {loading && !messages.length && (
          <div className="wbc-chat-split-state" role="status">
            <span className="wbc-spinner" aria-hidden="true" />
            <span>{wbcT("workbenchChat.loading", "Loading chats...")}</span>
          </div>
        )}
        {!loading && !messages.length && !errorText && (
          <div className="wbc-chat-split-state">{wbcT("workbenchChat.noMessages", "No messages yet")}</div>
        )}
        {errorText && <div className="wbc-side-agent-error" role="alert">{errorText}</div>}
        {messages.map(function (message) {
          var isActiveQuestion = !!(
            message.questionPrompt
            && chat.pendingQuestion
            && String(chat.pendingQuestion.id || "") === String(message.questionId || "")
          );
          if (isActiveQuestion) {
            return <WbcThreadItem key={message.id || message.createdAt}><WbcQuestionPrompt pending={chat.pendingQuestion} onAnswer={answerPendingQuestion} busy={running && !wbcIsLiveAgentRequest(chat.pendingQuestion)} trace={message.trace} /></WbcThreadItem>;
          }
          if (message.modelStatusCard) {
            return <WbcThreadItem key={message.id || message.createdAt}><WbcModelStatusMessage msg={message} /></WbcThreadItem>;
          }
          if (message.notificationCard) {
            return <WbcThreadItem key={message.id || message.createdAt}><WbcAgentNotification notice={message.notification} /></WbcThreadItem>;
          }
          return (
            <WbcThreadItem key={message.id || message.createdAt}>
              {message.role === "user"
                ? <WbcUserMessage msg={message} onOpenFile={openFile} />
                : <WbcAssistantMessage msg={message} onOpenFile={openFile} chatId={typeof chatId === "string" ? chatId : ""} />}
            </WbcThreadItem>
          );
        })}
        {running && <WbcRuntimeTranscript runtime={streamRuntime || { ...wbcCreateDetachedRuntime(runStartedAtRef.current), text: streamText, notifications: streamNotifications }} onOpenFile={openFile} />}
        {chat && chat.pendingQuestion && chat.pendingQuestion.id && (!running || wbcIsLiveAgentRequest(chat.pendingQuestion)) && !messages.some(function (message) {
          return message.questionPrompt && String(message.questionId || "") === String(chat.pendingQuestion.id || "");
        }) && (
          <WbcThreadItem><WbcQuestionPrompt pending={chat.pendingQuestion} onAnswer={answerPendingQuestion} busy={running && !wbcIsLiveAgentRequest(chat.pendingQuestion)} /></WbcThreadItem>
        )}
        </div>
      </div>
      <WbcComposer
        key={"split-composer:" + String(chat && chat.id || chatId || "")}
        chat={chat}
        project={project}
        runtime={streamText ? { text: streamText } : null}
        running={running}
        onSend={submit}
        onGuidance={guide}
        onInterrupt={stop}
        draftNamespace={"chat-split:"}
        autoFocus={false}
        clearOnSend={true}
        error={error}
        errorKind="message"
        compact={false}
        placeholder={wbcT("workbenchChat.placeholder", "Message Cyrene...")}
        runningPlaceholder={wbcT("workbenchChat.placeholderRunning", "Send guidance to the running agent...")}
      />
    </aside>
  );
}

function WbcPaneContextTrackDropSurface({ card, dropKey, dropTarget, onDropOver, onDrop }) {
  var activeEdge = dropTarget && String(dropTarget.dropKey || "") === String(dropKey || "")
    ? dropTarget.edge
    : "";
  var activeLabel = activeEdge === "right"
    ? wbcT("workbenchChat.dropPaneRight", "Release to open on the right")
    : wbcT("workbenchChat.dropPaneLeft", "Release to open on the left");
  return (
    <React.Fragment>
      <div
        className="wbc-pane-context-drop-sensor left"
        onDragEnter={function (event) { onDropOver(event, card.id, "left", dropKey); }}
        onDragOver={function (event) { onDropOver(event, card.id, "left", dropKey); }}
        onDrop={function (event) { onDrop(event, card.id, "left"); }}
      />
      <div
        className="wbc-pane-context-drop-sensor right"
        onDragEnter={function (event) { onDropOver(event, card.id, "right", dropKey); }}
        onDragOver={function (event) { onDropOver(event, card.id, "right", dropKey); }}
        onDrop={function (event) { onDrop(event, card.id, "right"); }}
      />
      {activeEdge === "left" || activeEdge === "right" ? (
        <div className={"wbc-pane-card-drop-zone context-preview " + activeEdge + " active"}>
          <span>{activeLabel}</span>
        </div>
      ) : null}
    </React.Fragment>
  );
}

function WbcPaneFiveWayDropSurface({ card, dropKey, replaceConversation, dropTarget, onDropOver, onDrop }) {
  var activeEdge = dropTarget && String(dropTarget.dropKey || "") === String(dropKey || "")
    ? dropTarget.edge
    : "";
  var replaceLabel = replaceConversation
    ? wbcT("workbenchChat.dropConversationReplace", "Release to replace the current conversation")
    : wbcT("workbenchChat.dropPaneReplace", "Release to replace this split");
  var axisLabel = activeEdge === "left"
    ? wbcT("workbenchChat.dropPaneLeft", "Release to open on the left")
    : activeEdge === "right"
    ? wbcT("workbenchChat.dropPaneRight", "Release to open on the right")
    : activeEdge === "top"
    ? wbcT("workbenchChat.dropPaneTop", "Release to open above")
    : activeEdge === "bottom"
    ? wbcT("workbenchChat.dropPaneBottom", "Release to open below")
    : replaceLabel;
  return (
    <React.Fragment>
      <div
        className="wbc-pane-card-axis-sensor top"
        onDragEnter={function (event) { onDropOver(event, card.id, "top", dropKey); }}
        onDragOver={function (event) { onDropOver(event, card.id, "top", dropKey); }}
        onDrop={function (event) { onDrop(event, card.id, "top"); }}
      />
      <div
        className="wbc-pane-card-axis-sensor left"
        onDragEnter={function (event) { onDropOver(event, card.id, "left", dropKey); }}
        onDragOver={function (event) { onDropOver(event, card.id, "left", dropKey); }}
        onDrop={function (event) { onDrop(event, card.id, "left"); }}
      />
      <div
        className="wbc-pane-card-axis-sensor replace"
        onDragEnter={function (event) { onDropOver(event, card.id, "replace", dropKey); }}
        onDragOver={function (event) { onDropOver(event, card.id, "replace", dropKey); }}
        onDrop={function (event) { onDrop(event, card.id, "replace"); }}
      />
      <div
        className="wbc-pane-card-axis-sensor right"
        onDragEnter={function (event) { onDropOver(event, card.id, "right", dropKey); }}
        onDragOver={function (event) { onDropOver(event, card.id, "right", dropKey); }}
        onDrop={function (event) { onDrop(event, card.id, "right"); }}
      />
      <div
        className="wbc-pane-card-axis-sensor bottom"
        onDragEnter={function (event) { onDropOver(event, card.id, "bottom", dropKey); }}
        onDragOver={function (event) { onDropOver(event, card.id, "bottom", dropKey); }}
        onDrop={function (event) { onDrop(event, card.id, "bottom"); }}
      />
      {activeEdge ? (
        <div className={"wbc-pane-card-drop-zone axis-preview " + activeEdge + " active"}>
          <span>{axisLabel}</span>
        </div>
      ) : null}
    </React.Fragment>
  );
}

function WbcPaneCardFrame({ card, dropKey, children, grip, dropEnabled, replaceOnly, axisEnabled, replaceConversation, dropTarget, onDropOver, onDrop, onDropLeave }) {
  var activeEdge = dropTarget && String(dropTarget.dropKey || "") === String(dropKey || "")
    ? dropTarget.edge
    : "";
  var replaceLabel = replaceConversation
    ? wbcT("workbenchChat.dropConversationReplace", "Release to replace the current conversation")
    : wbcT("workbenchChat.dropPaneReplace", "Release to replace this split");
  return (
    <article
      className={"wbc-pane-card wbc-pane-card-" + String(card && card.kind || "content")}
      data-pane-card-id={card && card.id || ""}
      data-pane-drop-key={dropKey || card.id}
    >
      {grip ? <div className="wbc-pane-card-grip">{grip}</div> : null}
      {children}
      {dropEnabled ? (
        <div className={"wbc-pane-card-drop-layer" + (replaceOnly ? " replace-only" : "") + (axisEnabled ? " axis-enabled" : "")} onDragLeave={onDropLeave}>
          {replaceOnly ? (
            <div
              className={"wbc-pane-card-drop-zone replace" + (activeEdge === "replace" ? " active" : "")}
              onDragEnter={function (event) { onDropOver(event, card.id, "replace", dropKey); }}
              onDragOver={function (event) { onDropOver(event, card.id, "replace", dropKey); }}
              onDrop={function (event) { onDrop(event, card.id, "replace"); }}
            ><span>{replaceLabel}</span></div>
          ) : axisEnabled ? (
            <WbcPaneFiveWayDropSurface
              card={card}
              dropKey={dropKey}
              replaceConversation={replaceConversation}
              dropTarget={dropTarget}
              onDropOver={onDropOver}
              onDrop={onDrop}
            />
          ) : (
            <React.Fragment>
              <div
                className={"wbc-pane-card-drop-zone top" + (activeEdge === "top" ? " active" : "")}
                onDragEnter={function (event) { onDropOver(event, card.id, "top", dropKey); }}
                onDragOver={function (event) { onDropOver(event, card.id, "top", dropKey); }}
                onDrop={function (event) { onDrop(event, card.id, "top"); }}
              ><span>{wbcT("workbenchChat.dropPaneTop", "Release to open above")}</span></div>
              <div
                className={"wbc-pane-card-drop-zone replace" + (activeEdge === "replace" ? " active" : "")}
                onDragEnter={function (event) { onDropOver(event, card.id, "replace", dropKey); }}
                onDragOver={function (event) { onDropOver(event, card.id, "replace", dropKey); }}
                onDrop={function (event) { onDrop(event, card.id, "replace"); }}
              ><span>{replaceLabel}</span></div>
              <div
                className={"wbc-pane-card-drop-zone bottom" + (activeEdge === "bottom" ? " active" : "")}
                onDragEnter={function (event) { onDropOver(event, card.id, "bottom", dropKey); }}
                onDragOver={function (event) { onDropOver(event, card.id, "bottom", dropKey); }}
                onDrop={function (event) { onDrop(event, card.id, "bottom"); }}
              ><span>{wbcT("workbenchChat.dropPaneBottom", "Release to open below")}</span></div>
            </React.Fragment>
          )}
        </div>
      ) : null}
    </article>
  );
}

function WbcPaneRowResizer({ ratio, onResize }) {
  var safeRatio = Math.max(0.2, Math.min(0.8, Number(ratio) || 0.5));
  // Grid gaps do not participate in fr sizing. Position the separator at the
  // exact centre of the 12px gap instead of at a percentage of the full
  // column, which drifts into one of the cards as the ratio changes.
  var seamOffset = 6 - (safeRatio * 12);
  var seamTop = "calc(" + (safeRatio * 100) + "% "
    + (seamOffset < 0 ? "- " : "+ ") + Math.abs(seamOffset) + "px)";
  function startResize(event) {
    if (event.button !== 0 || !onResize) return;
    event.preventDefault();
    var column = event.currentTarget && event.currentTarget.closest
      ? event.currentTarget.closest(".wbc-pane-column")
      : null;
    if (!column) return;
    var rect = column.getBoundingClientRect();
    function move(moveEvent) {
      var trackHeight = Math.max(1, rect.height - 12);
      onResize(Math.max(0.2, Math.min(0.8, (moveEvent.clientY - rect.top - 6) / trackHeight)));
    }
    function stop() {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
      document.body.classList.remove("wbc-resizing-pane-row");
    }
    document.body.classList.add("wbc-resizing-pane-row");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
    window.addEventListener("pointercancel", stop, { once: true });
  }
  function keyboardResize(event) {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    onResize((Number(ratio) || 0.5) + (event.key === "ArrowUp" ? -0.04 : 0.04));
  }
  return (
    <div
      className="wbc-pane-row-resizer"
      role="separator"
      aria-orientation="horizontal"
      aria-label={wbcT("workbenchChat.resizePaneHeight", "Resize split height")}
      aria-valuenow={Math.round(safeRatio * 100)}
      tabIndex={0}
      style={{ top: seamTop }}
      onPointerDown={startResize}
      onKeyDown={keyboardResize}
    />
  );
}

function WbcPaneColumnResizer({ width, onResize }) {
  function boundsFor(layout) {
    var rect = layout.getBoundingClientRect();
    // 24px outer padding + 12px card gap. Both tracks receive the exact same
    // 380px floor; on compact windows that floor shrinks symmetrically.
    var trackWidth = Math.max(0, rect.width - 36);
    var minimum = Math.min(380, trackWidth / 2);
    return {
      minimum: minimum,
      maximum: Math.max(minimum, trackWidth - minimum),
    };
  }
  function clampFor(layout, value) {
    var bounds = boundsFor(layout);
    return Math.max(bounds.minimum, Math.min(bounds.maximum, Number(value) || 520));
  }
  function startResize(event) {
    if (event.button !== 0 || !onResize) return;
    event.preventDefault();
    var layout = event.currentTarget && event.currentTarget.closest
      ? event.currentTarget.closest(".wbc-pane-layout")
      : null;
    if (!layout) return;
    var startX = event.clientX;
    var startWidth = clampFor(layout, width);
    function move(moveEvent) {
      var next = startWidth + (startX - moveEvent.clientX);
      onResize(clampFor(layout, next));
    }
    function stop() {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
      document.body.classList.remove("wbc-resizing-pane-column");
    }
    document.body.classList.add("wbc-resizing-pane-column");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
    window.addEventListener("pointercancel", stop, { once: true });
  }
  function keyboardResize(event) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    var layout = event.currentTarget && event.currentTarget.closest
      ? event.currentTarget.closest(".wbc-pane-layout")
      : null;
    var next = (Number(width) || 520) + (event.key === "ArrowLeft" ? 16 : -16);
    onResize(layout ? clampFor(layout, next) : next);
  }
  return (
    <div
      className="wbc-pane-column-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label={wbcT("workbenchChat.detailPanel.resize", "Resize detail panel")}
      aria-valuenow={Math.round(Number(width) || 520)}
      tabIndex={0}
      onPointerDown={startResize}
      onKeyDown={keyboardResize}
    />
  );
}

function WbcSideAgentSplitResizer({ width, onResize, splitSide }) {
  function clampWidth(next, page) {
    return wbcClampSideSplitWidthForPage(next, page);
  }

  function startResize(event) {
    if (event.button !== 0 || !onResize) return;
    event.preventDefault();
    var startX = event.clientX;
    var startWidth = Number(width) || 520;
    var handle = event.currentTarget;
    var page = handle && handle.closest ? handle.closest(".wbc-page") : null;
    var frame = 0;
    var nextWidth = startWidth;

    // Keep the panels live, but do only the single layout write here. Heavy
    // renderer observers pause while the body class is present and receive one
    // explicit refresh when the gesture finishes. The native browser is the
    // exception: its own ResizeObserver streams coalesced bounds so the page
    // stays attached to the divider throughout the drag.
    function paint() {
      frame = 0;
      if (page) page.style.setProperty("--wbc-side-track-width", nextWidth + "px");
    }
    function move(moveEvent) {
      // The resizer rides the edge that faces the conversation: right-anchored
      // panels widen when dragged left, left-anchored ones when dragged right.
      var delta = splitSide === "left"
        ? (moveEvent.clientX - startX)
        : (startX - moveEvent.clientX);
      nextWidth = clampWidth(startWidth + delta, page);
      if (!frame) frame = requestAnimationFrame(paint);
    }
    function stop(stopEvent) {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
      if (frame) {
        cancelAnimationFrame(frame);
        paint();
      }
      if (handle && handle.releasePointerCapture && handle.hasPointerCapture && handle.hasPointerCapture(event.pointerId)) {
        handle.releasePointerCapture(event.pointerId);
      }
      document.body.classList.remove("wbc-resizing-side-agent");
      // pointercancel keeps the last painted size as the least surprising
      // result and, like pointerup, performs one durable React/storage commit.
      onResize(nextWidth);
      window.dispatchEvent(new CustomEvent("workbench:split-resize-end", {
        detail: { width: nextWidth, side: splitSide },
      }));
      wbcNotifyBrowserLayoutChanged();
    }
    if (handle && handle.setPointerCapture) handle.setPointerCapture(event.pointerId);
    document.body.classList.add("wbc-resizing-side-agent");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
    window.addEventListener("pointercancel", stop, { once: true });
  }

  function resizeWithKeyboard(event) {
    if (!onResize || (event.key !== "ArrowLeft" && event.key !== "ArrowRight")) return;
    event.preventDefault();
    var step = splitSide === "left"
      ? (event.key === "ArrowRight" ? 16 : -16)
      : (event.key === "ArrowLeft" ? 16 : -16);
    var page = event.currentTarget && event.currentTarget.closest
      ? event.currentTarget.closest(".wbc-page")
      : null;
    onResize(clampWidth((Number(width) || 520) + step, page));
  }

  return (
    <div
      className="wbc-side-agent-split-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label={wbcT("workbenchChat.detailPanel.resize", "Resize detail panel")}
      aria-valuenow={Math.round(Number(width) || 520)}
      aria-valuemin={380}
      tabIndex={0}
      onPointerDown={startResize}
      onKeyDown={resizeWithKeyboard}
    />
  );
}

// Horizontal grip for a detail split: pointer capture keeps the drag alive
// across the native window boundary. Dropping it on the rail closes it,
// dropping it on either side moves it there, and click opens a menu (open the floating
// conversation panel or a new conversation, swap the side/vertical order, or
// close). Every card exposes one grip; detached chat cards keep their existing
// internal grip while the shared card frame supplies it for all other kinds.
function WbcSplitGripBar({ dragSource, side, onToggleSide, onClose, onOpenConversationPanel, openPanelLabel, onNewConversation, menuType, onSplitPointerDown, onSplitDragStart, onSplitDragEnd, menuDisabled }) {
  var [menuOpen, setMenuOpen] = useWbcState(false);
  var rootRef = useWbcRef(null);
  var pointerDragRef = useWbcRef(null);

  // Electron's native browser surface is composited above renderer DOM, so a
  // CSS z-index alone cannot keep this menu visible. Reuse the shared overlay
  // coordinator: it paints an equal-sized screenshot proxy before hiding the
  // native layer, preserving the browser body's exact geometry without the
  // stretched white placeholder caused by resizing its viewport.
  useWbcEffect(function () {
    if (menuDisabled && menuOpen) setMenuOpen(false);
  }, [menuDisabled, menuOpen]);

  useWbcEffect(function () {
    if (!menuOpen) return undefined;
    var overlays;
    try { overlays = window.CyreneUI.require("browser-overlays"); } catch (e) {}
    if (!overlays || typeof overlays.adjust !== "function") return undefined;
    overlays.adjust(1);
    return function () { overlays.adjust(-1); };
  }, [menuOpen]);

  useWbcEffect(function () {
    if (!menuOpen) return undefined;
    function closeOutside(event) {
      if (rootRef.current && !rootRef.current.contains(event.target)) setMenuOpen(false);
    }
    document.addEventListener("pointerdown", closeOutside);
    return function () { document.removeEventListener("pointerdown", closeOutside); };
  }, [menuOpen]);

  function handleKey(event) {
    if (menuDisabled) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setMenuOpen(function (open) { return !open; });
    } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      if (onToggleSide) onToggleSide();
    }
  }

  function captureDragPointer(event) {
    if (event.button !== 0) return;
    var target = event.currentTarget;
    var rect = target.getBoundingClientRect();
    target.dataset.wbcDragClientX = String(event.clientX);
    target.dataset.wbcDragClientY = String(event.clientY);
    target.dataset.wbcDragHandleX = String(event.clientX - rect.left);
    target.dataset.wbcDragHandleY = String(event.clientY - rect.top);
    pointerDragRef.current = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      moved: false,
    };
    if (typeof target.setPointerCapture === "function") {
      try { target.setPointerCapture(event.pointerId); } catch (e) {}
    }
    event.preventDefault();
  }

  function trackDragPointer(event) {
    var drag = pointerDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (!drag.moved && Math.abs(event.clientX - drag.x) + Math.abs(event.clientY - drag.y) > 4) {
      drag.moved = true;
      // A click should only open the grip menu. Mounting the full-card drag
      // clone on pointerdown made task controls briefly reflow in the clone.
      // Start the shared drag pipeline only after an intentional movement.
      if (onSplitDragStart) onSplitDragStart(event, dragSource);
    }
  }

  function releaseDragPointer(event) {
    var drag = pointerDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    var target = event.currentTarget;
    if (typeof target.releasePointerCapture === "function") {
      try { target.releasePointerCapture(event.pointerId); } catch (e) {}
    }
    target.dataset.wbcPointerDragged = drag.moved ? "true" : "false";
    pointerDragRef.current = null;
  }

  var swapLabel = wbcT("workbenchChat.splitMoveOtherSide", "Move split to the other side");
  function openConversationPanel() {
    setMenuOpen(false);
    if (onOpenConversationPanel) onOpenConversationPanel();
  }
  return (
    <div className="wbc-split-grip-bar-host" ref={rootRef}>
      <div
        className="wbc-side-split-grip-bar"
        role="button"
        tabIndex={0}
        aria-label={wbcT("workbenchChat.detailPanel.move", "Move split panel")}
        title={wbcT("workbenchChat.detailPanel.move", "Move split panel")}
        onPointerDown={captureDragPointer}
        onPointerMove={trackDragPointer}
        onPointerUp={releaseDragPointer}
        onPointerCancel={releaseDragPointer}
        onClick={function (event) {
          if (event.currentTarget.dataset.wbcPointerDragged === "true") {
            event.currentTarget.dataset.wbcPointerDragged = "false";
            return;
          }
          if (!menuDisabled) setMenuOpen(function (open) { return !open; });
        }}
        onKeyDown={handleKey}
      >
        <span className="wbc-side-split-grip-bar-visual" aria-hidden="true" />
      </div>
      {menuOpen && !menuDisabled && (
        <div className="wbc-side-split-grip-menu" role="menu">
          {menuType === "content" ? (
            onNewConversation ? <button
              type="button"
              role="menuitem"
              onClick={function () { setMenuOpen(false); onNewConversation(); }}
            >
              <span aria-hidden="true">{WBC_ICONS.plus}</span>
              <span>{wbcT("workbenchChat.newConversation", "New conversation")}</span>
            </button> : null
          ) : (
            <button
              type="button"
              role="menuitem"
              onClick={openConversationPanel}
            >
              <span aria-hidden="true">{WBC_ICONS.sidebar}</span>
              <span>{openPanelLabel || wbcT("workbenchChat.detailPanel.openConversationPanel", "Open conversation panel")}</span>
            </button>
          )}
          <button
            type="button"
            role="menuitem"
            onClick={function () { setMenuOpen(false); if (onToggleSide) onToggleSide(); }}
          >
            <span aria-hidden="true">{WBC_ICONS.chevronLeft}{WBC_ICONS.chevronRight}</span>
            <span>{swapLabel}</span>
          </button>
          {onClose ? (
            <button
              type="button"
              role="menuitem"
              onClick={function () { setMenuOpen(false); onClose(); }}
            >
              <span aria-hidden="true">{WBC_ICONS.x}</span>
              <span>{wbcT("workbenchChat.detailPanel.close", "Close split panel")}</span>
            </button>
          ) : null}
        </div>
      )}
    </div>
  );
}

function WbcSideAgentSplit({ agent, agents, project, onOpenFile, onUpdate, onSelect, onClose }) {
  var [pickerOpen, setPickerOpen] = useWbcState(false);
  var headerRef = useWbcRef(null);
  var items = Array.isArray(agents) ? agents : [];
  var title = String((agent && (agent.sourceQuote || agent.title)) || "")
    .replace(/\s+/g, " ")
    .trim();

  useWbcEffect(function () {
    if (!pickerOpen) return undefined;
    function closePicker(event) {
      if (headerRef.current && !headerRef.current.contains(event.target)) setPickerOpen(false);
    }
    document.addEventListener("pointerdown", closePicker);
    return function () { document.removeEventListener("pointerdown", closePicker); };
  }, [pickerOpen]);

  return (
    <aside className="wbc-side-agent-split" aria-label={wbcT("workbenchChat.sideAgent.conversation", "Side conversation")}>
      <header className="wbc-side-agent-split-head" ref={headerRef}>
        <button
          type="button"
          className="wbc-side-agent-split-picker"
          onClick={function () { setPickerOpen(function (open) { return !open; }); }}
          aria-expanded={pickerOpen}
          aria-haspopup="listbox"
        >
          <span className="wbc-side-agent-split-title">
          <span>{wbcT("workbenchChat.sideAgent.tab", "Side questions")}</span>
          <b title={title}>{title || wbcT("workbenchChat.sideAgent.untitled", "Side question")}</b>
          </span>
          <span className="wbc-side-agent-split-picker-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
        </button>
        <button
          type="button"
          className="wbc-side-agent-split-close"
          onClick={onClose}
          title={wbcT("workbenchChat.sideAgent.closeConversation", "Close side conversation")}
          aria-label={wbcT("workbenchChat.sideAgent.closeConversation", "Close side conversation")}
        >{WBC_ICONS.x}</button>
        <WbcSplitPickerMenu open={pickerOpen} role="listbox" aria-label={wbcT("workbenchChat.sideAgent.list", "Side questions")}>
            {items.map(function (item, index) {
              var itemTitle = String(item.sourceQuote || item.title || "").replace(/\s+/g, " ").trim();
              var selected = item.id === (agent && agent.id);
              return (
                <button
                  type="button"
                  key={item.id}
                  className={selected ? "active" : ""}
                  role="option"
                  aria-selected={selected}
                  onClick={function () {
                    setPickerOpen(false);
                    if (onSelect) onSelect(item.id);
                  }}
                >
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <b>{itemTitle || wbcT("workbenchChat.sideAgent.untitled", "Side question")}</b>
                </button>
              );
            })}
        </WbcSplitPickerMenu>
      </header>
      <WbcSideAgentTab agent={agent} project={project} onOpenFile={onOpenFile} onUpdate={onUpdate} />
    </aside>
  );
}

function WbcSideAgentTab({ agent, project, onOpenFile, onUpdate }) {
  var agentRef = useWbcRef(agent);
  var scrollRef = useWbcRef(null);
  var mountedRef = useWbcRef(true);
  var streamAttachedRef = useWbcRef(false);
  var runStartedAtRef = useWbcRef(Date.now());
  var [running, setRunning] = useWbcState(!!(agent && agent.status === "running"));
  var [streamText, setStreamText] = useWbcState("");
  var [streamNotifications, setStreamNotifications] = useWbcState([]);
  var [streamRuntime, setStreamRuntime] = useWbcState(null);
  var [error, setError] = useWbcState("");

  useWbcEffect(function () {
    agentRef.current = agent;
    setRunning(!!(agent && agent.status === "running"));
  }, [agent]);

  useWbcEffect(function () {
    mountedRef.current = true;
    return function () { mountedRef.current = false; };
  }, []);

  useWbcLayoutEffect(function () {
    var el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [agent && agent.messages && agent.messages.length, streamText, running]);

  function refreshAgent() {
    return WorkbenchChatModel.getChat(agentRef.current.id).then(function (fresh) {
      agentRef.current = fresh;
      onUpdate(fresh);
      if (mountedRef.current) setRunning(fresh.status === "running");
      return fresh;
    });
  }

  function streamHandlers() {
    return {
      onReplyStart: function () {
        if (mountedRef.current) setStreamText("");
        if (mountedRef.current) setStreamRuntime(function (current) { return wbcReduceDetachedRuntime(current, "reply_start"); });
      },
      onReplyDelta: function (delta) {
        if (mountedRef.current) setStreamText(function (current) { return current + delta; });
        if (mountedRef.current) setStreamRuntime(function (current) { return wbcReduceDetachedRuntime(current, "reply_delta", delta); });
      },
      onReplyDone: function (text) {
        if (mountedRef.current) setStreamText(String(text || ""));
        if (mountedRef.current) setStreamRuntime(function (current) { return wbcReduceDetachedRuntime(current, "reply_done", text); });
      },
      onNotification: function (notice) {
        if (!mountedRef.current || !notice || !notice.message) return;
        setStreamNotifications(function (current) {
          var key = String(notice.id || (notice.category + "\n" + notice.message));
          if (current.some(function (item) { return String(item.id || (item.category + "\n" + item.message)) === key; })) return current;
          return current.concat([notice]);
        });
        setStreamRuntime(function (current) { return wbcReduceDetachedRuntime(current, "notification", notice); });
      },
      onReasoningStart: function () { if (mountedRef.current) setStreamRuntime(function (current) { return wbcReduceDetachedRuntime(current, "reasoning_start"); }); },
      onReasoningDelta: function (delta) { if (mountedRef.current) setStreamRuntime(function (current) { return wbcReduceDetachedRuntime(current, "reasoning_delta", delta); }); },
      onReasoningDone: function (text) { if (mountedRef.current) setStreamRuntime(function (current) { return wbcReduceDetachedRuntime(current, "reasoning_done", text); }); },
      onFinalizing: function () { if (mountedRef.current) setStreamRuntime(function (current) { return wbcReduceDetachedRuntime(current, "finalizing"); }); },
      onToolStarted: function (event) { if (mountedRef.current) setStreamRuntime(function (current) { return wbcReduceDetachedRuntime(current, "tool", event); }); },
      onToolUpdated: function (event) { if (mountedRef.current) setStreamRuntime(function (current) { return wbcReduceDetachedRuntime(current, "tool", event); }); },
      onToolCompleted: function (event) { if (mountedRef.current) setStreamRuntime(function (current) { return wbcReduceDetachedRuntime(current, "tool", event); }); },
      onArtifactEvent: function (event) { if (mountedRef.current) setStreamRuntime(function (current) { return wbcReduceDetachedRuntime(current, "artifact", null, event); }); },
      onSaved: function () {
        refreshAgent().finally(function () {
          streamAttachedRef.current = false;
          if (mountedRef.current) {
            setStreamText("");
            setStreamNotifications([]);
            setStreamRuntime(null);
            setRunning(false);
          }
        });
      },
      onAwaitingUser: function (pending) {
        if (!mountedRef.current) return;
        if (wbcIsLiveAgentRequest(pending)) {
          var current = agentRef.current;
          if (current && current.id) {
            var next = { ...current, pendingQuestion: pending, status: "running" };
            agentRef.current = next;
            onUpdate(next);
          }
          return;
        }
        refreshAgent().finally(function () {
          streamAttachedRef.current = false;
          if (mountedRef.current) {
            setStreamText("");
            setStreamNotifications([]);
            setStreamRuntime(null);
            setRunning(false);
          }
        });
      },
      onPermissionResolved: function () {
        if (!mountedRef.current) return;
        var current = agentRef.current;
        if (!current || !current.id) return;
        var next = { ...current, pendingQuestion: null };
        agentRef.current = next;
        onUpdate(next);
      },
      onElicitationResolved: function () {
        if (!mountedRef.current) return;
        var current = agentRef.current;
        if (!current || !current.id) return;
        var next = { ...current, pendingQuestion: null };
        agentRef.current = next;
        onUpdate(next);
      },
      onGuidanceReceived: function (event) {
        if (!mountedRef.current || !event || !event.userMessage) return;
        var current = agentRef.current;
        if (!current || !current.id) return;
        var next = {
          ...current,
          messages: wbcMergeChronologicalMessages(current.messages || [], [event.userMessage]),
        };
        agentRef.current = next;
        onUpdate(next);
      },
      onError: function (err) {
        if (mountedRef.current) setError(wbcErrorText(err));
      },
    };
  }

  function ownStream(promise) {
    streamAttachedRef.current = true;
    promise.catch(function (err) {
      if (mountedRef.current && !(err && err.name === "AbortError")) {
        setError(wbcErrorText(err));
      }
    }).finally(function () {
      if (!streamAttachedRef.current) return;
      streamAttachedRef.current = false;
      refreshAgent().catch(function () {}).finally(function () {
        if (mountedRef.current) {
          setStreamText("");
          setStreamNotifications([]);
          setStreamRuntime(null);
          setRunning(false);
        }
      });
    });
  }

  useWbcEffect(function () {
    if (!agent || agent.status !== "running" || streamAttachedRef.current) return undefined;
    runStartedAtRef.current = Date.now();
    setRunning(true);
    ownStream(WorkbenchChatModel.reconnectRun(agent.id, streamHandlers()));
    return undefined;
  }, [agent && agent.id, agent && agent.status]);

  function submit(payload) {
    var question = String(payload && payload.message || "").trim();
    var attachments = payload && Array.isArray(payload.attachments) ? payload.attachments : [];
    var current = agentRef.current;
    if ((!question && !attachments.length) || running || !current || !current.id) return;
    var optimistic = {
      id: "side_user_pending_" + Date.now(),
      role: "user",
      content: question,
      attachments: attachments,
      createdAt: new Date().toISOString(),
      optimistic: true,
    };
    var next = {
      ...current,
      status: "running",
      messages: (current.messages || []).concat([optimistic]),
    };
    agentRef.current = next;
    onUpdate(next);
    setError("");
    setStreamText("");
    setStreamNotifications([]);
    runStartedAtRef.current = Date.now();
    setStreamRuntime(wbcCreateDetachedRuntime(runStartedAtRef.current));
    setRunning(true);
    ownStream(WorkbenchChatModel.sendMessage(
      current.id,
      {
        message: question,
        attachments: attachments,
        mode: current.permissionMode || payload.mode || "default",
        model: current.modelSelectionId || payload.model || "",
        reasoningEffort: current.reasoningEffort || payload.reasoningEffort || "",
      },
      streamHandlers()
    ));
  }

  function stop() {
    if (!running) return;
    WorkbenchChatModel.interrupt(agentRef.current.id).catch(function (err) {
      if (mountedRef.current) setError(wbcErrorText(err));
    });
  }

  function guide(message) {
    var current = agentRef.current;
    var text = String(message || "").trim();
    if (!running || !current || !current.id || !text) return Promise.resolve(null);
    setError("");
    return WorkbenchChatModel.sendGuidance(
      current.id,
      text,
      "guide_" + Date.now() + "_" + Math.random().toString(36).slice(2, 8)
    ).then(function (response) {
      if (response && response.userMessage && mountedRef.current) {
        var latest = agentRef.current;
        var next = {
          ...latest,
          messages: wbcMergeChronologicalMessages(latest.messages || [], [response.userMessage]),
        };
        agentRef.current = next;
        onUpdate(next);
      }
      return response;
    }).catch(function (err) {
      if (mountedRef.current) setError(wbcErrorText(err));
      throw err;
    });
  }

  function answerPendingQuestion(questionId, optionText) {
    var current = agentRef.current;
    var pending = current && current.pendingQuestion || null;
    var formAnswer = optionText && typeof optionText === "object" && optionText.__agentForm === true;
    var answer = formAnswer ? optionText : String(optionText || "").trim();
    if (!current || !current.id || !questionId || !wbcIsLiveAgentRequest(pending) || (!formAnswer && !answer)) return;
    var response = String(pending.kind || "") === "permission.requested"
      ? { type: "option", optionId: String(answer || "") }
      : (formAnswer
        ? { type: "form", form: answer.values && typeof answer.values === "object" ? answer.values : {} }
        : { type: "text", text: String(answer || "") });
    var next = { ...current, pendingQuestion: null, status: "running" };
    agentRef.current = next;
    onUpdate(next);
    setError("");
    return WorkbenchChatModel.answerAgentRequest(current.id, questionId, response).catch(function (err) {
      if (!mountedRef.current) return;
      setError(wbcErrorText(err));
      var restored = { ...agentRef.current, pendingQuestion: pending };
      agentRef.current = restored;
      onUpdate(restored);
    });
  }

  var messages = agent && Array.isArray(agent.messages) ? agent.messages : [];
  var hasAsked = messages.some(function (message) { return message.role === "user"; });
  return (
    <section className="wbc-side-agent">
      {!hasAsked && <blockquote className="wbc-side-agent-quote">
        <span>{wbcT("workbenchChat.sideAgent.quote", "Selected text")}</span>
        <p>{agent && agent.sourceQuote}</p>
      </blockquote>}
      <div className="wbc-side-agent-thread wbc-thread" data-cyrene-revision-volatile="true" ref={scrollRef}>
        {!messages.length && !running && (
          <div className="wbc-side-agent-empty">
            <b>{wbcT("workbenchChat.sideAgent.askTitle", "Ask about this text")}</b>
            <p>{wbcT("workbenchChat.sideAgent.askHint", "This agent has its own context and will not interrupt the main conversation.")}</p>
          </div>
        )}
        {messages.map(function (message) {
          if (message.modelStatusCard) {
            return <WbcThreadItem key={message.id || message.createdAt}><WbcModelStatusMessage msg={message} /></WbcThreadItem>;
          }
          if (message.notificationCard) {
            return <WbcThreadItem key={message.id || message.createdAt}><WbcAgentNotification notice={message.notification} /></WbcThreadItem>;
          }
          return (
            <WbcThreadItem key={message.id || message.createdAt}>
              {message.role === "user"
                ? <WbcUserMessage msg={message} onOpenFile={onOpenFile} />
                : <WbcAssistantMessage msg={message} onOpenFile={onOpenFile} chatId={typeof chatId === "string" ? chatId : ""} />}
            </WbcThreadItem>
          );
        })}
        {running && <WbcRuntimeTranscript runtime={streamRuntime || { ...wbcCreateDetachedRuntime(runStartedAtRef.current), text: streamText, notifications: streamNotifications }} onOpenFile={onOpenFile} />}
        {agent && agent.pendingQuestion && wbcIsLiveAgentRequest(agent.pendingQuestion) && (
          <WbcThreadItem><WbcQuestionPrompt pending={agent.pendingQuestion} onAnswer={answerPendingQuestion} busy={false} /></WbcThreadItem>
        )}
      </div>
      {error && <div className="wbc-side-agent-error" role="alert">{error}</div>}
      <div className="wbc-side-agent-composer-host">
        <WbcComposer
          key={"side-agent-composer:" + String(agent && agent.id || "")}
          chat={agent}
          project={project}
          runtime={streamText ? { text: streamText } : null}
          running={running}
          onSend={submit}
          onGuidance={guide}
          onInterrupt={stop}
          draftNamespace="side-agent:"
          autoFocus={false}
          clearOnSend={true}
          error={error}
          errorKind="message"
          compact={true}
          placeholder={wbcT("workbenchChat.sideAgent.placeholder", "Ask a question about the selected text…")}
          runningPlaceholder={wbcT("workbenchChat.sideAgent.placeholderRunning", "Agent is working…")}
        />
      </div>
    </section>
  );
}

function WbcSideAgentsPanel({
  agents,
  activeAgentId,
  loading,
  onSelect,
  onDelete,
}) {
  var items = Array.isArray(agents) ? agents : [];

  if (loading && !items.length) {
    return (
      <div className="wbc-side-agent-panel-state" role="status">
        <span className="wbc-spinner" aria-hidden="true" />
        <span>{wbcT("workbenchChat.sideAgent.loading", "Loading side agents…")}</span>
      </div>
    );
  }

  if (!items.length) {
    return (
      <div className="wbc-side-agent-panel-state">
        <b>{wbcT("workbenchChat.sideAgent.empty", "No side questions")}</b>
        <span>{wbcT("workbenchChat.sideAgent.emptyHint", "Select text in the conversation to start one.")}</span>
      </div>
    );
  }

  return (
    <div className="wbc-side-agents-panel">
      <div className="wbc-side-agent-list" role="list" aria-label={wbcT("workbenchChat.sideAgent.list", "Side questions")}>
        {items.map(function (agent, index) {
          var selected = agent.id === activeAgentId;
          var preview = String(agent.sourceQuote || agent.title || "")
            .replace(/\s+/g, " ")
            .trim();
          var running = agent.status === "running";
          return (
            <div key={agent.id} className={"wbc-side-agent-index-row" + (selected ? " active" : "")} role="listitem">
              <button
                type="button"
                className="wbc-side-agent-index-select"
                onClick={function () { onSelect(agent.id); }}
                aria-current={selected ? "true" : undefined}
                title={preview}
              >
                <span className="wbc-side-agent-index-number">{String(index + 1).padStart(2, "0")}</span>
                <span className="wbc-side-agent-index-copy">
                  <b>{preview || wbcT("workbenchChat.sideAgent.untitled", "Side question")}</b>
                  <small>{running
                    ? wbcT("workbenchChat.sideAgent.thinking", "Thinking…")
                    : wbcT("workbenchChat.sideAgent.ready", "Ready")}</small>
                </span>
              </button>
              <button
                type="button"
                className="wbc-side-agent-index-close"
                onClick={function () { onDelete(agent.id); }}
                title={wbcT("workbenchChat.sideAgent.close", "Close side agent")}
                aria-label={wbcT("workbenchChat.sideAgent.close", "Close side agent")}
              >×</button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function WbcSideAccordionBody({ expanded, flush, bodyClass, children }) {
  var [mounted, setMounted] = useWbcState(expanded);
  var contentRef = useWbcRef(children);

  if (expanded) contentRef.current = children;

  useWbcEffect(function () {
    if (expanded) {
      setMounted(true);
      return undefined;
    }
    if (!mounted) return undefined;
    var timer = window.setTimeout(function () { setMounted(false); }, 190);
    return function () { window.clearTimeout(timer); };
  }, [expanded, mounted]);

  if (!mounted && !expanded) return null;
  return (
    <div className={"wbc-side-collapse" + (expanded ? " open" : " closing")} aria-hidden={!expanded}>
      <div className="wbc-side-collapse-inner">
        <div className={"wbc-side-body" + (flush ? " flush" : "") + (bodyClass ? " " + bodyClass : "")}>{contentRef.current}</div>
      </div>
    </div>
  );
}

function WbcConversationTerminalList({ terminals, loading, onSelect }) {
  var items = Array.isArray(terminals) ? terminals : [];
  if (loading && !items.length) {
    return <div className="workbench-muted wbc-side-terminal-empty">{wbcT("terminal.loading", "Loading terminals...")}</div>;
  }
  return (
    <div className="wbc-side-terminal-list" role="list">
      {items.map(function (terminal) {
        var running = terminal.status === "running" || terminal.status === "starting";
        return (
          <button
            key={terminal.id}
            type="button"
            className="wbc-side-terminal-row"
            role="listitem"
            onClick={function () { if (onSelect) onSelect(terminal.id); }}
          >
            <span className={"wbc-side-terminal-status" + (running ? " running" : " exited")} aria-hidden="true" />
            <span className="wbc-side-terminal-copy">
              <b>{terminal.title || wbcT("terminal.title", "Terminal")}</b>
              <small>{running ? String(terminal.cwd || "") : wbcT("terminal.exited", "Process exited")}</small>
            </span>
            <time>{wbcFormatTime(terminal.updatedAt || terminal.createdAt)}</time>
            <span aria-hidden="true">{WBC_ICONS.chevronRight}</span>
          </button>
        );
      })}
    </div>
  );
}

function WbcSide({
  project,
  chat,
  chatLoading,
  chatDetailed,
  chats,
  activeChatId,
  onSelectChat,
  runtime,
  subagentData,
  subagentLoading,
  onSelectSubagentRound,
  tab,
  onTabChange,
  viewerFile,
  onOpenFile,
  onSelectArtifact,
  onSelectChange,
  onSelectViewer,
  onSelectMap,
  onSelectBrowser,
  onOpenSubagents,
  onViewerViewed,
  onRename,
  onDelete,
  onToTask,
  toTaskBusy,
  onCompact,
  compactBusy,
  sideAgents,
  sideAgentsLoading,
  activeSideAgentId,
  onSelectSideAgent,
  onUpdateSideAgent,
  onDeleteSideAgent,
  terminals,
  terminalsLoading,
  onSelectTerminal,
  onBrowserTakeoverComplete,
  browserActiveByChat,
  browserSuppressed,
  onToggleSide,
  floating,
  widthResizable,
  onCloseFloating,
}) {
  window.CyreneUI.require("data").useVersion();
  var [changesAvailability, setChangesAvailability] = useWbcState({ chatId: "", hasChanges: false });
  var hasWorkspaceChanges = (
    String(changesAvailability.chatId || "") === String(activeChatId || "")
    && !!changesAvailability.hasChanges
  );

  useWbcEffect(function () {
    var currentChatId = String(activeChatId || "");
    var disposed = false;
    setChangesAvailability({ chatId: currentChatId, hasChanges: false });
    if (!currentChatId || currentChatId.indexOf("legacy:") === 0) return undefined;

    function revealFromEvent(event) {
      var detail = (event && event.detail) || {};
      var eventChatId = String(detail.chatId || detail.session_id || "");
      if (eventChatId && eventChatId !== currentChatId) return;
      if (Number(detail.fileCount || 0) > 0) {
        setChangesAvailability({ chatId: currentChatId, hasChanges: true });
      }
    }

    window.addEventListener("workbench:workspace-changes", revealFromEvent);
    WorkbenchChatModel.getChanges(currentChatId, { toast: false })
      .then(function (payload) {
        if (disposed) return;
        var sets = Array.isArray(payload && payload.changeSets) ? payload.changeSets : [];
        setChangesAvailability(function (current) {
          if (String(current.chatId || "") === currentChatId && current.hasChanges) return current;
          return { chatId: currentChatId, hasChanges: sets.length > 0 };
        });
      })
      .catch(function () {
        if (!disposed) {
          setChangesAvailability(function (current) {
            if (String(current.chatId || "") === currentChatId && current.hasChanges) return current;
            return { chatId: currentChatId, hasChanges: false };
          });
        }
      });
    return function () {
      disposed = true;
      window.removeEventListener("workbench:workspace-changes", revealFromEvent);
    };
  }, [activeChatId]);

  var browserState = wbcBrowserStateForChat(activeChatId);
  var browserMarkedActive = !!(browserActiveByChat && browserActiveByChat[activeChatId]);
  var browserPanelState = browserState || {};
  var hasMap = wbcChatUsedMap(chat, runtime);
  var hasBrowser = !!((browserState && browserState.active) || browserMarkedActive);
  var fileItems = wbcChatArtifactFiles(chat);
  var artifactItems = wbcChatDeliveredArtifacts(chat);
  var hasFiles = fileItems.length > 0;
  var viewerItems = fileItems;
  // viewerFile also tracks project files opened from the Files rail. Only
  // expose the Viewer row when that selection belongs to this conversation;
  // otherwise a file split leaks a phantom Viewer into unrelated chats.
  var chatViewerFile = wbcViewerFileFromItems(viewerFile, viewerItems);
  var hasBranches = useWbcMemo(function () {
    return !!wbcBranchLineage(chats, activeChatId);
  }, [chats, activeChatId]);
  var pendingPlan = wbcActivePlan(chat);
  var hasSubagents = !!(
    subagentData
    && (
      (Array.isArray(subagentData.rounds) && subagentData.rounds.length)
      || (Array.isArray(subagentData.agents) && subagentData.agents.length)
    )
  );
  var tabs = [
    { id: "overview", label: wbcT("chat.side.overview", "Overview") },
  ];
  if (pendingPlan) tabs.push({ id: "plan", label: wbcT("chat.side.plan", "Plan") });
  if (hasSubagents) tabs.push({ id: "subagents", label: wbcT("workbenchChat.subagents", "Subagents") });
  tabs.push({ id: "context", label: wbcT("workbenchChat.context", "Context") });
  if (hasFiles) tabs.push({ id: "files", label: wbcT("workbenchChat.files", "Files") });
  if (artifactItems.length) tabs.push({ id: "artifacts", label: wbcT("workbenchChat.artifacts", "Artifacts") });
  if (hasWorkspaceChanges) {
    tabs.push({ id: "changes", label: wbcT("workbenchChat.changes", "Changes") });
  }
  if (hasBranches) tabs.push({ id: "branches", label: wbcT("chat.side.branches", "Branches") });
  if (chatViewerFile) tabs.push({ id: "viewer", label: wbcT("workbenchChat.viewer", "Viewer") });
  if (hasMap) tabs.push({ id: "map", label: wbcT("chat.side.map", "Map") });
  if (hasBrowser) tabs.push({ id: "browser", label: wbcT("chat.side.browser", "Browser") });
  var conversationTerminals = (Array.isArray(terminals) ? terminals : []).filter(function (terminal) {
    return terminal
      && String(terminal.createdBy || "") === "agent"
      && String(terminal.ownerChatId || "") === String(activeChatId || "");
  });
  if (conversationTerminals.length) {
    tabs.push({ id: "terminal", label: wbcT("terminal.title", "Terminal") });
  }
  if (sideAgents && sideAgents.length) {
    tabs.push({
      id: "side-agents",
      label: wbcT("workbenchChat.sideAgent.tab", "Side questions"),
    });
  }
  var activeTab = tabs.some(function (item) { return item.id === tab; }) ? tab : "";
  useWbcLiveChatMetrics(chat, !!runtime);
  // Keep the Context tab's two async sources warm while the panel is visible
  // so expanding the tab renders the latest data with no loading flash.
  var contextBlocks = useWbcLiveContextBlocks(chat, !!runtime);
  var inboxView = useWbcLiveInbox(chat, !!runtime);
  var flush = false;
  var sideTabMeta = {
    plan: pendingPlan && Array.isArray(pendingPlan.steps) && pendingPlan.steps.length
      ? pendingPlan.steps.filter(function (step) { return step.status === "completed"; }).length + "/" + pendingPlan.steps.length
      : "",
    subagents: subagentData && Array.isArray(subagentData.agents) && subagentData.agents.length
      ? String(subagentData.agents.length)
      : "",
    files: hasFiles ? String(fileItems.length) : "",
    artifacts: artifactItems.length ? String(artifactItems.length) : "",
    viewer: viewerItems.length ? String(viewerItems.length) : "",
    browser: browserPanelState && Array.isArray(browserPanelState.tabs) ? String(browserPanelState.tabs.length) : "",
    terminal: conversationTerminals.length ? String(conversationTerminals.length) : "",
    "side-agents": sideAgents && sideAgents.length ? String(sideAgents.length) : "",
  };
  var activeContent = (
    <>
      {activeTab === "overview" && <WbcOverviewTab chat={chat} loading={chatLoading} detailed={chatDetailed} runtime={runtime} onRename={onRename} onDelete={onDelete} onToTask={onToTask} toTaskBusy={toTaskBusy} onCompact={onCompact} compactBusy={compactBusy} />}
      {activeTab === "plan" && <WbcPlanTab plan={pendingPlan} />}
      {activeTab === "context" && <WbcContextTab project={project} chat={chat} runtime={runtime} contextBlocks={contextBlocks} inboxView={inboxView} />}
      {activeTab === "files" && <WbcArtifactsTab chat={chat} onSelectArtifact={onSelectArtifact} />}
      {activeTab === "artifacts" && <WbcArtifactsTab chat={chat} files={artifactItems} emptyKey="workbenchChat.noArtifacts" emptyFallback="This chat has not delivered any artifacts yet." onSelectArtifact={onSelectArtifact} />}
      {activeTab === "changes" && <WbcChangesTab chatId={activeChatId} onSelectChange={onSelectChange} />}
      {activeTab === "branches" && <WbcBranchTab chats={chats} activeChatId={activeChatId} onSelectChat={onSelectChat} />}
      {activeTab === "viewer" && <WbcViewerList files={viewerItems} selectedFile={chatViewerFile} onSelect={onSelectViewer} />}
      {activeTab === "map" && <WbcMapList chatId={chat ? chat.id : ""} onSelect={onSelectMap} />}
      {activeTab === "browser" && !browserSuppressed && (
        <WbcBrowserList browserState={browserPanelState} onSelect={onSelectBrowser} />
      )}
      {activeTab === "terminal" && (
        <WbcConversationTerminalList
          terminals={conversationTerminals}
          loading={terminalsLoading}
          onSelect={onSelectTerminal}
        />
      )}
      {activeTab === "side-agents" && (
        <WbcSideAgentsPanel
          agents={sideAgents}
          project={project}
          onOpenFile={onOpenFile}
          activeAgentId={activeSideAgentId}
          loading={sideAgentsLoading}
          onSelect={onSelectSideAgent}
          onDelete={onDeleteSideAgent}
          onUpdate={onUpdateSideAgent}
        />
      )}
    </>
  );
  return (
    <aside className={"wbc-side" + (floating ? " wbc-side-floating" : "")}>
      <div className="wbc-side-card">
        {widthResizable && React.createElement(
          window.CyreneUI.require("shell").ColResizer,
          { trackGutter: true, surfaceId: "conversation" }
        )}
        <div className="wbc-side-card-head">
          <strong>{wbcT("workbenchChat.sidePanelTitle", "Conversation panel")}</strong>
          <button
            type="button"
            className={"wbc-side-hide-btn" + (floating ? " wbc-side-floating-close" : "")}
            onClick={floating ? onCloseFloating : onToggleSide}
            title={floating
              ? wbcT("workbenchChat.closeFloatingConversationPanel", "Close floating conversation panel")
              : wbcT("workbenchChat.hideSidebar", "Hide side panel")}
            aria-label={floating
              ? wbcT("workbenchChat.closeFloatingConversationPanel", "Close floating conversation panel")
              : wbcT("workbenchChat.hideSidebar", "Hide side panel")}
          >
            {floating ? WBC_ICONS.x : WBC_ICONS.chevronsRight}
          </button>
        </div>
        <div className="wbc-side-accordion" data-tour="chat_sidebar">
          {tabs.map(function (item) {
            var opensSplit = item.id === "subagents" || item.id === "browser";
            var expanded = !opensSplit && activeTab === item.id;
            var meta = sideTabMeta[item.id] || "";
            return (
              <section key={item.id} className={"wbc-side-accordion-item" + (expanded ? " expanded" : "")}>
                <button
                  type="button"
                  className="wbc-side-accordion-trigger"
                  aria-expanded={expanded}
                  onClick={function () {
                    if (opensSplit) {
                      onTabChange("");
                      if (item.id === "subagents" && onOpenSubagents) onOpenSubagents();
                      if (item.id === "browser" && onSelectBrowser) {
                        var currentBrowserTab = browserPanelState && (browserPanelState.activeTabId || browserPanelState.activeTab && browserPanelState.activeTab.id);
                        onSelectBrowser(currentBrowserTab || "__active__");
                      }
                      return;
                    }
                    onTabChange(expanded ? "" : item.id);
                  }}
                >
                  <span className="wbc-side-accordion-icon" aria-hidden="true">{WBC_SIDE_TAB_ICONS[item.id] || WBC_SIDE_TAB_ICONS.overview}</span>
                  <span className="wbc-side-accordion-label">{item.label}</span>
                  {meta && <span className="wbc-side-accordion-meta">{meta}</span>}
                  <span className="wbc-side-accordion-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
                </button>
                <WbcSideAccordionBody expanded={expanded} flush={flush}>{activeContent}</WbcSideAccordionBody>
              </section>
            );
          })}
        </div>
      </div>
    </aside>
  );
}

function wbcChangeTypeLabel(changeType) {
  if (changeType === "created") return wbcT("workbenchChat.changes.created", "Created");
  if (changeType === "deleted") return wbcT("workbenchChat.changes.deleted", "Deleted");
  return wbcT("workbenchChat.changes.modified", "Modified");
}

function WbcChangesTab({ chatId, onSelectChange }) {
  var [payload, setPayload] = useWbcState({ changeSets: [], fileCount: 0, additions: 0, deletions: 0 });
  var [loading, setLoading] = useWbcState(true);
  var [error, setError] = useWbcState("");
  var [selectedSetId, setSelectedSetId] = useWbcState("");
  var refreshTimerRef = useWbcRef(null);
  var chatIdRef = useWbcRef(chatId);
  var refreshSeqRef = useWbcRef(0);
  chatIdRef.current = chatId;

  function refresh(background) {
    if (!chatId) return Promise.resolve(null);
    var requestedChatId = String(chatId);
    var requestSeq = ++refreshSeqRef.current;
    if (!background) setLoading(true);
    setError("");
    return WorkbenchChatModel.getChanges(chatId, { toast: false })
      .then(function (next) {
        if (String(chatIdRef.current || "") !== requestedChatId || refreshSeqRef.current !== requestSeq) return null;
        var sets = Array.isArray(next.changeSets) ? next.changeSets : [];
        setPayload(next);
        setSelectedSetId(function (current) {
          return sets.some(function (item) { return item.id === current; })
            ? current
            : (sets[0] ? sets[0].id : "");
        });
        return next;
      })
      .catch(function (err) {
        if (String(chatIdRef.current || "") === requestedChatId && refreshSeqRef.current === requestSeq) setError(wbcErrorText(err));
        return null;
      })
      .finally(function () {
        if (String(chatIdRef.current || "") === requestedChatId && refreshSeqRef.current === requestSeq) setLoading(false);
      });
  }

  useWbcEffect(function () {
    setPayload({ changeSets: [], fileCount: 0, additions: 0, deletions: 0 });
    setSelectedSetId("");
    refreshSeqRef.current += 1;
    refresh(false);
  }, [chatId]);

  useWbcEffect(function () {
    function onChanges(event) {
      var detail = (event && event.detail) || {};
      var eventChatId = String(detail.chatId || detail.session_id || "");
      if (eventChatId && eventChatId !== String(chatId || "")) return;
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = setTimeout(function () { refresh(true); }, 80);
    }
    window.addEventListener("workbench:workspace-changes", onChanges);
    return function () {
      window.removeEventListener("workbench:workspace-changes", onChanges);
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
    };
  }, [chatId]);

  var changeSets = Array.isArray(payload.changeSets) ? payload.changeSets : [];
  var selectedSet = changeSets.find(function (item) { return item.id === selectedSetId; }) || changeSets[0] || null;
  var files = selectedSet && Array.isArray(selectedSet.files) ? selectedSet.files : [];
  return (
    <div className="wbc-changes-tab">
      {changeSets.length > 1 && (
        <div className="wbc-changes-run-picker">
          <select
            aria-label={wbcT("workbenchChat.changes.latestRun", "Latest run")}
            value={selectedSet ? selectedSet.id : ""}
            onChange={function (event) { setSelectedSetId(event.target.value); }}
          >
            {changeSets.map(function (item, index) {
              return <option value={item.id} key={item.id}>{index === 0 ? wbcT("workbenchChat.changes.latestRun", "Latest run") : wbcFormatTime(item.completedAt)}</option>;
            })}
          </select>
          <span className="wbc-changes-run-picker-chevron" aria-hidden="true">{WBC_ICONS.chevronDown}</span>
        </div>
      )}
      {loading && !changeSets.length ? (
        <p className="workbench-muted wbc-changes-state">{wbcT("workbenchChat.changes.loading", "Loading changes...")}</p>
      ) : error ? (
        <div className="wbc-changes-state"><p className="workbench-muted">{error}</p><button type="button" className="wb-btn ghost" onClick={function () { refresh(false); }}>{wbcT("workbenchChat.error.retry", "Retry")}</button></div>
      ) : !changeSets.length ? (
        <div className="wbc-changes-empty">
          <b>{wbcT("workbenchChat.changes.emptyTitle", "No agent changes yet")}</b>
          <p>{wbcT("workbenchChat.changes.emptyBody", "Files created, edited, or deleted by future agent runs will appear here automatically.")}</p>
        </div>
      ) : (
        <React.Fragment>
          <div className="wbc-resource-list wbc-changes-files">
            {files.map(function (item) {
              return (
                <button
                  type="button"
                  key={item.id || item.path}
                  className={"wbc-resource-list-row wbc-change-file " + item.changeType}
                  onClick={function () {
                    if (onSelectChange) onSelectChange({ chatId: chatId, setId: selectedSet.id, path: item.path, file: item, files: files });
                  }}
                >
                  <span className="wbc-resource-list-icon" aria-hidden="true">{WBC_SIDE_TAB_ICONS.changes}</span>
                  <span className="wbc-resource-list-copy">
                    <b className="wbc-change-file-path" title={item.path}>{item.path}</b>
                    <small><span className="wbc-change-file-status">{wbcChangeTypeLabel(item.changeType)}</span><span className="wbc-change-file-lines"><i>+{item.additions || 0}</i><em>−{item.deletions || 0}</em></span></small>
                  </span>
                  <span className="wbc-resource-list-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
                </button>
              );
            })}
          </div>
        </React.Fragment>
      )}
    </div>
  );
}

// Workbench subagent panel, styled as a read-only group chat room. The main
// agent's delegated subagents appear as roster members; their inter-agent
// messages and results stream as chat bubbles. Observational only — the
// Workbench has no user→subagent send endpoint, so there is no composer.
function WbcSubagentsTab({ data, loading, onSelectRound }) {
  var rounds = data && Array.isArray(data.rounds) ? data.rounds : [];
  var agents = data && Array.isArray(data.agents) ? data.agents : [];
  var messages = data && Array.isArray(data.messages) ? data.messages : [];
  var activeRoundId = String((data && data.activeRoundId) || "");
  var [selectedAgentId, setSelectedAgentId] = useWbcState("");

  var roster = agents.map(function (agent) { return agent.id; }).join("|");
  // Default to no focused agent; reset when the round / roster changes.
  useWbcEffect(function () { setSelectedAgentId(""); }, [activeRoundId, roster]);

  var selectedAgent = agents.find(function (agent) { return agent.id === selectedAgentId; }) || null;
  var activeCount = agents.filter(function (agent) {
    return ["running", "resumed", "waiting"].indexOf(String(agent.status || "")) >= 0;
  }).length;
  var activeRound = rounds.find(function (round) { return round.id === activeRoundId; }) || rounds[0] || null;

  function focusAgent(id) {
    setSelectedAgentId(id === selectedAgentId ? "" : id);
  }

  if (loading && !rounds.length && !agents.length) {
    return (
      <div className="wbc-subagent-empty">
        <span className="wbc-spinner" aria-hidden="true"></span>
        <p>{wbcT("workbenchChat.subagent.loading", "Loading subagents...")}</p>
      </div>
    );
  }
  if (!rounds.length && !agents.length) {
    return (
      <div className="wbc-subagent-empty">
        <span className="wbc-subagent-empty-glyph" aria-hidden="true">⠿</span>
        <b>{wbcT("workbenchChat.subagent.emptyTitle", "No subagents in this chat")}</b>
        <p>{wbcT("workbenchChat.subagent.emptyBody", "When the main agent delegates work, subagents and their results will appear here.")}</p>
      </div>
    );
  }

  return (
    <div className="wbc-subagent-page">
      <header className="wbc-subagent-bar">
        <div className="wbc-subagent-bar-main">
          <span className="wbc-subagent-eyebrow">{wbcT("workbenchChat.subagent.title", "Subagent activity")}</span>
          <b title={activeRound ? activeRound.title : ""}>
            {(activeRound && activeRound.title) || wbcT("workbenchChat.subagents", "Subagents")}
          </b>
        </div>
        <span className={"wbc-subagent-livepill " + (activeCount ? "live" : "idle")}>
          <i aria-hidden="true"></i>
          {activeCount
            ? wbcT("workbenchChat.subagent.liveCount", "{n} working", { n: activeCount })
            : wbcT("workbenchChat.subagent.complete", "Complete")}
        </span>
      </header>

      {rounds.length > 1 ? (
        <label className="wbc-subagent-round">
          <span>{wbcT("workbenchChat.subagent.round", "Round")}</span>
          <select value={activeRoundId} onChange={function (event) { onSelectRound && onSelectRound(event.target.value); }}>
            {rounds.map(function (round) {
              return <option key={round.id} value={round.id}>{round.title}</option>;
            })}
          </select>
        </label>
      ) : null}

      <WbcSubagentRoster agents={agents} selectedId={selectedAgentId} onSelect={focusAgent} />

      {selectedAgent ? (
        <WbcSubagentSpotlight agent={selectedAgent} onClose={function () { setSelectedAgentId(""); }} />
      ) : null}

      <WbcSubagentStream
        messages={messages}
        agents={agents}
        active={activeCount > 0}
        selectedId={selectedAgentId}
        onSelectAgent={focusAgent}
      />
    </div>
  );
}

// Horizontal avatar strip of the round's subagents — the chat room's member list.
function WbcSubagentRoster({ agents, selectedId, onSelect }) {
  return (
    <div className="wbc-subagent-roster">
      {agents.map(function (agent) {
        var color = wbcAgentColor(agent.id);
        var statusCls = wbcSubagentStatusClass(agent.status);
        var name = agent.name || agent.id;
        return (
          <button
            key={agent.id}
            type="button"
            className={"wbc-subagent-chip" + (agent.id === selectedId ? " active" : "")}
            style={{ "--wb-agent-color": color }}
            onClick={function () { onSelect(agent.id); }}
            title={name + " · " + wbcSubagentStatusText(agent.status)}
          >
            <span className="wbc-subagent-avatar" style={{ background: color }}>
              {wbcAgentInitials(name)}
              <i className={"wbc-subagent-avatar-dot " + statusCls} aria-hidden="true"></i>
            </span>
            <span className="wbc-subagent-chip-name">{name}</span>
          </button>
        );
      })}
    </div>
  );
}

// Focused-agent card: its task brief and (when available) full result.
function WbcSubagentSpotlight({ agent, onClose }) {
  var color = wbcAgentColor(agent.id);
  var name = agent.name || agent.id;
  return (
    <section className="wbc-subagent-spotlight" style={{ "--wb-agent-color": color }}>
      <header>
        <span className="wbc-subagent-avatar lg" style={{ background: color }}>{wbcAgentInitials(name)}</span>
        <div className="wbc-subagent-spotlight-id">
          <b title={name}>{name}</b>
          <span className={"wbc-subagent-status " + wbcSubagentStatusClass(agent.status)}>
            {wbcSubagentStatusText(agent.status)}
          </span>
        </div>
        <button type="button" className="wbc-subagent-spotlight-close" onClick={onClose} aria-label={wbcT("workbenchChat.subagent.close", "Close")}>×</button>
      </header>
      <div className="wbc-subagent-spotlight-body">
        <label>{wbcT("workbenchChat.subagent.task", "Task")}</label>
        <p>{agent.task || "—"}</p>
        <label>{wbcT("workbenchChat.subagent.result", "Result")}</label>
        {agent.result ? (
          <div className="markdown wbc-subagent-result" dangerouslySetInnerHTML={{ __html: wbcRenderMarkdown(agent.result) }} />
        ) : (
          <p className="workbench-muted">{wbcT("workbenchChat.subagent.resultPending", "No result yet.")}</p>
        )}
      </div>
    </section>
  );
}

// Scrolling chat transcript of inter-agent messages and results.
function WbcSubagentStream({ messages, agents, active, selectedId, onSelectAgent }) {
  var scrollRef = useWbcRef(null);
  var atBottomRef = useWbcRef(true);
  var initedRef = useWbcRef(false);

  function handleScroll() {
    var el = scrollRef.current;
    if (!el) return;
    atBottomRef.current = el.scrollTop + el.clientHeight >= el.scrollHeight - 48;
  }

  // First render jumps to the latest message; later updates only follow when the
  // reader is already near the bottom.
  useWbcLayoutEffect(function () {
    var el = scrollRef.current;
    if (!el) return;
    if (!initedRef.current) {
      initedRef.current = true;
      el.scrollTop = el.scrollHeight;
      atBottomRef.current = true;
      return;
    }
    if (atBottomRef.current) el.scrollTop = el.scrollHeight;
  }, [messages.length]);

  var nameById = {};
  var agentIds = [];
  agents.forEach(function (agent) { nameById[agent.id] = agent.name || agent.id; agentIds.push(agent.id); });

  if (!messages.length) {
    return (
      <div className="wbc-subagent-stream is-empty">
        <div className="wbc-subagent-stream-empty">
          {active ? (
            <span className="wbc-subagent-typing">
              <i></i><i></i><i></i>
              {wbcT("workbenchChat.subagent.working", "Subagents are working…")}
            </span>
          ) : (
            wbcT("workbenchChat.subagent.noActivity", "No messages recorded for this round.")
          )}
        </div>
      </div>
    );
  }

  var rows = [];
  var prevFrom = null;
  var prevTs = null;
  for (var i = 0; i < messages.length; i++) {
    var msg = messages[i];
    if (prevTs && msg.timestamp) {
      try {
        if (new Date(msg.timestamp) - new Date(prevTs) > 300000) {
          rows.push(<div className="wbc-subagent-timesep" key={"ts_" + i}><span>{wbcFormatTime(msg.timestamp)}</span></div>);
          prevFrom = null;
        }
      } catch (e) { /* unparseable timestamp — skip separator */ }
    }
    rows.push(
      <WbcSubagentBubble
        key={msg.id || i}
        msg={msg}
        name={nameById[msg.from] || msg.from}
        agentIds={agentIds}
        grouped={prevFrom === msg.from && msg.from}
        dimmed={!!selectedId && msg.from !== selectedId}
        onSelectAgent={onSelectAgent}
      />
    );
    prevFrom = msg.from;
    prevTs = msg.timestamp;
  }

  return (
    <div className="wbc-subagent-stream" ref={scrollRef} onScroll={handleScroll}>
      {rows}
    </div>
  );
}

// A single transcript entry: agent message, broadcast, result card, or system note.
function WbcSubagentBubble({ msg, name, agentIds, grouped, dimmed, onSelectAgent }) {
  var kind = String(msg.type || "message");
  var from = String(msg.from || "");
  var color = wbcAgentColor(from);
  var html = wbcHighlightMentions(wbcRenderMarkdown(msg.content || ""), agentIds);

  if (!from) {
    return <div className="wbc-subagent-syssep"><span dangerouslySetInnerHTML={{ __html: html }} /></div>;
  }

  if (kind === "result") {
    return (
      <article className={"wbc-subagent-msg result" + (dimmed ? " dimmed" : "")} style={{ "--wb-agent-color": color }}>
        <div className="wbc-subagent-msg-head">
          <span className="wbc-subagent-avatar sm" style={{ background: color }}>{wbcAgentInitials(name)}</span>
          <b>{name}</b>
          <span className="wbc-subagent-tag result">{wbcT("workbenchChat.subagent.result", "Result")}</span>
          <time>{wbcFormatTime(msg.timestamp)}</time>
        </div>
        <div className="wbc-subagent-bubble result markdown" dangerouslySetInnerHTML={{ __html: html }} />
      </article>
    );
  }

  var isBroadcast = kind === "broadcast" || String(msg.to || "") === "all";
  var toUser = String(msg.to || "") === "user";

  return (
    <article className={"wbc-subagent-msg" + (grouped ? " grouped" : "") + (dimmed ? " dimmed" : "")} style={{ "--wb-agent-color": color }}>
      {grouped ? (
        <span className="wbc-subagent-avatar-spacer" aria-hidden="true"></span>
      ) : (
        <button type="button" className="wbc-subagent-avatar sm" style={{ background: color }}
          onClick={function () { onSelectAgent && onSelectAgent(from); }} title={name}>
          {wbcAgentInitials(name)}
        </button>
      )}
      <div className="wbc-subagent-msg-body">
        {grouped ? null : (
          <div className="wbc-subagent-msg-head">
            <b style={{ color: color }}>{name}</b>
            {isBroadcast ? <span className="wbc-subagent-tag broadcast">{wbcT("workbenchChat.subagent.broadcast", "Broadcast")}</span> : null}
            {toUser ? <span className="wbc-subagent-tag touser">{wbcT("workbenchChat.subagent.toUser", "To you")}</span> : null}
            <time>{wbcFormatTime(msg.timestamp)}</time>
          </div>
        )}
        <div className="wbc-subagent-bubble markdown" dangerouslySetInnerHTML={{ __html: html }} />
      </div>
    </article>
  );
}

// Prefer the durable chat plan. Fall back to the pending confirmation payload
// during the small window before the chat record is re-fetched.
function wbcActivePlan(chat) {
  var active = chat && chat.activePlan;
  if (active && typeof active === "object") return active;
  var pq = chat && chat.pendingQuestion;
  var plan = pq && pq.plan;
  if (!plan || typeof plan !== "object") return null;
  var hasSteps = (Array.isArray(plan.steps) && plan.steps.length > 0)
    || (Array.isArray(plan.entries) && plan.entries.length > 0);
  return (plan.title || plan.summary || hasSteps) ? plan : null;
}

function wbcPlanStatusText(status) {
  return {
    proposed: wbcT("chat.side.planProposed", "Awaiting approval"),
    active: wbcT("chat.side.planActive", "In progress"),
    paused: wbcT("chat.side.planPaused", "Paused"),
  }[status] || "";
}

function wbcPlanStepStatusText(status) {
  return {
    pending: wbcT("chat.side.planStepPending", "Pending"),
    in_progress: wbcT("chat.side.planStepActive", "Working"),
    completed: wbcT("chat.side.planStepCompleted", "Completed"),
    failed: wbcT("chat.side.planStepFailed", "Failed"),
    skipped: wbcT("chat.side.planStepSkipped", "Skipped"),
  }[status] || "";
}

// Right-panel 计划 tab — durable from proposal through execution completion.
function WbcPlanTab({ plan }) {
  var p = plan || {};
  var steps = Array.isArray(p.steps) ? p.steps : (Array.isArray(p.entries) ? p.entries : []);
  return (
    <div className="workbench-side-stack">
      <section className="workbench-side-section wbc-plan">
        <div className="wbc-plan-head">
          <h3>{p.title || wbcT("chat.side.planTitle", "Proposed plan")}</h3>
          {p.status ? <span className={"wbc-plan-state " + p.status}>{wbcPlanStatusText(p.status)}</span> : null}
        </div>
        {p.summary ? <p className="workbench-muted">{p.summary}</p> : null}
        {p.markdownPath ? <p className="wbc-plan-path" title={p.markdownPath}>{p.markdownPath}</p> : null}
        {steps.length === 0 ? (
          <p className="workbench-muted">{wbcT("chat.side.planEmpty", "The agent has not detailed any steps yet.")}</p>
        ) : (
          <ol className="wbc-plan-steps">
            {steps.map(function (step, i) {
              var tasks = Array.isArray(step.tasks) ? step.tasks : [];
              var status = step.status || "pending";
              return (
                <li key={step.id || i} className={"wbc-plan-step " + status}>
                  <div className="wbc-plan-step-title">
                    <b>{step.title || step.content || (wbcT("chat.side.planStep", "Step") + " " + (i + 1))}</b>
                    <span>{wbcPlanStepStatusText(status)}</span>
                  </div>
                  {tasks.length > 0 && (
                    <ul className="wbc-plan-tasks">
                      {tasks.map(function (t, j) { return <li key={j}>{String(t)}</li>; })}
                    </ul>
                  )}
                  {step.note ? <p className="wbc-plan-note">{step.note}</p> : null}
                </li>
              );
            })}
          </ol>
        )}
      </section>
    </div>
  );
}

function wbcZoomAnchorRestorer(container, oldScale, clientX, clientY) {
  if (!container) return function () {};
  var rect = container.getBoundingClientRect();
  var anchorX = Number.isFinite(clientX) ? clientX - rect.left : rect.width / 2;
  var anchorY = Number.isFinite(clientY) ? clientY - rect.top : rect.height / 2;
  anchorX = Math.max(0, Math.min(rect.width, anchorX));
  anchorY = Math.max(0, Math.min(rect.height, anchorY));
  var safeOldScale = Math.max(0.0001, Number(oldScale) || 1);
  var contentX = (container.scrollLeft + anchorX) / safeOldScale;
  var contentY = (container.scrollTop + anchorY) / safeOldScale;
  return function (newScale) {
    var safeNewScale = Math.max(0.0001, Number(newScale) || 1);
    window.requestAnimationFrame(function () {
      container.scrollLeft = Math.max(0, contentX * safeNewScale - anchorX);
      container.scrollTop = Math.max(0, contentY * safeNewScale - anchorY);
    });
  };
}

// ---- PDF.js viewer (replaces <embed> for PDF files) -------------------------

export { WBC_PROJECT_FILE_DRAFTS, WbcArtifactSplit, WbcArtifactSplitHost, WbcBrowserSplit, WbcBrowserSplitHost, WbcChangeSplit, WbcChangeSplitHost, WbcChatSplit, WbcChatSplitHost, WbcMapPaneContent, WbcMapSplitHost, WbcPaneCardFrame, WbcPaneColumnResizer, WbcPaneContextTrackDropSurface, WbcPaneRowResizer, WbcSide, WbcSideAgentSplit, WbcSideAgentSplitHost, WbcSplitGripBar, WbcSubagentsSplitHost, WbcSubagentsTab, useWbcMapData, wbcArtifactFileKey, wbcCanEditProjectTextFile, wbcChatArtifactFiles, wbcDiscardProjectFileDraft, wbcEditableChatFileResource, wbcMapItemKey, wbcMapItemLabel, wbcProjectFileDraftKey, wbcProjectFileEditUrl, wbcZoomAnchorRestorer }
