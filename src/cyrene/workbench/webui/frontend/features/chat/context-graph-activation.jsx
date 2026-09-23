import { wbcErrorText } from './errors.jsx';
import { wbcNormalizePermissionMode } from './capabilities.jsx';
export function graphActivationListener({ setChats, skipNextHydrationChatIdRef, setActiveChat, selectChat, runtimeEngine, model, setError, refreshChats, projectId }) {
    function activateGraphBranch(event) {
      var detail = event.detail || {};
      if (!detail.chatId) return;
      if (detail.chat) {
        setChats(function (previous) { return [detail.chat].concat(previous.filter(function (item) { return item.id !== detail.chatId; })); });
        skipNextHydrationChatIdRef.current = detail.chatId;
        setActiveChat(detail.chat);
      }
      selectChat(detail.chatId);
      if (detail.replay) runtimeEngine.start(detail.chatId, { retry: true, forkReplay: true, mode: wbcNormalizePermissionMode(detail.chat && detail.chat.permissionMode, "auto") }, model).catch(function (error) { setError(wbcErrorText(error)); });
      else refreshChats(detail.chatId);
    }
    function onRefresh(event) {
      const detail = event.detail || {};
      if (!detail.projectId || String(detail.projectId) === String(projectId)) refreshChats(detail.selectId || "");
    }
    window.addEventListener("cyrene:wbc-refresh-chats", onRefresh);
    window.addEventListener("cyrene:context-graph-activate", activateGraphBranch);
    return function () { window.removeEventListener("cyrene:wbc-refresh-chats", onRefresh); window.removeEventListener("cyrene:context-graph-activate", activateGraphBranch); };
}
