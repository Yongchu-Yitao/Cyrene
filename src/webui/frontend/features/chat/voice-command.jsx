import { workbenchServices } from "../../shared/runtime/services.jsx"
import { wbcT } from "./core.jsx"
import { WbcVoice, wbcSetVoiceQueueClearer } from "./voice-playback.jsx"
import { WBC_TOPBAR_INITIAL_SILENCE_MS, wbcStartVoiceRecorder } from "./voice-input.jsx"

  var phase = "";
  var ready = false;
  var recorder = null;
  var listeners = new Set();
  var voiceSnapshot = WbcVoice.getSnapshot();
  var speechQueue = [];
  var speaking = false;
  var runStates = new Map();
  var statusToastId = 0;

  function snapshot() {
    return { phase: phase, ready: ready };
  }

  function notify() {
    var value = snapshot();
    listeners.forEach(function (listener) { listener(value); });
  }

  function setPhase(next) {
    phase = next;
    notify();
  }

  function showStatusToast(message, type, duration) {
    var feedback = workbenchServices.feedback();
    if (statusToastId) feedback.dismissToast(statusToastId);
    statusToastId = feedback.showToast(message, type || "info", {
      duration: duration == null ? 0 : duration,
    });
  }

  function showError(error) {
    var message = error && error.message ? error.message : String(error || "");
    showStatusToast(
      wbcT("topbar.voiceCommandFailed", "Voice command failed: {error}", { error: message }),
      "error",
      6000
    );
  }

  function responsePayload(response) {
    return response.json().catch(function () { return {}; }).then(function (payload) {
      if (!response.ok) throw new Error(payload.error || payload.detail || ("HTTP " + response.status));
      return payload;
    });
  }

  function currentLanguage() {
    try { return workbenchServices.i18n().getLang(); } catch (e) { return ""; }
  }

  function currentUiInstanceId() {
    try {
      if (window.CyreneUI.has("uiSurface")) return workbenchServices.uiSurface().getInstanceId();
    } catch (e) {}
    return "";
  }

  function clearSpeechQueue() {
    speechQueue = [];
  }

  function speechEnabled() {
    var status = voiceSnapshot && voiceSnapshot.status;
    return !!(status && status.auto_read && status.tts_ready);
  }

  function drainSpeechQueue() {
    if (speaking || !speechQueue.length || !speechEnabled()) return;
    if (voiceSnapshot && voiceSnapshot.activeKey) return;
    var item = speechQueue.shift();
    speaking = true;
    WbcVoice.speak(item.text, "voice-command:" + item.runId + ":" + item.id)
      .finally(function () {
        speaking = false;
        drainSpeechQueue();
      });
  }

  function enqueueSpeech(state, text, kind) {
    var plain = WbcVoice.plainText(text);
    if (!plain || !speechEnabled()) return;
    var dedupeKey = kind + ":" + plain;
    if (state.seen.has(dedupeKey)) return;
    state.seen.add(dedupeKey);
    speechQueue.push({
      id: state.sequence += 1,
      runId: state.runId,
      kind: kind,
      text: plain,
    });
    drainSpeechQueue();
  }

  function replaceQueuedRunSpeech(state, text, kind) {
    // Never interrupt an item that has already started. Everything for this
    // run that is still waiting is provisional and can be replaced atomically.
    speechQueue = speechQueue.filter(function (item) { return item.runId !== state.runId; });
    enqueueSpeech(state, text, kind);
  }

  function pendingQuestionText(pending) {
    var question = pending && typeof pending === "object" ? pending : {};
    var prompt = String(question.text || question.prompt || question.question || question.title || "").trim();
    var values = Array.isArray(question.options) ? question.options : (Array.isArray(question.choices) ? question.choices : []);
    var options = values.map(function (item) {
      if (item && typeof item === "object") return String(item.label || item.text || item.title || item.value || "").trim();
      return String(item || "").trim();
    }).filter(Boolean);
    if (!options.length) return prompt;
    return prompt + (currentLanguage() === "zh" ? "。可选项：" : ". Options: ") + options.join(currentLanguage() === "zh" ? "；" : "; ");
  }

  function latestAssistantText(chatId) {
    return fetch("/api/workbench/chats/" + encodeURIComponent(chatId))
      .then(responsePayload)
      .then(function (payload) {
        var messages = payload && payload.chat && Array.isArray(payload.chat.messages) ? payload.chat.messages : [];
        for (var i = messages.length - 1; i >= 0; i -= 1) {
          if (messages[i] && messages[i].role === "assistant" && String(messages[i].content || "").trim()) {
            return String(messages[i].content || "");
          }
        }
        return "";
      })
      .catch(function () { return ""; });
  }

  function handleRunEvent(state, event) {
    var data = event && event.data && typeof event.data === "object" ? event.data : {};
    if (event.type === "intermediate_message" && !state.finalSeen) {
      var message = data.message && typeof data.message === "object" ? data.message : {};
      if (!message.role || message.role === "assistant") {
        enqueueSpeech(state, message.content || message.text || "", "intermediate");
      }
      return;
    }
    if (event.type === "reply_done") {
      var finalText = String(data.response || "").trim();
      if (finalText) {
        state.finalSeen = true;
        replaceQueuedRunSpeech(state, finalText, "final");
      }
      return;
    }
    if (event.type === "awaiting_user") {
      var questionText = pendingQuestionText(data.pending_question || data.pendingQuestion);
      state.finalSeen = true;
      replaceQueuedRunSpeech(state, questionText, "question");
      return;
    }
    if (event.type === "error") {
      showError(new Error(data.message || data.error || data.code || "Agent run failed"));
    }
  }

  function pollRun(state) {
    if (!runStates.has(state.runId)) return;
    fetch(
      "/v1/control/runs/" + encodeURIComponent(state.runId)
      + "/events?after=" + encodeURIComponent(state.cursor) + "&limit=200"
    ).then(responsePayload).then(function (payload) {
      var events = Array.isArray(payload.events) ? payload.events : [];
      events.forEach(function (event) {
        state.cursor = Math.max(state.cursor, Number(event.cursor) || 0);
        handleRunEvent(state, event);
      });
      state.cursor = Math.max(state.cursor, Number(payload.next_cursor) || 0);
      if (!payload.completed) {
        state.timer = setTimeout(function () { pollRun(state); }, 350);
        return;
      }
      runStates.delete(state.runId);
      if (state.finalSeen) return;
      latestAssistantText(state.chatId).then(function (text) {
        if (text) replaceQueuedRunSpeech(state, text, "final-fallback");
      });
    }).catch(function (error) {
      runStates.delete(state.runId);
      showError(error);
    });
  }

  function monitorRun(runId, chatId, cursor) {
    if (!runId || runStates.has(runId)) return;
    var state = {
      runId: String(runId),
      chatId: String(chatId || ""),
      cursor: Number(cursor) || 0,
      finalSeen: false,
      sequence: 0,
      seen: new Set(),
      timer: 0,
    };
    runStates.set(state.runId, state);
    pollRun(state);
  }

  function finishRecording(controller) {
    if (phase !== "recording" && phase !== "starting") return Promise.resolve(false);
    var activeRecorder = controller || recorder;
    recorder = null;
    setPhase("recognizing");
    showStatusToast(
      wbcT("topbar.voiceCommandRecognizingNotice", "Recognizing speech…"),
      "info",
      0
    );
    if (!activeRecorder) {
      setPhase("");
      return Promise.resolve(false);
    }
    return activeRecorder.stop().then(function (blob) {
      var form = new FormData();
      form.append("audio", blob, "voice-command.wav");
      form.append("lang", currentLanguage());
      form.append("ui_instance_id", currentUiInstanceId());
      return fetch("/api/workbench/voice-command", { method: "POST", body: form });
    }).then(responsePayload).then(function (payload) {
      if (payload.created) {
        showStatusToast(
          wbcT("topbar.voiceCommandComplete", "Recognized and sent to a new chat"),
          "success",
          3600
        );
        monitorRun(payload.run_id, payload.chat_id, payload.event_cursor);
      } else {
        showStatusToast(
          wbcT("topbar.voiceCommandNoSpeech", "No speech recognized; no chat was created"),
          "warning",
          3600
        );
      }
      return !!payload.created;
    }).catch(function (error) {
      showError(error);
      return false;
    }).finally(function () {
      setPhase("");
    });
  }

  function start() {
    if (phase) return Promise.resolve(false);
    clearSpeechQueue();
    WbcVoice.stop();
    setPhase("starting");
    showStatusToast(
      wbcT("topbar.voiceCommandStartingNotice", "Starting voice input…"),
      "info",
      0
    );
    return WbcVoice.refresh(true).then(function (status) {
      ready = !!(status && status.asr_ready && status.tts_ready);
      notify();
      if (!ready) throw new Error(wbcT("topbar.voiceModelsNotReady", "Configure both local voice models first"));
      return wbcStartVoiceRecorder({
        autoStopOnSilence: true,
        initialSilenceMs: WBC_TOPBAR_INITIAL_SILENCE_MS,
        onSilence: finishRecording,
      });
    }).then(function (controller) {
      recorder = controller;
      setPhase("recording");
      showStatusToast(
        wbcT("topbar.voiceCommandListening", "Listening; start speaking"),
        "info",
        0
      );
      return true;
    }).catch(function (error) {
      recorder = null;
      setPhase("");
      showError(error);
      return false;
    });
  }

  function subscribe(listener) {
    listeners.add(listener);
    listener(snapshot());
    return function () { listeners.delete(listener); };
  }

  WbcVoice.subscribe(function (nextSnapshot) {
    voiceSnapshot = nextSnapshot;
    var status = nextSnapshot && nextSnapshot.status;
    var nextReady = !!(status && status.asr_ready && status.tts_ready);
    if (ready !== nextReady) {
      ready = nextReady;
      notify();
    }
    drainSpeechQueue();
  });

  var WbVoiceCommand = {
    clearSpeechQueue: clearSpeechQueue,
    start: start,
    subscribe: subscribe,
    snapshot: snapshot,
  };

wbcSetVoiceQueueClearer(WbVoiceCommand.clearSpeechQueue);

export { WbVoiceCommand }
