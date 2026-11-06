import { workbenchServices } from "../../shared/runtime/services.jsx"
import { WbcHoverMarquee } from "../chat/rail-model.jsx"
import { WBC_ICONS, wbcSetResourceDrag, wbcFileDragPayload, wbcFormatTime } from "../../workbench-chat.jsx"

// Keep workspace files on their owning chat's URL: tasks may use different
// directories even though they share the global assistant's execution scope.
export function assistantTaskFiles(chat) {
  var files = new Map();
  function add(file, output) {
    if (!file) return;
    var path = String(file.path || "").replace(/\\/g, "/");
    var key = path || file.url || file.id;
    if (!key) return;
    var url = file.url || (path ? "/api/workbench/chats/" + encodeURIComponent(chat.id) + "/files/" + path.split("/").map(encodeURIComponent).join("/") : "");
    var previous = files.get(key);
    files.set(key, { ...file, url: url, source: "assistant-task", output: output || previous && previous.output,
      name: file.name || path.split("/").pop() || "文件" });
  }
  (chat.files || []).forEach(function (file) { add(file, false); });
  (chat.messages || []).forEach(function (message) {
    (message.attachments || []).forEach(function (file) { add(file, message.role === "assistant"); });
  });
  return Array.from(files.values());
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

export function AssistantTaskResources({ task, onOpen, onFiles, fileOwners, query, active = true }) {
  var [resources, setResources] = React.useState({ files: [], terminals: [], tabs: [] });
  var [error, setError] = React.useState("");
  React.useEffect(function () {
    if (!active) return;
    var cancelled = false, timer;
    async function refresh() {
      var api = workbenchServices.api();
      var bridge = window.cyrene && window.cyrene.browser;
      var results = await Promise.allSettled([
        workbenchServices.chat().Model.getChat(task.session_id),
        api.json("/api/terminals?ownerChatId=" + encodeURIComponent(task.session_id), {toast: false}),
        bridge && bridge.getState ? bridge.getState(task.session_id) : Promise.resolve({tabs: []}),
      ]);
      if (cancelled) return;
      setResources(function (old) {
        return {
          files: results[0].status === "fulfilled" ? assistantTaskFiles(results[0].value || {}) : old.files,
          terminals: results[1].status === "fulfilled" ? (results[1].value.terminals || []).filter(function (terminal) {
            return terminal.ownerChatId === task.session_id;
          }) : old.terminals,
          tabs: results[2].status === "fulfilled" ? (results[2].value && results[2].value.tabs || []) : old.tabs,
        };
      });
      setError(results.some(function (result) { return result.status === "rejected"; }) ? "部分资源暂时无法加载，正在重试。" : "");
      timer = setTimeout(refresh, 5000);
    }
    refresh();
    return function () { cancelled = true; clearTimeout(timer); };
  }, [task.session_id, active]);
  React.useEffect(function () {
    if (onFiles) onFiles(task.session_id, resources.files);
  }, [task.session_id, resources.files, onFiles]);
  var items = [
    ...resources.files.filter(function (file) {
      var owner = fileOwners && fileOwners[assistantFileKey(task, file)];
      return !owner || owner === task.session_id;
    }).map(function (file) { return {kind: "file", key: file.path || file.url || file.id,
      title: file.name, detail: file.path || file.name, icon: WBC_ICONS.file, payload: file,
      drag: wbcFileDragPayload(file, task.session_id, task.project_id)}; }),
    ...resources.terminals.map(function (terminal) { return {kind: "terminal", key: terminal.id,
      title: terminal.title || "终端", detail: terminal.cwd, icon: WBC_ICONS.code, payload: terminal.id,
      drag: {kind: "terminal", terminalId: terminal.id, title: terminal.title, ownerSessionId: task.session_id}}; }),
    ...resources.tabs.map(function (tab) { return {kind: "browser", key: tab.id,
      title: tab.title || tab.url || "浏览器标签", detail: tab.url, icon: WBC_ICONS.browser, payload: tab.id,
      drag: {kind: "browser", tabId: tab.id, title: tab.title, url: tab.url, ownerSessionId: task.session_id}}; }),
  ];
  return <>
    {items.filter(function (item) { return !query || (item.title + " " + (item.detail || "")).toLocaleLowerCase().includes(query); }).map(function (item) { return <div key={item.kind + ":" + item.key}
      role="button" tabIndex={0} draggable="true" className="wbc-chat-card assistant-resource-row"
      aria-label={item.title} title={task.title + " · " + (item.detail || item.title)}
      onClick={function () { onOpen(task.session_id, item.kind, item.payload); }}
      onKeyDown={function (event) { if (event.key === "Enter" || event.key === " ") {
        event.preventDefault(); onOpen(task.session_id, item.kind, item.payload);
      } }}
      onDragStart={function (event) { wbcSetResourceDrag(event, item.drag); }}>
      <span className="wbc-chat-card-top">
        <span className="wbc-chat-row-icon" aria-hidden="true">{item.icon}</span>
        <span className="wbc-chat-card-title"><b><WbcHoverMarquee text={item.title} /></b></span>
        <span className="wbc-chat-card-right"><time className="wbc-chat-card-time">{wbcFormatTime(task.updated_at)}</time></span>
      </span>
    </div>; })}
    {error && <p className="assistant-resource-empty" role="status">{error}</p>}
  </>;
}
