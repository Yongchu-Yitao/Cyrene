// Explicit Workbench event bridge. It owns SSE subscriber lifecycle, the
// recent-event ring, and idempotence for events carrying a stable event id.
(function (root) {
  "use strict";

  var platform = root.CyreneUI;
  if (!platform) throw new Error("CyreneUI platform registry must load first");

  var listeners = new Set();
  var recent = [];
  var seenIds = new Set();
  var seenOrder = [];
  var MAX_RECENT = 200;
  var MAX_SEEN_IDS = 512;
  var source = null;

  var KNOWN_EVENT_NAMES = new Set([
    "agent_chat_user_message",
    "agent_comm",
    "assistant_message",
    "browser_frame",
    "browser_takeover_cancelled",
    "browser_takeover_request",
    "browser_user_operation",
    "cc_learning",
    "chat_message",
    "destructive_confirmation",
    "entity_created",
    "entity_deleted",
    "entity_updated",
    "external_upload_confirmation",
    "goal_loop_update",
    "guidance_applied",
    "guidance_acknowledged",
    "heartbeat",
    "llm_call",
    "map_pin",
    "notification",
    "phase_transition",
    "plan",
    "plan_progress",
    "round_guidance_update",
    "session_update",
    "shell_update",
    "subagent_update",
    "tool_call",
    "tool_call_finished",
    "tool_call_started",
    "user_question",
    "user_question_answered",
    "workbench_proactive_message",
    "workbench_chat_changed",
    "workspace_changes",
  ]);

  function eventIdentity(event) {
    if (!event || typeof event !== "object") return "";
    return String(event.event_id || event.eventId || "").trim();
  }

  function rememberIdentity(identity) {
    if (!identity) return true;
    if (seenIds.has(identity)) return false;
    seenIds.add(identity);
    seenOrder.push(identity);
    if (seenOrder.length > MAX_SEEN_IDS) {
      seenIds.delete(seenOrder.shift());
    }
    return true;
  }

  function publish(event) {
    if (!event || typeof event !== "object" || typeof event.type !== "string") {
      console.warn("Cyrene: ignored malformed UI event", event);
      return false;
    }
    if (!rememberIdentity(eventIdentity(event))) return false;
    if (!KNOWN_EVENT_NAMES.has(event.type)) {
      console.debug("Cyrene: received unknown UI event", event.type);
    }
    recent.push(event);
    if (recent.length > MAX_RECENT) recent.shift();
    listeners.forEach(function (listener) {
      try {
        listener(event);
      } catch (error) {
        console.error("Cyrene: UI event subscriber failed", error);
      }
    });
    return true;
  }

  function subscribe(listener) {
    if (typeof listener !== "function") {
      throw new TypeError("CyreneUI.events.subscribe requires a function");
    }
    listeners.add(listener);
    return function unsubscribe() {
      listeners.delete(listener);
    };
  }

  function setSource(nextSource) {
    if (source && source !== nextSource && typeof source.close === "function") {
      source.close();
    }
    source = nextSource || null;
  }

  function close() {
    if (source && typeof source.close === "function") source.close();
    source = null;
    listeners.clear();
  }

  var bridge = {
    knownEventNames: KNOWN_EVENT_NAMES,
    listeners: listeners,
    recent: recent,
    publish: publish,
    subscribe: subscribe,
    setSource: setSource,
    getSource: function () { return source; },
    close: close,
  };
  platform.events = platform.register("events", bridge);
})(window);
