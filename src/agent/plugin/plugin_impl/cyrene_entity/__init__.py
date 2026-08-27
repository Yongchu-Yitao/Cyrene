"""Editable durable-entity Plugin pack."""

from collections.abc import Mapping

from agent.plugin import (
    PluginApplicationContext,
    PluginPack,
    PluginSetupContext,
)

from .delete_entity import plugin as delete_plugin
from .list_entities import plugin as list_plugin
from .query_entities import plugin as query_plugin
from .track_entity import plugin as track_plugin
from .update_entity import plugin as update_plugin


def _session_id(data: Mapping[str, object]) -> str:
    direct = str(data.get("session_id") or "").strip()
    if direct:
        return direct
    run_context = data.get("run_context")
    if isinstance(run_context, Mapping):
        return str(run_context.get("session_id") or "").strip()
    return ""


def setup(context: PluginSetupContext) -> None:
    """Publish a session-scoped service created from this editable pack."""

    if context.services.get("entities") is not None:
        return
    db_path = str(context.data.get("db_path") or "").strip()
    if not db_path:
        return
    from .service import EntityService

    context.provide(
        "entities",
        EntityService(
            db_path,
            reminder_chat_id=context.data.get("chat_id"),
            origin_session_id=_session_id(context.data),
        ),
    )


def application_setup(context: PluginApplicationContext) -> None:
    from .application import setup_application

    setup_application(context)


plugin_pack = PluginPack(
    id="cyrene_entity",
    description="Track, search, update and delete durable entities.",
    plugins=(
        track_plugin,
        update_plugin,
        list_plugin,
        query_plugin,
        delete_plugin,
    ),
    setup=setup,
    application_setup=application_setup,
)


__all__ = ["application_setup", "plugin_pack", "setup"]
