"""Editable durable-entity Plugin pack."""

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

from agent.hook import TURN_START, HookEvent
from agent.plugin import (
    PluginApplicationContext,
    PluginPack,
    PluginSetupContext,
)
from cyrene.localization import app_language, localized

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


def _register_proactive_context_hook(context: PluginSetupContext, plugin) -> None:
    hook_id = "cyrene-entity-proactive-turn-start"
    legacy_hook_id = "cyrene-entity-proactive-session-start"
    plugin_id = "cyrene_entity.proactive_context"
    existing = {hook.id: hook for hook in context.hooks.list()}
    if legacy_hook_id in existing:
        context.hooks.unregister(legacy_hook_id)
    if hook_id in existing:
        context.hooks.bind_plugin(plugin_id, plugin, replace=True)
        return
    context.hooks.register(
        TURN_START,
        plugin,
        plugin_id=plugin_id,
        hook_id=hook_id,
        root_only=True,
        failure_policy="open",
    )


def setup(context: PluginSetupContext) -> None:
    """Publish entity operations and their proactive context contribution."""

    service = context.services.get("entities")
    if service is None:
        db_path = str(context.data.get("db_path") or "").strip()
        if not db_path:
            return
        from .service import EntityService

        run_context = context.data.get("run_context")
        explicit_language = (
            run_context.get("language")
            if isinstance(run_context, Mapping)
            else None
        ) or context.data.get("language")
        service = EntityService(
            db_path,
            reminder_chat_id=context.data.get("chat_id"),
            origin_session_id=_session_id(context.data),
            language=app_language(explicit_language),
        )
        context.provide("entities", service)
    if context.hooks is None:
        return

    async def mount_proactive_entities(event: HookEvent) -> dict[str, str]:
        details = event.payload if isinstance(event.payload, Mapping) else {}
        metadata = details.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        if not bool(metadata.get("proactive")):
            return {}
        run_context = context.data.get("run_context")
        language = app_language(
            (
                run_context.get("language")
                if isinstance(run_context, Mapping)
                else None
            )
            or context.data.get("language")
        )

        now = datetime.now(timezone.utc)
        due_cutoff = (now + timedelta(hours=24)).isoformat()
        stale_cutoff = (now - timedelta(days=7)).isoformat()
        try:
            due_soon = await service.query(due_before=due_cutoff, status="active")
            active = await service.list(status="active", limit=200)
        except Exception:
            return {}
        stale = [
            item
            for item in active
            if str(item.get("last_referenced_at") or "") < stale_cutoff
        ]
        open_decisions = [
            item
            for item in active
            if str(item.get("type") or "") == "decision"
            and not (
                item.get("metadata", {}).get("outcome")
                if isinstance(item.get("metadata"), Mapping)
                else False
            )
        ]
        lines: list[str] = []
        if due_soon:
            titles = ", ".join(
                str(item.get("title") or "") for item in due_soon[:3]
            )
            lines.append(localized(
                "- Due within 24 hours: {titles}",
                "- 24 小时内到期：{titles}",
                language=language,
                titles=titles,
            ))
        if stale:
            lines.append(localized(
                "- Not referenced recently: {title}",
                "- 最近未提及：{title}",
                language=language,
                title=stale[0].get("title", ""),
            ))
        if open_decisions:
            lines.append(
                localized(
                    "- Open decision to follow up: {title}",
                    "- 待跟进的未决事项：{title}",
                    language=language,
                    title=open_decisions[0].get("title", ""),
                )
            )
        if not lines:
            return {}
        return {
            "context": localized(
                "## Items needing attention\n{items}",
                "## 需要关注的事务\n{items}",
                language=language,
                items="\n".join(lines),
            ),
            "context_kind": "proactive_entities",
            "context_source": "cyrene_entity",
        }

    _register_proactive_context_hook(context, mount_proactive_entities)


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
    metadata={
        "i18n": {
            "en": {
                "name": "Entities",
                "description": "Track, search, update and delete durable entities.",
            },
            "zh": {
                "name": "事务",
                "description": "跟踪、搜索、更新和删除持久事务。",
            },
        }
    },
)


__all__ = ["application_setup", "plugin_pack", "setup"]
