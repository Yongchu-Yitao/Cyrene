import { workbenchServices } from "../../shared/runtime/services.jsx"
import { wbcT } from "./core.jsx"

function wbcResampleVoice(samples, sourceRate, targetRate) {
  if (sourceRate === targetRate) return samples;
  var targetLength = Math.max(1, Math.round(samples.length * targetRate / sourceRate));
  var output = new Float32Array(targetLength);
  var scale = (samples.length - 1) / Math.max(1, targetLength - 1);
  for (var i = 0; i < targetLength; i += 1) {
    var position = i * scale;
    var left = Math.floor(position);
    var right = Math.min(samples.length - 1, left + 1);
    var weight = position - left;
    output[i] = samples[left] * (1 - weight) + samples[right] * weight;
  }
  return output;
}

function wbcVoiceWavBlob(chunks, sourceRate) {
  var length = chunks.reduce(function (total, chunk) { return total + chunk.length; }, 0);
  var merged = new Float32Array(length);
  var offset = 0;
  chunks.forEach(function (chunk) { merged.set(chunk, offset); offset += chunk.length; });
  var samples = wbcResampleVoice(merged, sourceRate, 16000);
  var buffer = new ArrayBuffer(44 + samples.length * 2);
  var view = new DataView(buffer);
  function writeString(at, value) {
    for (var i = 0; i < value.length; i += 1) view.setUint8(at + i, value.charCodeAt(i));
  }
  writeString(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, 16000, true);
  view.setUint32(28, 32000, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (var sampleIndex = 0; sampleIndex < samples.length; sampleIndex += 1) {
    var value = Math.max(-1, Math.min(1, samples[sampleIndex]));
    view.setInt16(44 + sampleIndex * 2, value < 0 ? value * 32768 : value * 32767, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

// Shared by every composer that supports local voice input. Keep FireRedASR's
// silence-token handling and response parsing in one place so conversation
// inputs cannot drift into subtly different behavior.
function wbcCleanVoiceTranscript(value) {
  var content = String(value || "").trim();
  if (!content) return "";
  content = content.replace(
    /(?:\*{1,3}|_{1,3})?\s*<\s*sil(?:ence)?\s*>\s*(?:\*{1,3}|_{1,3})?\s*[。.!！?？,，、;；:：…]*/gi,
    " "
  );
  content = content.replace(/\s+/g, " ").trim();
  if (/^[*_~。.!！?？,，、;；:：…\s]+$/.test(content)) return "";
  return content;
}

function wbcIsVoiceSilenceTranscript(value) {
  var content = String(value || "");
  return /<\s*sil(?:ence)?\s*>/i.test(content) && !wbcCleanVoiceTranscript(content);
}

function wbcTranscribeVoiceBlob(blob) {
  var form = new FormData();
  form.append("audio", blob, "voice-input.wav");
  return fetch("/api/voice/asr", { method: "POST", body: form })
    .then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (payload) {
        if (!response.ok) throw new Error(payload.error || payload.detail || ("HTTP " + response.status));
        return payload;
      });
    })
    .then(function (payload) {
      var rawTranscript = String(payload.text || "").trim();
      var silenceOnly = payload.silence_only === true || wbcIsVoiceSilenceTranscript(rawTranscript);
      var transcript = wbcCleanVoiceTranscript(rawTranscript);
      // FireRedASR can emit a literal <sil> token for a silent recording.
      // Treat it as an intentional no-op: never touch a draft or auto-send.
      if (silenceOnly) return false;
      if (!transcript) throw new Error(wbcT("workbenchChat.noRecognizedSpeech", "No speech was recognized"));
      return transcript;
    });
}

// Composer voice input uses the same persistent, replacing status-toast
// pattern as the top-bar voice command. Each mounted composer owns its toast
// id so a phase change updates one notice instead of stacking several.
function wbcCreateComposerVoiceFeedback() {
  var statusToastId = 0;

  function dismiss() {
    if (!statusToastId) return;
    try { workbenchServices.feedback().dismissToast(statusToastId); } catch (e) {}
    statusToastId = 0;
  }

  function show(message, type, duration) {
    dismiss();
    try {
      statusToastId = workbenchServices.feedback().showToast(message, type || "info", {
        duration: duration == null ? 0 : duration,
      });
    } catch (e) {
      statusToastId = 0;
    }
  }

  return {
    starting: function () {
      show(wbcT("topbar.voiceCommandStartingNotice", "Starting voice input…"), "info", 0);
    },
    listening: function () {
      show(wbcT("topbar.voiceCommandListening", "Listening; start speaking"), "info", 0);
    },
    transcribing: function () {
      show(wbcT("topbar.voiceCommandRecognizingNotice", "Recognizing speech…"), "info", 0);
    },
    complete: function () {
      show(wbcT("workbenchChat.voiceInputComplete", "Voice recognition complete"), "success", 3600);
    },
    noSpeech: function () {
      show(wbcT("workbenchChat.noRecognizedSpeech", "No speech was recognized"), "warning", 3600);
    },
    error: function (error) {
      var message = error && error.message ? error.message : String(error || "");
      show(
        wbcT("workbenchChat.voiceInputFailed", "Could not recognize speech: {error}", { error: message }),
        "error",
        6000
      );
    },
    dismiss: dismiss,
  };
}

var WBC_VOICE_SILENCE_MS = 1600;
var WBC_VOICE_MIN_SPEECH_MS = 240;
var WBC_VOICE_SPEECH_RMS = 0.012;
var WBC_VOICE_SPEECH_PEAK = 0.08;

function wbcCreateVoiceSilenceDetector(onSilence, options) {
  var detectorOptions = options || {};
  var initialSilenceMs = Math.max(0, Number(detectorOptions.initialSilenceMs) || 0);
  var speechMs = 0;
  var silenceMs = 0;
  var elapsedBeforeSpeechMs = 0;
  var speechStarted = false;
  var triggered = false;
  return function (samples, sampleRate) {
    if (triggered || !samples.length || !sampleRate) return;
    var sumSquares = 0;
    var peak = 0;
    for (var i = 0; i < samples.length; i += 1) {
      var amplitude = Math.abs(samples[i]);
      sumSquares += amplitude * amplitude;
      if (amplitude > peak) peak = amplitude;
    }
    var rms = Math.sqrt(sumSquares / samples.length);
    var durationMs = samples.length * 1000 / sampleRate;
    if (!speechStarted) elapsedBeforeSpeechMs += durationMs;
    var voiced = rms >= WBC_VOICE_SPEECH_RMS || peak >= WBC_VOICE_SPEECH_PEAK;
    if (voiced) {
      speechMs += durationMs;
      silenceMs = 0;
      if (speechMs >= WBC_VOICE_MIN_SPEECH_MS) speechStarted = true;
    } else if (speechStarted) {
      silenceMs += durationMs;
    } else {
      speechMs = Math.max(0, speechMs - durationMs);
    }
    if (
      (speechStarted && silenceMs >= WBC_VOICE_SILENCE_MS)
      || (!speechStarted && initialSilenceMs > 0 && elapsedBeforeSpeechMs >= initialSilenceMs)
    ) {
      triggered = true;
      onSilence();
    }
  };
}

function wbcStartVoiceRecorder(options) {
  var recorderOptions = options || {};
  if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
    return Promise.reject(new Error(wbcT("workbenchChat.microphoneUnavailable", "Microphone access is unavailable")));
  }
  return navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  }).then(function (stream) {
    var AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) {
      stream.getTracks().forEach(function (track) { track.stop(); });
      throw new Error(wbcT("workbenchChat.microphoneUnavailable", "Microphone access is unavailable"));
    }
    var context = new AudioContextClass();
    var source = context.createMediaStreamSource(stream);
    var processor = context.createScriptProcessor(4096, 1, 1);
    var silent = context.createGain();
    var chunks = [];
    var stopped = false;
    var stopPromise = null;
    var controller = null;
    var detectSilence = recorderOptions.autoStopOnSilence
      ? wbcCreateVoiceSilenceDetector(function () {
          setTimeout(function () {
            if (!stopped && controller && typeof recorderOptions.onSilence === "function") {
              recorderOptions.onSilence(controller);
            }
          }, 0);
        }, { initialSilenceMs: recorderOptions.initialSilenceMs })
      : null;
    silent.gain.value = 0;
    processor.onaudioprocess = function (event) {
      var chunk = new Float32Array(event.inputBuffer.getChannelData(0));
      chunks.push(chunk);
      if (detectSilence) detectSilence(chunk, context.sampleRate);
    };
    source.connect(processor);
    processor.connect(silent);
    silent.connect(context.destination);
    controller = {
      stop: function () {
        if (stopPromise) return stopPromise;
        stopped = true;
        processor.onaudioprocess = null;
        try { source.disconnect(); processor.disconnect(); silent.disconnect(); } catch (e) {}
        stream.getTracks().forEach(function (track) { track.stop(); });
        var sourceRate = context.sampleRate;
        stopPromise = Promise.resolve(context.close()).catch(function () {}).then(function () {
          if (!chunks.length) throw new Error(wbcT("workbenchChat.noRecordedAudio", "No audio was recorded"));
          return wbcVoiceWavBlob(chunks, sourceRate);
        });
        return stopPromise;
      },
    };
    return controller;
  });
}

var WBC_TOPBAR_INITIAL_SILENCE_MS = 5000;

export {
  WBC_TOPBAR_INITIAL_SILENCE_MS,
  WBC_VOICE_MIN_SPEECH_MS,
  WBC_VOICE_SILENCE_MS,
  WBC_VOICE_SPEECH_PEAK,
  WBC_VOICE_SPEECH_RMS,
  wbcCleanVoiceTranscript,
  wbcCreateComposerVoiceFeedback,
  wbcCreateVoiceSilenceDetector,
  wbcIsVoiceSilenceTranscript,
  wbcResampleVoice,
  wbcStartVoiceRecorder,
  wbcTranscribeVoiceBlob,
  wbcVoiceWavBlob,
}
