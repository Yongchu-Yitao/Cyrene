"""Explicit tool exposure for the personal assistant entry point.

Core tools (including dynamic task contexts) are supplied by AgentSession.
This profile adds capabilities per session, never through global visibility.
"""

from dataclasses import replace
from cyrene.core.plugin.tool_scope import PluginToolScope

from cyrene.plugins.builtin.session_management import TOOL_NAMES
from cyrene.plugins.builtin.cyrene_cards import TOOL_NAMES as ASSISTANT_CARD_TOOL_NAMES
from .conversation_runtime import ConversationConfig

ASSISTANT_SCHEDULE_TOOL_NAMES = tuple(
    f"schedule.{action}" for action in ("create", "list", "edit", "pause", "resume", "cancel", "runs")
)
ASSISTANT_DIRECT_TOOL_NAMES = (*TOOL_NAMES, *ASSISTANT_SCHEDULE_TOOL_NAMES, *ASSISTANT_CARD_TOOL_NAMES, "ask_user", "send_message", "send_file", "WebSearch")


def personal_assistant_config(config: ConversationConfig) -> ConversationConfig:
    """Apply on every assistant run, including resume and notification wakes.

    The host must load session_management, cyrene_cards, ask_user, cyrene_delivery and
    cyrene_content with their application services. Task and ordinary chat
    entry points must use their own unmodified ConversationConfig.
    """
    return replace(
        config,
        extra_direct_tool_names=ASSISTANT_DIRECT_TOOL_NAMES,
        tool_scope=PluginToolScope(
            model_audience="assistant",
            tool_names=frozenset(ASSISTANT_DIRECT_TOOL_NAMES),
            excluded_tool_names=frozenset({"toolbox"}),
        ),
    )
