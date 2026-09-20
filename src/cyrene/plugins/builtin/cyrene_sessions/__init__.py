"""Editable tools for messaging other running or idle Cyrene conversations."""
from __future__ import annotations

from cyrene.core.plugin import Plugin, PluginPack
from cyrene.core.plugin.execution import current_plugin_execution
from cyrene.plugins.native_runtime import run_context_value


def _identity(context):
    if str(run_context_value(context, "agent_id", "main")) != "main":
        raise ValueError("Only the conversation's main Agent can send session messages.")
    sender = str(run_context_value(context, "session_id") or "").strip()
    if not sender:
        raise ValueError("An active Workbench session is required.")
    return sender


async def list_sessions(_arguments, context):
    service = context.services["session_messaging"]
    return {"sessions": await service.list_sessions(_identity(context))}


async def send_session_message(arguments, context):
    service = context.services["session_messaging"]
    execution = current_plugin_execution()
    return await service.send(
        _identity(context), str(arguments["target_session_id"]).strip(),
        str(arguments["content"]),
        f"{run_context_value(context, 'round_id')}:{execution.call.id}" if execution else "",
    )


def application_setup(context):
    from cyrene.workbench.application.app_services import chat_application_port
    from .service import SessionMessagingService

    service = SessionMessagingService(
        context.data_directory / "plugin_data" / "cyrene_sessions", chat_application_port,
    )
    context.provide("session_messaging", service)
    context.on_startup(service.start)
    context.on_shutdown(service.stop)


plugin_pack = PluginPack(
    id="cyrene_sessions",
    description="Discover running conversations and send messages to other session Agents.",
    plugins=(
        Plugin(
            name="list_sessions",
            description="List other currently running built-in Cyrene chat sessions. Returns session_id and title; no arguments.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=list_sessions,
            metadata={"main_only": True, "read_only": True, "permission_review": False,
                      "i18n": {"en": {"name": "List running sessions", "description": "List other running conversations."},
                               "zh": {"name": "列出运行中的对话", "description": "列出其他正在运行的对话及其会话 ID。"}}},
        ),
        Plugin(
            name="send_session_message",
            description="Send text to another built-in Cyrene chat session. The source session ID and title are attached automatically. Idle targets are awakened; busy targets receive queued input. Returns after durable acceptance, not after a reply. Reply using this same tool and the source session ID. Do not send acknowledgement-only replies.",
            input_schema={"type": "object", "properties": {
                "target_session_id": {"type": "string", "minLength": 1, "maxLength": 160},
                "content": {"type": "string", "minLength": 1, "maxLength": 20000},
            }, "required": ["target_session_id", "content"], "additionalProperties": False},
            handler=send_session_message,
            metadata={"main_only": True, "permission_review": False,
                      "i18n": {"en": {"name": "Send session message", "description": "Send a message to another conversation, waking it if idle."},
                               "zh": {"name": "发送对话消息", "description": "向其他对话的 Agent 发送消息，空闲时自动唤醒；回复也使用本工具。"}}},
        ),
    ),
    application_setup=application_setup,
    metadata={"i18n": {
        "en": {"name": "Session communication", "description": "Discover running sessions and exchange Agent messages."},
        "zh": {"name": "对话通信", "description": "发现运行中的对话，让各对话的 Agent 互相发送消息。"},
    }},
)
