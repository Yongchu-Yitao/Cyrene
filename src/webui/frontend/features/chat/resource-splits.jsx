import { workbenchServices } from "../../shared/runtime/services.jsx"
import { WBC_ICONS, WBC_SIDE_TAB_ICONS, WbcSplitPickerMenu, WorkbenchChatModel, useWbcEffect, useWbcRef, useWbcState, wbcAttachmentTypeLabel, wbcBrowserTabPickerPayload, wbcBrowserTabPickerToggleIsDebounced, wbcClampSideSplitWidthForPage, wbcErrorText, wbcFileViewKind, wbcNotifyBrowserLayoutChanged, wbcRenderMapMarkdown, wbcT } from "../../workbench-chat.jsx"
import { wbcBrowserStateForChat } from "./composer.jsx"
import { wbcProjectFileResource } from "./rail.jsx"
import { wbcCanOpenExternally, wbcChatUsedMap, wbcDownloadLink, wbcStartFileDrag } from "./file-resources.jsx"
import { WbcMapTab, WbcViewerTab } from "./viewer.jsx"

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
    var feedback = workbenchServices.feedback();
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
        ) : diffState.diff && workbenchServices.diff().Panel ? (
          React.createElement(workbenchServices.diff().Panel, { diff: diffState.diff, mode: "text", hideHeader: true, hideHunkHeaders: true })
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

    function paint() {
      frame = 0;
      if (page) page.style.setProperty("--wbc-side-track-width", nextWidth + "px");
    }
    function move(moveEvent) {
      var delta = splitSide === "left"
        ? (moveEvent.clientX - startX)
        : (startX - moveEvent.clientX);
      nextWidth = clampWidth(startWidth + delta, page);
      if (!frame) frame = requestAnimationFrame(paint);
    }
    function stop() {
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
  var BrowserIcon = workbenchServices.browser().Icon;
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
        {splitActive && workbenchServices.browser().ViewportPanel ? React.createElement(workbenchServices.browser().ViewportPanel, { browserState: liveState, browserSessionId: browserSessionId, roundId: liveState && liveState.roundId || browserState && browserState.roundId || "", onClose: onClose, onTakeoverComplete: onTakeoverComplete, zoomEnabled: true, resizeEdgeHintEnabled: true, hideTabStrip: true, hideReload: true, hideMute: true, splitChrome: true }) : null}
      </div>
    </aside>
  );
  if (maximized && window.ReactDOM && typeof window.ReactDOM.createPortal === "function") {
    var workbenchPortalRoot = document.querySelector(".workbench-shell") || document.body;
    return window.ReactDOM.createPortal(browserSplit, workbenchPortalRoot);
  }
  return browserSplit;
}


export { WBC_PROJECT_FILE_DRAFTS, WbcArtifactSplit, WbcArtifactSplitHost, WbcBrowserList, WbcBrowserSplit, WbcBrowserSplitHost, WbcChangeSplit, WbcChangeSplitHost, WbcMapList, WbcMapPaneContent, WbcMapSplitHost, WbcResourceSplitHost, WbcViewerList, useWbcMapData, wbcArtifactFileKey, wbcCanEditProjectTextFile, wbcChatArtifactFiles, wbcChatDeliveredArtifacts, wbcDiscardProjectFileDraft, wbcEditableChatFileResource, wbcMapItemKey, wbcMapItemLabel, wbcProjectFileDraftKey, wbcProjectFileEditUrl, wbcViewerFileFromItems }
