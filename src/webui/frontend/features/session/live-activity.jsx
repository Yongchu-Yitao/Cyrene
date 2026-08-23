import { workbenchServices } from "../../shared/runtime/services.jsx"
import { wbReduceSessionActivity } from "./activity.jsx"

var { useState, useEffect } = React;

function useWorkbenchLiveActivityState(chatRuntimeEngine) {
  var [chatRuntimes, setChatRuntimes] = useState(function () {
    return chatRuntimeEngine && chatRuntimeEngine.snapshot ? chatRuntimeEngine.snapshot() : {};
  });
  var [sessionActivityLive, setSessionActivityLive] = useState({});

  return {
    chatRuntimes: chatRuntimes,
    setChatRuntimes: setChatRuntimes,
    sessionActivityLive: sessionActivityLive,
    setSessionActivityLive: setSessionActivityLive,
  };
}

function useWorkbenchLiveActivitySubscriptions(chatRuntimeEngine, setChatRuntimes, setSessionActivityLive) {
  useEffect(function () {
    if (!chatRuntimeEngine || typeof chatRuntimeEngine.subscribe !== "function") return undefined;
    setChatRuntimes(chatRuntimeEngine.snapshot());
    var subscribe = typeof chatRuntimeEngine.subscribeSummary === "function"
      ? chatRuntimeEngine.subscribeSummary
      : chatRuntimeEngine.subscribe;
    return subscribe(function (snapshot) { setChatRuntimes(snapshot); });
  }, [chatRuntimeEngine]);

  useEffect(function () {
    function onActivityEvent(data) {
      if (!data) return;
      var sessionId = String(data.session_id || data.chatId || data.chat_id || "").trim();
      if (!sessionId) return;
      var type = String(data.type || "");
      if (["tool_call", "tool_call_started", "tool_call_progress", "tool_call_finished", "llm_call", "phase_transition", "subagent_update", "goal_loop_update", "session_update", "error", "interrupted", "awaiting_user"].indexOf(type) < 0) return;
      setSessionActivityLive(function (previous) {
        var prior = previous[sessionId] || { agents: {} };
        var next = wbReduceSessionActivity(prior, data);
        return Object.assign({}, previous, { [sessionId]: next });
      });
    }
    function onChatLifecycle(event) {
      var detail = event && event.detail || {};
      var status = String(detail.status || "");
      var sessionId = String(detail.chatId || detail.sessionId || "");
      if (!sessionId || !status || status === "refresh") return;
      onActivityEvent({
        type: "session_update",
        session_id: sessionId,
        runId: String(detail.runId || ""),
        status: status,
        timestamp: String(detail.timestamp || new Date().toISOString()),
      });
    }
    var unsubscribe = workbenchServices.events().subscribe(onActivityEvent);
    window.addEventListener("cyrene:wbc-chat-lifecycle", onChatLifecycle);
    return function () {
      unsubscribe();
      window.removeEventListener("cyrene:wbc-chat-lifecycle", onChatLifecycle);
    };
  }, []);
}

export { useWorkbenchLiveActivityState, useWorkbenchLiveActivitySubscriptions }
