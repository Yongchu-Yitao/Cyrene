"""Conversation-native durable Goal workflow Plugin pack."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from cyrene.core.plugin import (
    ExtensionContribution,
    Plugin,
    PluginPack,
    PluginSetupContext,
    application_plugin_service,
)
from cyrene.localization import localized
from cyrene.plugins import WORKBENCH_SLASH_COMMAND, WorkbenchSlashCommandContribution
from cyrene.plugins.context import PluginApplicationContext

from .service import ConversationGoalService
from .tools import PROPOSE_SCHEMA, SUBMIT_SCHEMA, propose_goal, submit_goal_result


def setup(context: PluginSetupContext) -> None:
    service = context.services.get("goal") or application_plugin_service("goal")
    if service is not None and context.services.get("goal") is None:
        context.provide("goal", service)


def _error(exc: Exception) -> JSONResponse:
    if isinstance(exc, LookupError):
        status, code = 404, "goal_not_found"
    elif isinstance(exc, ValueError):
        status, code = 409, "invalid_goal_transition"
    else:
        status, code = 500, "goal_operation_failed"
    return JSONResponse(
        {
            "ok": False,
            "code": code,
            "error": str(exc) or localized(
                "The Goal operation failed.",
                "目标操作失败。",
            ),
        },
        status_code=status,
    )


async def _body(request: Request) -> dict[str, Any]:
    try:
        value = await request.json()
    except Exception:
        value = {}
    return dict(value) if isinstance(value, Mapping) else {}


def application_setup(context: PluginApplicationContext) -> None:
    service = ConversationGoalService(db_path=context.db_path, bot=context.bot)
    context.provide("goal", service)
    context.on_startup(service.startup)
    context.on_shutdown(service.shutdown)
    context.expose_frontend("goal")

    @context.router.get("/api/workbench/chats/{chat_id}/goal")
    async def get_goal(chat_id: str):
        goal = await service.get(chat_id)
        return {
            "ok": True,
            "goal": goal,
            "activeGoal": service.public(goal) if goal is not None else None,
        }

    @context.router.post("/api/workbench/chats/{chat_id}/goal/confirm")
    async def confirm_goal(chat_id: str, request: Request):
        try:
            goal = await service.confirm(chat_id, await _body(request))
            return {"ok": True, "goal": goal, "activeGoal": service.public(goal)}
        except Exception as exc:
            return _error(exc)

    @context.router.patch("/api/workbench/chats/{chat_id}/goal")
    async def update_goal(chat_id: str, request: Request):
        try:
            goal = await service.update(chat_id, await _body(request))
            return {"ok": True, "goal": goal, "activeGoal": service.public(goal)}
        except Exception as exc:
            return _error(exc)

    def action_route(path: str, action: str) -> None:
        async def handler(chat_id: str):
            try:
                goal = await getattr(service, action)(chat_id)
                return {"ok": True, "goal": goal, "activeGoal": service.public(goal)}
            except Exception as exc:
                return _error(exc)

        context.router.add_api_route(
            f"/api/workbench/chats/{{chat_id}}/goal/{path}",
            handler,
            methods=["POST"],
            name=f"conversation_goal_{path}",
        )

    action_route("pause", "pause")
    action_route("resume", "resume")
    action_route("abort", "abort")
    action_route("accept", "accept")


plugin_pack = PluginPack(
    id="cyrene_goal",
    description=(
        "Define, execute, independently review, and continuously refine a Goal "
        "inside its owning conversation."
    ),
    plugins=(
        Plugin(
            name="propose_goal",
            description=(
                "Propose a concrete Goal for the user to edit and confirm. Use only "
                "during a /goal discussion after understanding the request."
            ),
            input_schema=PROPOSE_SCHEMA,
            handler=propose_goal,
            metadata={"main_only": True, "requires_order": True},
        ),
        Plugin(
            name="submit_goal_result",
            description=(
                "Submit the current Goal result and evidence to an independent reviewer. "
                "This does not declare completion by itself."
            ),
            input_schema=SUBMIT_SCHEMA,
            handler=submit_goal_result,
            metadata={"main_only": True, "requires_order": True},
        ),
    ),
    setup=setup,
    application_setup=application_setup,
    contributions=(
        ExtensionContribution(
            WORKBENCH_SLASH_COMMAND,
            WorkbenchSlashCommandContribution(
                id="goal",
                title="Goal",
                description="Discuss and confirm a durable goal before continuous execution.",
                system_prompt=(
                    "You are starting a conversation-native Goal workflow. Research the request "
                    "and discuss unclear success conditions with the user in the normal conversation. "
                    "Do not start execution yet. Once the objective and measurable acceptance criteria "
                    "are concrete, call propose_goal exactly once. The user will edit and confirm the "
                    "proposal in a modal before the durable Goal loop begins."
                ),
                workflow_service="goal",
                workflow_action="begin_negotiation",
                i18n={
                    "zh": {
                        "title": "目标",
                        "description": "先讨论并确认目标，再开始持续执行。",
                    }
                },
            ),
        ),
    ),
    metadata={
        "i18n": {
            "en": {
                "name": "Conversation Goal",
                "description": "Durable Goal loops with independent review.",
            },
            "zh": {
                "name": "对话目标",
                "description": "在对话中持续执行目标，并进行独立审查。",
            },
        },
    },
)


__all__ = ["application_setup", "plugin_pack", "setup"]
