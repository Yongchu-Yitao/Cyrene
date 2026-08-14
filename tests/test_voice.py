import io
from pathlib import Path

import numpy as np
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from cyrene.voice import engine
from route import voice as voice_routes


def test_voice_status_uses_bundled_preset_without_custom_profile(monkeypatch):
    monkeypatch.setattr(engine, "_runtime_available", lambda: True)
    monkeypatch.setattr(engine, "_settings", lambda: {
        "auto_read": True,
        "auto_send_after_asr": False,
        "auto_stop_on_silence": True,
        "reference_text": "",
        "voice_mode": "preset",
        "voice_preset": engine.DEFAULT_PRESET_ID,
    })
    monkeypatch.setattr(engine, "_profile_ready", lambda _settings=None: False)
    monkeypatch.setattr(engine.local_models, "is_ready", lambda model_id: model_id == engine.PRESET_TTS_MODEL_ID)

    payload = engine.status()

    assert payload["asr_ready"] is False
    assert payload["tts_ready"] is True
    assert payload["tts_model"] == engine.PRESET_TTS_MODEL_ID
    assert payload["voice_presets"][0]["id"] == "kokoro-af_maple"
    assert len(payload["voice_presets"]) == 103
    assert payload["voice_mode"] == "preset"
    assert payload["voice_profile_ready"] is False
    assert payload["auto_read"] is True
    assert payload["auto_send_after_asr"] is False
    assert payload["auto_stop_on_silence"] is True

    with __import__("pytest").raises(RuntimeError, match="ZipVoice model is not downloaded"):
        engine.update_settings(voice_mode=engine.VOICE_MODE_CUSTOM)


def test_tts_text_normalization_removes_display_only_tokens():
    normalized = engine.normalize_tts_text(
        "## 结果 ✅\n「Cyrene」请阅读[文档](https://example.com)，版本（测试）……\n"
        "![不可见的流程图](diagram.png)\n"
        "| 字段 | 值 |\n| --- | --- |\n::code-comment{file=demo.py}\n🎉 🔧 1️⃣\n"
        "**完成**"
    )

    assert normalized == "结果。Cyrene，请阅读文档，版本，测试。字段，值。完成"
    assert not any(token in normalized for token in ("不可见的流程图", "「", "」", "（", "）", "[", "]", "|", "**", "::", "https://", "✅", "🎉", "🔧", "1️⃣"))


def test_tts_text_normalization_rejects_display_only_fragments():
    assert engine.normalize_tts_text("❓") == ""
    assert engine.normalize_tts_text("：——【】") == ""
    assert engine.normalize_tts_text("✅ https://example.com") == ""


def test_asr_silence_placeholder_is_removed_without_discarding_speech():
    assert engine._clean_asr_text("**< sil >。") == ("", True)
    assert engine._clean_asr_text("<SILENCE>!") == ("", True)
    assert engine._clean_asr_text("你好，<sil> 世界") == ("你好, 世界", True)
    assert engine._clean_asr_text("正常识别结果。") == ("正常识别结果。", False)


def test_zipvoice_generation_uses_balanced_quality_and_slower_natural_pacing():
    assert engine.DEFAULT_PRESET_TEXT == "那还是三十六年前，一九八七年。我呢考上了武汉大学的计算机系。"
    assert engine.TTS_FIRST_SENTENCE_NUM_STEPS == 4
    assert engine.TTS_NUM_STEPS == 6
    assert engine.TTS_ALLOWED_NUM_STEPS == {4, 6}
    assert engine.TTS_SPEED == 0.92
    assert engine.TTS_SILENCE_SCALE == 0.24


def test_custom_voice_profile_save_status_synthesis_and_delete(monkeypatch, tmp_path):
    voice_root = tmp_path / "voice"
    reference_audio = voice_root / "reference.wav"
    settings = {
        "auto_read": False,
        "auto_send_after_asr": False,
        "auto_stop_on_silence": True,
        "reference_text": "",
        "voice_mode": engine.VOICE_MODE_PRESET,
        "voice_preset": engine.DEFAULT_PRESET_ID,
    }

    monkeypatch.setattr(engine, "VOICE_ROOT", voice_root)
    monkeypatch.setattr(engine, "REFERENCE_AUDIO", reference_audio)
    monkeypatch.setattr(engine.config_store, "get_setting", lambda key, default=None: dict(settings))
    monkeypatch.setattr(engine.config_store, "set_setting", lambda key, value: settings.update(value))
    monkeypatch.setattr(engine, "_runtime_available", lambda: True)
    monkeypatch.setattr(engine.local_models, "is_ready", lambda _model_id: True)
    monkeypatch.setattr(engine, "_preset_ready", lambda: True)

    source = io.BytesIO()
    sf_samples = np.sin(np.linspace(0, np.pi * 440, 24_000, dtype=np.float32)) * 0.1
    import soundfile as sf
    sf.write(source, sf_samples, 24_000, format="WAV", subtype="PCM_16")

    saved = engine.save_voice_profile(source.getvalue(), "这是自动识别的参考文字。")
    assert saved["voice_mode"] == engine.VOICE_MODE_CUSTOM
    assert saved["voice_profile_ready"] is True
    assert saved["tts_ready"] is True
    assert reference_audio.exists()

    class FakeAudio:
        samples = np.ones(240, dtype=np.float32) * 0.05
        sample_rate = 24_000

    class FakeTts:
        def generate(self, content, generation):
            assert content == "你好"
            assert generation.reference_text == "这是自动识别的参考文字。"
            assert len(generation.reference_audio) == 24_000
            assert generation.reference_sample_rate == 24_000
            return FakeAudio()

    class FakeGenerationConfig:
        def __init__(self):
            self.extra = {}

    monkeypatch.setattr(engine, "_load_zipvoice_tts", lambda: FakeTts())
    monkeypatch.setitem(__import__("sys").modules, "sherpa_onnx", type("Sherpa", (), {"GenerationConfig": FakeGenerationConfig}))
    speech = engine.synthesize("你好", num_steps=4)
    assert speech.startswith(b"RIFF")

    deleted = engine.delete_voice_profile()
    assert deleted["voice_mode"] == engine.VOICE_MODE_PRESET
    assert deleted["voice_profile_ready"] is False
    assert not reference_audio.exists()


def test_kokoro_preset_selection_is_saved_and_used_for_generation(monkeypatch):
    settings = {
        "auto_read": False,
        "auto_send_after_asr": False,
        "auto_stop_on_silence": True,
        "reference_text": "",
        "voice_mode": engine.VOICE_MODE_PRESET,
        "voice_preset": engine.DEFAULT_PRESET_ID,
    }
    monkeypatch.setattr(engine.config_store, "get_setting", lambda key, default=None: dict(settings))
    monkeypatch.setattr(engine.config_store, "set_setting", lambda key, value: settings.update(value))
    monkeypatch.setattr(engine, "_runtime_available", lambda: True)
    monkeypatch.setattr(engine.local_models, "is_ready", lambda model_id: model_id == engine.PRESET_TTS_MODEL_ID)

    selected = "kokoro-zf_001"
    payload = engine.update_settings(voice_mode="preset", voice_preset=selected)
    assert payload["voice_preset"] == selected
    assert payload["tts_ready"] is True

    class FakeAudio:
        samples = np.ones(240, dtype=np.float32) * 0.05
        sample_rate = 24_000

    class FakeTts:
        def generate(self, content, generation):
            assert content == "你好"
            assert generation.sid == 3
            assert generation.speed == 1.0
            return FakeAudio()

    class FakeGenerationConfig:
        def __init__(self):
            self.extra = {}

    monkeypatch.setattr(engine, "_load_kokoro_tts", lambda: FakeTts())
    monkeypatch.setitem(__import__("sys").modules, "sherpa_onnx", type("Sherpa", (), {"GenerationConfig": FakeGenerationConfig}))

    speech = engine.synthesize("你好", num_steps=4)
    assert speech.startswith(b"RIFF")


def test_zipvoice_default_preset_is_available_and_uses_bundled_reference(monkeypatch, tmp_path):
    model_root = tmp_path / "zipvoice"
    model_root.mkdir()
    preset_audio = np.sin(np.linspace(0, np.pi * 440, 24_000, dtype=np.float32)) * 0.1
    import soundfile as sf
    sf.write(model_root / "preset-default.wav", preset_audio, 24_000, format="WAV", subtype="PCM_16")
    settings = {
        "auto_read": False,
        "auto_send_after_asr": False,
        "auto_stop_on_silence": True,
        "reference_text": "",
        "voice_mode": engine.VOICE_MODE_PRESET,
        "voice_preset": engine.ZIPVOICE_DEFAULT_PRESET_ID,
    }
    monkeypatch.setattr(engine.config_store, "get_setting", lambda key, default=None: dict(settings))
    monkeypatch.setattr(engine.config_store, "set_setting", lambda key, value: settings.update(value))
    monkeypatch.setattr(engine, "_runtime_available", lambda: True)
    monkeypatch.setattr(engine.local_models, "is_ready", lambda model_id: model_id == engine.CUSTOM_TTS_MODEL_ID)
    monkeypatch.setattr(engine.local_models, "model_dir", lambda _model_id: model_root)

    payload = engine.status()
    assert payload["tts_model"] == engine.CUSTOM_TTS_MODEL_ID
    assert payload["tts_ready"] is True
    assert payload["voice_presets"] == [dict(engine.ZIPVOICE_PRESETS[0])]

    class FakeAudio:
        samples = np.ones(240, dtype=np.float32) * 0.05
        sample_rate = 24_000

    class FakeTts:
        def generate(self, content, generation):
            assert content == "你好"
            assert generation.reference_text == engine.DEFAULT_PRESET_TEXT
            assert len(generation.reference_audio) == 24_000
            assert generation.reference_sample_rate == 24_000
            return FakeAudio()

    class FakeGenerationConfig:
        def __init__(self):
            self.extra = {}

    monkeypatch.setattr(engine, "_load_zipvoice_tts", lambda: FakeTts())
    monkeypatch.setitem(__import__("sys").modules, "sherpa_onnx", type("Sherpa", (), {"GenerationConfig": FakeGenerationConfig}))

    speech = engine.synthesize("你好", num_steps=4)
    assert speech.startswith(b"RIFF")


def test_custom_voice_profile_restores_previous_audio_when_settings_save_fails(monkeypatch, tmp_path):
    voice_root = tmp_path / "voice"
    voice_root.mkdir()
    reference_audio = voice_root / "reference.wav"
    reference_audio.write_bytes(b"previous-reference")
    monkeypatch.setattr(engine, "VOICE_ROOT", voice_root)
    monkeypatch.setattr(engine, "REFERENCE_AUDIO", reference_audio)
    monkeypatch.setattr(engine.local_models, "is_ready", lambda model_id: model_id == engine.CUSTOM_TTS_MODEL_ID)
    monkeypatch.setattr(engine, "_settings", lambda: {
        "auto_read": False,
        "auto_send_after_asr": False,
        "auto_stop_on_silence": True,
        "reference_text": "旧文字",
        "voice_mode": engine.VOICE_MODE_CUSTOM,
        "voice_preset": engine.DEFAULT_PRESET_ID,
    })
    monkeypatch.setattr(engine.config_store, "set_setting", lambda *_args: (_ for _ in ()).throw(OSError("disk full")))

    source = io.BytesIO()
    import soundfile as sf
    sf.write(source, np.zeros(16_000, dtype=np.float32), 16_000, format="WAV", subtype="PCM_16")
    with __import__("pytest").raises(OSError, match="disk full"):
        engine.save_voice_profile(source.getvalue(), "新文字")

    assert reference_audio.read_bytes() == b"previous-reference"


def test_tts_audio_edges_are_silent_and_smooth():
    sample_rate = 24_000
    source = np.ones(sample_rate // 2, dtype=np.float32)

    smoothed = engine._smooth_tts_audio_edges(source, sample_rate)

    leading = sample_rate * engine.TTS_LEADING_SILENCE_MS // 1000
    trailing = sample_rate * engine.TTS_TRAILING_SILENCE_MS // 1000
    assert np.all(smoothed[:leading] == 0)
    assert smoothed[leading] == 0
    assert 0 < smoothed[leading + 100] < 1
    assert np.all(smoothed[-trailing:] == 0)
    assert len(smoothed) == len(source) + leading + trailing


def test_voice_routes_reuse_engine_adapters(monkeypatch):
    app = FastAPI()
    router = APIRouter()
    voice_routes.register_voice_routes(router)
    app.include_router(router)

    ready = {
        "asr_ready": True,
        "tts_ready": True,
        "auto_read": False,
        "voice_profile_ready": True,
    }
    monkeypatch.setattr(voice_routes.engine, "status", lambda: ready)
    monkeypatch.setattr(
        voice_routes.engine,
        "update_settings",
        lambda **changes: {**ready, **changes},
    )
    monkeypatch.setattr(
        voice_routes.engine,
        "transcribe",
        lambda payload: {"text": "语音输入成功", "bytes": len(payload)},
    )
    monkeypatch.setattr(
        voice_routes.engine,
        "save_voice_profile",
        lambda payload, text: {**ready, "reference_text": text, "voice_mode": "custom", "bytes": len(payload)},
    )
    monkeypatch.setattr(voice_routes.engine, "delete_voice_profile", lambda: {**ready, "voice_profile_ready": False})
    synthesized = []

    def synthesize(text, *, num_steps=None):
        synthesized.append((text, num_steps))
        return b"RIFF" + text.encode("utf-8")

    monkeypatch.setattr(voice_routes.engine, "synthesize", synthesize)

    client = TestClient(app)
    assert client.get("/api/voice/status").json()["asr_ready"] is True
    assert client.put("/api/voice/settings", json={"auto_read": True}).json()["auto_read"] is True
    voice_input_settings = client.put(
        "/api/voice/settings",
        json={"auto_send_after_asr": True, "auto_stop_on_silence": False},
    ).json()
    assert voice_input_settings["auto_send_after_asr"] is True
    assert voice_input_settings["auto_stop_on_silence"] is False
    assert client.put("/api/voice/settings", json={"voice_mode": "preset"}).json()["voice_mode"] == "preset"
    transcript = client.post(
        "/api/voice/asr",
        files={"audio": ("voice.wav", b"wave-data", "audio/wav")},
    ).json()
    assert transcript == {"text": "语音输入成功", "bytes": 9}
    profile = client.post(
        "/api/voice/profile",
        files={"audio": ("reference.wav", b"recorded-reference", "audio/wav")},
        data={"reference_text": "自动识别文字"},
    ).json()
    assert profile["voice_mode"] == "custom"
    assert profile["reference_text"] == "自动识别文字"
    assert profile["bytes"] == 18
    assert client.delete("/api/voice/profile").json()["voice_profile_ready"] is False
    speech = client.post("/api/voice/tts", json={"text": "你好", "num_steps": 4})
    assert speech.status_code == 200
    assert speech.headers["content-type"].startswith("audio/wav")
    assert speech.content.startswith(b"RIFF")
    skipped_speech = client.post("/api/voice/tts", json={"text": "❓ ：——", "num_steps": 6})
    assert skipped_speech.status_code == 204
    assert skipped_speech.content == b""
    assert synthesized == [("你好", 4)]


def test_voice_controls_follow_existing_chat_layout():
    root = Path(__file__).resolve().parents[1]
    chat = (root / "src/webui/frontend/workbench-chat.jsx").read_text(encoding="utf-8")
    settings = (root / "src/webui/frontend/settings-overlay.jsx").read_text(encoding="utf-8")
    shell = (root / "src/webui/frontend/workbench.jsx").read_text(encoding="utf-8")
    shortcuts = (root / "src/webui/frontend/workbench-shortcuts.jsx").read_text(encoding="utf-8")

    composer = chat.split("function WbcComposer", 1)[1].split("function wbcClearComposerDraft", 1)[0]
    model_index = composer.index("wbc-model-anchor")
    microphone_index = composer.index("wbc-voice-input", model_index)
    send_index = composer.index('className={"wbc-send"', microphone_index)
    assert model_index < microphone_index < send_index

    assistant = chat.split("function WbcAssistantMessage", 1)[1].split("var WBC_HEARTBEAT_STALL_MS", 1)[0]
    assert assistant.index("voicePlayback") < assistant.index("workbenchChat.copy")
    assert "voiceSnapshot.status.tts_ready" in assistant

    capabilities = settings.split("function CapabilitiesPanel", 1)[1]
    assert "settings.voiceAutoRead" in capabilities
    assert "settings.voiceAutoSend" in capabilities
    assert "settings.voiceAutoStop" in capabilities
    assert "settings.voicePresetMode" in capabilities
    assert "settings.voiceCustomMode" in capabilities
    assert "saveVoicePreset(event.target.value)" in capabilities
    assert "function voicePresetOptions()" in capabilities
    assert 'React.createElement("optgroup"' in capabilities
    assert '["zipvoice", "settings.voiceZipVoiceGroup"]' in capabilities
    assert 'preset.group === "zipvoice"' in capabilities
    assert 'nextMode === "custom" && !voiceStatus.custom_tts_model_ready' in settings
    assert '!voiceStatus.custom_tts_model_ready,' in capabilities
    assert 'settings.voiceCustomRequiresZipVoice' in capabilities
    assert 'className: "wb-select wb-voice-preset-select"' in capabilities
    custom_voice = capabilities.split('customVoiceSelected\n            ?', 1)[1].split(': React.createElement("div", { className: "wb-voice-preset-row" }', 1)[0]
    assert 'type: "file"' not in custom_voice
    assert 'React.createElement("textarea"' not in custom_voice
    assert "wbcStartVoiceRecorder" in settings
    assert "wbcTranscribeVoiceBlob(blob)" in settings
    assert "voiceReferenceSessionRef.current += 1" in settings
    assert 'if (tab === "capabilities") return;' in settings
    assert "wbcStartVoiceRecorder().then" in settings
    assert "autoStopOnSilence: true" not in settings.split("function startVoiceReferenceRecording", 1)[1].split("useEffectSt(function ()", 1)[0]
    capabilities_call = settings.split('tab === "capabilities" && CapabilitiesPanel({', 1)[1].split("}),", 1)[0]
    capabilities_props = settings.split("function CapabilitiesPanel(p)", 1)[1].split("} = p;", 1)[0]
    assert "voiceReferencePhase, voiceReferenceElapsed" in capabilities_call
    assert "voiceReferencePhase, voiceReferenceElapsed" in capabilities_props
    assert "voiceReferenceElapsed.toFixed(1)" in custom_voice
    assert 'settings.voiceReferenceRecordingStatus' in custom_voice
    assert "if (elapsed >= 14) finishVoiceReferenceRecording(recorder);" in settings
    assert 'className: "wb-voice-reference-transcript"' in custom_voice
    assert "/api/voice/settings" in settings
    assert 'window.addEventListener("cyrene:voice-status-changed", onVoiceStatusChanged)' in settings
    assert 'window.removeEventListener("cyrene:voice-status-changed", onVoiceStatusChanged)' in settings
    assert 'return settingsFetch("/api/voice/status")' in settings
    assert "auto_send_after_asr" in composer
    assert "auto_stop_on_silence" in composer
    assert "wbcTranscribeVoiceBlob(blob)" in composer
    assert "payload.silence_only === true" in chat
    assert "if (silenceOnly) return false" in chat
    assert "function wbcCleanVoiceTranscript" in chat
    voice_start_index = composer.index('WbcVoice.stop();\n    setVoicePhase("starting")')
    recorder_start_index = composer.index("wbcStartVoiceRecorder", voice_start_index)
    assert voice_start_index < recorder_start_index
    assert "WBC_VOICE_SILENCE_MS = 1600" in chat
    assert "wbcCreateVoiceSilenceDetector" in chat
    assert "onSilence: finishVoiceInput" in composer
    assert "function voiceTextChunks" in chat
    assert "function voicePlainText" in chat
    assert "function hasSpeakableText(chunk)" in chat
    assert "/[\\p{L}\\p{N}]/u.test(chunk)" in chat
    assert "chunk && hasSpeakableText(chunk)" in chat
    assert "remaining && hasSpeakableText(remaining)" in chat
    assert ".replace(/```[\\s\\S]*?(?:```|$)/g, \" \")" in chat
    assert ".replace(/(?:[#*0-9]\\uFE0F?\\u20E3)/g, \" \")" in chat
    assert ".replace(/\\|/g, \"，\")" in chat
    assert "var markdownStable = openSquare <= closeSquare" in chat
    assert "var plainSource = voicePlainText(source)" in chat
    assert "function playSpeechChunks" in chat
    assert "num_steps: numSteps === 4 ? 4 : 6" in chat
    assert "if (response.status === 204) return null" in chat
    assert "if (!result.blob) return true" in chat
    assert "index === 0 ? 4 : 6" in chat
    assert "state.streamSentenceCount === 0 ? 4 : 6" in chat
    assert "requestSpeechChunk(chunks[index + 1]" in chat
    assert "function autoStream" in chat
    assert 'fire("onReplyStream"' in chat
    assert "WbcVoice.autoStream" in chat
    assert 'fire("onIntermediateMessage", chatId, message)' in chat
    assert "WbcVoice.autoSpeak" in chat
    assert "WbcVoice.autoSpeakFinal" in chat
    assert "autoStreamFinalText.set(targetKey, voicePlainText(text))" in chat
    assert "function wbcVoiceQuestionText" in chat
    assert '"question:" + String(pendingQuestion.id' in chat
    assert 'return [text, optionText].filter(Boolean).join("。 ")' in chat
    assert '"可选择："' not in chat
    assert "var maxChars = 60" in chat
    assert "var WbVoiceCommand = (function ()" in chat
    assert "initialSilenceMs: WBC_TOPBAR_INITIAL_SILENCE_MS" in chat
    assert 'fetch("/api/workbench/voice-command"' in chat
    assert "speechQueue = speechQueue.filter(function (item) { return item.runId !== state.runId; });" in chat
    assert 'event.type === "reply_done"' in chat
    assert 'event.type === "awaiting_user"' in chat
    assert 'sc.matches(event, "voice-command")' in shell
    assert shell.index("workbench-voice-command-btn") < shell.index('data-cyrene-node-id="open_settings"')
    assert 'id: "voice-command"' in shortcuts
    assert 'keys: ["mod", "shift", "M"]' in shortcuts
    assert "function showStatusToast" in chat
    assert 'wbcT("topbar.voiceCommandListening"' in chat
    assert 'wbcT("topbar.voiceCommandRecognizingNotice"' in chat
    assert 'wbcT("topbar.voiceCommandComplete"' in chat
    assert 'wbcT("topbar.voiceCommandNoSpeech"' in chat
    assert "function wbcCreateComposerVoiceFeedback()" in chat
    assert 'wbcT("topbar.voiceCommandStartingNotice"' in chat
    assert 'wbcT("topbar.voiceCommandListening"' in chat
    assert 'wbcT("topbar.voiceCommandRecognizingNotice"' in chat
    assert 'wbcT("workbenchChat.voiceInputComplete"' in chat
    assert "voiceFeedbackRef.current.starting();" in composer
    assert "voiceFeedbackRef.current.listening();" in composer
    assert "voiceFeedbackRef.current.transcribing();" in composer
    assert "voiceFeedbackRef.current.noSpeech();" in composer
    assert "voiceFeedbackRef.current.complete();" in composer
    assert 'aria-pressed={voicePhase === "recording"}' in composer
    assert 'aria-busy={voicePhase === "starting" || voicePhase === "transcribing"}' in composer

    task_composer = shell.split("function TaskComposer(", 1)[1].split(
        "function ComposerDisclaimer", 1
    )[0]
    assert "wbcCreateComposerVoiceFeedback()" in task_composer
    assert "voiceFeedbackRef.current.starting();" in task_composer
    assert "voiceFeedbackRef.current.listening();" in task_composer
    assert "voiceFeedbackRef.current.transcribing();" in task_composer
    assert "voiceFeedbackRef.current.noSpeech();" in task_composer
    assert "voiceFeedbackRef.current.complete();" in task_composer

    styles = (root / "src/webui/frontend/workbench.css").read_text(encoding="utf-8")
    composer_recording_css = styles.split(".wbc-voice-input.recording {", 1)[1].split("}", 1)[0]
    assert "animation: wb-voice-command-pulse 1.4s ease-in-out infinite;" in composer_recording_css
    assert ".wbc-voice-input.starting," in styles
    assert ".wbc-voice-input.transcribing:disabled" in styles

    electron_main = (root / "electron/main.js").read_text(encoding="utf-8")
    assert "autoplay-policy" in electron_main
    assert "no-user-gesture-required" in electron_main

    assert "permission === 'media' && isLocalBackend && audioOnly" in electron_main
    assert "mediaTypes.every((mediaType) => mediaType === 'audio')" in electron_main


def test_local_model_card_hides_stale_error_and_localizes_backend_errors():
    root = Path(__file__).resolve().parents[1]
    settings = (root / "src/webui/frontend/settings-overlay.jsx").read_text(encoding="utf-8")
    translations = (root / "src/webui/frontend/workbench-i18n.jsx").read_text(encoding="utf-8")
    models_panel = settings.split("function ModelsPanel(p) {", 1)[1].split("function modelDraftField", 1)[0]

    # Readiness takes precedence over a stale download error: the error is only
    # surfaced for models that are not ready, and the raw backend string is
    # never rendered as the card's primary error label.
    assert "var hasError = !item.ready && !!item.error;" in models_panel
    assert "hasError && React.createElement(\"small\", { className: \"wb-local-model-error\" }, localizeLocalModelError(item.error, t))" in models_panel
    assert '"wb-local-model-error" }, item.error)' not in models_panel
    assert "item.error ? t(\"settings.localModelError\")" not in models_panel

    # A ready model clears stale error state so later status reads stay clean.
    assert "if (item && item.ready) return { ...item, error: \"\" };" in settings
    assert "normalizeLocalModels(payload.models || [])" in settings

    # Known download/extraction/checksum/network failures map to localized keys.
    assert '"archive output is missing or invalid"' in settings
    assert '"all mirrors failed"' in settings
    assert '"checksum"' in settings
    assert "settings.localModelErrorExtract" in settings
    assert "settings.localModelErrorChecksum" in settings
    assert "settings.localModelErrorNetwork" in settings
    assert "settings.localModelErrorGeneric" in settings

    # Retry still triggers a fresh download for a failed, non-ready model.
    assert 'manageLocalModel(item.id, item.ready ? "delete" : "download")' in models_panel
    assert 't("settings.retry")' in models_panel

    # Every new key ships in both the English and Chinese dictionaries.
    for key in (
        "settings.localModelErrorExtract",
        "settings.localModelErrorChecksum",
        "settings.localModelErrorNetwork",
        "settings.localModelErrorGeneric",
    ):
        assert translations.count(f'"{key}"') == 2
