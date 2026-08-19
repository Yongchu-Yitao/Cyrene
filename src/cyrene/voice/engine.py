"""Voice settings and adapters for local models plus MiniMax cloud TTS."""

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
from cyrene.voice import minimax as minimax_tts


ASR_MODEL_ID = "fireredasr2-aed-int8"
PRESET_TTS_MODEL_ID = "kokoro-zh-en"
CUSTOM_TTS_MODEL_ID = "zipvoice-zh-en"
# Compatibility name for callers that treat the default speech model as the
# single TTS model. Voice status exposes both concrete model ids below.
TTS_MODEL_ID = PRESET_TTS_MODEL_ID
TTS_MODEL_AUTO = "auto"
MINIMAX_TTS_MODEL_IDS = minimax_tts.MODEL_IDS
TTS_MODEL_SELECTIONS = frozenset({
    TTS_MODEL_AUTO,
    PRESET_TTS_MODEL_ID,
    CUSTOM_TTS_MODEL_ID,
    *MINIMAX_TTS_MODEL_IDS,
})
VOICE_ROOT = Path(CACHE_DIR) / "voice"
REFERENCE_AUDIO = VOICE_ROOT / "reference.wav"
VOICE_MODE_PRESET = "preset"
VOICE_MODE_CUSTOM = "custom"
_KOKORO_ENGLISH_FEMALE = ("af_maple", "af_sol", "bf_vale")
_KOKORO_CHINESE_FEMALE = (
    "zf_001", "zf_002", "zf_003", "zf_004", "zf_005", "zf_006", "zf_007", "zf_008",
    "zf_017", "zf_018", "zf_019", "zf_021", "zf_022", "zf_023", "zf_024", "zf_026",
    "zf_027", "zf_028", "zf_032", "zf_036", "zf_038", "zf_039", "zf_040", "zf_042",
    "zf_043", "zf_044", "zf_046", "zf_047", "zf_048", "zf_049", "zf_051", "zf_059",
    "zf_060", "zf_067", "zf_070", "zf_071", "zf_072", "zf_073", "zf_074", "zf_075",
    "zf_076", "zf_077", "zf_078", "zf_079", "zf_083", "zf_084", "zf_085", "zf_086",
    "zf_087", "zf_088", "zf_090", "zf_092", "zf_093", "zf_094", "zf_099",
)
_KOKORO_CHINESE_MALE = (
    "zm_009", "zm_010", "zm_011", "zm_012", "zm_013", "zm_014", "zm_015", "zm_016",
    "zm_020", "zm_025", "zm_029", "zm_030", "zm_031", "zm_033", "zm_034", "zm_035",
    "zm_037", "zm_041", "zm_045", "zm_050", "zm_052", "zm_053", "zm_054", "zm_055",
    "zm_056", "zm_057", "zm_058", "zm_061", "zm_062", "zm_063", "zm_064", "zm_065",
    "zm_066", "zm_068", "zm_069", "zm_080", "zm_081", "zm_082", "zm_089", "zm_091",
    "zm_095", "zm_096", "zm_097", "zm_098", "zm_100",
)
_KOKORO_SPEAKERS = _KOKORO_ENGLISH_FEMALE + _KOKORO_CHINESE_FEMALE + _KOKORO_CHINESE_MALE
DEFAULT_PRESET_ID = "kokoro-zm_009"
ZIPVOICE_DEFAULT_PRESET_ID = "zipvoice-default"
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
_VOICE_PROFILE_LOCK = threading.RLock()
_RECOGNIZER: Any = None
_PUNCTUATION: Any = None
_KOKORO_TTS: Any = None
_ZIPVOICE_TTS: Any = None


def _voice_presets() -> list[dict[str, Any]]:
    presets: list[dict[str, Any]] = []
    for sid, name in enumerate(_KOKORO_SPEAKERS):
        if name.startswith("zf_"):
            group, gender, language, ordinal = "zh_female", "female", "zh", sid - 2
        elif name.startswith("zm_"):
            group, gender, language, ordinal = "zh_male", "male", "zh", sid - 57
        else:
            group, gender, language, ordinal = "en_female", "female", "en", sid + 1
        presets.append({
            "id": f"kokoro-{name}",
            "name": name,
            "sid": sid,
            "group": group,
            "gender": gender,
            "language": language,
            "ordinal": ordinal,
        })
    return presets


KOKORO_PRESETS = tuple(_voice_presets())
ZIPVOICE_PRESETS = ({
    "id": ZIPVOICE_DEFAULT_PRESET_ID,
    "name": "default",
    "group": "zipvoice",
    "gender": "male",
    "language": "zh",
    "ordinal": 1,
    "model": CUSTOM_TTS_MODEL_ID,
},)
VOICE_PRESETS = KOKORO_PRESETS + ZIPVOICE_PRESETS
_VOICE_PRESET_BY_ID = {preset["id"]: preset for preset in VOICE_PRESETS}


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
    settings = {
        "auto_read": bool(source.get("auto_read", False)),
        "auto_send_after_asr": bool(source.get("auto_send_after_asr", False)),
        "auto_stop_on_silence": bool(source.get("auto_stop_on_silence", True)),
        "reference_text": str(source.get("reference_text") or "").strip(),
        "voice_mode": voice_mode,
        "voice_preset": str(source.get("voice_preset") or DEFAULT_PRESET_ID).strip(),
        "tts_model": str(source.get("tts_model") or TTS_MODEL_AUTO).strip().lower(),
        "minimax_voice_id": str(
            source.get("minimax_voice_id") or minimax_tts.DEFAULT_VOICE_ID
        ).strip(),
    }
    if settings["tts_model"] not in TTS_MODEL_SELECTIONS:
        settings["tts_model"] = TTS_MODEL_AUTO
    if settings["voice_preset"] not in _VOICE_PRESET_BY_ID:
        settings["voice_preset"] = DEFAULT_PRESET_ID
    elif (
        settings["voice_preset"] == ZIPVOICE_DEFAULT_PRESET_ID
        and not local_models.is_ready(CUSTOM_TTS_MODEL_ID)
        and local_models.is_ready(PRESET_TTS_MODEL_ID)
    ):
        settings["voice_preset"] = DEFAULT_PRESET_ID
    elif (
        settings["voice_preset"] != ZIPVOICE_DEFAULT_PRESET_ID
        and not local_models.is_ready(PRESET_TTS_MODEL_ID)
        and local_models.is_ready(CUSTOM_TTS_MODEL_ID)
    ):
        settings["voice_preset"] = ZIPVOICE_DEFAULT_PRESET_ID
    return settings


def _local_tts_model(settings: dict[str, Any]) -> str:
    custom_selected = settings.get("voice_mode") == VOICE_MODE_CUSTOM
    zipvoice_preset_selected = (
        not custom_selected
        and settings.get("voice_preset") == ZIPVOICE_DEFAULT_PRESET_ID
    )
    return CUSTOM_TTS_MODEL_ID if custom_selected or zipvoice_preset_selected else PRESET_TTS_MODEL_ID


def _resolved_tts_model(
    settings: dict[str, Any],
    *,
    minimax_configured: bool,
) -> tuple[str, str]:
    selection = str(settings.get("tts_model") or "").strip().lower()
    if not selection:
        # Compatibility for tests and callers that still provide the pre-model-
        # selector settings shape.
        return _local_tts_model(settings), "local"
    if selection == TTS_MODEL_AUTO:
        if minimax_configured:
            return minimax_tts.TURBO_MODEL_ID, "minimax"
        return _local_tts_model(settings), "local"
    if selection in MINIMAX_TTS_MODEL_IDS:
        return selection, "minimax"
    return selection, "local"


def _profile_ready(settings: dict[str, Any] | None = None) -> bool:
    with _VOICE_PROFILE_LOCK:
        current = settings or _settings()
        try:
            return bool(current["reference_text"] and REFERENCE_AUDIO.stat().st_size > 1_000)
        except OSError:
            return False


def _preset_ready() -> bool:
    return local_models.is_ready(PRESET_TTS_MODEL_ID)


def _active_reference(settings: dict[str, Any]) -> tuple[Path, str]:
    if not _profile_ready(settings):
        raise RuntimeError("ZipVoice custom voice is not configured")
    return REFERENCE_AUDIO, settings["reference_text"]


def status() -> dict[str, Any]:
    with _VOICE_PROFILE_LOCK:
        settings = _settings()
        profile_ready = _profile_ready(settings)
    asr_ready = local_models.is_ready(ASR_MODEL_ID)
    preset_model_ready = local_models.is_ready(PRESET_TTS_MODEL_ID)
    custom_model_ready = local_models.is_ready(CUSTOM_TTS_MODEL_ID)
    minimax_configured = minimax_tts.is_configured()
    selected_model, tts_provider = _resolved_tts_model(
        settings,
        minimax_configured=minimax_configured,
    )
    local_tts_active = tts_provider == "local"
    custom_selected = (
        local_tts_active
        and selected_model == CUSTOM_TTS_MODEL_ID
        and settings["voice_mode"] == VOICE_MODE_CUSTOM
    )
    selected_model_ready = (
        minimax_configured
        if tts_provider == "minimax"
        else custom_model_ready
        if selected_model == CUSTOM_TTS_MODEL_ID
        else preset_model_ready
    )
    selected_voice_ready = profile_ready if custom_selected else True
    runtime_available = _runtime_available()
    available_presets: list[dict[str, Any]] = []
    if local_tts_active and selected_model == CUSTOM_TTS_MODEL_ID and custom_model_ready:
        available_presets = [dict(ZIPVOICE_PRESETS[0])]
    elif local_tts_active and selected_model == PRESET_TTS_MODEL_ID and preset_model_ready:
        available_presets = [dict(preset) for preset in KOKORO_PRESETS]
    tts_ready = selected_model_ready and selected_voice_ready and (
        tts_provider == "minimax" or runtime_available
    )
    tts_models = [
        {
            "id": TTS_MODEL_AUTO,
            "provider": "auto",
            "available": minimax_configured or (
                runtime_available and (preset_model_ready or custom_model_ready)
            ),
        },
        {
            "id": minimax_tts.TURBO_MODEL_ID,
            "provider": "minimax",
            "available": minimax_configured,
        },
        {
            "id": minimax_tts.HD_MODEL_ID,
            "provider": "minimax",
            "available": minimax_configured,
        },
        {
            "id": PRESET_TTS_MODEL_ID,
            "provider": "local",
            "available": preset_model_ready and runtime_available,
        },
        {
            "id": CUSTOM_TTS_MODEL_ID,
            "provider": "local",
            "available": custom_model_ready and runtime_available,
        },
    ]
    return {
        "asr_model": ASR_MODEL_ID,
        "tts_model": selected_model,
        "preset_tts_model": PRESET_TTS_MODEL_ID,
        "custom_tts_model": CUSTOM_TTS_MODEL_ID,
        "asr_ready": asr_ready and runtime_available,
        "tts_model_selection": settings.get("tts_model") or selected_model,
        "tts_models": tts_models,
        "tts_provider": tts_provider,
        "minimax_configured": minimax_configured,
        "minimax_voice_id": settings.get("minimax_voice_id") or minimax_tts.DEFAULT_VOICE_ID,
        "local_tts_active": local_tts_active,
        "tts_model_ready": selected_model_ready and (tts_provider == "minimax" or runtime_available),
        "preset_tts_model_ready": preset_model_ready and runtime_available,
        "custom_tts_model_ready": custom_model_ready and runtime_available,
        "voice_profile_ready": profile_ready,
        "voice_preset_ready": bool(available_presets) and runtime_available,
        "voice_mode": settings["voice_mode"],
        "voice_preset": settings["voice_preset"],
        "voice_presets": available_presets,
        "tts_ready": tts_ready,
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
    tts_model: str | None = None,
) -> dict[str, Any]:
    with _VOICE_PROFILE_LOCK:
        current = _settings()
        if auto_read is not None:
            current["auto_read"] = bool(auto_read)
        if auto_send_after_asr is not None:
            current["auto_send_after_asr"] = bool(auto_send_after_asr)
        if auto_stop_on_silence is not None:
            current["auto_stop_on_silence"] = bool(auto_stop_on_silence)
        if tts_model is not None:
            normalized_tts_model = str(tts_model).strip().lower()
            if normalized_tts_model not in TTS_MODEL_SELECTIONS:
                raise ValueError("unknown TTS model")
            if (
                normalized_tts_model in MINIMAX_TTS_MODEL_IDS
                and not minimax_tts.is_configured()
            ):
                raise RuntimeError("Configure MiniMax in Model Services before using MiniMax TTS")
            if normalized_tts_model == PRESET_TTS_MODEL_ID:
                if not local_models.is_ready(PRESET_TTS_MODEL_ID):
                    raise RuntimeError("Kokoro model is not downloaded")
                current["voice_mode"] = VOICE_MODE_PRESET
                if current["voice_preset"] == ZIPVOICE_DEFAULT_PRESET_ID:
                    current["voice_preset"] = DEFAULT_PRESET_ID
            elif normalized_tts_model == CUSTOM_TTS_MODEL_ID:
                if not local_models.is_ready(CUSTOM_TTS_MODEL_ID):
                    raise RuntimeError("ZipVoice model is not downloaded")
                if current["voice_mode"] != VOICE_MODE_CUSTOM:
                    current["voice_preset"] = ZIPVOICE_DEFAULT_PRESET_ID
            current["tts_model"] = normalized_tts_model
        if voice_mode is not None:
            normalized_mode = str(voice_mode).strip().lower()
            if normalized_mode not in {VOICE_MODE_PRESET, VOICE_MODE_CUSTOM}:
                raise ValueError("voice_mode must be preset or custom")
            if normalized_mode == VOICE_MODE_CUSTOM and not local_models.is_ready(CUSTOM_TTS_MODEL_ID):
                raise RuntimeError("ZipVoice model is not downloaded")
            current["voice_mode"] = normalized_mode
            if normalized_mode == VOICE_MODE_CUSTOM:
                current["tts_model"] = CUSTOM_TTS_MODEL_ID
            elif current.get("tts_model") == CUSTOM_TTS_MODEL_ID:
                current["voice_preset"] = ZIPVOICE_DEFAULT_PRESET_ID
        if voice_preset is not None:
            normalized_preset = str(voice_preset).strip()
            if normalized_preset not in _VOICE_PRESET_BY_ID:
                raise ValueError("unknown voice preset")
            required_model = (
                CUSTOM_TTS_MODEL_ID
                if normalized_preset == ZIPVOICE_DEFAULT_PRESET_ID
                else PRESET_TTS_MODEL_ID
            )
            if not local_models.is_ready(required_model):
                raise RuntimeError("selected voice model is not downloaded")
            current["voice_preset"] = normalized_preset
            current["tts_model"] = (
                CUSTOM_TTS_MODEL_ID
                if normalized_preset == ZIPVOICE_DEFAULT_PRESET_ID
                else PRESET_TTS_MODEL_ID
            )
        resolved_model, resolved_provider = _resolved_tts_model(
            current,
            minimax_configured=minimax_tts.is_configured(),
        )
        if (
            resolved_provider == "local"
            and resolved_model == CUSTOM_TTS_MODEL_ID
            and current["voice_mode"] == VOICE_MODE_CUSTOM
            and not _profile_ready(current)
        ):
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
    if not local_models.is_ready(CUSTOM_TTS_MODEL_ID):
        raise RuntimeError("ZipVoice model is not downloaded")
    text = str(reference_text or "").strip()
    if not text:
        raise ValueError("reference text is required")
    if len(text) > 1_000:
        raise ValueError("reference text is too long")
    samples, sample_rate = _decode_audio(payload)
    duration = len(samples) / sample_rate
    if duration < 1 or duration > 15:
        raise ValueError("reference audio must be between 1 and 15 seconds")
    with _VOICE_PROFILE_LOCK:
        VOICE_ROOT.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix="reference-", suffix=".wav", dir=VOICE_ROOT)
        os.close(fd)
        temporary = Path(temporary_name)
        backup = VOICE_ROOT / f"reference-backup-{os.getpid()}-{threading.get_ident()}.wav"
        had_previous = REFERENCE_AUDIO.exists()
        reference_replaced = False
        try:
            sf.write(temporary, samples, sample_rate, subtype="PCM_16")
            if had_previous:
                os.replace(REFERENCE_AUDIO, backup)
            os.replace(temporary, REFERENCE_AUDIO)
            reference_replaced = True
            current = _settings()
            current["reference_text"] = text
            current["voice_mode"] = VOICE_MODE_CUSTOM
            current["tts_model"] = CUSTOM_TTS_MODEL_ID
            config_store.set_setting("voice", current)
        except Exception:
            if reference_replaced:
                REFERENCE_AUDIO.unlink(missing_ok=True)
            if had_previous and backup.exists():
                os.replace(backup, REFERENCE_AUDIO)
            raise
        finally:
            temporary.unlink(missing_ok=True)
            backup.unlink(missing_ok=True)
    reset_zipvoice_tts()
    return status()


def delete_voice_profile() -> dict[str, Any]:
    with _VOICE_PROFILE_LOCK:
        VOICE_ROOT.mkdir(parents=True, exist_ok=True)
        backup = VOICE_ROOT / f"reference-delete-{os.getpid()}-{threading.get_ident()}.wav"
        had_previous = REFERENCE_AUDIO.exists()
        try:
            if had_previous:
                os.replace(REFERENCE_AUDIO, backup)
            current = _settings()
            current["reference_text"] = ""
            current["voice_mode"] = VOICE_MODE_PRESET
            if current.get("tts_model") == CUSTOM_TTS_MODEL_ID:
                current["voice_preset"] = ZIPVOICE_DEFAULT_PRESET_ID
            config_store.set_setting("voice", current)
        except Exception:
            if had_previous and backup.exists():
                os.replace(backup, REFERENCE_AUDIO)
            raise
        finally:
            backup.unlink(missing_ok=True)
    reset_zipvoice_tts()
    return status()


def _load_zipvoice_tts() -> Any:
    global _ZIPVOICE_TTS
    if not local_models.is_ready(CUSTOM_TTS_MODEL_ID):
        raise RuntimeError("ZipVoice model is not downloaded")
    if _ZIPVOICE_TTS is not None:
        return _ZIPVOICE_TTS
    import sherpa_onnx

    root = local_models.model_dir(CUSTOM_TTS_MODEL_ID)
    provider = local_models.sherpa_provider(CUSTOM_TTS_MODEL_ID)
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
    _ZIPVOICE_TTS = sherpa_onnx.OfflineTts(config)
    return _ZIPVOICE_TTS


def _load_kokoro_tts() -> Any:
    global _KOKORO_TTS
    if not local_models.is_ready(PRESET_TTS_MODEL_ID):
        raise RuntimeError("Kokoro model is not downloaded")
    if _KOKORO_TTS is not None:
        return _KOKORO_TTS
    import sherpa_onnx

    root = local_models.model_dir(PRESET_TTS_MODEL_ID)
    config = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
                model=str(root / "model.onnx"),
                voices=str(root / "voices.bin"),
                tokens=str(root / "tokens.txt"),
                data_dir=str(root / "espeak-ng-data"),
                lexicon=f"{root / 'lexicon-us-en.txt'},{root / 'lexicon-zh.txt'}",
            ),
            debug=False,
            num_threads=max(1, min(4, (os.cpu_count() or 2) // 2)),
            provider=local_models.sherpa_provider(PRESET_TTS_MODEL_ID),
        )
    )
    if not config.validate():
        raise RuntimeError("Kokoro model configuration is invalid")
    _KOKORO_TTS = sherpa_onnx.OfflineTts(config)
    return _KOKORO_TTS


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
    with _VOICE_PROFILE_LOCK:
        settings = _settings()
        selected_model, tts_provider = _resolved_tts_model(
            settings,
            minimax_configured=minimax_tts.is_configured(),
        )
    if tts_provider == "minimax":
        try:
            return minimax_tts.synthesize(
                content,
                model=selected_model,
                voice_id=settings.get("minimax_voice_id") or minimax_tts.DEFAULT_VOICE_ID,
            )
        except RuntimeError:
            if settings.get("tts_model") != TTS_MODEL_AUTO:
                raise
            # Automatic mode prefers MiniMax but keeps the existing local voice
            # as a transparent availability fallback.
            fallback_model = _local_tts_model(settings)
            fallback_ready = _runtime_available() and local_models.is_ready(fallback_model)
            if (
                fallback_model == CUSTOM_TTS_MODEL_ID
                and settings["voice_mode"] == VOICE_MODE_CUSTOM
            ):
                fallback_ready = fallback_ready and _profile_ready(settings)
            if not fallback_ready:
                raise
            selected_model = fallback_model

    with _VOICE_PROFILE_LOCK:
        custom_selected = (
            selected_model == CUSTOM_TTS_MODEL_ID
            and settings["voice_mode"] == VOICE_MODE_CUSTOM
        )
        uses_zipvoice = selected_model == CUSTOM_TTS_MODEL_ID
        if uses_zipvoice:
            if custom_selected:
                reference_path, reference_text = _active_reference(settings)
            else:
                reference_path = local_models.model_dir(CUSTOM_TTS_MODEL_ID) / "preset-default.wav"
                reference_text = DEFAULT_PRESET_TEXT
            reference_audio, reference_sample_rate = sf.read(
                reference_path,
                dtype="float32",
                always_2d=True,
            )
            reference_audio = np.ascontiguousarray(reference_audio.mean(axis=1), dtype=np.float32)
    with _TTS_LOCK:
        import sherpa_onnx

        generation = sherpa_onnx.GenerationConfig()
        if uses_zipvoice:
            selected_num_steps = TTS_NUM_STEPS if num_steps is None else int(num_steps)
            if selected_num_steps not in TTS_ALLOWED_NUM_STEPS:
                raise ValueError("num_steps must be 4 or 6")
            tts = _load_zipvoice_tts()
            generation.reference_audio = reference_audio
            generation.reference_sample_rate = int(reference_sample_rate)
            generation.reference_text = reference_text
            # The first queued sentence uses four distilled-flow steps for a
            # faster first audible result; later sentences use six for quality.
            generation.num_steps = selected_num_steps
            generation.speed = TTS_SPEED
            generation.silence_scale = TTS_SILENCE_SCALE
            generation.extra["min_char_in_sentence"] = "10"
        else:
            preset_id = settings["voice_preset"]
            if preset_id == ZIPVOICE_DEFAULT_PRESET_ID:
                preset_id = DEFAULT_PRESET_ID
            preset = _VOICE_PRESET_BY_ID[preset_id]
            tts = _load_kokoro_tts()
            generation.sid = int(preset["sid"])
            generation.speed = 1.0
        audio = tts.generate(content, generation)
        if len(audio.samples) == 0:
            engine_name = "ZipVoice" if uses_zipvoice else "Kokoro"
            raise RuntimeError(f"{engine_name} generated empty audio")
        smoothed_samples = _smooth_tts_audio_edges(audio.samples, int(audio.sample_rate))
        output = io.BytesIO()
        sf.write(output, smoothed_samples, int(audio.sample_rate), format="WAV", subtype="PCM_16")
        return output.getvalue()


def reset_asr() -> None:
    global _RECOGNIZER, _PUNCTUATION
    with _ASR_LOCK:
        _RECOGNIZER = None
        _PUNCTUATION = None


def reset_kokoro_tts() -> None:
    global _KOKORO_TTS
    with _TTS_LOCK:
        _KOKORO_TTS = None


def reset_zipvoice_tts() -> None:
    global _ZIPVOICE_TTS
    with _TTS_LOCK:
        _ZIPVOICE_TTS = None


def reset_tts() -> None:
    reset_kokoro_tts()
    reset_zipvoice_tts()


local_models.register_resetter(ASR_MODEL_ID, reset_asr)
local_models.register_resetter(PRESET_TTS_MODEL_ID, reset_kokoro_tts)
local_models.register_resetter(CUSTOM_TTS_MODEL_ID, reset_zipvoice_tts)
