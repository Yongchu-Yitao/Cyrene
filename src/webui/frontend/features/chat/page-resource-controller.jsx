import { wbcProjectFileResource } from "./rail.jsx"
import { wbcArtifactFileKey } from "./split-pane.jsx"
import { wbcErrorText } from "../../workbench-chat.jsx"

function wbcOpenViewer(context, file, preferredSide) {
  if (!file) return;
  context.setViewerFile(file);
  context.setSideTab("");
  context.setSideVisible(true);
  context.selectResourceSplit("viewer", wbcArtifactFileKey(file), true);
  context.openPaneContent("file", file, { side: preferredSide === "left" ? "left" : "right" });
}

function wbcOpenProjectFile(context, entry) {
  var file = wbcProjectFileResource(context.projectId, entry);
  if (file) wbcOpenViewer(context, file);
}

function wbcRevealTopbarResource(context, chatId, resource) {
  if (!chatId || !resource) return;
  if (resource.type === "browser") {
    context.setBrowserActiveByChat(function (previous) {
      return Object.assign({}, previous, { [chatId]: true });
    });
    context.setBrowserWindowModeByChat(function (previous) {
      return Object.assign({}, previous, { [chatId]: "pip" });
    });
    return;
  }
  if (resource.type === "file" && resource.file) wbcOpenViewer(context, resource.file);
}

function wbcMarkViewerFileRead(context, file) {
  if (!file || file.source === "project" || !context.projectId || !file.url) return;
  fetch("/api/workbench/library/read?workspace=" + encodeURIComponent(context.projectId), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      attachment_url: String(file.url || ""),
      file_name: String(file.name || ""),
    }),
  }).catch(function () {});
}

function wbcLoadSubagents(context, chatId, roundId) {
  if (!chatId) {
    context.setSubagentData({ rounds: [], activeRoundId: "", agents: [], messages: [] });
    return Promise.resolve(null);
  }
  context.setSubagentLoading(true);
  return context.model.getSubagents(chatId, roundId).then(function (payload) {
    if (context.activeChatIdRef.current === chatId) context.setSubagentData(payload);
    return payload;
  }).catch(function (error) {
    if (context.activeChatIdRef.current === chatId) context.setError(wbcErrorText(error));
    return null;
  }).finally(function () {
    if (context.activeChatIdRef.current === chatId) context.setSubagentLoading(false);
  });
}

export { wbcLoadSubagents, wbcMarkViewerFileRead, wbcOpenProjectFile, wbcOpenViewer, wbcRevealTopbarResource }
