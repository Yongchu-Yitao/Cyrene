"""Shared plugin state and mutations for tools, HTTP, and maintenance clients."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Literal

from cyrene.platform import settings_service, settings_store


def pack_status(registry: Any, pack: Any, host: Any = None) -> dict[str, Any]:
    enabled = registry.pack_enabled(pack.id)
    running = bool(host.pack_running(pack.id)) if host is not None else False
    return {
        "configured_enabled": registry.pack_configured_enabled(pack.id),
        "effective_enabled": enabled,
        "enabled": enabled,
        "enabled_count": sum(registry.plugin_enabled(p.name) for p in pack.plugins),
        "operational": bool(host.pack_operational(pack.id)) if host is not None else enabled,
        "running": running,
        "application_running": running if pack.has_application_contributions else None,
        "setup_error": getattr(host, "setup_failures", {}).get(pack.id, ""),
        "startup_error": getattr(host, "startup_failures", {}).get(pack.id, ""),
        "restart_required": pack.id in getattr(host, "restart_required_packs", ()),
    }


def plugin_status(registry: Any, registered: Any, host: Any = None) -> dict[str, Any]:
    enabled = registry.plugin_enabled(registered.plugin.name)
    pack = next((p for p in registry.list_packs() if p.id == registered.pack_id), None)
    parent = pack_status(registry, pack, host) if pack is not None else {}
    return {
        "configured_enabled": registry.plugin_configured_enabled(registered.plugin.name),
        "effective_enabled": enabled,
        "enabled": enabled,
        "operational": enabled and parent.get("operational", True),
        "running": enabled and parent.get("running", False),
        "application_running": parent.get("application_running"),
        "setup_error": parent.get("setup_error", ""),
        "startup_error": parent.get("startup_error", ""),
        "restart_required": parent.get("restart_required", False),
    }


def persist_activation(
    registry: Any, *, plugins: dict[str, bool], packs: dict[str, bool],
    actor: Literal["ui", "agent"], expected_revision: int | None = None,
) -> dict[str, Any]:
    """Commit a patch before changing memory; usable by synchronous discovery."""
    changes = {}
    if plugins:
        changes["enabled_plugins"] = plugins
    if packs:
        changes["enabled_plugin_packs"] = packs
    result = settings_service.update(
        "runtime", changes, actor=actor, expected_revision=expected_revision,
        # PluginManager is the dedicated activation mutation entry point. Keep
        # its existing authority, while retaining agent lock/self-disable checks.
        approved_risks=frozenset({"R2"}),
    )
    registry.configure_activation(
        plugins=settings_store.get_enabled_plugins(),
        packs=settings_store.get_enabled_plugin_packs(),
    )
    return result


async def update_activation(
    registry: Any, host: Any = None, *, plugins: dict[str, bool],
    packs: dict[str, bool], actor: Literal["ui", "agent"],
    expected_revision: int | None = None,
    publish_settings_changed: Any = None,
) -> dict[str, Any]:
    result = persist_activation(registry, plugins=plugins, packs=packs,
                                actor=actor, expected_revision=expected_revision)
    if host is not None and host.registry is registry:
        await host.reconcile_activation()
    publisher = publish_settings_changed or settings_service.publish_settings_changed
    await publisher("runtime", result["revision"], result["changed"])
    return result


async def delete_source(host: Any, source: Path):
    """Delete only a direct managed entry; callers resolve registered/failed identities."""
    from cyrene.plugins.native_tools import mark_builtin_plugin_deleted

    root = Path(host.plugin_directory).resolve()
    source = source.resolve()
    if source.parent != root or source.name.startswith((".", "_")):
        raise ValueError("Plugin source is not a managed installed entry")
    mark_builtin_plugin_deleted(root, source.name)
    if source.is_dir():
        shutil.rmtree(source)
    else:
        source.unlink()
    return await host.reload_user_plugins()
