from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Form, UploadFile
from fastapi.responses import JSONResponse

from cyrene.workbench.chat_events import publish_chat_changed
from route.workbench.chat_routes.context import ChatRouteContext


async def _transcribe_voice_audio(audio: UploadFile):
    from cyrene.voice import engine as voice_engine

    try:
        voice_status = await asyncio.to_thread(voice_engine.status)
        if not voice_status.get("asr_ready") or not voice_status.get("tts_ready"):
            return JSONResponse(
                {"error": "voice models are not ready", "created": False},
                status_code=409,
            )
        audio_payload = await audio.read(voice_engine.MAX_AUDIO_BYTES + 1)
        if len(audio_payload) > voice_engine.MAX_AUDIO_BYTES:
            raise ValueError("audio file is too large")
        return await asyncio.to_thread(voice_engine.transcribe, audio_payload)
    except (ValueError, RuntimeError, OSError) as exc:
        return JSONResponse(
            {"error": str(exc), "created": False},
            status_code=409 if isinstance(exc, RuntimeError) else 400,
        )


def register_voice_routes(
    router: APIRouter,
    context: ChatRouteContext,
    *,
    send_chat_detached,
) -> None:
    service = context.service
    _routes = context.runtime

    @router.post("/api/workbench/voice-command")
    async def api_workbench_voice_command(
        audio: UploadFile,
        lang: str = Form(""),
        ui_instance_id: str = Form(""),
    ):
        """Transcribe first, then silently create and dispatch a default-project chat.

        Keeping ASR and chat creation in one backend operation guarantees that
        empty/silence-only captures never leave an orphan conversation behind.
        """
        transcript = await _transcribe_voice_audio(audio)
        if isinstance(transcript, JSONResponse):
            return transcript

        text = str((transcript or {}).get("text") or "").strip()
        if not text or bool((transcript or {}).get("silence_only")):
            return {"ok": True, "created": False, "text": ""}

        R = _routes()
        store = await asyncio.to_thread(R.read_store)
        projects = store.get("projects", []) or []
        default_project = next(
            (project for project in projects if R.project_data_key(project) == "default"),
            None,
        )
        if default_project is None and projects:
            default_project = projects[0]
        project_id = str((default_project or {}).get("id") or "")
        if not project_id:
            return JSONResponse(
                {"error": "default project not found", "created": False},
                status_code=404,
            )

        memory_snapshot = await context.project_memory_snapshot(project_id)

        def create_and_persist() -> dict[str, Any]:
            payload = service.repository.read()
            chat = service.create_chat(
                project_id,
                "",
                R.get_model(),
                project_memory_snapshot=memory_snapshot,
            )
            chat["permissionMode"] = "auto"
            payload.setdefault("chats", []).insert(0, chat)
            service.repository.write(payload)
            return chat

        chat = await asyncio.to_thread(create_and_persist)
        chat_id = str(chat.get("id") or "")
        await publish_chat_changed(chat_id, project_id, "created")

        dispatch = await send_chat_detached(
            chat_id,
            {
                "message": text,
                "mode": "auto",
                "lang": lang if lang in {"en", "zh"} else "",
                "stream": True,
                "uiInstanceId": str(ui_instance_id or ""),
                "voiceCommand": True,
            },
            detached=True,
        )
        if isinstance(dispatch, JSONResponse):
            try:
                dispatch_payload = json.loads(bytes(dispatch.body).decode("utf-8"))
            except Exception:
                dispatch_payload = {"error": "voice command dispatch failed"}
            if not 200 <= dispatch.status_code < 300:
                return JSONResponse(
                    {
                        **dispatch_payload,
                        "created": True,
                        "chat_id": chat_id,
                        "text": text,
                    },
                    status_code=dispatch.status_code,
                )
            return {
                "ok": True,
                "created": True,
                "text": text,
                **dispatch_payload,
            }
        return {"ok": True, "created": True, "text": text, "chat_id": chat_id}
