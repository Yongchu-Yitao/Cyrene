from __future__ import annotations

import json

import pytest

from agent.plugin import Plugin, PluginContext, PluginRegistry, PluginRuntime


@pytest.mark.asyncio
async def test_plugin_argument_validation_follows_invocation_language() -> None:
    async def handler(_arguments, _context):
        return "ok"

    registry = PluginRegistry(include_core=False)
    registry.register_plugin(
        Plugin(
            name="i18n_validation_probe",
            description="Probe validation localization.",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            handler=handler,
        ),
        source="test",
    )

    result = await PluginRuntime(registry).call(
        "i18n_validation_probe",
        {},
        PluginContext(data={"language": "zh"}),
    )

    assert result.success is False
    assert result.error == "插件参数无效。"


@pytest.mark.asyncio
async def test_map_and_cli_validation_follow_invocation_language(tmp_path) -> None:
    from agent.plugin.plugin_impl.cyrene_cli.tools import search_cli_plugins
    from agent.plugin.plugin_impl.cyrene_map.service import MapService, map_database
    from agent.plugin.plugin_impl.cyrene_map.tools import _tool_pin_location

    map_service = MapService(map_database(tmp_path))
    map_service.initialize()
    context = PluginContext(
        workspace=tmp_path,
        data={"language": "zh", "session_id": "map-i18n"},
        services={"maps": map_service},
    )

    map_result = json.loads(await _tool_pin_location(
        {"lat": 1, "lng": 2, "name": ""},
        context,
    ))
    cli_result = json.loads(await search_cli_plugins({}, context))

    assert map_result == {
        "status": "error",
        "code": "map_pin_name_required",
        "message": "必须填写标记名称。",
    }
    assert cli_result["error"] == "必须提供搜索关键词。"


@pytest.mark.asyncio
async def test_plugin_development_errors_and_guide_are_bilingual(tmp_path) -> None:
    from agent.plugin.plugin_impl.cyrene_plugin_development.tools import (
        authoring_guide,
        scaffold,
    )

    zh_context = PluginContext(workspace=tmp_path, data={"language": "zh"})
    en_context = PluginContext(workspace=tmp_path, data={"language": "en"})

    invalid = json.loads(await scaffold(
        {"path": "demo", "plugin_type": "unknown", "pack_id": "demo"},
        zh_context,
    ))
    zh_guide = json.loads(await authoring_guide({}, zh_context))["guide"]
    en_guide = json.loads(await authoring_guide({}, en_context))["guide"]

    assert "plugin_type 必须是" in invalid["error"]
    assert "创建 Cyrene 插件" in zh_guide
    assert "Create a Cyrene Plugin" in en_guide
    for plugin_type in (
        "standalone_tool", "tool_pack", "model_plugin", "context_plugin",
        "application_plugin", "ui_plugin", "full_pack",
    ):
        assert plugin_type in zh_guide
        assert plugin_type in en_guide
    assert "工具、模型 Provider、Context Hook、应用后端和 UI" in zh_guide


@pytest.mark.asyncio
async def test_voice_readiness_error_uses_requested_language() -> None:
    from agent.plugin.plugin_impl.cyrene_voice.voice_command import (
        VoiceCommandApplicationService,
    )

    class Voice:
        MAX_AUDIO_BYTES = 1024

        @staticmethod
        def status():
            return {"asr_ready": False, "tts_ready": False}

    class Audio:
        async def read(self, _limit):
            raise AssertionError("audio should not be read before readiness")

    service = VoiceCommandApplicationService(
        Voice(),
        chat=object(),
        projects=object(),
    )

    result = await service._transcribe(Audio(), language="zh")

    assert result.payload["code"] == "voice_models_not_ready"
    assert result.payload["error"] == "语音模型尚未就绪。"


def test_application_envelope_localizes_and_masks_error_summary(monkeypatch) -> None:
    from cyrene.workbench import app_control

    monkeypatch.setattr(
        app_control,
        "_active_context",
        lambda: PluginContext(data={"language": "zh"}),
    )

    success = app_control.envelope(
        "success",
        "cyrene.project.manage",
        "Projects listed.",
    )
    failure = app_control.envelope(
        "error",
        "cyrene.data.restore",
        "private filesystem diagnostic",
        error_code="backup_error",
    )

    assert success["summary"] == "已列出项目。"
    assert failure["summary"] == "备份操作失败。"
    assert "private filesystem diagnostic" not in json.dumps(failure)
