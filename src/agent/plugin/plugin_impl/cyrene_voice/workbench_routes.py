"""Voice Plugin-owned HTTP adapter for Workbench voice commands."""

from __future__ import annotations

from typing import Protocol

from fastapi import APIRouter, Form, UploadFile

from route.errors import localized_error_response


class VoiceCommandResult(Protocol):
    payload: dict
    status_code: int


class VoiceCommandService(Protocol):
    async def execute(
        self,
        audio: UploadFile,
        *,
        lang: str,
        ui_instance_id: str,
    ) -> VoiceCommandResult: ...


def register_voice_routes(
    router: APIRouter,
    service: VoiceCommandService,
) -> None:
    @router.post("/api/workbench/voice-command")
    async def api_workbench_voice_command(
        audio: UploadFile,
        lang: str = Form(""),
        ui_instance_id: str = Form(""),
    ):
        result = await service.execute(
            audio,
            lang=str(lang or ""),
            ui_instance_id=str(ui_instance_id or ""),
        )
        if result.status_code != 200:
            payload = dict(result.payload)
            raw_error = str(payload.get("error") or "")
            code = str(payload.get("code") or "")
            if not code and raw_error == "voice models are not ready":
                code = "voice_models_not_ready"
            elif not code and raw_error == "default project not found":
                code = "project_not_found"
            elif not code and result.status_code == 400:
                code = "invalid_voice_audio"
            elif not code and result.status_code == 409:
                code = "voice_unavailable"
            else:
                code = code or "voice_command_failed"
            messages = {
                "voice_models_not_ready": (
                    "Voice models are not ready yet.",
                    "语音模型尚未准备就绪。",
                ),
                "project_not_found": (
                    "The default project was not found.",
                    "未找到默认项目。",
                ),
                "invalid_voice_audio": (
                    "The audio file is invalid or too large.",
                    "音频文件无效或过大。",
                ),
                "voice_unavailable": (
                    "Voice input is temporarily unavailable.",
                    "语音输入暂时不可用。",
                ),
            }
            en, zh = messages.get(
                code,
                (
                    "The voice command could not be completed.",
                    "无法完成语音命令。",
                ),
            )
            details = {
                key: value
                for key, value in payload.items()
                if key not in {"code", "detail", "error", "message"}
            }
            return localized_error_response(
                en,
                zh,
                result.status_code,
                code,
                language=lang,
                **details,
            )
        return result.payload


__all__ = [
    "VoiceCommandResult",
    "VoiceCommandService",
    "register_voice_routes",
]
