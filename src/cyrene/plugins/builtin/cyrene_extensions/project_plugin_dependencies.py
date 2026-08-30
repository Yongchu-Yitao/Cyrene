"""Install and activate project Plugins required by environment extensions."""

from __future__ import annotations

import logging
import threading
from typing import Any

from cyrene.platform import settings_store
from cyrene.plugins import WORKSPACE_PROJECT_TYPE, application_plugin_scope
from cyrene.plugins.native_tools import restore_builtin_plugin

logger = logging.getLogger(__name__)

# Bundled marketplace entries are needed when a user removed the corresponding
# pack, so its own contribution metadata is no longer available to inspect.
BUILTIN_PROJECT_PLUGIN_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "toolchain:python": ("cyrene_project_python",),
    "toolchain:uv": ("cyrene_project_python",),
    "toolchain:node": ("cyrene_project_javascript",),
    "toolchain:bun": ("cyrene_project_javascript",),
    "toolchain:deno": ("cyrene_project_javascript",),
    "toolchain:tex": ("cyrene_project_tex",),
    "toolchain:go": ("cyrene_project_go",),
    "toolchain:java": ("cyrene_project_java",),
    "toolchain:rust": ("cyrene_project_rust",),
    "cli:github-cli": ("cyrene_project_github",),
}

_RECONCILE_LOCK = threading.RLock()
_LINK_SETTING = "extension_project_plugin_links"


def _registered_dependencies(host: Any, dependency: str) -> set[str]:
    return {
        pack.id
        for pack in host.registry.list_packs()
        for contribution in pack.extensions.values(WORKSPACE_PROJECT_TYPE)
        if dependency in contribution.runtime_extensions
    }


def ensure_project_plugins(
    kind: str,
    extension_id: str,
    *,
    force_enable: bool,
) -> list[dict[str, Any]]:
    """Reconcile one installed extension with its project Plugin dependencies.

    Passive system-PATH discovery auto-enables a dependency once. Explicit
    install, bind, or enable actions force-enable it again. This preserves a
    later manual project-Plugin disable instead of undoing it on every list
    refresh.
    """

    normalized_kind = str(kind or "").strip().lower()
    normalized_id = str(extension_id or "").strip()
    if not normalized_kind or not normalized_id:
        return []
    dependency = f"{normalized_kind}:{normalized_id}"
    host = application_plugin_scope()
    if host is None:
        return []
    with _RECONCILE_LOCK:
        pack_ids = set(BUILTIN_PROJECT_PLUGIN_DEPENDENCIES.get(dependency, ()))
        pack_ids.update(_registered_dependencies(host, dependency))
        if not pack_ids:
            return []

        raw_links = settings_store.get(_LINK_SETTING, {})
        links = dict(raw_links) if isinstance(raw_links, dict) else {}
        reconciled = {
            str(item) for item in links.get(dependency, ())
        } if isinstance(links.get(dependency, ()), (list, tuple)) else set()
        registered = {pack.id for pack in host.registry.list_packs()}
        installed_now: set[str] = set()
        missing = sorted(pack_ids - registered)
        if missing:
            for pack_id in missing:
                restore_builtin_plugin(host.plugin_directory, pack_id)
            failures = host.registry.refresh_directory(host.plugin_directory)
            host.load_failures = tuple(failures)
            registered = {pack.id for pack in host.registry.list_packs()}
            installed_now = set(missing) & registered

        statuses: list[dict[str, Any]] = []
        activation_changed = False
        for pack_id in sorted(pack_ids):
            if pack_id not in registered:
                statuses.append({
                    "packId": pack_id,
                    "installed": False,
                    "enabled": False,
                    "error": "project_plugin_unavailable",
                })
                continue
            was_enabled = host.registry.pack_enabled(pack_id)
            should_enable = force_enable or pack_id not in reconciled or pack_id in installed_now
            if should_enable and not was_enabled:
                host.registry.set_pack_enabled(pack_id, True)
                activation_changed = True
            reconciled.add(pack_id)
            statuses.append({
                "packId": pack_id,
                "installed": True,
                "installedNow": pack_id in installed_now,
                "enabled": host.registry.pack_enabled(pack_id),
                "enabledNow": should_enable and not was_enabled,
            })

        if activation_changed:
            snapshot = host.registry.activation.snapshot()
            settings_store.save_enabled_plugins(snapshot.plugins)
            settings_store.save_enabled_plugin_packs(snapshot.packs)
        next_links = {**links, dependency: sorted(reconciled)}
        if next_links != links:
            settings_store.set_(_LINK_SETTING, next_links)
        return statuses


def reconcile_installed_cards(cards: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Passively link every enabled runtime/CLI observed by Extension Center."""

    result: dict[str, list[dict[str, Any]]] = {}
    for card in cards:
        if card.get("observed_state") != "installed" or not card.get("enabled"):
            continue
        key = f"{card.get('kind')}:{card.get('id')}"
        try:
            statuses = ensure_project_plugins(
                str(card.get("kind") or ""),
                str(card.get("id") or ""),
                force_enable=False,
            )
        except Exception:
            logger.exception("Unable to reconcile project Plugin dependency for %s", key)
            continue
        if statuses:
            result[key] = statuses
    return result


__all__ = [
    "BUILTIN_PROJECT_PLUGIN_DEPENDENCIES",
    "ensure_project_plugins",
    "reconcile_installed_cards",
]
