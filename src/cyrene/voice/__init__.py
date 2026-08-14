"""Local speech recognition and synthesis services."""

from cyrene.voice.engine import (
    ASR_MODEL_ID,
    CUSTOM_TTS_MODEL_ID,
    PRESET_TTS_MODEL_ID,
    TTS_MODEL_ID,
    delete_voice_profile,
    save_voice_profile,
    status,
    synthesize,
    transcribe,
    update_settings,
)

__all__ = [
    "ASR_MODEL_ID",
    "CUSTOM_TTS_MODEL_ID",
    "PRESET_TTS_MODEL_ID",
    "TTS_MODEL_ID",
    "delete_voice_profile",
    "save_voice_profile",
    "status",
    "synthesize",
    "transcribe",
    "update_settings",
]
