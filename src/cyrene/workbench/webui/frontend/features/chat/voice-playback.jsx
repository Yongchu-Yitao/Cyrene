import { workbenchServices } from "../../shared/runtime/services.jsx"
import { wbcT } from "./core.jsx"

var wbcVoiceQueueClearer = null;

function wbcSetVoiceQueueClearer(clearer) {
  wbcVoiceQueueClearer = typeof clearer === "function" ? clearer : null;
}

  var currentStatus = {
    asr_ready: false,
    tts_ready: false,
    auto_read: false,
    auto_send_after_asr: false,
    auto_stop_on_silence: true,
    tts_provider: "local",
  };
  var statusPromise = null; var listeners = new Set();
  var activeAudio = null; var activeUrl = ""; var activeKey = "";
  var activeRequest = null; var activePlaybackCancel = null; var activeSequenceId = 0;
  var autoStreamState = null; var autoStreamFinalText = new Map(); var autoStreamStatusKey = ""; var autoStreamStatusPromise = null;

  function snapshot() {
    return { status: currentStatus, activeKey: activeKey };
  }

  function notify() {
    var value = snapshot();
    listeners.forEach(function (listener) { listener(value); });
  }

  function setStatus(next) {
    currentStatus = Object.assign({}, currentStatus, next || {});
    notify();
    return currentStatus;
  }

  function refresh(force) {
    if (statusPromise && !force) return statusPromise;
    statusPromise = fetch("/api/voice/status")
      .then(function (response) { return response.ok ? response.json() : Promise.reject(new Error("voice unavailable")); })
      .then(setStatus)
      .catch(function () { return currentStatus; })
      .finally(function () { statusPromise = null; });
    return statusPromise;
  }

  function subscribe(listener) {
    listeners.add(listener);
    listener(snapshot());
    refresh(false);
    return function () { listeners.delete(listener); };
  }

  function releaseAudio() {
    if (activeAudio) {
      activeAudio.pause();
      activeAudio.onended = null;
      activeAudio.onerror = null;
      activeAudio.src = "";
      activeAudio = null;
    }
    if (activeUrl) URL.revokeObjectURL(activeUrl);
    activeUrl = "";
  }

  function stop() {
    activeSequenceId += 1;
    autoStreamState = null;
    if (activeRequest) {
      activeRequest.abort();
      activeRequest = null;
    }
    if (activePlaybackCancel) {
      var cancelPlayback = activePlaybackCancel;
      activePlaybackCancel = null;
      cancelPlayback();
    }
    releaseAudio();
    activeKey = "";
    notify();
  }

  function responseError(response) {
    return response.json().catch(function () { return {}; }).then(function (payload) {
      throw new Error(payload.error || payload.detail || ("HTTP " + response.status));
    });
  }

  function voicePlainText(value) {
    var content = String(value || "")
      // Emoji are visual-only here.  Sending them to ZipVoice makes the model
      // pronounce Unicode names such as "WHITE HEAVY CHECK MARK" or invent a
      // Chinese-sounding syllable before the visible sentence.
      .replace(/(?:[#*0-9]\uFE0F?\u20E3)/g, " ")
      .replace(/[\u{1F000}-\u{1FAFF}\u{1FC00}-\u{1FFFF}\u2600-\u27BF\u00A9\u00AE\u2122]/gu, " ")
      .replace(/[\uFE0E\uFE0F\u200D\u20E3\u{E0020}-\u{E007F}]/gu, "")
      .replace(/<!--[\s\S]*?-->/g, " ")
      .replace(/```[\s\S]*?(?:```|$)/g, " ")
      .replace(/~~~[\s\S]*?(?:~~~|$)/g, " ")
      .replace(/\$\$[\s\S]*?\$\$/g, " ")
      .replace(/\$[^$\n]+\$/g, " ")
      .replace(/^\s*::[a-zA-Z][\w-]*\{[^\n}]*\}\s*$/gm, " ")
      .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/^\s*\[\^[^\]]+\]:.*$/gm, "")
      .replace(/\[\^[^\]]+\]/g, "")
      .replace(/\[([^\]\n]+)\]/g, "$1")
      .replace(/`([^`]+)`/g, "$1")
      .replace(/^\s{0,3}(?:#{1,6}\s*|(?:>\s*)+)/gm, "")
      .replace(/^\s{0,3}(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s*)?/gm, "")
      .replace(/^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$/gm, "")
      .replace(/^\s*(?:[-*_]\s*){3,}$/gm, "")
      .replace(/\s+#{1,6}\s*$/gm, "")
      .replace(/<\/?(?:br|p|div|li|h[1-6])\b[^>]*>/gi, "\n")
      .replace(/<https?:\/\/[^>]+>/g, " ")
      .replace(/<[^>]+>/g, " ")
      .replace(/[*_~]+/g, "")
      .replace(/\\([\\`*{}\[\]()#+.!_>~-])/g, "$1")
      .replace(/https?:\/\/\S+/g, "")
      .replace(/\|/g, "，")
      .replace(/[ \t]+/g, " ")
      .replace(/\s*\n+\s*/g, "\n")
      .trim();
    if (content && typeof document === "object" && document.createElement) {
      var decoder = document.createElement("textarea");
      decoder.innerHTML = content;
      content = decoder.value;
    }
    return content.trim();
  }

  function voiceTextChunks(value) {
    var content = voicePlainText(value);
    if (!content) return [];
    function hasSpeakableText(chunk) {
      // Backend normalization can turn punctuation- or emoji-only display
      // fragments into an empty string.  Never enqueue those fragments: one
      // empty synthesis request would otherwise stop the whole playback queue.
      try {
        return /[\p{L}\p{N}]/u.test(chunk);
      } catch (e) {
        return /[A-Za-z0-9\u3400-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]/.test(chunk);
      }
    }
    var sentences = [];
    var clauses = content.match(/[^。！？!?；;\n]+[。！？!?；;\n]*/g) || [content];
    clauses.forEach(function (clause) {
      var segmented = [];
      if (typeof Intl === "object" && typeof Intl.Segmenter === "function") {
        try {
          var segmenter = new Intl.Segmenter(undefined, { granularity: "sentence" });
          segmented = Array.from(segmenter.segment(clause), function (item) { return item.segment; });
        } catch (e) {}
      }
      sentences = sentences.concat(segmented.length ? segmented : [clause]);
    });
    var chunks = [];
    // Long model sentences are internally divided at natural clause breaks so
    // the first audible result does not wait for an entire paragraph-length
    // sentence. Completed short sentences remain one synthesis request.
    // Cloud TTS is request-rate limited, so batch more text per synthesis call
    // while keeping the low-latency local voice chunks unchanged.
    var maxChars = currentStatus.tts_provider === "minimax" ? 240 : 60;
    sentences.forEach(function (sentence) {
      var remaining = String(sentence || "").trim();
      while (remaining.length > maxChars) {
        var windowText = remaining.slice(0, maxChars + 1);
        var breakAt = Math.max(
          windowText.lastIndexOf("，"), windowText.lastIndexOf(","),
          windowText.lastIndexOf("；"), windowText.lastIndexOf(";"),
          windowText.lastIndexOf("："), windowText.lastIndexOf(":"),
          windowText.lastIndexOf(" ")
        );
        if (breakAt < 24) breakAt = maxChars;
        else breakAt += 1;
        var chunk = remaining.slice(0, breakAt).trim();
        if (chunk && hasSpeakableText(chunk)) chunks.push(chunk);
        remaining = remaining.slice(breakAt).trim();
      }
      if (remaining && hasSpeakableText(remaining)) chunks.push(remaining);
    });
    return chunks;
  }

  function requestSpeechChunk(content, sequenceId, numSteps) {
    if (sequenceId !== activeSequenceId) return Promise.reject(new DOMException("Aborted", "AbortError"));
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    activeRequest = controller;
    return fetch("/api/voice/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: content, num_steps: numSteps === 4 ? 4 : 6 }),
      signal: controller ? controller.signal : undefined,
    }).then(function (response) {
      if (response.status === 204) return null;
      if (!response.ok) return responseError(response);
      return response.blob();
    }).then(function (blob) {
      if (sequenceId !== activeSequenceId) throw new DOMException("Aborted", "AbortError");
      return blob;
    }).finally(function () {
      if (activeRequest === controller) activeRequest = null;
    });
  }

  function playSpeechBlob(blob, sequenceId, onStarted) {
    if (sequenceId !== activeSequenceId) return Promise.resolve(false);
    releaseAudio();
    activeUrl = URL.createObjectURL(blob);
    activeAudio = new Audio(activeUrl);
    return new Promise(function (resolve, reject) {
      var settled = false;
      function finish(played, error) {
        if (settled) return;
        settled = true;
        if (activePlaybackCancel === cancel) activePlaybackCancel = null;
        releaseAudio();
        if (error) reject(error);
        else resolve(played);
      }
      function cancel() { finish(false); }
      activePlaybackCancel = cancel;
      activeAudio.onended = function () { finish(true); };
      activeAudio.onerror = function () { finish(false, new Error("audio playback failed")); };
      activeAudio.play().then(function () {
        if (typeof onStarted === "function") onStarted();
      }).catch(function (error) { finish(false, error); });
    });
  }

  function playSpeechChunks(chunks, index, targetKey, sequenceId, preparedBlob) {
    if (sequenceId !== activeSequenceId || activeKey !== targetKey) return Promise.resolve(false);
    var blobPromise = preparedBlob
      ? Promise.resolve(preparedBlob)
      : requestSpeechChunk(chunks[index], sequenceId, index === 0 ? 4 : 6);
    return blobPromise.then(function (blob) {
      if (sequenceId !== activeSequenceId || activeKey !== targetKey) return false;
      if (!blob) {
        if (index + 1 < chunks.length) {
          return playSpeechChunks(chunks, index + 1, targetKey, sequenceId, null);
        }
        activeKey = "";
        notify();
        return true;
      }
      var nextResultPromise = null;
      return playSpeechBlob(blob, sequenceId, function () {
        if (index + 1 < chunks.length) {
          nextResultPromise = requestSpeechChunk(chunks[index + 1], sequenceId, 6).then(
            function (nextBlob) { return { blob: nextBlob }; },
            function (error) { return { error: error }; }
          );
        }
      }).then(function (played) {
        if (!played || sequenceId !== activeSequenceId || activeKey !== targetKey) return false;
        if (index + 1 >= chunks.length) {
          activeKey = "";
          notify();
          return true;
        }
        var pending = nextResultPromise || requestSpeechChunk(chunks[index + 1], sequenceId, 6).then(
          function (nextBlob) { return { blob: nextBlob }; },
          function (error) { return { error: error }; }
        );
        return pending.then(function (result) {
          if (result.error) throw result.error;
          return playSpeechChunks(chunks, index + 1, targetKey, sequenceId, result.blob);
        });
      });
    });
  }

  function speechResult(content, sequenceId, numSteps) {
    return requestSpeechChunk(content, sequenceId, numSteps).then(
      function (blob) { return { blob: blob }; },
      function (error) { return { error: error }; }
    );
  }

  function streamReadyChunks(value, finished) {
    var source = String(value || "");
    var fences = source.match(/```/g) || [];
    if (fences.length % 2 === 1) source = source.slice(0, source.lastIndexOf("```"));
    var chunks = voiceTextChunks(source);
    if (finished || !chunks.length) return chunks;
    source = source.trim();
    var inlineSource = source.replace(/```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)/g, "");
    var openSquare = (inlineSource.match(/(^|[^\\])\[/g) || []).length;
    var closeSquare = (inlineSource.match(/(^|[^\\])\]/g) || []).length;
    var markdownStable = openSquare <= closeSquare
      && (inlineSource.match(/`/g) || []).length % 2 === 0
      && (inlineSource.split("**").length - 1) % 2 === 0
      && (inlineSource.split("__").length - 1) % 2 === 0
      && (inlineSource.split("~~").length - 1) % 2 === 0
      && !/\]\([^)]*$/.test(inlineSource)
      && !/<[^>]*$/.test(inlineSource)
      && !/\\$/.test(inlineSource);
    var plainSource = voicePlainText(source);
    var endsAtSentence = markdownStable && /[。！？!?；;\n]\s*$/.test(plainSource);
    // Keep the unfinished tail buffered. Once it grows beyond maxChars,
    // voiceTextChunks yields stable clause-sized prefixes and only the final
    // partial chunk remains withheld.
    if (!endsAtSentence) chunks.pop();
    return chunks;
  }

  function prepareAutoStreamNext(state) {
    if (
      !state
      || state !== autoStreamState
      || state.sequenceId !== activeSequenceId
      || !state.playing
      || !state.queue.length
      || state.queue[0].resultPromise
    ) return;
    state.queue[0].resultPromise = speechResult(
      state.queue[0].text,
      state.sequenceId,
      state.queue[0].numSteps
    );
  }

  function completeAutoStream(state) {
    if (state !== autoStreamState || state.sequenceId !== activeSequenceId) return;
    autoStreamState = null;
    activeKey = "";
    notify();
  }

  function pumpAutoStream(state) {
    if (
      !state
      || state !== autoStreamState
      || state.sequenceId !== activeSequenceId
      || activeKey !== state.key
      || state.busy
    ) return;
    if (!state.queue.length) {
      if (state.closed) completeAutoStream(state);
      return;
    }
    var item = state.queue.shift();
    state.busy = true;
    var pending = item.resultPromise || speechResult(item.text, state.sequenceId, item.numSteps);
    pending.then(function (result) {
      if (result.error) throw result.error;
      if (state !== autoStreamState || state.sequenceId !== activeSequenceId) return false;
      if (!result.blob) return true;
      return playSpeechBlob(result.blob, state.sequenceId, function () {
        state.playing = true;
        prepareAutoStreamNext(state);
      });
    }).then(function (played) {
      if (state !== autoStreamState || state.sequenceId !== activeSequenceId) return;
      state.busy = false;
      state.playing = false;
      if (!played) return;
      pumpAutoStream(state);
    }).catch(function (error) {
      if (error && error.name === "AbortError") return;
      stop();
      try {
        workbenchServices.feedback().showToast(
          wbcT("workbenchChat.voicePlaybackFailed", "Could not play speech: {error}", { error: error.message || String(error) }),
          "error"
        );
      } catch (e) {}
    });
  }

  function newAutoStreamState(targetKey) {
    return {
      key: targetKey,
      sequenceId: activeSequenceId,
      queue: [],
      produced: [],
      streamGeneration: 0,
      streamSentenceCount: 0,
      queuedKeys: new Set(),
      busy: false,
      playing: false,
      closed: false,
    };
  }

  function autoStream(text, key, finished, restart) {
    var targetKey = String(key || "auto-stream");
    if (restart || autoStreamStatusKey !== targetKey) {
      autoStreamStatusKey = targetKey;
      autoStreamStatusPromise = refresh(false);
    }
    var statusReady = autoStreamStatusPromise || Promise.resolve(currentStatus);
    return statusReady.then(function () {
      var voiceStatus = currentStatus;
      if (!voiceStatus.auto_read || !voiceStatus.tts_ready) return false;
      if (restart) autoStreamFinalText.delete(targetKey);
      var state = autoStreamState;
      if (!state || state.key !== targetKey) {
        stop();
        state = newAutoStreamState(targetKey);
        autoStreamState = state;
        activeKey = targetKey;
        notify();
      } else if (restart) {
        // A visible reply stream can begin after one or more intermediate
        // messages. Reset only the stream cursor so those queued messages
        // finish speaking instead of being cut off by reply_start.
        state.produced = [];
        state.streamGeneration += 1;
        state.streamSentenceCount = 0;
        state.closed = false;
      }
      var chunks = streamReadyChunks(text, finished === true);
      if (
        finished === true
        && state.closed
        && JSON.stringify(state.produced) !== JSON.stringify(chunks)
      ) {
        // Some providers emit a provisional reply_done before Cyrene publishes
        // the authoritative terminal reply.  Restart the terminal cursor when
        // their content differs so the actual final answer is never skipped.
        state.produced = [];
        state.streamGeneration += 1;
        state.streamSentenceCount = 0;
      }
      for (var i = state.produced.length; i < chunks.length; i += 1) {
        var streamItemKey = "stream:" + state.streamGeneration + ":" + i + ":" + chunks[i];
        if (state.queuedKeys.has(streamItemKey)) continue;
        state.queuedKeys.add(streamItemKey);
        state.queue.push({
          text: chunks[i],
          numSteps: state.streamSentenceCount === 0 ? 4 : 6,
          resultPromise: null,
        });
        state.streamSentenceCount += 1;
      }
      if (chunks.length > state.produced.length) state.produced = chunks.slice();
      if (finished === true) {
        state.closed = true;
        autoStreamFinalText.set(targetKey, voicePlainText(text));
      }
      if (state.playing) prepareAutoStreamNext(state);
      pumpAutoStream(state);
      return true;
    });
  }

  function speak(text, key) {
    var chunks = voiceTextChunks(text);
    var targetKey = String(key || "voice");
    if (!chunks.length || !currentStatus.tts_ready) return Promise.resolve(false);
    if (activeKey === targetKey) {
      stop();
      return Promise.resolve(false);
    }
    stop();
    activeKey = targetKey;
    var sequenceId = activeSequenceId;
    notify();
    return playSpeechChunks(chunks, 0, targetKey, sequenceId, null).catch(function (error) {
      if (error && error.name === "AbortError") return false;
      stop();
      try {
        workbenchServices.feedback().showToast(
          wbcT("workbenchChat.voicePlaybackFailed", "Could not play speech: {error}", { error: error.message || String(error) }),
          "error"
        );
      } catch (e) {}
      return false;
    });
  }

  function queueAutoSpeech(text, key, itemKey, voiceStatus) {
    if (!voiceStatus.auto_read || !voiceStatus.tts_ready) return false;
    var chunks = voiceTextChunks(text);
    if (!chunks.length) return false;
    var targetKey = String(key || "auto-speech");
    var state = autoStreamState;
    if (!state || state.key !== targetKey) {
      stop();
      state = newAutoStreamState(targetKey);
      state.closed = true;
      autoStreamState = state;
      activeKey = targetKey;
      notify();
    }
    var sourceKey = String(itemKey || text || "message");
    chunks.forEach(function (chunk, index) {
      var chunkKey = "message:" + sourceKey + ":" + index;
      if (state.queuedKeys.has(chunkKey)) return;
      state.queuedKeys.add(chunkKey);
      state.queue.push({ text: chunk, numSteps: index === 0 ? 4 : 6, resultPromise: null });
    });
    if (state.playing) prepareAutoStreamNext(state);
    pumpAutoStream(state);
    return true;
  }

  function autoSpeak(text, key, itemKey) {
    return refresh(false).then(function (voiceStatus) {
      return queueAutoSpeech(text, key, itemKey, voiceStatus);
    });
  }

  function autoSpeakFinal(text, key, itemKey) {
    var targetKey = String(key || "auto-speech");
    return refresh(false).then(function (voiceStatus) {
      if (!voiceStatus.auto_read || !voiceStatus.tts_ready) return false;
      var finalText = voicePlainText(text);
      var streamedText = autoStreamFinalText.get(targetKey);
      autoStreamFinalText.delete(targetKey);
      // reply_done already queued this exact terminal snapshot.  The durable
      // saved event is a fallback for providers/reconnects that did not deliver
      // a usable final stream, not a request to read the same answer twice.
      if (finalText && streamedText === finalText) return true;
      return queueAutoSpeech(text, targetKey, itemKey, voiceStatus);
    });
  }

  window.addEventListener("cyrene:voice-status-changed", function (event) {
    var detail = event && event.detail;
    if (detail && typeof detail === "object") setStatus(detail);
    else refresh(true);
  });
  window.addEventListener("cyrene:voice-stop", function () {
    if (wbcVoiceQueueClearer) wbcVoiceQueueClearer();
    stop();
  });

  var WbcVoice = {
    autoSpeak: autoSpeak,
    autoSpeakFinal: autoSpeakFinal,
    autoStream: autoStream,
    refresh: refresh,
    speak: speak,
    plainText: voicePlainText,
    splitText: voiceTextChunks,
    getSnapshot: snapshot,
    stop: stop,
    subscribe: subscribe,
  };

export { WbcVoice, wbcSetVoiceQueueClearer }
