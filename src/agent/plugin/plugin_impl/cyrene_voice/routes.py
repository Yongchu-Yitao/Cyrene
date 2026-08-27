"""Local ASR, TTS, and reference-voice API routes."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Form, UploadFile
from fastapi.responses import JSONResponse, Response

from cyrene.localization import localized
from route.errors import localized_error_response

from . import engine

logger = logging.getLogger(__name__)


def _error(
    exc: Exception,
    *,
    en: str,
    zh: str,
    code: str,
    unavailable_status: int = 409,
) -> JSONResponse:
    status_code = unavailable_status if isinstance(exc, RuntimeError) else 400
    logger.warning("Voice operation failed (%s)", code, exc_info=(type(exc), exc, exc.__traceback__))
    return localized_error_response(en, zh, status_code, code)


async def _audio_bytes(audio: UploadFile, service: Any) -> bytes:
    payload = await audio.read(service.MAX_AUDIO_BYTES + 1)
    if len(payload) > service.MAX_AUDIO_BYTES:
        raise ValueError(localized("Audio file is too large.", "音频文件过大。"))
    return payload


def register_voice_routes(router: APIRouter, *, service: Any = engine) -> None:
    @router.get("/api/voice/status")
    async def api_voice_status():
        return service.status()

    @router.put("/api/voice/settings")
    async def api_voice_settings(body: dict[str, Any]):
        boolean_settings = {"auto_read", "auto_send_after_asr", "auto_stop_on_silence"}
        allowed = boolean_settings | {"voice_mode", "voice_preset", "tts_model"}
        changes = {key: body[key] for key in allowed if key in body}
        if not changes:
            return localized_error_response(
                "A voice setting is required.",
                "请提供要更新的语音设置。",
                400,
                "voice_setting_required",
            )
        for key in boolean_settings:
            if key in changes:
                changes[key] = changes[key] is True
        try:
            return {"ok": True, **service.update_settings(**changes)}
        except (ValueError, RuntimeError, OSError) as exc:
            return _error(
                exc,
                en="Voice settings could not be updated.",
                zh="无法更新语音设置。",
                code="voice_settings_update_failed",
            )

    @router.post("/api/voice/profile")
    async def api_voice_profile(
        audio: UploadFile,
        reference_text: str = Form(...),
    ):
        try:
            payload = await _audio_bytes(audio, service)
            result = await asyncio.to_thread(
                service.save_voice_profile,
                payload,
                reference_text,
            )
            return {"ok": True, **result}
        except (ValueError, RuntimeError, OSError) as exc:
            return _error(
                exc,
                en="The reference voice could not be saved.",
                zh="无法保存参考音色。",
                code="voice_profile_save_failed",
            )

    @router.delete("/api/voice/profile")
    async def api_delete_voice_profile():
        try:
            return {"ok": True, **await asyncio.to_thread(service.delete_voice_profile)}
        except (ValueError, RuntimeError, OSError) as exc:
            return _error(
                exc,
                en="The reference voice could not be deleted.",
                zh="无法删除参考音色。",
                code="voice_profile_delete_failed",
            )

    @router.post("/api/voice/asr")
    async def api_voice_asr(audio: UploadFile):
        try:
            payload = await _audio_bytes(audio, service)
            return await asyncio.to_thread(service.transcribe, payload)
        except (ValueError, RuntimeError, OSError) as exc:
            return _error(
                exc,
                en="The audio could not be transcribed.",
                zh="无法识别该音频。",
                code="voice_transcription_failed",
            )

    @router.post("/api/voice/tts")
    async def api_voice_tts(body: dict[str, Any]):
        try:
            requested_steps = body.get("num_steps")
            content = service.normalize_tts_text(str(body.get("text") or ""))
            if not content:
                # Sentence chunking happens before backend normalization. A
                # display-only fragment is a valid no-op, not a fatal playback
                # error that should stop every later chunk in the queue.
                return Response(status_code=204, headers={"Cache-Control": "no-store"})
            payload = await asyncio.to_thread(
                service.synthesize,
                content,
                num_steps=requested_steps,
            )
            return Response(
                content=payload,
                media_type="audio/wav",
                headers={"Cache-Control": "no-store"},
            )
        except (ValueError, RuntimeError, OSError) as exc:
            return _error(
                exc,
                en="Speech could not be generated.",
                zh="无法生成语音。",
                code="voice_synthesis_failed",
            )


__all__ = ["register_voice_routes"]
