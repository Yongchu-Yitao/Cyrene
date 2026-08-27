from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from agent.plugin import PluginApplicationHost, PluginRegistry
from agent.plugin.plugin_impl.cyrene_voice import plugin_pack
from agent.plugin.plugin_impl.cyrene_voice import service as voice_service
from agent.plugin.plugin_impl.cyrene_voice.voice_command import (
    VoiceCommandApplicationService,
    VoiceCommandResult,
)


def _host(tmp_path: Path, registry: PluginRegistry) -> PluginApplicationHost:
    return PluginApplicationHost(
        app=FastAPI(),
        registry=registry,
        bot=None,
        db_path=str(tmp_path / "app.db"),
        data_directory=tmp_path / "data",
        plugin_directory=tmp_path / "plugin_impl",
    )


def test_voice_pack_owns_routes_service_lifecycle_and_frontend_marker(
    tmp_path,
    monkeypatch,
) -> None:
    registry = PluginRegistry(include_core=False)
    registry.register_pack(plugin_pack, source="test")
    host = _host(tmp_path, registry)
    router = APIRouter()
    host.attach(router)
    host.app.include_router(router)
    service = host.service("voice")
    voice_command = host.service("voice_command")
    assert service is not None
    assert voice_command is not None
    monkeypatch.setattr(service, "status", lambda: {"tts_ready": True})

    async def execute_voice_command(
        audio,
        *,
        lang: str,
        ui_instance_id: str,
    ) -> VoiceCommandResult:
        assert await audio.read() == b"RIFF"
        assert lang == "en"
        assert ui_instance_id == "voice-ui"
        return VoiceCommandResult(
            {"ok": True, "created": False, "text": "hello"}
        )

    monkeypatch.setattr(voice_command, "execute", execute_voice_command)

    assert host.frontend_modules == ["voice"]
    route_paths = {route.path for route in router.routes}
    assert "/api/voice/status" in route_paths
    assert "/api/workbench/voice-command" in route_paths
    with TestClient(host.app) as client:
        assert client.get("/api/voice/status").json() == {"tts_ready": True}
        response = client.post(
            "/api/workbench/voice-command",
            files={"audio": ("voice.wav", b"RIFF", "audio/wav")},
            data={"lang": "en", "ui_instance_id": "voice-ui"},
        )
        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
            "created": False,
            "text": "hello",
        }
        registry.set_pack_enabled("cyrene_voice", False)
        assert client.get("/api/voice/status").status_code == 404
        assert (
            client.post(
                "/api/workbench/voice-command",
                files={"audio": ("voice.wav", b"RIFF", "audio/wav")},
            ).status_code
            == 404
        )
        assert host.service("voice") is None
        assert host.service("voice_command") is None
        assert host.frontend_modules == []

    registry.set_pack_enabled("cyrene_voice", True)
    assert host.service("voice_command") is voice_command
    resets: list[str] = []
    monkeypatch.setattr(voice_service.engine, "reset_asr", lambda: resets.append("asr"))
    monkeypatch.setattr(voice_service.engine, "reset_tts", lambda: resets.append("tts"))

    async def lifecycle() -> None:
        await host.startup()
        assert service.started is True
        await host.shutdown()

    asyncio.run(lifecycle())
    assert service.started is False
    assert resets == ["asr", "tts"]


def test_voice_command_uses_only_generic_workbench_ports() -> None:
    class Voice:
        MAX_AUDIO_BYTES = 128

        def status(self):
            return {"asr_ready": True, "tts_ready": True}

        def transcribe(self, payload: bytes):
            assert payload == b"RIFF"
            return {"text": "hello"}

    class Projects:
        async def list_projects(self):
            return [{"id": "project-default", "dataKey": "default"}]

    calls: list[tuple[str, object]] = []

    class Chat:
        async def create(self, command):
            calls.append(("create", command))
            return {"ok": True, "chat": {"id": "chat-voice"}}

        async def send(self, chat_id, body):
            calls.append((chat_id, body))
            return {"run_id": "run-voice"}

    class Audio:
        async def read(self, size=-1):
            assert size == 129
            return b"RIFF"

    application = VoiceCommandApplicationService(
        Voice(),
        chat=Chat(),
        projects=Projects(),
    )
    result = asyncio.run(
        application.execute(Audio(), lang="en", ui_instance_id="voice-ui")
    )

    assert result.status_code == 200
    assert result.payload == {
        "ok": True,
        "created": True,
        "text": "hello",
        "chat_id": "chat-voice",
        "run_id": "run-voice",
    }
    assert calls == [
        ("create", {"project": "project-default", "title": ""}),
        (
            "chat-voice",
            {
                "message": "hello",
                "mode": "auto",
                "lang": "en",
                "stream": True,
                "uiInstanceId": "voice-ui",
                "voiceCommand": True,
            },
        ),
    ]
