"""Apply user-facing preferences associated with a chat send request."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cyrene.runtime.settings_store import get as get_setting, set_ as set_setting
from cyrene.workbench.notifications import append_notification


@dataclass(slots=True)
class VoiceCommandAttention:
    enabled: bool
    chat_id: str
    project_id: str
    chat_title: str


class ChatSendPreferencesApplicationService:
    @staticmethod
    def persist_language(lang: str) -> None:
        if lang not in {"en", "zh"}:
            return
        try:
            if str(get_setting("app_language", "") or "") != lang:
                set_setting("app_language", lang)
        except Exception:
            return

    @staticmethod
    def notify_voice_attention(
        request: VoiceCommandAttention,
        pending: Any,
    ) -> None:
        if not request.enabled:
            return
        question = pending if isinstance(pending, dict) else {}
        prompt = next(
            (
                str(question.get(key) or "").strip()
                for key in ("text", "prompt", "question", "title")
                if str(question.get(key) or "").strip()
            ),
            "Agent 正在等待你的回答。",
        )
        append_notification(
            title="语音命令需要你的回答",
            body=prompt,
            tab="mention",
            project_ref=request.project_id,
            source="voice_command_attention",
            source_label="语音命令",
            link_label=request.chat_title or "新对话",
            meta={"chatId": request.chat_id, "voiceCommand": True},
        )


__all__ = ["ChatSendPreferencesApplicationService", "VoiceCommandAttention"]
