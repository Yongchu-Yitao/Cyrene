import { wbcTaskSessionFromStore } from "./file-resources.jsx"

function wbcRememberTaskPaneSession(context, taskId, session) {
  var id = String(taskId || "");
  if (!id || !session) return;
  context.setTaskPaneSessions(function (current) {
    if (current[id] === session) return current;
    return Object.assign({}, current, { [id]: session });
  });
}

function wbcOpenTaskRightPanel(context, taskId, tab) {
  var id = String(taskId || "");
  if (!id) return;
  context.setTaskRightTabs(function (current) {
    return Object.assign({}, current, { [id]: String(tab || "context") });
  });
  context.setSideVisible(true);
}

function wbcRefreshTaskRightPanel(context, taskId, nextStore) {
  var id = String(taskId || "");
  var nextSession = wbcTaskSessionFromStore(nextStore, id);
  if (nextSession) wbcRememberTaskPaneSession(context, id, nextSession);
  if (context.onTaskStoreChange) context.onTaskStoreChange(nextStore, id);
}

export { wbcOpenTaskRightPanel, wbcRefreshTaskRightPanel, wbcRememberTaskPaneSession }
