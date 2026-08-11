"""FireRedASR2 and ZipVoice adapters backed by user-managed model packs."""

from __future__ import annotations

import io
import json
import os
import re
import tempfile
import threading
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from cyrene.config import CACHE_DIR
from cyrene.knowledge import local_models
from cyrene.runtime import config_store


ASR_MODEL_ID = "fireredasr2-aed-int8"
TTS_MODEL_ID = "zipvoice-zh-en"
VOICE_ROOT = Path(CACHE_DIR) / "voice"
REFERENCE_AUDIO = VOICE_ROOT / "reference.wav"
VOICE_MODE_PRESET = "preset"
VOICE_MODE_CUSTOM = "custom"
DEFAULT_PRESET_ID = "zipvoice-default"
DEFAULT_PRESET_TEXT = "那还是三十六年前，一九八七年。我呢考上了武汉大学的计算机系。"
MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_AUDIO_SECONDS = 10 * 60
MAX_TTS_CHARS = 2_000
TTS_NUM_STEPS = 6
TTS_FIRST_SENTENCE_NUM_STEPS = 4
TTS_ALLOWED_NUM_STEPS = frozenset({TTS_FIRST_SENTENCE_NUM_STEPS, TTS_NUM_STEPS})
TTS_SPEED = 0.92
TTS_SILENCE_SCALE = 0.24
TTS_LEADING_SILENCE_MS = 60
TTS_FADE_IN_MS = 30
TTS_FADE_OUT_MS = 20
TTS_TRAILING_SILENCE_MS = 25

_ASR_LOCK = threading.RLock()
_TTS_LOCK = threading.RLock()
_RECOGNIZER: Any = None
_PUNCTUATION: Any = None
_TTS: Any = None


def _runtime_available() -> bool:
    try:
        import sherpa_onnx  # noqa: F401

        return True
    except (ImportError, OSError, RuntimeError):
        return False


def _settings() -> dict[str, Any]:
    value = config_store.get_setting("voice", {})
    source = value if isinstance(value, dict) else {}
    voice_mode = str(source.get("voice_mode") or VOICE_MODE_PRESET).strip().lower()
    if voice_mode not in {VOICE_MODE_PRESET, VOICE_MODE_CUSTOM}:
        voice_mode = VOICE_MODE_PRESET
    return {
        "auto_read": bool(source.get("auto_read", False)),
        "auto_send_after_asr": bool(source.get("auto_send_after_asr", False)),
        "auto_stop_on_silence": bool(source.get("auto_stop_on_silence", True)),
        "reference_text": str(source.get("reference_text") or "").strip(),
        "voice_mode": voice_mode,
        "voice_preset": DEFAULT_PRESET_ID,
    }


def _profile_ready(settings: dict[str, Any] | None = None) -> bool:
    current = settings or _settings()
    try:
        return bool(current["reference_text"] and REFERENCE_AUDIO.stat().st_size > 1_000)
    except OSError:
        return False


def _preset_audio() -> Path:
    return local_models.model_dir(TTS_MODEL_ID) / "preset-default.wav"


def _preset_ready() -> bool:
    try:
        return _preset_audio().stat().st_size > 1_000
    except OSError:
        return False


def _active_reference(settings: dict[str, Any]) -> tuple[Path, str]:
    if settings.get("voice_mode") == VOICE_MODE_CUSTOM:
        if not _profile_ready(settings):
            raise RuntimeError("ZipVoice custom voice is not configured")
        return REFERENCE_AUDIO, settings["reference_text"]
    if not _preset_ready():
        raise RuntimeError("ZipVoice preset voice is not available")
    return _preset_audio(), DEFAULT_PRESET_TEXT


def status() -> dict[str, Any]:
    settings = _settings()
    asr_ready = local_models.is_ready(ASR_MODEL_ID)
    tts_model_ready = local_models.is_ready(TTS_MODEL_ID)
    profile_ready = _profile_ready(settings)
    preset_ready = tts_model_ready and _preset_ready()
    selected_voice_ready = profile_ready if settings["voice_mode"] == VOICE_MODE_CUSTOM else preset_ready
    runtime_available = _runtime_available()
    return {
        "asr_model": ASR_MODEL_ID,
        "tts_model": TTS_MODEL_ID,
        "asr_ready": asr_ready and runtime_available,
        "tts_model_ready": tts_model_ready and runtime_available,
        "voice_profile_ready": profile_ready,
        "voice_preset_ready": preset_ready,
        "voice_mode": settings["voice_mode"],
        "voice_preset": settings["voice_preset"],
        "voice_presets": [{"id": DEFAULT_PRESET_ID}],
        "tts_ready": tts_model_ready and selected_voice_ready and runtime_available,
        "runtime_available": runtime_available,
        "auto_read": settings["auto_read"],
        "auto_send_after_asr": settings["auto_send_after_asr"],
        "auto_stop_on_silence": settings["auto_stop_on_silence"],
        "reference_text": settings["reference_text"],
    }


def update_settings(
    *,
    auto_read: bool | None = None,
    auto_send_after_asr: bool | None = None,
    auto_stop_on_silence: bool | None = None,
    voice_mode: str | None = None,
    voice_preset: str | None = None,
) -> dict[str, Any]:
    current = _settings()
    if auto_read is not None:
        current["auto_read"] = bool(auto_read)
    if auto_send_after_asr is not None:
        current["auto_send_after_asr"] = bool(auto_send_after_asr)
    if auto_stop_on_silence is not None:
        current["auto_stop_on_silence"] = bool(auto_stop_on_silence)
    if voice_mode is not None:
        normalized_mode = str(voice_mode).strip().lower()
        if normalized_mode not in {VOICE_MODE_PRESET, VOICE_MODE_CUSTOM}:
            raise ValueError("voice_mode must be preset or custom")
        current["voice_mode"] = normalized_mode
    if voice_preset is not None and str(voice_preset).strip() != DEFAULT_PRESET_ID:
        raise ValueError("unknown voice preset")
    current["voice_preset"] = DEFAULT_PRESET_ID
    if current["voice_mode"] == VOICE_MODE_CUSTOM and not _profile_ready(current):
        current["auto_read"] = False
    config_store.set_setting("voice", current)
    return status()


def _decode_audio(payload: bytes) -> tuple[np.ndarray, int]:
    if not payload:
        raise ValueError("audio is required")
    if len(payload) > MAX_AUDIO_BYTES:
        raise ValueError("audio file is too large")
    try:
        samples, sample_rate = sf.read(
            io.BytesIO(payload),
            dtype="float32",
            always_2d=True,
        )
    except Exception as exc:
        raise ValueError("unsupported or invalid audio file") from exc
    if sample_rate <= 0 or samples.size == 0:
        raise ValueError("audio is empty")
    mono = np.ascontiguousarray(samples.mean(axis=1), dtype=np.float32)
    duration = len(mono) / sample_rate
    if duration > MAX_AUDIO_SECONDS:
        raise ValueError("audio must be 10 minutes or shorter")
    if not np.isfinite(mono).all():
        raise ValueError("audio contains invalid samples")
    return mono, int(sample_rate)


def _resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return np.ascontiguousarray(samples, dtype=np.float32)
    target_size = max(1, int(round(len(samples) * target_rate / source_rate)))
    source_points = np.arange(len(samples), dtype=np.float64)
    target_points = np.linspace(0, max(0, len(samples) - 1), target_size)
    return np.ascontiguousarray(
        np.interp(target_points, source_points, samples),
        dtype=np.float32,
    )


def _load_asr() -> tuple[Any, Any, Any]:
    global _RECOGNIZER, _PUNCTUATION
    if not local_models.is_ready(ASR_MODEL_ID):
        raise RuntimeError("FireRedASR2 model is not downloaded")
    import sherpa_onnx

    root = local_models.model_dir(ASR_MODEL_ID)
    provider = local_models.sherpa_provider(ASR_MODEL_ID)
    if _RECOGNIZER is None:
        _RECOGNIZER = sherpa_onnx.OfflineRecognizer.from_fire_red_asr(
            encoder=str(root / "encoder.int8.onnx"),
            decoder=str(root / "decoder.int8.onnx"),
            tokens=str(root / "tokens.txt"),
            num_threads=max(1, min(4, (os.cpu_count() or 2) // 2)),
            debug=False,
            provider=provider,
        )
    if _PUNCTUATION is None:
        config = sherpa_onnx.OfflinePunctuationConfig(
            model=sherpa_onnx.OfflinePunctuationModelConfig(
                ct_transformer=str(root / "punctuation.int8.onnx"),
                num_threads=1,
                debug=False,
                provider=provider,
            )
        )
        _PUNCTUATION = sherpa_onnx.OfflinePunctuation(config)
    return sherpa_onnx, _RECOGNIZER, _PUNCTUATION


def _speech_segments(sherpa_onnx: Any, samples: np.ndarray, root: Path) -> list[np.ndarray]:
    sample_rate = 16_000
    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = str(root / "silero_vad.onnx")
    config.silero_vad.min_silence_duration = 0.5
    config.silero_vad.min_speech_duration = 0.25
    config.silero_vad.max_speech_duration = 30
    config.sample_rate = sample_rate
    vad = sherpa_onnx.VoiceActivityDetector(
        config,
        buffer_size_in_seconds=min(MAX_AUDIO_SECONDS + 5, 615),
    )
    window_size = int(config.silero_vad.window_size)
    for offset in range(0, len(samples), window_size):
        block = samples[offset : offset + window_size]
        if len(block) < window_size:
            block = np.pad(block, (0, window_size - len(block)))
        vad.accept_waveform(block)
    vad.flush()
    segments: list[np.ndarray] = []
    while not vad.empty():
        segment = np.ascontiguousarray(vad.front.samples, dtype=np.float32)
        vad.pop()
        if len(segment) >= int(0.2 * sample_rate):
            segments.append(segment)
    return segments or [samples]


def _result_text(result: Any) -> str:
    text = str(getattr(result, "text", "") or "").strip()
    if text:
        return text
    try:
        decoded = json.loads(str(result))
        return str(decoded.get("text") or "").strip()
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""


_ASR_SILENCE_TOKEN_RE = re.compile(
    r"(?:\*{1,3}|_{1,3})?\s*<\s*sil(?:ence)?\s*>\s*"
    r"(?:\*{1,3}|_{1,3})?\s*[。.!！?？,，、;；:：…]*",
    flags=re.IGNORECASE,
)


def _clean_asr_text(text: str) -> tuple[str, bool]:
    """Remove model silence placeholders without discarding real speech."""
    content = unicodedata.normalize("NFKC", str(text or "")).strip()
    if not content:
        return "", False
    cleaned, removed = _ASR_SILENCE_TOKEN_RE.subn(" ", content)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # A silence placeholder can be wrapped by unmatched Markdown emphasis.
    # Do not let those display-only markers become composer content either.
    if removed and not cleaned.strip(" *_~。.!！?？,，、;；:：…"):
        cleaned = ""
    return cleaned, removed > 0


def transcribe(payload: bytes) -> dict[str, Any]:
    samples, source_rate = _decode_audio(payload)
    samples_16k = _resample(samples, source_rate, 16_000)
    with _ASR_LOCK:
        sherpa_onnx, recognizer, punctuation = _load_asr()
        segments = _speech_segments(
            sherpa_onnx,
            samples_16k,
            local_models.model_dir(ASR_MODEL_ID),
        )
        parts: list[str] = []
        silence_placeholder_seen = False
        for segment in segments:
            stream = recognizer.create_stream()
            stream.accept_waveform(16_000, segment)
            recognizer.decode_stream(stream)
            text, removed_silence = _clean_asr_text(_result_text(stream.result))
            silence_placeholder_seen = silence_placeholder_seen or removed_silence
            if text:
                parts.append(text)
        raw_text = " ".join(parts).strip()
        text = punctuation.add_punctuation(raw_text).strip() if raw_text else ""
        text, removed_silence = _clean_asr_text(text)
        silence_placeholder_seen = silence_placeholder_seen or removed_silence
    return {
        "text": text,
        "raw_text": raw_text,
        "silence_only": bool(silence_placeholder_seen and not text),
        "segments": len(parts),
        "duration_seconds": round(len(samples) / source_rate, 3),
        "model": ASR_MODEL_ID,
    }


def save_voice_profile(payload: bytes, reference_text: str) -> dict[str, Any]:
    text = str(reference_text or "").strip()
    if not text:
        raise ValueError("reference text is required")
    if len(text) > 1_000:
        raise ValueError("reference text is too long")
    samples, sample_rate = _decode_audio(payload)
    duration = len(samples) / sample_rate
    if duration < 1 or duration > 15:
        raise ValueError("reference audio must be between 1 and 15 seconds")
    VOICE_ROOT.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix="reference-", suffix=".wav", dir=VOICE_ROOT)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        sf.write(temporary, samples, sample_rate, subtype="PCM_16")
        os.replace(temporary, REFERENCE_AUDIO)
    finally:
        temporary.unlink(missing_ok=True)
    current = _settings()
    current["reference_text"] = text
    current["voice_mode"] = VOICE_MODE_CUSTOM
    config_store.set_setting("voice", current)
    reset_tts()
    return status()


def delete_voice_profile() -> dict[str, Any]:
    REFERENCE_AUDIO.unlink(missing_ok=True)
    current = _settings()
    current["reference_text"] = ""
    current["voice_mode"] = VOICE_MODE_PRESET
    config_store.set_setting("voice", current)
    reset_tts()
    return status()


def _load_tts() -> Any:
    global _TTS
    if not local_models.is_ready(TTS_MODEL_ID):
        raise RuntimeError("ZipVoice model is not downloaded")
    if _TTS is not None:
        return _TTS
    import sherpa_onnx

    root = local_models.model_dir(TTS_MODEL_ID)
    provider = local_models.sherpa_provider(TTS_MODEL_ID)
    config = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            zipvoice=sherpa_onnx.OfflineTtsZipvoiceModelConfig(
                tokens=str(root / "tokens.txt"),
                encoder=str(root / "encoder.onnx"),
                decoder=str(root / "decoder.onnx"),
                data_dir=str(root / "espeak-ng-data"),
                lexicon=str(root / "lexicon.txt"),
                vocoder=str(root / "vocos_24khz.onnx"),
            ),
            debug=False,
            num_threads=max(1, min(4, (os.cpu_count() or 2) // 2)),
            provider=provider,
        )
    )
    if not config.validate():
        raise RuntimeError("ZipVoice model configuration is invalid")
    _TTS = sherpa_onnx.OfflineTts(config)
    return _TTS


_TTS_PUNCTUATION_TRANSLATION = str.maketrans({
    "「": "，", "」": "，", "『": "，", "』": "，",
    "“": "，", "”": "，", "‘": "，", "’": "，",
    "《": "，", "》": "，", "〈": "，", "〉": "，",
    "（": "，", "）": "，", "(": "，", ")": "，",
    "【": "，", "】": "，", "[": "，", "]": "，",
    "—": "，", "–": "，", "…": "。", "·": "，",
    "：": "，", ":": "，", "；": "。", ";": "。",
})

_TTS_EMOJI_RE = re.compile(
    r"(?:[#*0-9]\ufe0f?\u20e3)|"
    r"[\U0001F000-\U0001FAFF\U0001FC00-\U0001FFFF\u2600-\u27BF\u00A9\u00AE\u2122]"
    r"[\ufe0e\ufe0f\U0001F3FB-\U0001F3FF\u200d\U000E0020-\U000E007F]*"
)


def normalize_tts_text(text: str) -> str:
    """Turn display-oriented prose into tokens ZipVoice can pronounce cleanly."""
    content = unicodedata.normalize("NFKC", str(text or ""))
    # ZipVoice verbalizes Unicode emoji names (for example, ✅ becomes
    # "WHITE HEAVY CHECK MARK") and can turn symbols such as 🔧 into a
    # Chinese-sounding false syllable.  Emoji are visual decoration, so remove
    # complete sequences before Markdown cleanup and sentence segmentation.
    content = _TTS_EMOJI_RE.sub(" ", content)
    content = re.sub(
        r"[\ufe0e\ufe0f\u200d\u20e3\U0001F3FB-\U0001F3FF\U000E0020-\U000E007F]",
        "",
        content,
    )
    content = re.sub(r"\.{3,}", "。", content)
    content = re.sub(r"<!--[\s\S]*?-->", " ", content)
    content = re.sub(r"```[\s\S]*?(?:```|\Z)", " ", content)
    content = re.sub(r"~~~[\s\S]*?(?:~~~|\Z)", " ", content)
    content = re.sub(r"\$\$[\s\S]*?\$\$", " ", content)
    content = re.sub(r"\$[^$\n]+\$", " ", content)
    content = re.sub(r"^\s*::[a-zA-Z][\w-]*\{[^\n}]*\}\s*$", " ", content, flags=re.MULTILINE)
    # Images are visual-only in the rendered message.  Their alt text is not
    # part of the visible prose and must not leak into speech.
    content = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", content)
    content = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", content)
    content = re.sub(r"^\s*\[\^[^\]]+\]:.*$", " ", content, flags=re.MULTILINE)
    content = re.sub(r"\[\^[^\]]+\]", "", content)
    content = re.sub(r"\[([^\]\n]+)\]", r"\1", content)
    content = re.sub(r"`([^`]+)`", r"\1", content)
    content = re.sub(r"https?://\S+", " ", content)
    content = re.sub(r"<\/?(?:br|p|div|li|h[1-6])\b[^>]*>", "\n", content, flags=re.IGNORECASE)
    content = re.sub(r"<[^>]+>", " ", content)
    content = re.sub(r"^\s{0,3}(?:#{1,6}\s*|(?:>\s*)+)", "", content, flags=re.MULTILINE)
    content = re.sub(r"^\s{0,3}(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s*)?", "", content, flags=re.MULTILINE)
    content = re.sub(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$", "", content, flags=re.MULTILINE)
    content = re.sub(r"^\s*(?:[-*_]\s*){3,}$", "", content, flags=re.MULTILINE)
    content = re.sub(r"\s+#{1,6}\s*$", "", content, flags=re.MULTILINE)
    content = re.sub(r"\\([\\`*{}\[\]()#+.!_>~-])", r"\1", content)
    content = content.replace("|", "，")
    content = content.translate(_TTS_PUNCTUATION_TRANSLATION)
    content = re.sub(r"[\r\n]+", "。", content)
    content = re.sub(r"[*_~]+", "", content)
    content = re.sub(r"\s+", " ", content)
    content = re.sub(r"[，、,.]*[。!?！？]+[，、,.]*", "。", content)
    content = re.sub(r"(?:。\s*){2,}", "。", content)
    content = re.sub(r"[，、,.]+", "，", content)
    content = re.sub(r"\s*([，。！？!?])\s*", r"\1", content)
    return content.strip(" ，")


def _smooth_tts_audio_edges(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Prevent hard waveform edges from sounding like a false initial consonant."""
    audio = np.ascontiguousarray(samples, dtype=np.float32).copy()
    if audio.size == 0 or sample_rate <= 0:
        return audio

    fade_in_size = min(audio.size, max(1, int(round(sample_rate * TTS_FADE_IN_MS / 1000))))
    fade_out_size = min(audio.size, max(1, int(round(sample_rate * TTS_FADE_OUT_MS / 1000))))
    # A half-cosine ramp has zero slope at both ends and is less audible than a
    # linear gain change on speech onsets.
    fade_in = np.sin(np.linspace(0, np.pi / 2, fade_in_size, dtype=np.float32)) ** 2
    fade_out = np.cos(np.linspace(0, np.pi / 2, fade_out_size, dtype=np.float32)) ** 2
    audio[:fade_in_size] *= fade_in
    audio[-fade_out_size:] *= fade_out

    leading_size = max(0, int(round(sample_rate * TTS_LEADING_SILENCE_MS / 1000)))
    trailing_size = max(0, int(round(sample_rate * TTS_TRAILING_SILENCE_MS / 1000)))
    return np.concatenate(
        (
            np.zeros(leading_size, dtype=np.float32),
            audio,
            np.zeros(trailing_size, dtype=np.float32),
        )
    )


def synthesize(text: str, *, num_steps: int | None = None) -> bytes:
    content = normalize_tts_text(text)
    if not content:
        raise ValueError("text is required")
    if len(content) > MAX_TTS_CHARS:
        raise ValueError(f"text must be {MAX_TTS_CHARS} characters or shorter")
    selected_num_steps = TTS_NUM_STEPS if num_steps is None else int(num_steps)
    if selected_num_steps not in TTS_ALLOWED_NUM_STEPS:
        raise ValueError("num_steps must be 4 or 6")
    settings = _settings()
    reference_path, reference_text = _active_reference(settings)
    reference_audio, reference_sample_rate = sf.read(
        reference_path,
        dtype="float32",
        always_2d=True,
    )
    reference_audio = np.ascontiguousarray(reference_audio.mean(axis=1), dtype=np.float32)
    with _TTS_LOCK:
        import sherpa_onnx

        tts = _load_tts()
        generation = sherpa_onnx.GenerationConfig()
        generation.reference_audio = reference_audio
        generation.reference_sample_rate = int(reference_sample_rate)
        generation.reference_text = reference_text
        # The first queued sentence uses four distilled-flow steps for a faster
        # first audible result; later sentences use six for better quality.
        # Slowing the model itself (rather than post-processing playback)
        # preserves pitch and voice identity.
        generation.num_steps = selected_num_steps
        generation.speed = TTS_SPEED
        generation.silence_scale = TTS_SILENCE_SCALE
        generation.extra["min_char_in_sentence"] = "10"
        audio = tts.generate(content, generation)
        if len(audio.samples) == 0:
            raise RuntimeError("ZipVoice generated empty audio")
        smoothed_samples = _smooth_tts_audio_edges(audio.samples, int(audio.sample_rate))
        output = io.BytesIO()
        sf.write(output, smoothed_samples, int(audio.sample_rate), format="WAV", subtype="PCM_16")
        return output.getvalue()


def reset_asr() -> None:
    global _RECOGNIZER, _PUNCTUATION
    with _ASR_LOCK:
        _RECOGNIZER = None
        _PUNCTUATION = None


def reset_tts() -> None:
    global _TTS
    with _TTS_LOCK:
        _TTS = None


local_models.register_resetter(ASR_MODEL_ID, reset_asr)
local_models.register_resetter(TTS_MODEL_ID, reset_tts)
