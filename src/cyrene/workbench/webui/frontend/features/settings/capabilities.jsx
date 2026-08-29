import {
  SectionTitle,
  SectionBlock,
  FieldRow,
  Toggle,
} from "./shared.jsx"

// ── Capabilities Panel ──
function CapabilitiesPanel(p) {
  var {
    t,
    voiceStatus, voiceReferenceText, setVoiceReferenceText,
    voiceReferenceFile, setVoiceReferenceFile, voiceReferencePhase, voiceReferenceElapsed,
    startVoiceReferenceRecording, finishVoiceReferenceRecording,
    voiceBusy, voiceNotice,
    saveVoiceBooleanSetting, saveVoiceMode, saveVoicePreset, saveVoiceTtsModel, saveVoiceProfile, deleteVoiceProfile,
  } = p;
  var localTtsActive = voiceStatus.tts_provider !== "minimax";
  var customVoiceSelected = localTtsActive && voiceStatus.voice_mode === "custom";
  var voiceReferenceActive = voiceReferencePhase === "starting"
    || voiceReferencePhase === "recording"
    || voiceReferencePhase === "transcribing";

  function voicePresetLabel(preset) {
    var number = Number(preset && preset.ordinal) || 1;
    if (preset && preset.group === "zipvoice") return t("settings.voiceZipVoiceDefault");
    if (preset && preset.group === "zh_female") return t("settings.voiceChineseFemale", { number: number });
    if (preset && preset.group === "zh_male") return t("settings.voiceChineseMale", { number: number });
    return t("settings.voiceEnglishFemale", { number: number });
  }

  function voicePresetOptions() {
    var presets = Array.isArray(voiceStatus.voice_presets) ? voiceStatus.voice_presets : [];
    return [
      ["zipvoice", "settings.voiceZipVoiceGroup"],
      ["zh_male", "settings.voiceChineseMaleGroup"],
      ["zh_female", "settings.voiceChineseFemaleGroup"],
      ["en_female", "settings.voiceEnglishFemaleGroup"],
    ].map(function (group) {
      var options = presets.filter(function (preset) { return preset.group === group[0]; });
      if (!options.length) return null;
      return React.createElement("optgroup", { key: group[0], label: t(group[1]) }, options.map(function (preset) {
        return React.createElement("option", { key: preset.id, value: preset.id }, voicePresetLabel(preset));
      }));
    }).filter(Boolean);
  }

  function voiceTtsModelLabel(model) {
    var id = String(model && model.id || "");
    if (id === "auto") return t("settings.voiceTtsModelAuto");
    if (id === "speech-2.8-turbo") return "MiniMax Speech 2.8 Turbo";
    if (id === "speech-2.8-hd") return "MiniMax Speech 2.8 HD";
    if (id === "kokoro-zh-en") return "Kokoro";
    if (id === "zipvoice-zh-en") return "ZipVoice";
    return id;
  }

  function voiceTtsModelOptions() {
    var models = Array.isArray(voiceStatus.tts_models) ? voiceStatus.tts_models : [];
    var selection = String(voiceStatus.tts_model_selection || "auto");
    return models.filter(function (model) {
      return model && (model.available !== false || model.id === selection || model.id === "auto");
    }).map(function (model) {
      return React.createElement("option", {
        key: model.id,
        value: model.id,
        disabled: model.available === false,
      }, voiceTtsModelLabel(model));
    });
  }

  return React.createElement("div", { className: "settings-panel" },
    SectionTitle(t("settings.voiceTab")),

    React.cloneElement(SectionBlock(t("settings.voiceCapability"), t("settings.voiceCapabilityHint"),
      React.createElement("div", { className: "wb-voice-settings" },
        FieldRow(
          t("settings.voiceAutoSend"),
          t("settings.voiceAutoSendHint"),
          Toggle(
            voiceStatus.auto_send_after_asr === true,
            function () { saveVoiceBooleanSetting("auto_send_after_asr", voiceStatus.auto_send_after_asr !== true); },
            voiceBusy === "settings",
            t("settings.voiceAutoSend"),
          ),
          "voice-auto-send",
        ),
        FieldRow(
          t("settings.voiceAutoStop"),
          t("settings.voiceAutoStopHint"),
          Toggle(
            voiceStatus.auto_stop_on_silence !== false,
            function () { saveVoiceBooleanSetting("auto_stop_on_silence", voiceStatus.auto_stop_on_silence === false); },
            voiceBusy === "settings",
            t("settings.voiceAutoStop"),
          ),
          "voice-auto-stop",
        ),
        FieldRow(
          t("settings.voiceAutoRead"),
          voiceStatus.tts_ready
            ? t("settings.voiceAutoReadHint")
            : t("settings.voiceAutoReadUnavailable"),
          Toggle(
            voiceStatus.auto_read === true,
            function () { saveVoiceBooleanSetting("auto_read", voiceStatus.auto_read !== true); },
            !voiceStatus.tts_ready || voiceBusy === "settings",
            t("settings.voiceAutoRead"),
          ),
          "voice-auto-read",
        ),
        FieldRow(
          t("settings.voiceTtsModel"),
          voiceStatus.tts_provider === "minimax"
            ? t("settings.voiceTtsModelMiniMaxHint", { model: voiceTtsModelLabel({ id: voiceStatus.tts_model }) })
            : t("settings.voiceTtsModelLocalHint"),
          React.createElement("select", {
            className: "wb-select wb-voice-model-select",
            value: voiceStatus.tts_model_selection || "auto",
            disabled: voiceBusy === "settings",
            "aria-label": t("settings.voiceTtsModel"),
            onChange: function (event) { saveVoiceTtsModel(event.target.value); },
          }, voiceTtsModelOptions()),
          "voice-tts-model",
        ),
        localTtsActive ? React.createElement("div", { className: "wb-voice-profile" },
          React.createElement("div", { className: "wb-voice-profile-copy" },
            React.createElement("b", null, t("settings.voiceProfile")),
            React.createElement("small", null, t("settings.voiceProfileHint")),
          ),
          React.createElement("div", {
            className: "wb-seg wb-voice-mode-switch",
            role: "group",
            "aria-label": t("settings.voiceProfile"),
          },
            React.createElement("button", {
              type: "button",
              className: "wb-seg-btn" + (!customVoiceSelected ? " active" : ""),
              "aria-pressed": customVoiceSelected ? "false" : "true",
              disabled: !!voiceBusy || voiceReferenceActive,
              onClick: function () { saveVoiceMode("preset"); },
            }, t("settings.voicePresetMode")),
            React.createElement("button", {
              type: "button",
              className: "wb-seg-btn" + (customVoiceSelected ? " active" : ""),
              "aria-pressed": customVoiceSelected ? "true" : "false",
              disabled: !!voiceBusy || voiceReferenceActive || !voiceStatus.custom_tts_model_ready,
              title: voiceStatus.custom_tts_model_ready ? "" : t("settings.voiceCustomRequiresZipVoice"),
              onClick: function () { saveVoiceMode("custom"); },
            }, t("settings.voiceCustomMode")),
          ),
          customVoiceSelected
            ? React.createElement("div", { className: "wb-voice-custom-fields" },
                React.createElement("div", { className: "wb-voice-profile-copy" },
                  React.createElement("b", null, t("settings.voiceCustomTitle")),
                  React.createElement("small", null, t("settings.voiceCustomHint")),
                ),
                React.createElement("div", { className: "wb-voice-reference-recorder" },
                  React.createElement("button", {
                    type: "button",
                    className: "wb-voice-record-btn" + (voiceReferencePhase === "recording" ? " recording" : ""),
                    disabled: !voiceStatus.asr_ready || voiceReferencePhase === "starting" || voiceReferencePhase === "transcribing" || !!voiceBusy,
                    "aria-label": voiceReferencePhase === "recording"
                      ? t("settings.voiceReferenceStop")
                      : t("settings.voiceReferenceRecord"),
                    onClick: function () {
                      if (voiceReferencePhase === "recording") finishVoiceReferenceRecording();
                      else startVoiceReferenceRecording();
                    },
                  },
                    React.createElement("span", { className: "wb-voice-record-dot", "aria-hidden": "true" }),
                    React.createElement("span", null,
                      voiceReferencePhase === "starting"
                        ? t("settings.voiceReferenceStarting")
                        : voiceReferencePhase === "recording"
                          ? t("settings.voiceReferenceStop")
                          : voiceReferencePhase === "transcribing"
                            ? t("settings.voiceReferenceRecognizing")
                            : voiceReferenceFile
                              ? t("settings.voiceReferenceRecordAgain")
                              : t("settings.voiceReferenceRecord")
                    ),
                  ),
                  React.createElement("small", null,
                    voiceStatus.asr_ready
                      ? voiceReferencePhase === "recording"
                        ? t("settings.voiceReferenceRecordingStatus", { seconds: voiceReferenceElapsed.toFixed(1) })
                        : t("settings.voiceReferenceRecordingHint")
                      : t("settings.voiceReferenceAsrUnavailable")
                  ),
                ),
                voiceReferenceFile && voiceReferenceText && React.createElement("div", {
                  className: "wb-voice-reference-transcript",
                  role: "status",
                },
                  React.createElement("b", null, t("settings.voiceReferenceTranscriptLabel")),
                  React.createElement("p", null, voiceReferenceText),
                ),
                React.createElement("div", { className: "wb-save-actions" },
                  React.createElement("button", {
                    type: "button",
                    className: "wb-btn primary",
                    disabled: !voiceReferenceFile || !voiceReferenceText.trim() || !!voiceBusy,
                    onClick: saveVoiceProfile,
                  }, voiceBusy === "profile" ? t("settings.saving") : t("settings.voiceSaveProfile")),
                  voiceStatus.voice_profile_ready && React.createElement("button", {
                    type: "button",
                    className: "wb-btn danger",
                    disabled: !!voiceBusy,
                    onClick: deleteVoiceProfile,
                  }, t("settings.delete")),
                ),
              )
            : React.createElement("div", { className: "wb-voice-preset-row" },
                React.createElement("div", { className: "wb-voice-profile-copy" },
                  React.createElement("b", null, t("settings.voicePresetName")),
                  React.createElement("small", null, t("settings.voicePresetHint")),
                ),
                voiceStatus.voice_preset_ready && Array.isArray(voiceStatus.voice_presets) && voiceStatus.voice_presets.length
                  ? React.createElement("select", {
                      className: "wb-select wb-voice-preset-select",
                      value: voiceStatus.voice_preset,
                      disabled: !!voiceBusy,
                      "aria-label": t("settings.voicePresetSelect"),
                      onChange: function (event) { saveVoicePreset(event.target.value); },
                    }, voicePresetOptions())
                  : React.createElement("span", { className: "" }, t("settings.localModelNotDownloaded")),
              ),
          voiceNotice && React.createElement("span", { className: "wb-hint saved" }, voiceNotice),
        ) : React.createElement("div", { className: "wb-voice-cloud-profile" },
          React.createElement("div", { className: "wb-voice-profile-copy" },
            React.createElement("b", null, t("settings.voiceMiniMaxVoice")),
            React.createElement("small", null, t("settings.voiceMiniMaxVoiceHint")),
          ),
          React.createElement("span", { className: voiceStatus.tts_ready ? "ready" : "" },
            t("settings.voiceMiniMaxDefaultVoice") + " · " + String(voiceStatus.minimax_voice_id || "male-qn-qingse")
          ),
          voiceNotice && React.createElement("span", { className: "wb-hint saved" }, voiceNotice),
        ),
        React.createElement("div", { className: "wb-voice-readiness" },
          React.createElement("span", { className: voiceStatus.asr_ready ? "ready" : "" },
            t("settings.voiceAsrStatus") + " · " + t(voiceStatus.asr_ready ? "settings.localModelReady" : "settings.localModelNotDownloaded")
          ),
          React.createElement("span", { className: voiceStatus.tts_ready ? "ready" : "" },
            t("settings.voiceTtsStatus") + " · " + t(voiceStatus.tts_ready
              ? voiceStatus.tts_provider === "minimax" ? "settings.voiceCloudReady" : "settings.localModelReady"
              : "settings.voiceTtsNeedsProfile")
          ),
        ),
      ),
    ), { id: "setting-voice" }),

  );
}

export { CapabilitiesPanel };
