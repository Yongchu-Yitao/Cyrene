"""Application-owned facade for voice engine lifecycle and API operations."""

from __future__ import annotations

import shutil
from typing import Any

from . import engine


class VoiceService:
    """Keep voice engine ownership behind the application Plugin boundary."""

    MAX_AUDIO_BYTES = engine.MAX_AUDIO_BYTES

    def __init__(self) -> None:
        self.started = False

    def startup(self) -> None:
        self.started = True

    def shutdown(self) -> None:
        engine.reset_asr()
        engine.reset_tts()
        self.started = False

    def storage_paths(self) -> dict[str, tuple[Any, ...]]:
        return {"caches": (engine.VOICE_ROOT,)}

    def prepare_data_reset(self) -> dict[str, bool]:
        self.shutdown()
        existed = engine.VOICE_ROOT.exists()
        shutil.rmtree(engine.VOICE_ROOT, ignore_errors=True)
        return {"voice_cache": existed and not engine.VOICE_ROOT.exists()}

    def status(self) -> dict[str, Any]:
        return engine.status()

    def update_settings(self, **changes: Any) -> dict[str, Any]:
        return engine.update_settings(**changes)

    def save_voice_profile(
        self,
        payload: bytes,
        reference_text: str,
    ) -> dict[str, Any]:
        return engine.save_voice_profile(payload, reference_text)

    def delete_voice_profile(self) -> dict[str, Any]:
        return engine.delete_voice_profile()

    def transcribe(self, payload: bytes) -> dict[str, Any]:
        return engine.transcribe(payload)

    def normalize_tts_text(self, text: str) -> str:
        return engine.normalize_tts_text(text)

    def synthesize(
        self,
        text: str,
        *,
        num_steps: Any = None,
    ) -> bytes:
        return engine.synthesize(text, num_steps=num_steps)


__all__ = ["VoiceService"]
