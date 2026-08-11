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
    monkeypatch.setattr(engine, "_preset_ready", lambda: True)
    monkeypatch.setattr(engine.local_models, "is_ready", lambda model_id: model_id == engine.TTS_MODEL_ID)

    payload = engine.status()

    assert payload["asr_ready"] is False
    assert payload["tts_ready"] is True
    assert payload["voice_mode"] == "preset"
    assert payload["voice_profile_ready"] is False
    assert payload["auto_read"] is True
    assert payload["auto_send_after_asr"] is False
    assert payload["auto_stop_on_silence"] is True


def test_tts_text_normalization_removes_display_only_tokens():
    normalized = engine.normalize_tts_text(
        "## 结果 ✅\n「Cyrene」请阅读[文档](https://example.com)，版本（测试）……\n"
        "![不可见的流程图](diagram.png)\n"
        "| 字段 | 值 |\n| --- | --- |\n::code-comment{file=demo.py}\n🎉 🔧 1️⃣\n"
        "**完成**"
    )

    assert normalized == "结果。Cyrene，请阅读文档，版本，测试。字段，值。完成"
    assert not any(token in normalized for token in ("不可见的流程图", "「", "」", "（", "）", "[", "]", "|", "**", "::", "https://", "✅", "🎉", "🔧", "1️⃣"))


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
    speech = client.post("/api/voice/tts", json={"text": "你好", "num_steps": 4})
    assert speech.status_code == 200
    assert speech.headers["content-type"].startswith("audio/wav")
    assert speech.content.startswith(b"RIFF")
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
    assert "/api/voice/settings" in settings
    assert "auto_send_after_asr" in composer
    assert "auto_stop_on_silence" in composer
    assert "payload.silence_only === true" in composer
    assert "if (silenceOnly) return false" in composer
    assert "function cleanVoiceTranscript" in composer
    voice_start_index = composer.index('WbcVoice.stop();\n    setVoicePhase("starting")')
    recorder_start_index = composer.index("wbcStartVoiceRecorder", voice_start_index)
    assert voice_start_index < recorder_start_index
    assert "WBC_VOICE_SILENCE_MS = 1600" in chat
    assert "wbcCreateVoiceSilenceDetector" in chat
    assert "onSilence: finishVoiceInput" in composer
    assert "function voiceTextChunks" in chat
    assert "function voicePlainText" in chat
    assert ".replace(/```[\\s\\S]*?(?:```|$)/g, \" \")" in chat
    assert ".replace(/(?:[#*0-9]\\uFE0F?\\u20E3)/g, \" \")" in chat
    assert ".replace(/\\|/g, \"，\")" in chat
    assert "var markdownStable = openSquare <= closeSquare" in chat
    assert "var plainSource = voicePlainText(source)" in chat
    assert "function playSpeechChunks" in chat
    assert "num_steps: numSteps === 4 ? 4 : 6" in chat
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
