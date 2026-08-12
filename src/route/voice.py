"""Local ASR, TTS, and reference-voice API routes."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Form, UploadFile
from fastapi.responses import JSONResponse, Response

from cyrene.voice import engine


def _error(exc: Exception, *, unavailable_status: int = 409) -> JSONResponse:
    status_code = unavailable_status if isinstance(exc, RuntimeError) else 400
    return JSONResponse({"error": str(exc)}, status_code=status_code)


async def _audio_bytes(audio: UploadFile) -> bytes:
    payload = await audio.read(engine.MAX_AUDIO_BYTES + 1)
    if len(payload) > engine.MAX_AUDIO_BYTES:
        raise ValueError("audio file is too large")
    return payload


def register_voice_routes(router: APIRouter) -> None:
    @router.get("/api/voice/status")
    async def api_voice_status():
        return engine.status()

    @router.put("/api/voice/settings")
    async def api_voice_settings(body: dict[str, Any]):
        boolean_settings = {"auto_read", "auto_send_after_asr", "auto_stop_on_silence"}
        allowed = boolean_settings | {"voice_mode", "voice_preset"}
        changes = {key: body[key] for key in allowed if key in body}
        if not changes:
            return JSONResponse({"error": "voice setting is required"}, status_code=400)
        for key in boolean_settings:
            if key in changes:
                changes[key] = changes[key] is True
        try:
            return {"ok": True, **engine.update_settings(**changes)}
        except (ValueError, RuntimeError, OSError) as exc:
            return _error(exc)

    @router.post("/api/voice/profile")
    async def api_voice_profile(
        audio: UploadFile,
        reference_text: str = Form(...),
    ):
        try:
            payload = await _audio_bytes(audio)
            result = await asyncio.to_thread(
                engine.save_voice_profile,
                payload,
                reference_text,
            )
            return {"ok": True, **result}
        except (ValueError, RuntimeError, OSError) as exc:
            return _error(exc)

    @router.delete("/api/voice/profile")
    async def api_delete_voice_profile():
        try:
            return {"ok": True, **await asyncio.to_thread(engine.delete_voice_profile)}
        except (ValueError, RuntimeError, OSError) as exc:
            return _error(exc)

    @router.post("/api/voice/asr")
    async def api_voice_asr(audio: UploadFile):
        try:
            payload = await _audio_bytes(audio)
            return await asyncio.to_thread(engine.transcribe, payload)
        except (ValueError, RuntimeError, OSError) as exc:
            return _error(exc)

    @router.post("/api/voice/tts")
    async def api_voice_tts(body: dict[str, Any]):
        try:
            requested_steps = body.get("num_steps")
            payload = await asyncio.to_thread(
                engine.synthesize,
                str(body.get("text") or ""),
                num_steps=requested_steps,
            )
            return Response(
                content=payload,
                media_type="audio/wav",
                headers={"Cache-Control": "no-store"},
            )
        except (ValueError, RuntimeError, OSError) as exc:
            return _error(exc)


__all__ = ["register_voice_routes"]
