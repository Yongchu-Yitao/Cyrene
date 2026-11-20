import {VoiceMethods, VoiceDownload} from "./voice-controls.jsx"
import {ChoiceCards} from "./choice-cards.jsx"
import {
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
    voiceBusy, voiceNotice, saveVoiceSetting, refreshVoiceStatus,
    saveVoiceMode, saveVoicePreset, saveVoiceTtsModel, saveVoiceProfile, deleteVoiceProfile,
  } = p;
  const [choosingCloud, setChoosingCloud] = React.useState(false);
  const localTtsActive = (voiceStatus.tts_provider || "local") === "local";
  const cloudModels = (voiceStatus.tts_models || []).filter(m => m.provider !== "local" && m.available);
  const cloudOutput = !localTtsActive || (choosingCloud && cloudModels.length > 0);
  const selectedModel = voiceStatus.tts_model;
  const cloudInput = voiceStatus.asr_provider === "deepseek";
  const zip = selectedModel === "zipvoice-zh-en";
  function chooseOutput(remote) {
    if (!remote) {
      setChoosingCloud(false);
      if (!localTtsActive) saveVoiceTtsModel("kokoro-zh-en");
    } else if (cloudModels.length === 1) saveVoiceTtsModel(cloudModels[0].id);
    else setChoosingCloud(true);
  }

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
    if (id === "deepseek") return "DeepSeek";
    if (id === "speech-2.8-turbo") return "MiniMax Speech 2.8 Turbo";
    if (id === "speech-2.8-hd") return "MiniMax Speech 2.8 HD";
    if (id === "kokoro-zh-en") return "Kokoro";
    if (id === "zipvoice-zh-en") return "ZipVoice";
    return id;
  }

  const modelOptions = cloudOutput ? cloudModels : (voiceStatus.tts_models || []).filter(m => m.provider === "local");
  const selected = modelOptions.some(m => m.id === selectedModel);
  return React.createElement("div", {className:"settings-panel voice-page", id:"setting-voice"},
    SectionBlock(t("settings.voice.asr"), voiceStatus.asr_ready ? t(cloudInput ? "settings.voiceCloudReady" : "settings.localModelReady") : null,
      <VoiceMethods kind="asr" t={t} cloud={cloudInput} available={voiceStatus.deepseek_ready}
        busy={!!voiceBusy} onChange={remote => saveVoiceSetting("asr_provider", remote ? "deepseek" : "local")}/>,
      !cloudInput && !voiceStatus.local_asr_ready &&
        <VoiceDownload id="fireredasr2-aed-int8" name="FireRedASR2" size={904} t={t} onReady={refreshVoiceStatus}/>
    ),
    SectionBlock(t("settings.voice.dictation"), null,
        FieldRow(
          t("settings.voiceAutoSend"),
          t("settings.voiceAutoSendHint"),
          Toggle(
            voiceStatus.auto_send_after_asr === true,
            function () { saveVoiceSetting("auto_send_after_asr", voiceStatus.auto_send_after_asr !== true); },
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
            function () { saveVoiceSetting("auto_stop_on_silence", voiceStatus.auto_stop_on_silence === false); },
            voiceBusy === "settings",
            t("settings.voiceAutoStop"),
          ),
          "voice-auto-stop",
        ),
    ),
    SectionBlock(t("settings.voice.tts"), selected && voiceStatus.tts_ready ? t(cloudOutput ? "settings.voiceCloudReady" : "settings.localModelReady") : null,
      <VoiceMethods kind="tts" t={t} cloud={cloudOutput} available={cloudModels.length > 0}
        busy={!!voiceBusy || voiceReferenceActive} onChange={chooseOutput}/>,
      FieldRow(t("settings.voiceTtsModel"), cloudOutput ? t("settings.voice.ttsCloudDetail") : t("settings.voiceTtsModelLocalHint"),
        <select className="wb-select" value={selected ? selectedModel : ""} aria-label={t("settings.voiceTtsModel")}
          disabled={!!voiceBusy || voiceReferenceActive} onChange={event => {setChoosingCloud(false);saveVoiceTtsModel(event.target.value)}}>
          {!selected && <option disabled value="">{t("settings.voice.choose")}</option>}
          {modelOptions.map(m => <option key={m.id} value={m.id}>{voiceTtsModelLabel(m)}</option>)}
        </select>),
      selected && !cloudOutput && !voiceStatus.tts_model_ready &&
        <VoiceDownload key={selectedModel} id={selectedModel} name={voiceTtsModelLabel({id:selectedModel})}
          size={zip ? 532 : 365} t={t} onReady={refreshVoiceStatus}/>,
        FieldRow(
          t("settings.voiceAutoRead"),
          voiceStatus.tts_ready
            ? t("settings.voiceAutoReadHint")
            : t("settings.voiceAutoReadUnavailable"),
          Toggle(
            voiceStatus.auto_read === true,
            function () { saveVoiceSetting("auto_read", voiceStatus.auto_read !== true); },
            !selected || !voiceStatus.tts_ready || voiceBusy === "settings",
            t("settings.voiceAutoRead"),
          ),
          "voice-auto-read",
        ),
        selected && !cloudOutput && voiceStatus.tts_model_ready && React.createElement("div", { className: "wb-voice-profile" },
          zip && React.createElement("div", { className: "wb-voice-profile-copy" },
            React.createElement("b", null, t("settings.voiceProfile")),
            React.createElement("small", null, t("settings.voiceProfileHint")),
          ),
          zip && <ChoiceCards compact descriptionId={voiceStatus.custom_tts_model_ready?undefined:"voice-mode-unavailable"} label={t("settings.voiceProfile")} value={customVoiceSelected?"custom":"preset"} onChange={saveVoiceMode} options={[
            {id:"preset",title:t("settings.voicePresetMode"),disabled:!!voiceBusy||voiceReferenceActive},
            {id:"custom",title:t("settings.voiceCustomMode"),disabled:!!voiceBusy||voiceReferenceActive||!voiceStatus.custom_tts_model_ready}
          ]}/>,
          zip && !voiceStatus.custom_tts_model_ready && <small className="voice-mode-note" id="voice-mode-unavailable">{t("settings.voiceCustomRequiresZipVoice")}</small>,
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
                    disabled: !voiceStatus.local_asr_ready || voiceReferencePhase === "starting" || voiceReferencePhase === "transcribing" || !!voiceBusy,
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
                    voiceStatus.local_asr_ready
                      ? voiceReferencePhase === "recording"
                        ? t("settings.voiceReferenceRecordingStatus", { seconds: voiceReferenceElapsed.toFixed(1) })
                        : t("settings.voiceReferenceRecordingHint")
                      : t("settings.voiceReferenceAsrUnavailable")
                  ),
                ),
                cloudInput && !voiceStatus.local_asr_ready &&
                  <VoiceDownload id="fireredasr2-aed-int8" name="FireRedASR2" size={904} t={t} onReady={refreshVoiceStatus}/>,
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
        ),
      selected && cloudOutput && FieldRow(t("settings.voiceProfile"), "",
        <select className="wb-select" aria-label={t("settings.voiceProfile")} disabled={!!voiceBusy}
          value={selectedModel === "deepseek" ? voiceStatus.deepseek_voice_id : voiceStatus.minimax_voice_id}
          onChange={event => saveVoiceSetting(selectedModel === "deepseek" ? "deepseek_voice_id" : "minimax_voice_id", event.target.value)}>
          {(selectedModel === "deepseek" ? ["stella","tide"] :
            [...new Set([voiceStatus.minimax_voice_id,"male-qn-qingse","female-shaonv","presenter_female"].filter(Boolean))])
            .map(id => <option key={id} value={id}>{["stella","tide","male-qn-qingse","female-shaonv","presenter_female"].includes(id) ? t("settings.voice."+id) : id}</option>)}
        </select>)
    )
  );
}

export default CapabilitiesPanel;
