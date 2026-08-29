"""Editable proactive Agent background Plugin pack."""

from cyrene.plugins.context import PluginApplicationContext
from cyrene.core.plugin import Plugin, PluginContext, PluginPack

from .service import ProactiveService, heartbeat_interval_seconds


async def proactive_heartbeat(
    _arguments: dict[str, object], context: PluginContext
) -> dict[str, object]:
    service = context.services.get("proactive")
    if service is None:
        raise RuntimeError("PluginContext.services['proactive'] is unavailable")
    outcome = await service.tick()
    status = str((outcome or {}).get("status") or "unknown")
    return {
        "ok": status not in {"error", "generation_timeout", "model_request_failed"},
        **dict(outcome or {}),
    }


def application_setup(context: PluginApplicationContext) -> None:
    from cyrene.runtime.settings_service import (
        PluginSettingsContribution,
        plugin_setting_spec,
    )

    context.provide(
        "proactive",
        ProactiveService(bot=context.bot, db_path=context.db_path),
    )
    context.provide(
        "proactive_settings",
        PluginSettingsContribution(specs=(
            plugin_setting_spec("heartbeat_interval", "integer", 1800, tab="agents", minimum=60, maximum=86400),
            plugin_setting_spec("agent_proactive", "boolean", True, tab="agents", apply_mode="next_run"),
        )),
    )
    context.expose_frontend("proactive")


plugin_pack = PluginPack(
    id="cyrene_proactive",
    description="Run optional proactive Agent work on an editable background policy.",
    plugins=(
        Plugin(
            name="proactive.heartbeat",
            description="Run one proactive Agent heartbeat. Hidden from models.",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=proactive_heartbeat,
            timeout_seconds=300.0,
            metadata={
                "model_visible": False,
                "i18n": {
                    "en": {
                        "name": "Proactive heartbeat",
                        "description": "Run one proactive Agent heartbeat.",
                    },
                    "zh": {
                        "name": "主动心跳",
                        "description": "执行一次主动 Agent 心跳。",
                    },
                },
                "background_job": {
                    "id": "proactive_heartbeat",
                    # Evaluated only after the pack and tool pass activation
                    # checks in BackgroundPluginHost. Merely discovering a
                    # disabled pack must not read its settings.
                    "interval_seconds": heartbeat_interval_seconds,
                    "coalesce": True,
                    "max_instances": 1,
                    # Startup should not postpone the first eligibility check
                    # by a full cadence.  Daytime, cooldown, busy-session, and
                    # lottery guards still apply inside the service.
                    "run_on_start": True,
                },
            },
        ),
    ),
    application_setup=application_setup,
    metadata={
        "i18n": {
            "en": {
                "name": "Proactive Agent",
                "description": "Run optional proactive Agent work in the background.",
            },
            "zh": {
                "name": "主动 Agent",
                "description": "在后台按策略执行可选的主动 Agent 工作。",
            },
        }
    },
)


__all__ = ["application_setup", "plugin_pack"]
