"""Focused contracts for localized HTTP error envelopes."""

from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import APIRouter

from cyrene.workbench.http import errors as route_errors
from cyrene.plugins.builtin.cyrene_content.routes import register_search_routes
from cyrene.workbench.http.workbench.chat_routes import run_action_routes, run_send_routes
from cyrene.plugins.builtin.cyrene_voice.workbench_routes import register_voice_routes


def _json_body(response) -> dict:
    return json.loads(bytes(response.body).decode("utf-8"))


def _endpoint(router: APIRouter, path: str):
    return next(route.endpoint for route in router.routes if route.path == path)


def test_localized_error_payload_uses_stable_code_and_explicit_language() -> None:
    payload = route_errors.localized_error_payload(
        "Chat not found.",
        "未找到对话。",
        "chat_not_found",
        language="zh",
    )

    assert payload == {"error": "未找到对话。", "code": "chat_not_found"}


async def test_empty_search_query_is_localized(monkeypatch) -> None:
    monkeypatch.setattr(route_errors, "app_language", lambda _explicit=None: "zh")
    router = APIRouter()
    queries = SimpleNamespace(search_types=set(), search_workbench=None)
    register_search_routes(router, queries)

    result = await _endpoint(router, "/api/workbench/search")()

    assert result == {
        "ok": False,
        "error": "请输入搜索内容。",
        "code": "query_required",
    }


async def test_button_action_projection_follows_app_language(monkeypatch) -> None:
    chat = {
        "id": "chat-1",
        "projectId": "project-1",
        "messages": [{"id": "message-1", "role": "assistant", "content": "button"}],
    }
    repository = SimpleNamespace(
        get=lambda _chat_id: chat,
        write_one=lambda _chat, *, base_chat: None,
    )
    service = SimpleNamespace(
        repository=repository,
        has_button_block=lambda _content, _action_id: True,
        disable_button_block=lambda _content, _action_id: ("disabled", "Continue"),
        utc_now_iso=lambda: "2026-08-28T00:00:00Z",
    )

    async def publish_chat_changed(*_args, **_kwargs):
        return None

    monkeypatch.setattr(run_action_routes, "app_language", lambda: "en")
    monkeypatch.setattr(
        run_action_routes,
        "publish_chat_changed",
        publish_chat_changed,
    )
    result = await run_action_routes._prepare_action_send(
        SimpleNamespace(service=service),
        "chat-1",
        {"actionId": "continue", "messageId": "message-1", "value": ""},
    )

    assert result == {"message": "[Button action] Continue", "stream": False}


def test_side_agent_prompt_is_not_forced_to_chinese() -> None:
    operation = run_send_routes._SendOperation(
        SimpleNamespace(context=SimpleNamespace(), service=SimpleNamespace()),
        "chat-1",
        {},
        detached=False,
    )
    operation.message = "What does this imply?"
    operation.is_external_agent = False
    operation.command = ""
    operation.is_side_agent = True
    operation.chat = {"sourceQuote": "selected text"}
    operation.parent_transcript = "User: context"
    operation.lang = "en"
    operation.normalized = []

    operation._build_agent_message()

    assert "User question:" in operation.agent_message
    assert "用户问题" not in operation.agent_message


async def test_voice_route_does_not_expose_raw_service_error() -> None:
    class VoiceService:
        async def execute(self, _audio, *, lang: str, ui_instance_id: str):
            assert lang == "zh"
            assert ui_instance_id == "voice-ui"
            return SimpleNamespace(
                payload={
                    "error": "sensitive runtime path: /private/tmp/model.bin",
                    "created": False,
                },
                status_code=400,
            )

    router = APIRouter()
    register_voice_routes(router, VoiceService())
    response = await _endpoint(router, "/api/workbench/voice-command")(
        audio=object(),
        lang="zh",
        ui_instance_id="voice-ui",
    )

    assert _json_body(response) == {
        "error": "音频文件无效或过大。",
        "code": "invalid_voice_audio",
        "created": False,
    }
