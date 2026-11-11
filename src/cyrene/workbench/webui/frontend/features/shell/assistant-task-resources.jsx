import { wbcChatArtifactFiles } from "../chat/resource-splits.jsx"
import { WbcFileVisual } from "../chat/file-resources.jsx"
import { workbenchServices } from "../../shared/runtime/services.jsx"
import { WbcHoverMarquee } from "../chat/rail-model.jsx"
import { WBC_ICONS, wbcSetResourceDrag, wbcFileDragPayload, wbcFormatTime } from "../../workbench-chat.jsx"

// Use the same uploads, workspace changes and live deliverables as the chat panel.
export function assistantTaskFiles(chat) {
  // Assistant tasks share a virtual project but may have distinct workspaces.
  // Keep the shared index while retaining session-scoped file URLs.
  return wbcChatArtifactFiles({ ...chat, projectId: "" }).map(function (item) {
    return { ...item.file, source: item.file.source === "agent" ? "assistant-task" : item.file.source, name: item.file.name || String(item.file.path || "").split("/").pop() || "文件", output: item.role === "assistant" };
  });
}

export function assistantFileKey(task, file) {
  var path = String(file.path || "").replace(/\\/g, "/");
  return path ? (path.startsWith("/") ? path : String(task.workspace_path || task.session_id).replace(/\/$/, "") + "/" + path)
    : String(file.url || file.id || "");
}

export function assistantFileOwners(tasks, filesByTask) {
  var owners = {}, delivered = {};
  tasks.forEach(function (task) {
    (filesByTask[task.session_id] || []).forEach(function (file) {
      var key = assistantFileKey(task, file);
      if (!owners[key] || (file.output && !delivered[key])) {
        owners[key] = task.session_id;
        delivered[key] = Boolean(file.output);
      }
    });
  });
  return owners;
}

// Retain unchanged array identities too: onFiles must not wake the parent rail
// just because a terminal changed, or because a reconciliation returned again.
export function mergeAssistantResources(previous, patch) {
  var next = { ...previous }, changed = false;
  Object.keys(patch).forEach(function (key) {
    if (JSON.stringify(previous[key]) === JSON.stringify(patch[key])) return;
    next[key] = patch[key]; changed = true;
  });
  return changed ? next : previous;
}

function resourceTabs(state) {
  return (state?.tabs || []).map(function (tab) {
    return { id: tab.id, title: tab.title, url: tab.url, favicon: tab.favicon };
  });
}

function resourceSnapshotPatch(task, results, acceptTabs) {
  var patch = {};
  if (results[0].status === "fulfilled") patch.files = assistantTaskFiles(results[0].value.chat || {});
  if (results[1].status === "fulfilled") patch.terminals = (results[1].value.terminals || [])
    .filter(terminal => terminal.ownerChatId === task.session_id)
    .map(terminal => ({ id: terminal.id, title: terminal.title, cwd: terminal.cwd }));
  if (results[2].status === "fulfilled" && acceptTabs) patch.tabs = resourceTabs(results[2].value);
  return patch;
}

export function observeAssistantResources(task, setResources, setError) {
  var api = workbenchServices.api();
  var bridge = window.cyrene && window.cyrene.browser;
  var disposed = false, inFlight = false, dirty = false, timer = null, queued = false;
  var controller, generation = 0, browserRevision = 0;
  var hidden = () => document.hidden || document.visibilityState === "hidden";
  function clearTimer() { window.clearTimeout(timer); timer = null; queued = false; }
  function schedule(delay, event = false) {
    clearTimer();
    if (disposed || hidden()) return;
    queued = event;
    timer = window.setTimeout(refresh, delay);
  }
  function invalidate() {
    if (disposed || hidden()) return;
    if (inFlight) { dirty = true; return; }
    if (!queued) schedule(200, true);
  }
  async function refresh() {
    clearTimer();
    if (disposed || hidden()) return;
    if (inFlight) { dirty = true; return; }
    inFlight = true; dirty = false;
    var revision = generation;
    var tabsRevision = browserRevision;
    controller = new AbortController();
    var options = { toast: false, signal: controller.signal };
    try {
      var results = await Promise.allSettled([
        api.json("/api/workbench/chats/" + encodeURIComponent(task.session_id) + "/resources", options),
        api.json("/api/terminals?ownerChatId=" + encodeURIComponent(task.session_id), options),
        bridge && bridge.getState ? bridge.getState(task.session_id) : Promise.resolve({ tabs: [] }),
      ]);
      if (disposed || hidden() || revision !== generation) return;
      var patch = resourceSnapshotPatch(task, results, tabsRevision === browserRevision);
      setResources(previous => mergeAssistantResources(previous, patch));
      setError(results.some(result => result.status === "rejected") ? "部分资源暂时无法加载，正在重试。" : "");
    } finally {
      inFlight = false;
      // Events handle normal updates. Reconcile infrequently for missed events
      // or browser hosts without push support; never poll a hidden document.
      schedule(dirty ? 200 : 60000, dirty);
    }
  }
  function visibility() {
    if (hidden()) { generation++; clearTimer(); controller?.abort(); }
    else { clearTimer(); refresh(); }
  }
  function changed(event) {
    var id = event.chat_id || event.chatId || event.session_id || event.sessionId;
    if (event.type === "terminal_list_changed") {
      if (!event.project_id || !task.project_id || event.project_id === task.project_id) invalidate();
    } else if (id === task.session_id && ["workbench_chat_changed", "workspace_changes"].includes(event.type)) invalidate();
  }
  function lifecycle(event) {
    var detail = event.detail || {};
    if ((detail.chatId || detail.sessionId) === task.session_id) invalidate();
  }
  var unsubscribe = workbenchServices.events().subscribe(changed);
  function browserChanged(state) {
    if (disposed || hidden()) return;
    // A browser push wins over an older browser snapshot, but does not discard
    // the independent file/terminal results or trigger another HTTP request.
    browserRevision++;
    setResources(previous => mergeAssistantResources(previous, { tabs: resourceTabs(state) }));
  }
  var unsubscribeBrowser = bridge?.onManagerState
    ? bridge.onManagerState(state => browserChanged({ tabs: (state.pages || []).filter(page => page.sessionId === task.session_id) }))
    : bridge?.onState?.(state => { if (state.sessionId === task.session_id) browserChanged(state); });
  document.addEventListener("visibilitychange", visibility);
  window.addEventListener("cyrene:wbc-chat-lifecycle", lifecycle);
  refresh();
  return function () {
    disposed = true; clearTimer(); controller?.abort(); unsubscribe(); unsubscribeBrowser?.();
    document.removeEventListener("visibilitychange", visibility);
    window.removeEventListener("cyrene:wbc-chat-lifecycle", lifecycle);
  };
}

export function AssistantTaskResources({ task, onOpen, onFiles, onCount, fileOwners, query, active = true }) {
  var [resources, setResources] = React.useState({ files: [], terminals: [], tabs: [] });
  var [error, setError] = React.useState("");
  React.useEffect(function () {
    if (!active) return;
    return observeAssistantResources(task, setResources, setError);
  }, [task.session_id, task.project_id, active]);
  React.useEffect(function () {
    if (onFiles) onFiles(task.session_id, resources.files);
  }, [task.session_id, resources.files, onFiles]);
  var presentation = task.presentation || {};
  var combinedFiles = new Map();
  resources.files.concat(presentation.files || []).forEach(function (file) {
    combinedFiles.set(file.path || file.url || file.id, file);
  });
  var items = [
    ...Array.from(combinedFiles.values()).filter(function (file) {
      var owner = fileOwners && fileOwners[assistantFileKey(task, file)];
      return !owner || owner === task.session_id;
    }).map(function (file) { return {kind: "file", key: file.id || file.path || file.url,
      title: file.name, detail: file.path || file.name, icon: WBC_ICONS.file, payload: file,
      drag: wbcFileDragPayload(file, task.session_id, task.project_id)}; }),
    ...resources.terminals.map(function (terminal) { return {kind: "terminal", key: terminal.id,
      title: terminal.title || "终端", detail: terminal.cwd, icon: WBC_ICONS.code, payload: terminal.id,
      drag: {kind: "terminal", terminalId: terminal.id, title: terminal.title, ownerSessionId: task.session_id}}; }),
    ...resources.tabs.map(function (tab) { return {kind: "browser", key: tab.id,
      title: tab.title || tab.url || "浏览器标签", detail: tab.url, favicon: tab.favicon, icon: WBC_ICONS.browser, payload: tab.id,
      drag: {kind: "browser", tabId: tab.id, title: tab.title, url: tab.url, ownerSessionId: task.session_id}}; }),
  ];
  var visibleItems = items.filter(function (item) { return !(presentation.hidden_resources || []).includes(item.kind + ":" + item.key); }).filter(function (item) { return !query || (item.title + " " + (item.detail || "")).toLocaleLowerCase().includes(query); });
  React.useEffect(function () {
    if (onCount) onCount(task.session_id, visibleItems.length);
  }, [task.session_id, visibleItems.length, onCount]);
  return <>
    {visibleItems.map(function (item) { return <div key={item.kind + ":" + item.key}
      role="button" tabIndex={0} draggable="true" className="wbc-chat-card wbc-chat-group-child assistant-resource-row"
      aria-label={item.title} title={task.title + " · " + (item.detail || item.title)}
      onClick={function () { onOpen(task.session_id, item.kind, item.payload); }}
      onKeyDown={function (event) { if (event.key === "Enter" || event.key === " ") {
        event.preventDefault(); onOpen(task.session_id, item.kind, item.payload);
      } }}
      onDragStart={function (event) { wbcSetResourceDrag(event, item.drag); }}>
      <span className="wbc-chat-card-top">
        {item.kind === "file" ? <WbcFileVisual file={item.payload} className="wbc-file-visual assistant-resource-file-icon" /> : item.kind === "browser" ? <span className="wbc-browser-picker-favicon" aria-hidden="true">
          <span className="wbc-browser-picker-favicon-fallback">{item.icon}</span>
          {item.favicon ? <img key={item.favicon} src={item.favicon} alt="" draggable="false" onError={function (event) { event.currentTarget.hidden = true; }} /> : null}
        </span> : <span className="wbc-chat-row-icon" aria-hidden="true">{item.icon}</span>}
        <span className="wbc-chat-card-title"><b><WbcHoverMarquee text={item.title} /></b></span>
        <span className="wbc-chat-card-right"><time className="wbc-chat-card-time">{wbcFormatTime(task.updated_at)}</time></span>
      </span>
    </div>; })}
    {error && <p className="assistant-resource-empty" role="status">{error}</p>}
  </>;
}
