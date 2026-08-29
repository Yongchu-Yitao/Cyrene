"""Workbench voice-command application service owned by cyrene_voice."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from cyrene.localization import localized

from .service import VoiceService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class VoiceCommandResult:
    payload: dict[str, Any]
    status_code: int = 200


class WorkbenchChatPort(Protocol):
    async def create(self, command: Any) -> dict[str, Any]: ...

    async def send(self, chat_id: str, body: dict[str, Any]) -> dict[str, Any]: ...


class WorkbenchProjectPort(Protocol):
    async def list_projects(self) -> list[dict[str, Any]]: ...


class VoiceCommandApplicationService:
    """Transcribe audio, create the default-project chat, and dispatch it."""

    def __init__(
        self,
        voice: VoiceService,
        *,
        chat: WorkbenchChatPort,
        projects: WorkbenchProjectPort,
    ) -> None:
        self.voice = voice
        self.chat = chat
        self.projects = projects
        self.max_audio_bytes = int(voice.MAX_AUDIO_BYTES)

    async def _transcribe(
        self,
        audio: Any,
        *,
        language: str,
    ) -> VoiceCommandResult | dict[str, Any]:
        try:
            voice_status = await asyncio.to_thread(self.voice.status)
            if not voice_status.get("asr_ready") or not voice_status.get("tts_ready"):
                return VoiceCommandResult(
                    {
                        "error": localized(
                            "Voice models are not ready.",
                            "语音模型尚未就绪。",
                            language=language,
                        ),
                        "code": "voice_models_not_ready",
                        "created": False,
                    },
                    409,
                )
            audio_payload = await audio.read(self.max_audio_bytes + 1)
            if len(audio_payload) > self.max_audio_bytes:
                return VoiceCommandResult(
                    {
                        "error": localized(
                            "The audio file is too large.",
                            "音频文件过大。",
                            language=language,
                        ),
                        "code": "voice_audio_too_large",
                        "created": False,
                    },
                    400,
                )
            return await asyncio.to_thread(self.voice.transcribe, audio_payload)
        except (ValueError, RuntimeError, OSError) as exc:
            logger.warning("Voice transcription failed: %s", exc)
            return VoiceCommandResult(
                {
                    "error": localized(
                        "The audio could not be transcribed.",
                        "无法转写音频。",
                        language=language,
                    ),
                    "code": "voice_transcription_failed",
                    "created": False,
                },
                409 if isinstance(exc, RuntimeError) else 400,
            )

    async def execute(
        self,
        audio: Any,
        *,
        lang: str,
        ui_instance_id: str,
    ) -> VoiceCommandResult:
        language = lang if lang in {"en", "zh"} else ""
        transcript = await self._transcribe(audio, language=language)
        if isinstance(transcript, VoiceCommandResult):
            return transcript

        text = str((transcript or {}).get("text") or "").strip()
        if not text or bool((transcript or {}).get("silence_only")):
            return VoiceCommandResult(
                {"ok": True, "created": False, "text": ""}
            )

        projects = await self.projects.list_projects()
        default_project = next(
            (
                project
                for project in projects
                if str(project.get("dataKey") or "") == "default"
            ),
            None,
        )
        if default_project is None and projects:
            default_project = projects[0]
        project_id = str((default_project or {}).get("id") or "")
        if not project_id:
            return VoiceCommandResult(
                {
                    "error": localized(
                        "The default project was not found.",
                        "未找到默认项目。",
                        language=language,
                    ),
                    "code": "default_project_not_found",
                    "created": False,
                },
                404,
            )

        try:
            created = await self.chat.create({
                "project": project_id,
                "title": "",
            })
            chat = created.get("chat") if isinstance(created, dict) else None
            chat_id = str((chat or {}).get("id") or "")
            if not chat_id:
                raise RuntimeError("voice_command_chat_not_created")
            dispatch = await self.chat.send(chat_id, {
                "message": text,
                "mode": "auto",
                "lang": lang if lang in {"en", "zh"} else "",
                "stream": True,
                "uiInstanceId": str(ui_instance_id or ""),
                "voiceCommand": True,
            })
        except Exception as exc:
            status_code = int(getattr(exc, "status_code", 0) or 0)
            if not status_code:
                logger.exception("Voice command dispatch failed")
                return VoiceCommandResult(
                    {
                        "error": localized(
                            "The voice command could not be sent.",
                            "无法发送语音指令。",
                            language=language,
                        ),
                        "code": "voice_command_dispatch_failed",
                        "created": bool(locals().get("chat_id")),
                        "chat_id": str(locals().get("chat_id") or ""),
                        "text": text,
                    },
                    500,
                )
            payload = dict(getattr(exc, "payload", {}) or {})
            payload.setdefault("error", localized(
                "The voice command could not be sent.",
                "无法发送语音指令。",
                language=language,
            ))
            payload.setdefault("code", str(getattr(exc, "code", "") or ""))
            return VoiceCommandResult(
                {
                    **payload,
                    "created": bool(locals().get("chat_id")),
                    "chat_id": str(locals().get("chat_id") or ""),
                    "text": text,
                },
                status_code,
            )
        return VoiceCommandResult(
            {
                "ok": True,
                "created": True,
                "text": text,
                "chat_id": chat_id,
                **(dispatch if isinstance(dispatch, dict) else {}),
            }
        )


__all__ = [
    "VoiceCommandApplicationService",
    "VoiceCommandResult",
    "WorkbenchChatPort",
    "WorkbenchProjectPort",
]
