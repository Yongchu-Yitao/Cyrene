"""Editable Cyrene skills Plugin pack."""

from types import ModuleType
from typing import Any

from agent.plugin import Plugin, PluginApplicationContext, PluginContext, PluginPack
from cyrene.runtime import config_store

from .learning_capture import setup_learning_capture

from . import (
    browser_user_events,
    get_learned_skill,
    install_skill,
    list_skills,
    load_skill,
    read_skill_resource,
    run_learned_skill,
    search_skills,
    uninstall_skill,
)


PATTERN_DETECTION_INTERVAL = int(
    config_store.get_env("PATTERN_DETECTION_INTERVAL", "600") or "600"
)


def application_setup(context: PluginApplicationContext) -> None:
    from .application import setup_application

    setup_application(context)


def _plugin(module: ModuleType) -> Plugin:
    function = module.TOOL_DEF["function"]
    name = str(function["name"])
    metadata: dict[str, Any] = dict(getattr(module, "TOOL_METADATA", {}))
    if name in {"InstallSkill", "UninstallSkill"}:
        metadata["main_only"] = True
    return Plugin(
        name=name,
        description=str(function.get("description") or ""),
        input_schema=dict(
            function.get("parameters")
            or {"type": "object", "properties": {}}
        ),
        handler=module.handler,
        allow_parallel=bool(
            metadata.get(
                "allow_parallel",
                not metadata.get("requires_order", True),
            )
        ),
        timeout_seconds=float(metadata.get("timeout_seconds", 180.0)),
        metadata=metadata,
    )


async def _learning_tick(
    _arguments: dict[str, Any], context: PluginContext
) -> dict[str, Any]:
    service = context.services.get("skills")
    if service is None:
        raise RuntimeError("PluginContext.services['skills'] is unavailable")
    await service.tick()
    return {"ok": True}


learning_job = Plugin(
    name="skills.learning.tick",
    description="Process pending behavior-learning turns. Hidden from models.",
    input_schema={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    handler=_learning_tick,
    timeout_seconds=600.0,
    metadata={
        "model_visible": False,
        "i18n": {
            "en": {
                "name": "Skill learning worker",
                "description": "Process pending behavior-learning turns.",
            },
            "zh": {
                "name": "技能学习任务",
                "description": "处理待分析的行为学习轮次。",
            },
        },
        "background_job": {
            "id": "behavior_learning",
            "interval_seconds": max(1, int(PATTERN_DETECTION_INTERVAL)),
            "coalesce": True,
            "max_instances": 1,
            "run_on_start": False,
        },
    },
)


plugin_pack = PluginPack(
    id="cyrene_skills",
    description="Install, inspect, load, and run Cyrene skills.",
    plugins=(
        *tuple(
            _plugin(module)
            for module in (
                browser_user_events,
                install_skill,
                uninstall_skill,
                list_skills,
                search_skills,
                load_skill,
                read_skill_resource,
                get_learned_skill,
                run_learned_skill,
            )
        ),
        learning_job,
    ),
    setup=setup_learning_capture,
    application_setup=application_setup,
    metadata={
        "i18n": {
            "en": {
                "name": "Skills",
                "description": "Install, inspect, load, run, and learn Cyrene skills.",
            },
            "zh": {
                "name": "技能",
                "description": "安装、检查、加载、运行并学习 Cyrene 技能。",
            },
        }
    },
)

__all__ = ["application_setup", "plugin_pack"]
